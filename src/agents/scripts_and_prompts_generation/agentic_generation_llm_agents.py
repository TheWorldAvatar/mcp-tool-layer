from __future__ import annotations

import asyncio
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
    build_prompt_task_prompt,
    build_validation_task_prompt,
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
            "output_root": ".",
            "scripts_dir": Path(context.scripts_dir).resolve().relative_to(output_root).as_posix(),
            "prompts_dir": Path(context.prompts_dir).resolve().relative_to(output_root).as_posix(),
            "parsed_markdown": Path(context.parsed_markdown_path).resolve().relative_to(output_root).as_posix(),
            "contract": Path(context.contract_path).resolve().relative_to(output_root).as_posix(),
            "validation_report": Path(context.report_path).resolve().relative_to(output_root).as_posix(),
        },
        "counts": {
            "classes": len(classes),
            "properties": len(properties),
        },
        "top_entity": contract.get("top_entity"),
        "ordered_member_profile": contract.get("ordered_member_profile"),
        "required_links": contract.get("required_links"),
        "step_scoped_object_properties": contract.get("step_scoped_object_properties"),
        "required_step_scoped_object_properties": contract.get("required_step_scoped_object_properties"),
        "artifact_policy": (
            "The deterministic files already present under scripts_dir/prompts_dir are scaffolds only. "
            "The LLM agents must inspect and patch them into final artifacts. "
            "Use agentic_generation_workspace MCP tools with paths relative to the repository root "
            "(see output_paths)."
        ),
    }
    if context.ontology.name == "medical":
        summary["medical_csv_roundtrip_alignment"] = (
            f"Retain {MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER} in every EXTRACTION_ITER_* and KG_BUILDING_ITER_* prompt "
            "(except EXTRACTION_ITER_1). Machine validation fails if the marker is removed. Keep checklist values "
            "CSV-friendly, never use JSON booleans for checklist scalars, and keep linked target labels class-distinct."
        )
    return summary


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
    output_root = Path(context.output_root).resolve()
    scripts_dir = Path(context.scripts_dir).resolve()
    prompts_dir = Path(context.prompts_dir).resolve()
    targets: dict[str, set[str]] = {"scripts": set(), "prompts": set()}

    for failure in report.get("failures") or []:
        for match in _ARTIFACT_RE.finditer(str(failure)):
            name = match.group("name")
            if name.endswith(".py"):
                targets["scripts"].add((scripts_dir / name).relative_to(output_root).as_posix())
            elif name.endswith(".md"):
                targets["prompts"].add((prompts_dir / name).relative_to(output_root).as_posix())

    return {
        "scripts": sorted(targets["scripts"]),
        "prompts": sorted(targets["prompts"]),
    }


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
        history: list[dict[str, Any]] = []
        report = build_validation_report(context, foreign_contracts=foreign_contracts, write_report=True)

        for round_idx in range(1, max(1, max_rounds) + 1):
            targets = _artifact_targets_from_report(context, report)
            unknown_failures = _has_unknown_failures(report, targets)
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
            payload_path = _write_agent_payload(
                context,
                f"round_{round_idx}_input.json",
                {"context": context_payload, "feedback": feedback},
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

            run_coding_agent = generate_scripts and (
                bool(targets["scripts"]) or unknown_failures
            )
            run_prompt_agent = generate_prompts and (
                bool(targets["prompts"]) or unknown_failures
            )

            if run_coding_agent:
                coding_prompt = build_coding_task_prompt(
                    context_summary=context_payload,
                    task_name=(
                        "Revise generated MCP scripts into robust final artifacts. "
                        "Focus on the target script artifacts from feedback.target_artifacts.scripts when provided. "
                        "Read only the relevant scaffold scripts, contract, parsed ontology, and validation report. "
                        "Use the agentic_generation_workspace MCP tools to inspect and edit files. "
                        "The orchestrator will run validation after your pass; do not call tools outside this MCP server. "
                        "Preserve T-Box-only domain knowledge."
                        + medical_alignment
                    ),
                    feedback=feedback,
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
                prompt_prompt = build_prompt_task_prompt(
                    context_summary=context_payload,
                    prompt_kind=(
                        "Revise extraction, pre-extraction, KG-building, and iteration prompts. "
                        "Focus on the target prompt artifacts from feedback.target_artifacts.prompts when provided. "
                        "Use scaffold prompts as drafts. Strengthen them using only T-Box comments, "
                        "generation contracts, and validation feedback. Use the agentic_generation_workspace MCP to inspect and edit files."
                        + medical_alignment
                    ),
                    feedback=feedback,
                )
                round_record["agents"]["prompt_agent"] = await _run_agent(
                    agent_name=f"{context.ontology.name}:prompt:round{round_idx}",
                    model_name=model_name,
                    prompt=prompt_prompt,
                    recursion_limit=PROMPT_AGENT_RECURSION_LIMIT,
                )
            elif generate_prompts:
                round_record["agents"]["prompt_agent"] = {
                    "response": "Skipped: validation did not identify prompt failures for this round.",
                    "metadata": {"skipped": True, "reason": "no_prompt_targets"},
                }

            report = build_validation_report(context, foreign_contracts=foreign_contracts, write_report=True)
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

        final_report = build_validation_report(context, foreign_contracts=foreign_contracts, write_report=True)
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
