"""Isolated exact-edit evaluation against audited generation checkpoints."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.exact_edit_editor import (
    run_llm_exact_edit_editor,
)
from src.agents.scripts_and_prompts_generation.generation_checkpoint import (
    replay_generation_checkpoint,
)
from src.agents.scripts_and_prompts_generation.repair_skill_catalog import (
    repair_skill_catalog,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _first_round(manifest: dict[str, Any]) -> dict[str, Any]:
    rounds = manifest.get("rounds") or []
    if not rounds or not isinstance(rounds[0], dict):
        raise ValueError("Historical manifest has no repair round")
    return rounds[0]


def _shadow_task(
    *,
    context: Any,
    historical_round: dict[str, Any],
    validation: dict[str, Any],
) -> str:
    return (
        "This is an isolated shadow evaluation of an exact-edit protocol. Implement the same "
        "repair objective from the audited historical run. Modify only selected targets. "
        "Use package-local `._fixed_rdf_runtime` only through symbols it actually exports. "
        "Materialize real semantic triples before export; do not use empty graphs, prefix-only "
        "Turtle, dummy triples, fixture values, or custom serializer implementations. Preserve "
        "public tool names, signatures, registration, and existing JSON fields.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "historical_focus": historical_round.get("focus") or {},
                "historical_diagnosis": historical_round.get("causal_diagnosis") or {},
                "historical_plan": historical_round.get("impact_plan") or {},
                "golden_repair_skills": repair_skill_catalog(),
                "generation_contract": context.contract,
                "current_validation": {
                    "failures": validation.get("failures") or [],
                    "observations": [
                        observation
                        for observation in (validation.get("observations") or [])
                        if observation.get("status") == "fail"
                    ],
                },
            },
            ensure_ascii=False,
        )
    )


def run_shadow_evaluation(
    *,
    model_name: str,
    checkpoint_summary: Path,
    historical_manifest: Path,
    output_root: Path,
    ontology_name: str = "ontosynthesis",
    max_attempts: int = 5,
) -> dict[str, Any]:
    """Replay one checkpoint, then run exact edits without touching production callers."""
    if output_root.exists():
        if any(output_root.iterdir()):
            raise ValueError("Shadow output root must not exist or must be empty")
        output_root.rmdir()
    replay = replay_generation_checkpoint(
        summary_path=checkpoint_summary,
        output_root=output_root,
        include_package_synthesis=False,
    )
    context = build_agentic_generation_context(
        ontology_name=ontology_name,
        output_root=output_root,
        write_files=True,
    )
    validation_before = build_validation_report(context)
    manifest = _read_json(historical_manifest)
    historical_round = _first_round(manifest)
    raw_targets = (
        ((historical_round.get("impact_plan") or {}).get("targets"))
        or ((historical_round.get("patch") or {}).get("changed_files"))
        or ["scripts/ontosynthesis/main.py"]
    )
    targets = [(output_root / str(path)).resolve() for path in raw_targets]
    root = output_root.resolve()
    if any(not target.is_relative_to(root) or not target.is_file() for target in targets):
        raise ValueError(f"Invalid shadow targets: {raw_targets}")

    candidate_report: dict[str, Any] = {}

    def validate() -> dict[str, Any]:
        nonlocal candidate_report
        candidate_report = build_validation_report(context)
        return {
            "ok": bool(candidate_report.get("ok")),
            "failures": list(candidate_report.get("failures") or []),
            "observations": candidate_report.get("observations") or [],
        }

    result = run_llm_exact_edit_editor(
        model_name=model_name,
        output_root=output_root,
        targets=targets,
        task_prompt=_shadow_task(
            context=context,
            historical_round=historical_round,
            validation=validation_before,
        ),
        max_attempts=max_attempts,
        validate=validate,
        max_targets=3,
        progress=lambda message: print(f"[exact_shadow] {message}", flush=True),
    )
    historical_attempts = list(
        (((historical_round.get("patch") or {}).get("attempts")) or [])
    )
    historical_mechanical_failures = [
        failure
        for attempt in historical_attempts
        for failure in (attempt.get("failures") or [])
        if isinstance(failure, str)
        and (
            "git_apply" in failure
            or "unified_diff" in failure
            or "hunk_" in failure
        )
    ]
    comparison = {
        "schema_version": 1,
        "kind": "exact_edit_shadow_comparison",
        "model": model_name,
        "checkpoint_summary": str(checkpoint_summary),
        "historical_manifest": str(historical_manifest),
        "output_root": str(output_root),
        "production_callers_unchanged": True,
        "replay": replay,
        "historical_baseline": {
            "attempt_count": len(historical_attempts),
            "accepted": bool(historical_round.get("accepted")),
            "mechanical_failure_count": len(historical_mechanical_failures),
            "mechanical_failures": historical_mechanical_failures,
        },
        "exact_shadow": result,
        "gate": {
            "no_diff_or_hunk_failure": not any(
                any(
                    token in json.dumps(failure, ensure_ascii=False)
                    for token in ("git_apply", "unified_diff", "hunk_")
                )
                for attempt in (result.get("attempts") or [])
                for failure in (attempt.get("failures") or [])
            ),
            "globally_valid": bool(result.get("ok")),
            "unauthorised_or_rollback_failure": any(
                any(
                    token in json.dumps(failure, ensure_ascii=False)
                    for token in ("unauthorised", "rollback", "stale_exact_edit_file")
                )
                for attempt in (result.get("attempts") or [])
                for failure in (attempt.get("failures") or [])
            ),
        },
    }
    reports = output_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / "exact_edit_shadow_comparison.json"
    report_path.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    comparison["report_path"] = str(report_path)
    return comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run isolated exact-edit shadow evaluation")
    parser.add_argument("--model", required=True)
    parser.add_argument("--checkpoint-summary", required=True)
    parser.add_argument("--historical-manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--ontology", default="ontosynthesis")
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_shadow_evaluation(
            model_name=args.model,
            checkpoint_summary=Path(args.checkpoint_summary),
            historical_manifest=Path(args.historical_manifest),
            output_root=Path(args.output_root),
            ontology_name=args.ontology,
            max_attempts=max(1, args.max_attempts),
        )
    except Exception:
        output_root = Path(args.output_root)
        if output_root.exists() and not (output_root / "reports").exists():
            shutil.rmtree(output_root, ignore_errors=True)
        raise
    if args.json:
        rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        try:
            sys.stdout.write(rendered)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(rendered.encode("utf-8"))
    return 0 if result["exact_shadow"].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
