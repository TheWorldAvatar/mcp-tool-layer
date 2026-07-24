from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_prompts import (
    build_coding_task_prompt,
    build_prompt_diagnosis_task_prompt,
    build_prompt_task_prompt,
    build_validation_task_prompt,
)
from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    artifact_manifest,
    parse_json_object,
    validate_diagnosis,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER,
    build_validation_report,
)

LOGGER = logging.getLogger(__name__)

SCRIPT_AGENT_RECURSION_LIMIT = 24
PROMPT_AGENT_RECURSION_LIMIT = 24
VALIDATION_AGENT_RECURSION_LIMIT = 12
AGENT_TIMEOUT_SECONDS = 600

_ARTIFACT_RE = re.compile(r"(?P<name>(?:[A-Za-z0-9_]+\.py|[A-Z][A-Z0-9_]*(?:_\d+)*\.md))")


def _progress(message: str, *args: Any) -> None:
    text = message % args if args else message
    LOGGER.info(text)
    print(f"[llm_agent] {text}", flush=True)


def _rel(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _context_summary(context: AgenticGenerationContext) -> dict[str, Any]:
    classes = context.parsed.get("classes") or {}
    properties = context.parsed.get("properties") or {}
    contract = context.contract or {}
    output_root = Path(context.output_root).resolve()
    summary: dict[str, Any] = {
        "ontology": {
            "name": context.ontology.name,
            "role": context.ontology.role,
            "ttl_file": context.ontology.ttl_file,
            "meta_task_config": context.ontology.meta_task_config_path,
        },
        "output_paths": {
            "filesystem_root": str(output_root),
            # Workspace MCP tools resolve paths against the repository root.
            "output_root": _rel(output_root),
            "scripts_dir": _rel(context.scripts_dir),
            "prompts_dir": _rel(context.prompts_dir),
            "parsed_markdown": _rel(context.parsed_markdown_path),
            "contract": _rel(context.contract_path),
            "validation_report": _rel(context.report_path),
        },
        "counts": {
            "classes": len(classes),
            "properties": len(properties),
        },
        "top_entity": contract.get("top_entity"),
        "ordered_member_profile": contract.get("ordered_member_profile"),
        "required_links": contract.get("required_links"),
        "step_scoped_object_properties": contract.get("step_scoped_object_properties"),
        "required_step_scoped_object_properties": contract.get(
            "required_step_scoped_object_properties"
        ),
        "artifact_policy": (
            "The deterministic files already present under scripts_dir/prompts_dir are scaffolds only. "
            "The LLM agents must inspect and patch them into final artifacts. "
            "Use agentic_generation_workspace MCP tools with repository-relative paths from "
            "output_paths (never assume cwd is the output_root)."
        ),
    }
    if context.ontology.name == "medical":
        summary["medical_csv_roundtrip_alignment"] = (
            f"Retain {MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER} in every EXTRACTION_ITER_* and KG_BUILDING_ITER_* prompt "
            "(except EXTRACTION_ITER_1). Machine validation fails if the marker is removed. Keep checklist values "
            "CSV-friendly, never use JSON booleans for checklist scalars, and keep linked target labels class-distinct."
        )
    return summary


def _content_diagnosis(context: AgenticGenerationContext) -> dict[str, Any] | None:
    path = Path(context.output_root) / "content_diagnosis_editor.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _snapshot_files(paths: list[str]) -> dict[str, str]:
    return {
        path: Path(path).read_text(encoding="utf-8")
        for path in paths
        if Path(path).is_file()
    }


def _prompt_protocol_report(
    *,
    before_manifest: dict[str, str],
    after_manifest: dict[str, str],
    before_targets: dict[str, str],
    targets: list[str],
    agent_result: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    metadata = agent_result.get("metadata") or {}
    activity = metadata.get("tool_activity") or {}
    executed = list(activity.get("executed_tool_names") or [])
    errors: list[str] = []
    if metadata.get("error"):
        errors.append(f"prompt_agent_error:{metadata.get('error')}")
    if not targets:
        errors.append("empty_prompt_target_set")
    if "read_workspace_file" not in executed:
        errors.append("prompt_targets_not_read")
    if "apply_unified_patch" not in executed:
        errors.append("prompt_targets_not_patched")
    if "write_workspace_file" in executed:
        errors.append("whole_file_write_forbidden")
    changed_prompts = sorted(
        path
        for path in set(before_manifest) | set(after_manifest)
        if path.startswith("prompts/") and before_manifest.get(path) != after_manifest.get(path)
    )
    changed_scripts = sorted(
        path
        for path in set(before_manifest) | set(after_manifest)
        if path.startswith("scripts/") and before_manifest.get(path) != after_manifest.get(path)
    )
    target_diffs: dict[str, str] = {}
    for target in targets:
        path = Path(target)
        before = before_targets.get(target, "")
        after = path.read_text(encoding="utf-8") if path.is_file() else ""
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
            )
        )
        target_diffs[target] = diff
    if not changed_prompts:
        errors.append("no_prompt_diff")
    if changed_scripts:
        errors.append("scripts_changed")
    try:
        allowed_relative = {
            Path(target).resolve().relative_to(output_root.resolve()).as_posix()
            for target in targets
        }
    except ValueError:
        allowed_relative = set()
        errors.append("prompt_target_outside_candidate")
    if changed_prompts and not set(changed_prompts).issubset(allowed_relative):
        errors.append("unauthorised_prompt_change")
    return {
        "ok": not errors,
        "failures": errors,
        "changed_prompts": changed_prompts,
        "changed_scripts": changed_scripts,
        "target_diffs": target_diffs,
        "executed_tools": executed,
    }


