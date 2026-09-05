"""Formal, prompt-only semantic enhancement loop for generated packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_llm_agents import (
    run_content_diagnosis_agent_sync,
)
from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    artifact_manifest,
    fixture_literals,
    redact_diagnosis,
    repair_artifact_inventory,
    validate_single_prompt_focus,
)
from src.agents.scripts_and_prompts_generation.content_fixture_score import (
    load_predicted_hints,
)
from src.agents.scripts_and_prompts_generation.llm_extraction_judge import (
    judge_extraction_delta_stability,
)

EVALUATION_PROTOCOL = "prompt-enhancement.v3.atomic-extensions"


@dataclass
class EnhancementEvents:
    """Persist machine-readable events while streaming concise progress to stderr."""

    path: Path

    def emit(self, phase: str, message: str, **data: Any) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "message": message,
            **data,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        suffix = " ".join(
            f"{key}={value}"
            for key, value in data.items()
            if value is not None and key not in {"details"}
        )
        print(
            f"[prompt-enhancement][{phase}] {message}"
            + (f" {suffix}" if suffix else ""),
            file=sys.stderr,
            flush=True,
        )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _acceptance(report: dict[str, Any], key: str) -> dict[str, Any]:
    return dict(((report.get(key) or {}).get("acceptance") or {}))


def _scores(report: dict[str, Any], key: str) -> dict[str, float]:
    raw = ((report.get(key) or {}).get("consensus") or {}).get("scores") or {}
    return {str(name): float(value) for name, value in raw.items()}


def _overall(report: dict[str, Any], key: str) -> float:
    return float(
        ((report.get(key) or {}).get("consensus") or {}).get("overall_score") or 0.0
    )


def _evaluation_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(report.get("ok")),
        "build_ok": bool((report.get("abox_build") or {}).get("ok")),
        "reasoner_ok": bool((report.get("reasoner") or {}).get("ok")),
        "extraction_overall": _overall(report, "extraction_soft_judge"),
        "semantic_overall": _overall(report, "semantic_soft_judge"),
        "extraction_scores": _scores(report, "extraction_soft_judge"),
        "semantic_scores": _scores(report, "semantic_soft_judge"),
        "extraction_accepted": bool(
            _acceptance(report, "extraction_soft_judge").get("accepted")
        ),
        "semantic_accepted": bool(
            _acceptance(report, "semantic_soft_judge").get("accepted")
        ),
    }


def _evaluate_generated_package(
    *,
    context: AgenticGenerationContext,
    artifact_root: Path,
    fixture: dict[str, Any],
    output_dir: Path,
    judge_model: str | None,
    repeats: int,
    events: EnhancementEvents,
    resume_case_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the real ReAct pipeline and semantic judges against one immutable package."""
    if context.ontology.name != "ontosynthesis":
        return {
            "ok": False,
            "status": "unsupported",
            "error": (
                "Formal mock ReAct adapter is not configured for ontology "
                f"{context.ontology.name!r}"
            ),
        }

    from src.agents.scripts_and_prompts_generation import (
        semantic_mcp_loop_ontosynthesis as semantic_loop,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if resume_case_dir is not None and max(1, repeats) != 1:
        raise ValueError("A retained runtime case can be resumed exactly once")
    repeat_reports: list[dict[str, Any]] = []
    for index in range(1, max(1, repeats) + 1):
        repeat_dir = output_dir / f"run_{index}"
        repeat_dir.mkdir(parents=True, exist_ok=True)
        runtime_override = os.environ.get(
            "PROMPT_ENHANCEMENT_RUNTIME_ROOT", ""
        ).strip()
        if resume_case_dir is not None:
            work_dir = repeat_dir / "resumed_runtime"
            runtime_root = resume_case_dir.resolve().parent
        elif runtime_override or os.name == "nt":
            runtime_base = (
                Path(runtime_override)
                if runtime_override
                else Path(__file__).resolve().parents[3]
                / "tmp"
                / "_prompt_enhancement_runtime"
            )
            runtime_key = hashlib.sha256(
                str(output_dir.resolve()).encode("utf-8")
            ).hexdigest()[:16]
            work_dir = runtime_base / runtime_key / f"run_{index}"
            runtime_root = work_dir / "runtime"
        else:
            work_dir = repeat_dir
            runtime_root = work_dir / "runtime"
        work_dir.mkdir(parents=True, exist_ok=True)
        abox_path = work_dir / "react_abox.ttl"
        events.emit("evaluate", "starting ReAct evaluation", repeat=index)
        previous_ccdc_timeout = os.environ.get("CCDC_SUBPROCESS_TIMEOUT_SEC")
        os.environ["CCDC_SUBPROCESS_TIMEOUT_SEC"] = os.environ.get(
            "PROMPT_ENHANCEMENT_CCDC_TIMEOUT_SEC", "45"
        )
        try:
            build = semantic_loop.run_react_pipeline_against_mock(
                artifact_root=artifact_root,
                meta_task_config=Path(context.ontology.meta_task_config_path),
                fixture=fixture,
                abox_path=abox_path,
                runtime_root=runtime_root,
                resume_from_step=(
                    "main_kg_building" if resume_case_dir is not None else None
                ),
            )
        finally:
            if previous_ccdc_timeout is None:
                os.environ.pop("CCDC_SUBPROCESS_TIMEOUT_SEC", None)
            else:
                os.environ["CCDC_SUBPROCESS_TIMEOUT_SEC"] = previous_ccdc_timeout
        reasoner = (
            semantic_loop.run_reasoner_gate(
                tbox_paths=semantic_loop._resolve_tbox_paths(None),
                abox_path=abox_path,
                report_path=work_dir / "reasoner_report.json",
            )
            if build.get("ok")
            else {"ok": False, "failures": [build.get("error") or "build failed"]}
        )
        ontology_contract = semantic_loop._semantic_ontology_contract(context)
        extraction = semantic_loop.judge_extraction_semantics(
            document_text=str(fixture.get("document_md") or ""),
            ontology_contract=ontology_contract,
            extracted_content=build.get("predicted_hints") or {},
            models=[judge_model],
            acceptance_threshold=0.95,
        )
        semantic = (
            semantic_loop.judge_semantic_abox(
                document_text=str(fixture.get("document_md") or ""),
                ontology_contract=ontology_contract,
                abox_path=abox_path,
                models=[judge_model],
                acceptance_threshold=0.95,
            )
            if reasoner.get("ok")
            else {
                "ok": False,
                "acceptance": {"accepted": False, "overall_score": 0.0},
                "consensus": {"overall_score": 0.0, "scores": {}},
                "observations": [],
            }
        )
        report = {
            "ok": bool(build.get("ok")) and bool(reasoner.get("ok")),
            "repeat": index,
            "artifact_root": str(artifact_root),
            "abox_build": build,
            "reasoner": reasoner,
            "extraction_soft_judge": extraction,
            "semantic_soft_judge": semantic,
            "abox_path": str(abox_path),
        }
        _write_json(repeat_dir / "evaluation.json", report)
        repeat_reports.append(report)
        summary = _evaluation_summary(report)
        events.emit(
            "evaluate",
            "completed ReAct evaluation",
            repeat=index,
            extraction=summary["extraction_overall"],
            semantic=summary["semantic_overall"],
            reasoner=summary["reasoner_ok"],
        )

    representative = min(
        repeat_reports,
        key=lambda item: (
            _overall(item, "extraction_soft_judge"),
            _overall(item, "semantic_soft_judge"),
        ),
    )
    result = {
        **representative,
        "repeats": [
            {"repeat": item["repeat"], **_evaluation_summary(item)}
            for item in repeat_reports
        ],
        "representative_repeat": representative["repeat"],
    }
    _write_json(output_dir / "evaluation_summary.json", result)
    return result


def _target_iteration_artifacts(case_dir: Path, iteration: int) -> dict[str, Any]:
    loaded = load_predicted_hints(case_dir)
    artifacts = [
        item
        for item in loaded.get("runtime_hint_artifacts") or []
        if int(item.get("iteration") or -1) == iteration
    ]
    return {
        "iteration": iteration,
        "runtime_hint_artifacts": artifacts,
    }


def _judge_target_extraction(
    *,
    context: AgenticGenerationContext,
    fixture: dict[str, Any],
    case_dir: Path,
    iteration: int,
    judge_model: str,
) -> dict[str, Any]:
    from src.agents.scripts_and_prompts_generation import (
        semantic_mcp_loop_ontosynthesis as semantic_loop,
    )

    extracted = _target_iteration_artifacts(case_dir, iteration)
    reference = _stage_fixture_projection(
        context=context,
        fixture=fixture,
        iteration=iteration,
    )
    return semantic_loop.judge_extraction_semantics(
        document_text=str(fixture.get("document_md") or ""),
        ontology_contract=semantic_loop._semantic_ontology_contract(context),
        extracted_content=extracted,
        reference_content=reference,
        models=[judge_model],
        acceptance_threshold=0.95,
    )


def _run_targeted_extraction_evaluation(
    *,
    context: AgenticGenerationContext,
    artifact_root: Path,
    fixture: dict[str, Any],
    baseline_case_dir: Path,
    iteration: int,
    sub_iteration: str | None,
    output_dir: Path,
    judge_model: str,
    events: EnhancementEvents,
    freeze_pre_extraction: bool = True,
    property_contract_block: str | None = None,
) -> dict[str, Any]:
    """Rerun one extraction iteration while preserving all upstream artifacts."""
    if context.ontology.name != "ontosynthesis":
        return {
            "ok": False,
            "status": "unsupported",
            "error": "No targeted extraction adapter is configured for this ontology",
        }
    from src.agents.scripts_and_prompts_generation import (
        semantic_mcp_loop_ontosynthesis as semantic_loop,
    )
    from src.pipelines.main_ontology_extractions.extract import run_step as main_extract

    runtime_override = str(os.getenv("PROMPT_ENHANCEMENT_RUNTIME_ROOT", "")).strip()
    if runtime_override or os.name == "nt":
        runtime_base = Path(runtime_override or "tmp/_prompt_enhancement_runtime")
        runtime_key = hashlib.sha256(
            str(output_dir.resolve()).encode("utf-8")
        ).hexdigest()[:16]
        runtime_root = runtime_base / runtime_key / "targeted"
    else:
        runtime_root = output_dir / "runtime"
    case_dir = runtime_root / baseline_case_dir.name
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    runtime_root.mkdir(parents=True)
    shutil.copytree(baseline_case_dir, case_dir)
    frozen_pre_extraction = {
        path.name: _digest(path)
        for path in sorted((case_dir / "pre_extraction").glob("entity_text_*.txt"))
    }
    if iteration == 3 and not freeze_pre_extraction:
        shutil.rmtree(case_dir / "pre_extraction", ignore_errors=True)
        (case_dir / "pre_extraction").mkdir()
    for marker in (
        case_dir / ".main_ontology_extractions_done",
        case_dir / f".main_ontology_extractions_iter{iteration}_done",
    ):
        marker.unlink(missing_ok=True)
    cleanup_patterns = (
        [
            (case_dir / "mcp_run", [f"iter{iteration}_{sub_iteration}_*"]),
            (case_dir / "prompts", [f"iter{sub_iteration}_*"]),
            (case_dir / "responses", [f"iter{sub_iteration}_*"]),
        ]
        if sub_iteration
        else [
            (case_dir / "mcp_run", [f"iter{iteration}_*"]),
            (case_dir / "prompts", [f"iter{iteration}_*"]),
            (case_dir / "responses", [f"iter{iteration}_*"]),
        ]
    )
    for folder, patterns in cleanup_patterns:
        for pattern in patterns:
            for path in folder.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
    config_path = semantic_loop._react_mcp_config_path(
        artifact_root=artifact_root,
        data_dir=runtime_root,
    )
    config_name = semantic_loop._write_ontosynthesis_react_mcp_config(
        artifact_root=artifact_root,
        config_path=config_path,
        data_dir=runtime_root,
    )
    cfg = {
        "data_dir": str(runtime_root.resolve()),
        "project_root": str(semantic_loop.ROOT),
        "meta_task_config": str(context.ontology.meta_task_config_path),
        "test_mcp_config": config_name,
        "force_react_kg": True,
        "skip_materialize_hints": True,
        "only_extraction_iterations": [iteration],
        "skip_extraction_sub_iterations": sub_iteration is None,
        "only_extraction_sub_iterations": [sub_iteration] if sub_iteration else [],
        "skip_parent_extraction_when_targeting_sub_iterations": bool(sub_iteration),
        "reuse_pre_extraction_artifacts": iteration == 3 and freeze_pre_extraction,
        "experimental_property_contract_block": property_contract_block or "",
    }
    previous_twa_env = {
        key: value for key, value in os.environ.items() if key.startswith("TWA_")
    }
    try:
        os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(artifact_root.resolve())
        os.environ["TWA_AGENTIC_DATA_DIR"] = str(runtime_root.resolve())
        os.environ["TWA_REQUIRE_GENERATED_ARTIFACT_ROOT"] = "1"
        events.emit(
            "evaluate",
            "starting targeted extraction evaluation",
            iteration=iteration,
            sub_iteration=sub_iteration or "",
            reused_case=str(baseline_case_dir),
        )
        ok = bool(main_extract(baseline_case_dir.name, cfg))
    finally:
        config_path.unlink(missing_ok=True)
        for key in [name for name in os.environ if name.startswith("TWA_")]:
            os.environ.pop(key, None)
        os.environ.update(previous_twa_env)
    after_pre_extraction = {
        path.name: _digest(path)
        for path in sorted((case_dir / "pre_extraction").glob("entity_text_*.txt"))
    }
    if iteration == 3 and freeze_pre_extraction:
        if not frozen_pre_extraction or after_pre_extraction != frozen_pre_extraction:
            raise RuntimeError(
                "Targeted Iteration 3 evaluation changed its frozen "
                "pre-extraction checkpoint"
            )
    judge = (
        _judge_target_extraction(
            context=context,
            fixture=fixture,
            case_dir=case_dir,
            iteration=iteration,
            judge_model=judge_model,
        )
        if ok and judge_model
        else {
            "ok": bool(ok),
            "skipped": bool(ok),
            "consensus": {"overall_score": 0.0, "scores": {}},
            "acceptance": {"accepted": False},
        }
    )
    report = {
        "ok": ok,
        "mode": "targeted_extraction",
        "iteration": iteration,
        "sub_iteration": sub_iteration,
        "frozen_pre_extraction_sha256": (
            frozen_pre_extraction if freeze_pre_extraction else {}
        ),
        "pre_extraction_sha256": after_pre_extraction,
        "property_contract_injected": bool(property_contract_block),
        "case_dir": str(case_dir),
        "reused_upstream": str(baseline_case_dir),
        "downstream_executed": False,
        "extraction_soft_judge": judge,
    }
    _write_json(output_dir / "targeted_evaluation.json", report)
    events.emit(
        "evaluate",
        "completed targeted extraction evaluation",
        iteration=iteration,
        extraction=_overall(report, "extraction_soft_judge"),
        downstream_executed=False,
    )
    return report


def _run_targeted_extraction_trial_worker(
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Run one trial in an isolated process and environment."""
    worker_kwargs = dict(kwargs)
    events_path = Path(worker_kwargs.pop("events_path"))
    return _run_targeted_extraction_evaluation(
        **worker_kwargs,
        events=EnhancementEvents(events_path),
    )


def run_targeted_extraction_trials(
    *,
    context: AgenticGenerationContext,
    artifact_root: Path,
    fixture: dict[str, Any],
    baseline_case_dir: Path,
    iteration: int,
    sub_iteration: str | None,
    output_dir: Path,
    repeats: int,
    judge_model: str | None = None,
    freeze_pre_extraction: bool = True,
    parallelism: int | None = None,
    property_contract_block: str | None = None,
) -> dict[str, Any]:
    """Run repeated extraction-only trials against one frozen upstream checkpoint."""
    trial_count = max(1, repeats)
    jobs = [
        {
            "context": context,
            "artifact_root": artifact_root,
            "fixture": fixture,
            "baseline_case_dir": baseline_case_dir,
            "iteration": iteration,
            "sub_iteration": sub_iteration,
            "output_dir": output_dir / f"trial_{index}",
            "judge_model": judge_model,
            "freeze_pre_extraction": freeze_pre_extraction,
            "property_contract_block": property_contract_block,
            "events_path": output_dir / f"trial_{index}" / "events.jsonl",
        }
        for index in range(1, trial_count + 1)
    ]
    worker_count = min(trial_count, max(1, parallelism or trial_count))
    if worker_count == 1:
        reports = [_run_targeted_extraction_trial_worker(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            reports = list(executor.map(_run_targeted_extraction_trial_worker, jobs))
    result = {
        "schema_version": "targeted-extraction-trials.v1",
        "artifact_root": str(artifact_root),
        "baseline_case_dir": str(baseline_case_dir),
        "iteration": iteration,
        "sub_iteration": sub_iteration,
        "repeats": len(reports),
        "freeze_pre_extraction": freeze_pre_extraction,
        "parallelism": worker_count,
        "property_contract_injected": bool(property_contract_block),
        "property_contract_sha256": (
            hashlib.sha256(property_contract_block.encode("utf-8")).hexdigest()
            if property_contract_block
            else None
        ),
        "frozen_pre_extraction_sha256": (
            reports[0].get("frozen_pre_extraction_sha256") if reports else {}
        ),
        "trials": reports,
    }
    _write_json(output_dir / "trials_summary.json", result)
    return result


def _evidence_index(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    registered_ids: set[str] = set()

    def register(
        evidence_id: str, *, layer: str, kind: str, payload: Any
    ) -> None:
        normalized = str(evidence_id or "").strip()
        if not normalized or normalized in registered_ids:
            return
        registered_ids.add(normalized)
        evidence.append(
            {
                "evidence_id": normalized,
                "layer": layer,
                "kind": kind,
                "payload": payload,
            }
        )

    raw_case_dir = str(
        (evaluation.get("abox_build") or {}).get("case_dir") or ""
    ).strip()
    feedback_root = Path(raw_case_dir) / "post_publish_feedback"
    if feedback_root.is_dir():
        for path in sorted(feedback_root.glob("**/*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            register(
                str(payload.get("evidence_id") or f"kg_building.{path.stem}"),
                layer="kg_building",
                kind="post_publish_structural_failure",
                payload=payload,
            )

    for layer, key in (
        ("extraction", "extraction_soft_judge"),
        ("kg_building", "semantic_soft_judge"),
    ):
        report = evaluation.get(key) or {}
        for judge_index, judge in enumerate(report.get("judges") or [], start=1):
            for deduction_index, deduction in enumerate(
                judge.get("deductions") or [], start=1
            ):
                register(
                    f"{layer}.judge{judge_index}.deduction{deduction_index}",
                    layer=layer,
                    kind="judge_deduction",
                    payload=deduction,
                )
                if isinstance(deduction, dict):
                    for alias in (
                        deduction.get("observation_ids")
                        or deduction.get("evidence_ids")
                        or []
                    ):
                        register(
                            str(alias),
                            layer=layer,
                            kind="judge_deduction_alias",
                            payload=deduction,
                        )
        for observation_index, observation in enumerate(
            report.get("observations") or [], start=1
        ):
            register(
                f"{layer}.observation{observation_index}",
                layer=layer,
                kind="judge_observation",
                payload=observation,
            )
            if isinstance(observation, dict):
                for key_name in ("observation_id", "evidence_id", "id"):
                    register(
                        str(observation.get(key_name) or ""),
                        layer=layer,
                        kind="judge_observation_alias",
                        payload=observation,
                    )
    build = evaluation.get("abox_build") or {}
    if build.get("error"):
        register(
            "runtime.build.error",
            layer="runtime",
            kind="runtime_error",
            payload={
                "error": build.get("error"),
                "step_results": build.get("step_results") or {},
            },
        )
    for repeat in evaluation.get("repeats") or []:
        register(
            f"repeat.{repeat.get('repeat')}.summary",
            layer="stability",
            kind="repeat_summary",
            payload=repeat,
        )
    return evidence


def _highest_priority_structural_feedback(
    evaluation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return mandatory post-publish feedback in deterministic evidence order."""
    return [
        item
        for item in _evidence_index(evaluation)
        if item.get("kind") == "post_publish_structural_failure"
        and (item.get("payload") or {}).get("priority") == "highest"
    ]


def _runtime_evidence_paths(evaluation: dict[str, Any]) -> list[Path]:
    raw_case_dir = str(
        (evaluation.get("abox_build") or {}).get("case_dir") or ""
    ).strip()
    if not raw_case_dir:
        return []
    case_dir = Path(raw_case_dir)
    if not case_dir.is_dir():
        return []
    candidates = [
        *case_dir.glob("responses/**/*"),
        *case_dir.glob("mcp_run/**/*"),
        *case_dir.glob("post_publish_feedback/**/*"),
    ]
    return [path for path in candidates if path.is_file()][:40]


def _failure_origin_evidence(
    *,
    context: AgenticGenerationContext,
    evaluation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Locate the first observable layer for each missing contract property."""
    property_locals = {
        str(name).strip()
        for name in (context.parsed.get("properties") or {})
        if str(name).strip()
    }
    for item in (
        (context.contract.get("ontology_publish_contract") or {}).get(
            "object_properties"
        )
        or []
    ):
        iri = str((item or {}).get("property_iri") or "")
        local = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        if local:
            property_locals.add(local)

    hints_text = json.dumps(
        (evaluation.get("abox_build") or {}).get("predicted_hints") or {},
        ensure_ascii=False,
        default=str,
    )
    trace_documents: list[Any] = []
    for path in _runtime_evidence_paths(evaluation):
        if not path.name.endswith(".trace.json"):
            continue
        try:
            trace_documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    called_tools: set[str] = set()
    successful_tools: set[str] = set()
    for trace in trace_documents:
        if not isinstance(trace, dict):
            continue
        called_tools.update(
            str(item.get("name") or "")
            for item in trace.get("tool_calls") or []
            if isinstance(item, dict)
        )
        successful_tools.update(
            str(item.get("name") or "")
            for item in trace.get("tool_outputs") or []
            if isinstance(item, dict)
            and str(item.get("status") or "").casefold() == "success"
        )
    abox_path = Path(str(evaluation.get("abox_path") or ""))
    abox_text = (
        abox_path.read_text(encoding="utf-8")
        if abox_path.is_file()
        else ""
    )

    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for judge_layer, report_key in (
        ("extraction", "extraction_soft_judge"),
        ("kg_building", "semantic_soft_judge"),
    ):
        for judgement in (evaluation.get(report_key) or {}).get("judges") or []:
            for deduction in judgement.get("deductions") or []:
                if not isinstance(deduction, dict):
                    continue
                deduction_text = json.dumps(deduction, ensure_ascii=False)
                mentioned = sorted(
                    local
                    for local in property_locals
                    if re.search(rf"\b{re.escape(local)}\b", deduction_text)
                )
                for local in mentioned:
                    key = (judge_layer, local)
                    if key in seen:
                        continue
                    seen.add(key)
                    hint_present = bool(
                        re.search(rf"\b{re.escape(local)}\b", hints_text)
                    )
                    tool_name = f"add_{local}"
                    tool_called = tool_name in called_tools
                    successful_call = tool_name in successful_tools
                    ttl_present = bool(
                        re.search(rf"[:/#]{re.escape(local)}\b", abox_text)
                    )
                    if judge_layer == "extraction":
                        origin = (
                            "judge_misclassification"
                            if hint_present
                            else "extraction_prompt_or_model"
                        )
                    elif not hint_present:
                        origin = "extraction_prompt_or_model"
                    elif not tool_called:
                        origin = "kg_prompt_or_model_behavior"
                    elif not successful_call:
                        origin = "script_or_runtime_contract_rejection"
                    elif not ttl_present:
                        origin = "script_persistence_or_publish"
                    else:
                        origin = "judge_misclassification"

                    ontology_evidence = str(
                        deduction.get("ontology_evidence") or ""
                    )
                    required = bool(
                        re.search(
                            r"\b(must|required|mandatory|exactly\s+one)\b",
                            ontology_evidence,
                            flags=re.I,
                        )
                    )
                    secondary: list[str] = []
                    if (
                        judge_layer == "kg_building"
                        and required
                        and not ttl_present
                        and (evaluation.get("abox_build") or {}).get("ok")
                    ):
                        secondary.append(
                            "script_validation_gap: export accepted an A-Box "
                            "missing a required contract relation"
                        )
                    evidence_id = (
                        "origin."
                        + hashlib.sha256(
                            f"{judge_layer}:{local}:{origin}".encode("utf-8")
                        ).hexdigest()[:16]
                    )
                    evidence.append(
                        {
                            "evidence_id": evidence_id,
                            "layer": judge_layer,
                            "property_local": local,
                            "first_failure_origin": origin,
                            "hint_present": hint_present,
                            "hint_marks_unresolved": bool(
                                re.search(
                                    rf"{re.escape(local)}[^\n]{{0,200}}unresolved",
                                    hints_text,
                                    flags=re.I,
                                )
                            ),
                            "tool_called": tool_called,
                            "successful_tool_call": successful_call,
                            "final_ttl_contains_property": ttl_present,
                            "secondary_findings": secondary,
                            "judge_deduction": deduction,
                        }
                    )
    return evidence


def _stage_fixture_projection(
    *,
    context: AgenticGenerationContext,
    fixture: dict[str, Any],
    iteration: int,
) -> dict[str, Any]:
    """Project fixture facts to one semantic-plan iteration without domain rules."""
    plan_path = (
        Path(context.output_root)
        / "semantic_planning"
        / context.ontology.name
        / "accepted_semantic_plan.json"
    )
    if not plan_path.is_file():
        raise ValueError(
            "Targeted evaluation requires the accepted semantic plan checkpoint"
        )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assignments = ((plan.get("assignments") or {}).get("assignments") or [])
    assignment = next(
        (
            item
            for item in assignments
            if str(item.get("slot") or "") == f"iter{iteration}"
        ),
        None,
    )
    if not isinstance(assignment, dict):
        raise ValueError(f"No semantic assignment found for iteration {iteration}")
    owned_classes = {
        str(value) for value in assignment.get("classes") or [] if str(value)
    }
    owned_properties = {
        str(value)
        for value in assignment.get("object_properties") or []
        if str(value)
    }
    hints = fixture.get("hints") or (fixture.get("content_gt") or {}).get("hints") or {}
    top_class = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    )
    projected: dict[str, Any] = {}
    for class_local, raw_items in hints.items():
        if class_local in owned_classes:
            projected[class_local] = raw_items
            continue
        if class_local != top_class:
            continue
        items = raw_items if isinstance(raw_items, list) else [raw_items]
        projected_items: list[Any] = []
        for raw in items:
            if not isinstance(raw, dict):
                projected_items.append(raw)
                continue
            kept = {
                key: value
                for key, value in raw.items()
                if key in {"label", "name"}
                or key in owned_properties
                or key.removesuffix("_label") in owned_properties
            }
            if kept:
                projected_items.append(kept)
        if projected_items:
            projected[class_local] = projected_items
    return {
        "iteration": iteration,
        "owned_classes": sorted(owned_classes),
        "owned_properties": sorted(owned_properties),
        "hints": projected,
    }


def _diagnosis_payload(
    *,
    context: AgenticGenerationContext,
    fixture: dict[str, Any],
    evaluation: dict[str, Any],
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = _evidence_index(evaluation)
    failure_origins = _failure_origin_evidence(
        context=context,
        evaluation=evaluation,
    )
    evidence.extend(failure_origins)
    payload = {
        "schema_version": "prompt-enhancement-evidence.v2",
        "mock_source": fixture.get("document_md"),
        "expected_hints": fixture.get("hints")
        or (fixture.get("content_gt") or {}).get("hints")
        or {},
        "predicted_hints": (evaluation.get("abox_build") or {}).get("predicted_hints")
        or {},
        "extraction_soft_judge": evaluation.get("extraction_soft_judge") or {},
        "semantic_soft_judge": evaluation.get("semantic_soft_judge") or {},
        "reasoner": evaluation.get("reasoner") or {},
        "abox_build": evaluation.get("abox_build") or {},
        "repeat_results": evaluation.get("repeats") or [],
        "evidence_index": evidence,
        "failure_origin_matrix": failure_origins,
        "artifact_inventory": inventory,
        "contract": {
            "top_entity": context.contract.get("top_entity"),
            "required_links": context.contract.get("required_links"),
            "ordered_member_profile": context.contract.get("ordered_member_profile"),
            "required_step_scoped_object_properties": context.contract.get(
                "required_step_scoped_object_properties"
            ),
            "relationship_tool_contracts": context.contract.get(
                "relationship_tool_contracts"
            ),
        },
        "iteration_blueprint": context.iteration_blueprint,
        "semantic_plan": (
            Path(context.output_root)
            / "semantic_planning"
            / context.ontology.name
            / "accepted_semantic_plan.json"
        ).as_posix(),
    }
    payload["input_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


def _route_signature(diagnosis: dict[str, Any]) -> tuple[str, str, str]:
    focus = diagnosis.get("focus") or {}
    target = next(iter(diagnosis.get("target_artifacts") or []), "")
    return (
        str(diagnosis.get("repair_kind") or ""),
        str(focus.get("owner_layer") or ""),
        Path(str(target)).name if target else "",
    )


def _run_consensus_diagnosis(
    *,
    model: str,
    payload: dict[str, Any],
    inventory: list[dict[str, Any]],
    output_dir: Path,
    events: EnhancementEvents,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for index in (1, 2):
        events.emit("diagnose", "running independent GPT diagnosis", attempt=index)
        result = run_content_diagnosis_agent_sync(
            model_name=model,
            payload=payload,
            inventory=inventory,
        )
        diagnosis = result["diagnosis"]
        if diagnosis.get("repair_kind") == "prompt":
            diagnosis = validate_single_prompt_focus(diagnosis)
            result["diagnosis"] = diagnosis
        attempts.append(result)
        _write_json(output_dir / f"diagnosis_attempt_{index}.json", result)
        events.emit(
            "diagnose",
            "completed independent GPT diagnosis",
            attempt=index,
            route=_route_signature(diagnosis),
            confidence=diagnosis.get("diagnostic_confidence"),
        )

    signatures = [_route_signature(item["diagnosis"]) for item in attempts]
    if signatures[0] == signatures[1]:
        consensus = attempts[0]["diagnosis"]
        agreement = "unanimous"
    else:
        events.emit(
            "diagnose",
            "diagnoses disagree; running adjudication",
            first=signatures[0],
            second=signatures[1],
        )
        adjudication_payload = {
            **payload,
            "diagnosis_mode": "adjudication",
            "candidate_diagnoses": [
                item["diagnosis"] for item in attempts
            ],
            "adjudication_instruction": (
                "Resolve the disagreement using only evidence IDs. Return one complete "
                "diagnosis under the same schema; do not combine targets opportunistically."
            ),
        }
        adjudication = run_content_diagnosis_agent_sync(
            model_name=model,
            payload=adjudication_payload,
            inventory=inventory,
        )
        diagnosis = adjudication["diagnosis"]
        if diagnosis.get("repair_kind") == "prompt":
            diagnosis = validate_single_prompt_focus(diagnosis)
            adjudication["diagnosis"] = diagnosis
        attempts.append(adjudication)
        _write_json(output_dir / "diagnosis_attempt_3_adjudication.json", adjudication)
        adjudicated_signature = _route_signature(diagnosis)
        if adjudicated_signature not in signatures:
            consensus = {
                "schema_version": "prompt-enhancement-diagnosis.v2",
                "status": "ambiguous_targets",
                "repair_kind": "adjudicate",
                "summary": "Independent diagnosis and adjudication did not converge.",
                "target_artifacts": [],
                "dependency_order": [],
                "must_preserve": [],
                "acceptance_evidence": [],
                "causal_findings": [],
                "diagnostic_confidence": 0.0,
            }
            agreement = "ambiguous"
        else:
            consensus = diagnosis
            agreement = "adjudicated"
    result = {
        "schema_version": "prompt-enhancement-diagnosis-consensus.v1",
        "agreement": agreement,
        "route_signature": _route_signature(consensus),
        "diagnosis": consensus,
        "attempt_count": len(attempts),
    }
    _write_json(output_dir / "diagnosis_consensus.json", result)
    return result


def _editor_projection(
    diagnosis: dict[str, Any], forbidden_literals: set[str]
) -> dict[str, Any]:
    targets = list(diagnosis.get("target_artifacts") or [])
    bridge = {
        **diagnosis,
        "target_prompt_set": targets,
        "issues": [
            {
                "issue_id": (finding.get("observation_ids") or ["semantic"])[0],
                "category": "semantic_content",
                "stage": "prompt",
                "root_cause": finding.get("cause"),
                "target_prompts": targets,
                "must_preserve": diagnosis.get("must_preserve") or [],
                "suggested_change": diagnosis.get("summary"),
            }
            for finding in diagnosis.get("causal_findings") or []
        ],
    }
    return redact_diagnosis(bridge, forbidden_literals)


def _candidate_improves(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if not (after.get("abox_build") or {}).get("ok"):
        failures.append("abox_build_failed")
    if not (after.get("reasoner") or {}).get("ok"):
        failures.append("reasoner_failed")
    improved = False
    for key in ("extraction_soft_judge", "semantic_soft_judge"):
        before_scores = _scores(before, key)
        after_scores = _scores(after, key)
        for dimension, old_value in before_scores.items():
            new_value = after_scores.get(dimension, 0.0)
            if new_value + 1e-9 < old_value:
                failures.append(
                    f"{key}.{dimension}_regressed:{old_value:.3f}->{new_value:.3f}"
                )
            if new_value > old_value + 1e-9:
                improved = True
    if not improved:
        failures.append("no_judge_dimension_improved")
    return not failures, failures


def _target_extraction_improves(
    before: dict[str, Any],
    after: dict[str, Any],
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if not after.get("ok"):
        failures.append("targeted_extraction_failed")
    before_scores = _scores(before, "extraction_soft_judge")
    after_scores = _scores(after, "extraction_soft_judge")
    improved = False
    for dimension, old_value in before_scores.items():
        new_value = after_scores.get(dimension, 0.0)
        if new_value + 1e-9 < old_value:
            failures.append(
                f"extraction_soft_judge.{dimension}_regressed:"
                f"{old_value:.3f}->{new_value:.3f}"
            )
        if new_value > old_value + 1e-9:
            improved = True
    if not improved:
        failures.append("no_target_extraction_dimension_improved")
    return not failures, failures


def run_formal_prompt_enhancement(
    *,
    context: AgenticGenerationContext,
    fixture_path: Path,
    model: str,
    max_rounds: int = 2,
    evaluation_repeats: int = 1,
    resume_case_dir: Path | None = None,
) -> dict[str, Any]:
    """Diagnose and repair prompts without ever mutating generated scripts."""
    from src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis import (
        _prompt_only_regeneration,
    )

    root = Path(context.output_root)
    run_label = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        os.environ.get("PROMPT_ENHANCEMENT_RUN_LABEL", "").strip(),
    ).strip("._")
    enhancement_root = (
        root / "prompt_enhancement_validations" / run_label
        if run_label
        else root / "prompt_enhancement"
    )
    fixture_sha256 = _digest(fixture_path)
    existing_manifest_path = enhancement_root / "run_manifest.json"
    existing_baseline_path = enhancement_root / "baseline" / "evaluation_summary.json"
    existing_manifest = (
        json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        if existing_manifest_path.is_file()
        else {}
    )
    existing_baseline = (
        json.loads(existing_baseline_path.read_text(encoding="utf-8"))
        if existing_baseline_path.is_file()
        else {}
    )
    existing_baseline_has_mandatory_feedback = bool(
        _highest_priority_structural_feedback(existing_baseline)
    )
    resume_baseline = bool(
        resume_case_dir is None
        and existing_baseline_path.is_file()
        and (
            existing_baseline.get("ok") is True
            or existing_baseline_has_mandatory_feedback
        )
        and existing_manifest.get("evaluation_protocol") == EVALUATION_PROTOCOL
        and existing_manifest.get("fixture_sha256") == fixture_sha256
        and Path(
            str(existing_manifest.get("source_artifact_root") or "")
        ).resolve()
        == root.resolve()
    )
    if enhancement_root.exists() and not resume_baseline:
        shutil.rmtree(enhancement_root)
    enhancement_root.mkdir(parents=True, exist_ok=True)
    events = EnhancementEvents(enhancement_root / "events.jsonl")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict):
        raise ValueError(f"Fixture must be a JSON object: {fixture_path}")
    run_manifest = {
        "schema_version": "formal-prompt-enhancement-run.v1",
        "evaluation_protocol": EVALUATION_PROTOCOL,
        "run_label": run_label,
        "ontology": context.ontology.name,
        "fixture": str(fixture_path.resolve()),
        "fixture_sha256": fixture_sha256,
        "source_artifact_root": str(root.resolve()),
        "model": model,
        "max_rounds": max_rounds,
        "evaluation_repeats": evaluation_repeats,
        "resume_case_dir": (
            str(resume_case_dir.resolve()) if resume_case_dir is not None else None
        ),
    }
    _write_json(enhancement_root / "run_manifest.json", run_manifest)
    events.emit(
        "start",
        "formal prompt-only enhancement started",
        ontology=context.ontology.name,
        fixture_sha256=run_manifest["fixture_sha256"][:12],
    )

    active_root = root
    active_context = context
    if resume_baseline:
        baseline = existing_baseline
        events.emit(
            "evaluate",
            "reused matching persisted baseline",
            fixture_sha256=fixture_sha256[:12],
            extraction=_overall(baseline, "extraction_soft_judge"),
            semantic=_overall(baseline, "semantic_soft_judge"),
        )
    else:
        baseline = _evaluate_generated_package(
            context=active_context,
            artifact_root=active_root,
            fixture=fixture,
            output_dir=enhancement_root / "baseline",
            judge_model=model,
            repeats=evaluation_repeats,
            events=events,
            resume_case_dir=resume_case_dir,
        )
    if baseline.get("status") == "unsupported":
        _write_json(enhancement_root / "handoff.json", baseline)
        return {
            "ok": False,
            "status": "unsupported",
            "final_artifact_root": str(active_root),
            "baseline": _evaluation_summary(baseline),
            "handoff": baseline,
        }

    rounds: list[dict[str, Any]] = []
    current_evaluation = baseline
    for round_index in range(1, max(1, max_rounds) + 1):
        if (
            _acceptance(current_evaluation, "extraction_soft_judge").get("accepted")
            and _acceptance(current_evaluation, "semantic_soft_judge").get("accepted")
            and not _highest_priority_structural_feedback(current_evaluation)
        ):
            events.emit("champion", "all semantic gates already pass")
            break
        round_dir = enhancement_root / f"round_{round_index}"
        if round_dir.exists():
            shutil.rmtree(round_dir)
        inventory = repair_artifact_inventory(
            prompts_dir=Path(active_context.prompts_dir),
            scripts_dir=Path(active_context.scripts_dir),
            evidence_paths=_runtime_evidence_paths(current_evaluation),
            max_chars=20000,
        )
        payload = _diagnosis_payload(
            context=active_context,
            fixture=fixture,
            evaluation=current_evaluation,
            inventory=inventory,
        )
        _write_json(round_dir / "evidence_bundle.json", payload)
        diagnosis_result = _run_consensus_diagnosis(
            model=model,
            payload=payload,
            inventory=inventory,
            output_dir=round_dir,
            events=events,
        )
        diagnosis = diagnosis_result["diagnosis"]
        repair_kind = str(diagnosis.get("repair_kind") or "")
        events.emit(
            "route",
            "diagnosis routed",
            repair_kind=repair_kind,
            owner=(diagnosis.get("focus") or {}).get("owner_layer"),
            target=Path(
                str(next(iter(diagnosis.get("target_artifacts") or []), ""))
            ).name,
            agreement=diagnosis_result.get("agreement"),
        )
        if repair_kind != "prompt":
            handoff = {
                "schema_version": "prompt-enhancement-handoff.v1",
                "status": "routed_non_prompt",
                "repair_kind": repair_kind,
                "diagnosis": diagnosis,
                "source_round": round_index,
                "scripts_modified": False,
            }
            _write_json(round_dir / "handoff.json", handoff)
            events.emit(
                "handoff",
                "prompt-only loop stopped without editing",
                repair_kind=repair_kind,
            )
            rounds.append({**handoff, "round": round_index, "status": "handoff"})
            break
        focus = diagnosis.get("focus") or {}
        owner_layer = str(focus.get("owner_layer") or "")
        target_iteration_label = str(focus.get("iteration") or "").strip()
        try:
            target_iteration = int(float(target_iteration_label))
        except ValueError:
            target_iteration = -1
        target_sub_iteration = (
            target_iteration_label if "." in target_iteration_label else None
        )
        priority_feedback = _highest_priority_structural_feedback(
            current_evaluation
        )
        matching_kg_feedback = [
            item
            for item in priority_feedback
            if int(
                ((item.get("payload") or {}).get("repair_owner") or {}).get(
                    "iteration"
                )
                or -1
            )
            == target_iteration
        ]
        kg_feedback_resolved = bool(matching_kg_feedback) and any(
            (item.get("payload") or {}).get("retry_status") == "resolved"
            for item in matching_kg_feedback
        )
        route_supported = (
            owner_layer == "extraction" and target_iteration >= 2
        ) or (
            owner_layer == "kg_building"
            and target_iteration >= 2
            and kg_feedback_resolved
        )
        if not route_supported:
            handoff = {
                "schema_version": "prompt-enhancement-handoff.v1",
                "status": "targeted_rerun_unavailable",
                "repair_kind": repair_kind,
                "diagnosis": diagnosis,
                "source_round": round_index,
                "scripts_modified": False,
                "reason": (
                    "KG prompt repair requires a highest-priority post-publish "
                    "feedback event that the retained-memory KG retry resolved."
                    if owner_layer == "kg_building"
                    else "No causal targeted adapter is available for this route."
                ),
            }
            _write_json(round_dir / "handoff.json", handoff)
            events.emit(
                "handoff",
                "targeted rerun unavailable; prompt left unchanged",
                owner_layer=owner_layer,
                iteration=target_iteration,
            )
            rounds.append({**handoff, "round": round_index, "status": "handoff"})
            break
        baseline_case_dir = Path(
            str((current_evaluation.get("abox_build") or {}).get("case_dir") or "")
        )
        if not baseline_case_dir.is_dir():
            raise RuntimeError(
                "Prompt enhancement requires a persisted baseline case"
            )
        if owner_layer == "extraction":
            baseline_target_evaluation = _run_targeted_extraction_evaluation(
                context=active_context,
                artifact_root=active_root,
                fixture=fixture,
                baseline_case_dir=baseline_case_dir,
                iteration=target_iteration,
                sub_iteration=target_sub_iteration,
                output_dir=round_dir / "targeted_baseline",
                judge_model=model,
                events=events,
            )
        else:
            baseline_target_evaluation = {
                "ok": False,
                "mode": "retained_memory_post_publish_failure",
                "case_dir": str(baseline_case_dir),
                "iteration": target_iteration,
                "feedback": matching_kg_feedback,
                "retry_resolved": True,
            }
        _write_json(
            round_dir / "targeted_baseline_evaluation.json",
            baseline_target_evaluation,
        )

        projection = _editor_projection(diagnosis, fixture_literals(fixture))
        if owner_layer == "kg_building" and kg_feedback_resolved:
            projection["allow_conflict_replacement"] = True
            projection["conflict_replacement_policy"] = (
                "Replace only the instruction proven by post-publish runtime evidence "
                "to contradict the T-Box/tool contract. Preserve all unrelated text."
            )
        _write_json(round_dir / "repair_projection.json", projection)
        feedback_path = round_dir / "content_feedback.md"
        feedback_path.write_text(
            "# Prompt enhancement diagnosis\n\n"
            + str(projection.get("summary") or "")
            + "\n",
            encoding="utf-8",
        )
        diagnosis_path = round_dir / "repair_projection.json"
        runtime_override = str(
            os.getenv("PROMPT_ENHANCEMENT_RUNTIME_ROOT", "")
        ).strip()
        if runtime_override or os.name == "nt":
            runtime_base = Path(runtime_override or "tmp/_prompt_enhancement_runtime")
            candidate_key = hashlib.sha256(
                str(round_dir.resolve()).encode("utf-8")
            ).hexdigest()[:16]
            candidate_root = runtime_base / candidate_key / "candidate_package"
            if candidate_root.exists():
                shutil.rmtree(candidate_root)
        else:
            candidate_root = round_dir / "candidate"
        before_manifest = artifact_manifest(active_root)
        events.emit(
            "edit",
            "running exact-edit prompt editor",
            target=Path(diagnosis["target_artifacts"][0]).name,
            backend="exact_edits",
        )
        candidate_context = _prompt_only_regeneration(
            previous_root=active_root,
            output_root=candidate_root,
            meta_task_config=Path(active_context.ontology.meta_task_config_path),
            content_feedback_path=feedback_path,
            diagnosis_editor_path=diagnosis_path,
            model=model,
            max_agent_rounds=2,
        )
        edit_report = json.loads(
            (candidate_root / "prompt_enhancement_summary.json").read_text(
                encoding="utf-8"
            )
        )
        _write_json(round_dir / "prompt_edit_report.json", edit_report)
        changed = sorted(
            key
            for key, value in artifact_manifest(candidate_root).items()
            if before_manifest.get(key) != value
        )
        allowed_target = (
            Path("prompts")
            / active_context.ontology.name
            / Path(diagnosis["target_artifacts"][0]).name
        ).as_posix()
        mutation_failures = [
            path for path in changed if path != allowed_target
        ]
        if mutation_failures:
            raise RuntimeError(
                "Prompt-only editor changed artifacts outside its target: "
                + ", ".join(mutation_failures)
            )
        events.emit(
            "validate",
            "prompt candidate passed static validation",
            changed=changed,
            scripts_unchanged=not any(path.startswith("scripts/") for path in changed),
        )
        if owner_layer == "kg_building":
            from src.agents.scripts_and_prompts_generation.semantic_script_review import (
                review_generated_prompt_semantics_with_llm,
            )

            candidate_prompt = (
                Path(candidate_context.prompts_dir)
                / Path(diagnosis["target_artifacts"][0]).name
            )
            semantic_review = review_generated_prompt_semantics_with_llm(
                context=candidate_context,
                artifact_path=candidate_prompt,
                model_name=model,
            )
            stage_ok = bool(edit_report.get("ok"))
            semantic_ok = semantic_review.get("decision") == "pass"
            accepted = stage_ok and semantic_ok and kg_feedback_resolved
            failures = []
            if not stage_ok:
                failures.append("stage_validation_failed")
            if not semantic_ok:
                failures.append("semantic_review_failed")
            if not kg_feedback_resolved:
                failures.append("retained_memory_retry_not_resolved")
            comparison = {
                "mode": "kg_post_publish_repair_evidence",
                "iteration": target_iteration,
                "before": baseline_target_evaluation,
                "after": {
                    "stage_ok": stage_ok,
                    "semantic_review": semantic_review,
                    "runtime_repair_evidence": matching_kg_feedback,
                },
                "accepted": accepted,
                "failures": failures,
                "changed_artifacts": changed,
                "reused_upstream": str(baseline_case_dir),
                "downstream_executed": False,
            }
            _write_json(round_dir / "before_after.json", comparison)
            events.emit(
                "reevaluate",
                "KG prompt candidate stage and semantic review completed",
                accepted=accepted,
                stage_ok=stage_ok,
                semantic_ok=semantic_ok,
                retained_memory_retry_resolved=kg_feedback_resolved,
            )
            rounds.append(
                {
                    "round": round_index,
                    "status": "accepted" if accepted else "rejected",
                    "diagnosis": diagnosis_result,
                    "comparison": comparison,
                    "candidate_root": str(candidate_root),
                }
            )
            if accepted:
                active_root = candidate_root
                active_context = candidate_context
                events.emit(
                    "champion",
                    "KG prompt candidate accepted after all-green review",
                    round=round_index,
                    artifact_root=str(active_root),
                )
            else:
                events.emit(
                    "champion",
                    "KG prompt candidate rejected; baseline retained",
                    reasons=failures,
                )
            break
        candidate_evaluation = _run_targeted_extraction_evaluation(
            context=candidate_context,
            artifact_root=candidate_root,
            fixture=fixture,
            baseline_case_dir=baseline_case_dir,
            iteration=target_iteration,
            sub_iteration=target_sub_iteration,
            output_dir=round_dir / "evaluation",
            judge_model=model,
            events=events,
        )
        paired_delta = judge_extraction_delta_stability(
            document_text=str(fixture.get("document_md") or ""),
            ontology_contract=__import__(
                "src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis",
                fromlist=["_semantic_ontology_contract"],
            )._semantic_ontology_contract(active_context),
            reference_content=_stage_fixture_projection(
                context=active_context,
                fixture=fixture,
                iteration=target_iteration,
            ),
            before_content=_target_iteration_artifacts(
                Path(str(baseline_target_evaluation["case_dir"])),
                target_iteration,
            ),
            after_content=_target_iteration_artifacts(
                Path(str(candidate_evaluation["case_dir"])),
                target_iteration,
            ),
            repair_focus=focus,
            model=model,
            repeats=max(3, evaluation_repeats),
        )
        _write_json(round_dir / "paired_delta_judgement.json", paired_delta)
        accepted = bool(paired_delta["accepted"])
        failures = [] if accepted else ["paired_delta_not_unanimously_accepted"]
        comparison = {
            "mode": "targeted_extraction",
            "iteration": target_iteration,
            "before": _evaluation_summary(baseline_target_evaluation),
            "after": _evaluation_summary(candidate_evaluation),
            "accepted": accepted,
            "failures": failures,
            "paired_delta": paired_delta,
            "changed_artifacts": changed,
            "reused_upstream": str(baseline_case_dir),
            "downstream_executed": False,
        }
        _write_json(round_dir / "before_after.json", comparison)
        events.emit(
            "reevaluate",
            "candidate semantic comparison completed",
            accepted=accepted,
            extraction_before=comparison["before"]["extraction_overall"],
            extraction_after=comparison["after"]["extraction_overall"],
            semantic_before=None,
            semantic_after=None,
        )
        rounds.append(
            {
                "round": round_index,
                "status": "accepted" if accepted else "rejected",
                "diagnosis": diagnosis_result,
                "comparison": comparison,
                "candidate_root": str(candidate_root),
            }
        )
        if not accepted:
            events.emit(
                "champion",
                "candidate rejected; baseline retained",
                reasons=failures,
            )
            break
        active_root = candidate_root
        active_context = candidate_context
        current_evaluation = candidate_evaluation
        events.emit(
            "champion",
            "candidate accepted as prompt champion",
            round=round_index,
            artifact_root=str(active_root),
        )
        # One invocation validates one queued causal focus. A later invocation
        # may consume this champion as the next topological repair candidate.
        break

    accepted = bool(rounds) and rounds[-1].get("status") == "accepted"
    fully_accepted = bool(
        _acceptance(current_evaluation, "extraction_soft_judge").get("accepted")
        and _acceptance(current_evaluation, "semantic_soft_judge").get("accepted")
    )
    result = {
        "ok": fully_accepted or accepted,
        "status": (
            "accepted"
            if fully_accepted
            else "targeted_accepted"
            if accepted
            else rounds[-1].get("status")
            if rounds
            else "no_change"
        ),
        "final_artifact_root": str(active_root),
        "fixture_sha256": run_manifest["fixture_sha256"],
        "baseline": _evaluation_summary(baseline),
        "final": _evaluation_summary(current_evaluation),
        "rounds": rounds,
        "events_path": str(events.path),
    }
    _write_json(enhancement_root / "summary.json", result)
    events.emit(
        "done",
        "formal prompt-only enhancement completed",
        status=result["status"],
        final_artifact_root=result["final_artifact_root"],
    )
    return result