def _write_agent_payload(
    context: AgenticGenerationContext,
    name: str,
    payload: dict[str, Any],
) -> str:
    root = Path(context.output_root) / "agent_runs" / context.ontology.name
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return _rel(path)


def _artifact_targets_from_report(
    context: AgenticGenerationContext,
    report: dict[str, Any],
) -> dict[str, list[str]]:
    scripts_dir = Path(context.scripts_dir).resolve()
    prompts_dir = Path(context.prompts_dir).resolve()
    targets: dict[str, set[str]] = {"scripts": set(), "prompts": set()}

    for failure in report.get("failures") or []:
        for match in _ARTIFACT_RE.finditer(str(failure)):
            name = match.group("name")
            if name.endswith(".py"):
                targets["scripts"].add(_rel(scripts_dir / name))
            elif name.endswith(".md"):
                targets["prompts"].add(_rel(prompts_dir / name))

    return {
        "scripts": sorted(targets["scripts"]),
        "prompts": sorted(targets["prompts"]),
    }


def _default_generation_targets(
    context: AgenticGenerationContext,
    *,
    generate_scripts: bool,
    generate_prompts: bool,
) -> dict[str, list[str]]:
    """Repo-relative scaffold targets for a forced first LLM generation pass."""
    scripts: list[str] = []
    prompts: list[str] = []
    if generate_scripts:
        scripts_dir = Path(context.scripts_dir)
        if scripts_dir.is_dir():
            scripts = sorted(
                _rel(path)
                for path in scripts_dir.glob("*.py")
                if path.is_file()
                and not path.name.startswith("main_part_")
                and "_attempt_" not in path.name
            )
    if generate_prompts:
        prompts_dir = Path(context.prompts_dir)
        if prompts_dir.is_dir():
            prompts = sorted(
                _rel(path) for path in prompts_dir.glob("*.md") if path.is_file()
            )
    return {"scripts": scripts, "prompts": prompts}


def _has_unknown_failures(report: dict[str, Any], targets: dict[str, list[str]]) -> bool:
    failures = report.get("failures") or []
    return bool(failures) and not targets["scripts"] and not targets["prompts"]


def _make_agent(model_name: str) -> BaseAgent:
    return BaseAgent(
        model_name=model_name,
        remote_model=True,
        model_config=ModelConfig(max_tokens=8000, timeout=300, temperature=0.1, top_p=0.05),
        mcp_tools=["agentic_generation_workspace"],
        mcp_set_name="agentic_generation_mcp_configs.json",
    )


async def run_content_diagnosis_agent(
    *,
    model_name: str,
    payload: dict[str, Any],
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ask GPT to diagnose content differences and select prompt targets."""
    result = await _run_agent(
        agent_name="content:diagnosis",
        model_name=model_name,
        prompt=build_prompt_diagnosis_task_prompt(payload=payload),
        recursion_limit=8,
    )
    if (result.get("metadata") or {}).get("error"):
        raise RuntimeError(
            f"Diagnosis agent failed: {(result.get('metadata') or {}).get('error')}"
        )
    diagnosis = validate_diagnosis(parse_json_object(result.get("response") or ""), inventory)
    return {"diagnosis": diagnosis, "agent": result}


def run_content_diagnosis_agent_sync(
    *,
    model_name: str,
    payload: dict[str, Any],
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    return asyncio.run(
        run_content_diagnosis_agent(
            model_name=model_name,
            payload=payload,
            inventory=inventory,
        )
    )


async def _run_agent(
    *,
    agent_name: str,
    model_name: str,
    prompt: str,
    recursion_limit: int,
    timeout_seconds: int = AGENT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    agent = _make_agent(model_name)
    started = time.monotonic()
    _progress(
        "Starting %s: prompt_chars=%s recursion_limit=%s timeout_seconds=%s",
        agent_name,
        len(prompt),
        recursion_limit,
        timeout_seconds,
    )
    try:
        text, metadata = await asyncio.wait_for(
            agent.run(prompt, recursion_limit=recursion_limit),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        elapsed = round(time.monotonic() - started, 2)
        _progress("%s timed out after %ss", agent_name, timeout_seconds)
        return {
            "response": "",
            "metadata": {
                "error": "timeout",
                "agent_name": agent_name,
                "timeout_seconds": timeout_seconds,
                "elapsed_seconds": elapsed,
            },
        }
    except Exception as exc:
        elapsed = round(time.monotonic() - started, 2)
        LOGGER.exception("%s failed after %ss", agent_name, elapsed)
        return {
            "response": "",
            "metadata": {
                "error": type(exc).__name__,
                "message": str(exc),
                "agent_name": agent_name,
                "elapsed_seconds": elapsed,
            },
        }
    elapsed = round(time.monotonic() - started, 2)
    _progress("%s completed in %ss", agent_name, elapsed)
    metadata["agent_name"] = agent_name
    metadata["elapsed_seconds"] = elapsed
    return {
        "response": text,
        "metadata": metadata,
    }


async def run_llm_agentic_generation_rounds(
    context: AgenticGenerationContext,
    *,
    model_name: str = "gpt-5.2",
    foreign_contracts: list[dict[str, Any]] | None = None,
    max_rounds: int = 2,
    generate_scripts: bool = True,
    generate_prompts: bool = True,
) -> dict[str, Any]:
    """Run LLM-driven Coding, Prompt, and Validation agents over scaffold artifacts."""
    previous_env = os.environ.get("AGENTIC_GENERATION_OUTPUT_ROOT")
    os.environ["AGENTIC_GENERATION_OUTPUT_ROOT"] = str(Path(context.output_root).resolve())
    try:
        context_payload = _context_summary(context)
        content_diagnosis = _content_diagnosis(context)
        history: list[dict[str, Any]] = []
        report = build_validation_report(context, foreign_contracts=foreign_contracts, write_report=True)

        for round_idx in range(1, max(1, max_rounds) + 1):
            targets = _artifact_targets_from_report(context, report)
            unknown_failures = _has_unknown_failures(report, targets)
            force_first_pass = round_idx == 1
            if force_first_pass and not targets["scripts"] and not targets["prompts"]:
                targets = _default_generation_targets(
                    context,
                    generate_scripts=generate_scripts,
                    generate_prompts=generate_prompts,
                )
            feedback = {
                "round": round_idx,
                "machine_validation_report": report,
                "target_artifacts": targets,
                "agent_scope_policy": (
                    "Repair only the target_artifacts when any are listed. "
                    "If target_artifacts is empty and machine failures remain, inspect the validation report first, "
                    "then make the smallest necessary edits."
                ),
                "history_summary": [
                    {
                        "round": item.get("round"),
                        "report_ok": item.get("report", {}).get("ok"),
                        "failures": item.get("report", {}).get("failures", [])[:5],
                    }
                    for item in history[-2:]
                ],
            }
            prompt_feedback = dict(feedback)
            if content_diagnosis:
                prompt_feedback["content_diagnosis"] = content_diagnosis
                prompt_feedback["content_feedback_policy"] = (
                    "Patch only diagnosis-selected prompts. The diagnosis is deliberately "
                    "redacted; implement general T-Box/contract rules without recovering or "
                    "guessing fixture-specific labels or values."
                )
                targets["prompts"] = list(content_diagnosis.get("target_prompt_set") or [])
            payload_path = _write_agent_payload(
                context,
                f"round_{round_idx}_input.json",
                {"context": context_payload, "feedback": prompt_feedback},
            )
            round_record: dict[str, Any] = {
                "round": round_idx,
                "input_payload": payload_path,
                "agents": {},
            }
            _progress(
                "LLM generation round %s for %s: ok=%s failures=%s script_targets=%s prompt_targets=%s",
                round_idx,
                context.ontology.name,
                report.get("ok"),
                len(report.get("failures") or []),
                len(targets["scripts"]),
                len(targets["prompts"]),
            )

            medical_alignment = ""
            if context.ontology.name == "medical":
                medical_alignment = (
                    " Medical-only: keep the `## CSV Round-Trip Contract (medical ontology)` block in every EXTRACTION_ITER_* and "
                    "KG_BUILDING_ITER_* prompt (skip EXTRACTION_ITER_1). Ensure German checklist hints use JSON strings "
                    '`"1"` / `"-"` and never JSON booleans or Python `True`/`False` strings; preserve OPS gating and name-order rules there.'
                )

            # Round 1 always runs coding/prompt agents so LLM generation is not a no-op
            # when deterministic scaffolds already pass machine validation.
            run_coding_agent = generate_scripts and (
                bool(targets["scripts"]) or unknown_failures or force_first_pass
            )
            run_prompt_agent = generate_prompts and (
                bool(targets["prompts"]) or unknown_failures or force_first_pass
            )

            if run_coding_agent:
                coding_prompt = build_coding_task_prompt(
                    context_summary=context_payload,
                    task_name=(
                        (
                            "First-pass LLM rewrite: treat deterministic scaffolds as drafts and "
                            "upgrade MCP scripts into robust final artifacts even if machine "
                            "validation currently passes. "
                            if force_first_pass and not targets["scripts"] and not unknown_failures
                            else "Revise generated MCP scripts into robust final artifacts. "
                        )
                        + "Focus on the target script artifacts from feedback.target_artifacts.scripts when provided. "
                        "Read only the relevant scaffold scripts, contract, parsed ontology, and validation report "
                        "using repository-relative paths from context.output_paths. "
                        "Use the agentic_generation_workspace MCP tools to inspect and edit files. "
                        "The orchestrator will run validation after your pass; do not call tools outside this MCP server. "
                        "Preserve T-Box-only domain knowledge."
                        + medical_alignment
                    ),
                    feedback=prompt_feedback,
                )
                round_record["agents"]["coding_agent"] = await _run_agent(
                    agent_name=f"{context.ontology.name}:coding:round{round_idx}",
                    model_name=model_name,
                    prompt=coding_prompt,
                    recursion_limit=SCRIPT_AGENT_RECURSION_LIMIT,
                )
            elif generate_scripts:
                round_record["agents"]["coding_agent"] = {
                    "response": "Skipped: validation did not identify script failures for this round.",
                    "metadata": {"skipped": True, "reason": "no_script_targets"},
                }

            if run_prompt_agent:
                content_task = ""
                if content_diagnosis:
                    content_task = (
                        " Diagnosis mode: read every selected existing target and modify it only "
                        "with apply_unified_patch. Do not call write_workspace_file, create files, "
                        "or touch scripts. Stop only after every selected target has a real diff."
                    )
                before_manifest = artifact_manifest(Path(context.output_root))
                before_targets = _snapshot_files(targets["prompts"])
                prompt_prompt = build_prompt_task_prompt(
                    context_summary=context_payload,
                    prompt_kind=(
                        "Revise extraction, pre-extraction, KG-building, and iteration prompts. "
                        "Focus on the target prompt artifacts from feedback.target_artifacts.prompts when provided. "
                        "Use scaffold prompts as drafts. Strengthen them using only T-Box comments, "
                        "generation contracts, and validation feedback. Use the agentic_generation_workspace MCP to inspect and edit files."
                        + medical_alignment
                        + content_task
                    ),
                    feedback=prompt_feedback,
                )
                round_record["agents"]["prompt_agent"] = await _run_agent(
                    agent_name=f"{context.ontology.name}:prompt:round{round_idx}",
                    model_name=model_name,
                    prompt=prompt_prompt,
                    recursion_limit=PROMPT_AGENT_RECURSION_LIMIT,
                )
                if content_diagnosis:
                    protocol = _prompt_protocol_report(
                        before_manifest=before_manifest,
                        after_manifest=artifact_manifest(Path(context.output_root)),
                        before_targets=before_targets,
                        targets=targets["prompts"],
                        agent_result=round_record["agents"]["prompt_agent"],
                        output_root=Path(context.output_root),
                    )
                    round_record["prompt_protocol"] = protocol
                    _write_agent_payload(
                        context,
                        f"round_{round_idx}_prompt_protocol.json",
                        protocol,
                    )
                    if not protocol["ok"]:
                        report = build_validation_report(
                            context,
                            foreign_contracts=foreign_contracts,
                            write_report=True,
                            prompts_required=True,
                            extra_failures=protocol["failures"],
                        )
                        round_record["report"] = report
                        history.append(round_record)
                        _write_agent_payload(
                            context, f"round_{round_idx}_result.json", round_record
                        )
                        break
            elif generate_prompts:
                round_record["agents"]["prompt_agent"] = {
                    "response": "Skipped: validation did not identify prompt failures for this round.",
                    "metadata": {"skipped": True, "reason": "no_prompt_targets"},
                }

            report = build_validation_report(
                context,
                foreign_contracts=foreign_contracts,
                write_report=True,
                prompts_required=bool(content_diagnosis),
            )
            validator_payload = {
                "context": context_payload,
                "machine_validation_report": report,
                "instruction": (
                    "Act as the LLM validation agent. Inspect the generated artifacts and the machine report. "
                    "Use the agentic_generation_workspace MCP only for reading files. "
                    "If problems remain, provide actionable feedback for the coding/prompt agents. "
                    "If the artifacts are acceptable, say so and mention residual risks."
                ),
            }
            validator_prompt = build_validation_task_prompt(report=validator_payload)
            round_record["agents"]["validation_agent"] = await _run_agent(
                agent_name=f"{context.ontology.name}:validation:round{round_idx}",
                model_name=model_name,
                prompt=validator_prompt,
                recursion_limit=VALIDATION_AGENT_RECURSION_LIMIT,
            )
            round_record["report"] = report
            history.append(round_record)

            _write_agent_payload(context, f"round_{round_idx}_result.json", round_record)
            if report.get("ok"):
                break

        protocol_failures = [
            failure
            for item in history
            for failure in ((item.get("prompt_protocol") or {}).get("failures") or [])
        ]
        final_report = build_validation_report(
            context,
            foreign_contracts=foreign_contracts,
            write_report=True,
            prompts_required=bool(content_diagnosis),
            extra_failures=protocol_failures,
        )
        return {
            "mode": "llm_agent",
            "model": model_name,
            "ok": bool(final_report.get("ok")),
            "final_report": final_report,
            "history": history,
        }
    finally:
        if previous_env is None:
            os.environ.pop("AGENTIC_GENERATION_OUTPUT_ROOT", None)
        else:
            os.environ["AGENTIC_GENERATION_OUTPUT_ROOT"] = previous_env


def run_llm_agentic_generation_rounds_sync(
    context: AgenticGenerationContext,
    *,
    model_name: str = "gpt-5.2",
    foreign_contracts: list[dict[str, Any]] | None = None,
    max_rounds: int = 2,
    generate_scripts: bool = True,
    generate_prompts: bool = True,
) -> dict[str, Any]:
    return asyncio.run(
        run_llm_agentic_generation_rounds(
            context,
            model_name=model_name,
            foreign_contracts=foreign_contracts,
            max_rounds=max_rounds,
            generate_scripts=generate_scripts,
            generate_prompts=generate_prompts,
        )
    )
