"""Run independent materialization-operation judgements on fixed candidates."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.materialization_operation_inference import (
    _judge_prompt,
    _validate_decisions,
    invoke_operation_judge,
)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--parallelism", type=int, default=5)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    candidates = contract["materialization_operation_candidates"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fixed_candidates_path = args.output_dir / "fixed_candidates.json"
    if fixed_candidates_path.is_file():
        if json.loads(fixed_candidates_path.read_text(encoding="utf-8")) != candidates:
            raise ValueError("existing output uses different fixed candidates")
    else:
        _write_json(fixed_candidates_path, candidates)

    valid_decision_counts: dict[str, Counter[str]] = defaultdict(Counter)
    all_decision_counts: dict[str, Counter[str]] = defaultdict(Counter)
    def run_once(run_number: int) -> dict[str, Any]:
        validation_errors: list[str] = []
        attempts: list[dict[str, Any]] = []
        normalized: dict[str, Any] = {}
        previous_response: dict[str, Any] | None = None
        for attempt_number in range(1, 3):
            raw = invoke_operation_judge(
                args.model,
                _judge_prompt(
                    candidates,
                    repair_errors=validation_errors,
                    previous_response=previous_response,
                ),
            )
            normalized, validation_errors = _validate_decisions(candidates, raw)
            attempts.append(
                {
                    "attempt": attempt_number,
                    "raw": raw,
                    "normalized": normalized,
                    "validation_errors": validation_errors,
                }
            )
            if not validation_errors:
                break
            previous_response = raw

        run_payload = {
            "run": run_number,
            "model": args.model,
            "valid": not validation_errors,
            "attempts": attempts,
            "final_normalized": normalized,
            "final_validation_errors": validation_errors,
        }
        _write_json(args.output_dir / f"run_{run_number:02d}.json", run_payload)
        return run_payload

    completed_runs: list[dict[str, Any]] = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(args.output_dir.glob("run_*.json"))
        if int(path.stem.rsplit("_", 1)[-1]) <= args.repeats
    ]
    completed_numbers = {int(item["run"]) for item in completed_runs}
    with ThreadPoolExecutor(
        max_workers=max(1, min(args.parallelism, args.repeats))
    ) as executor:
        futures = [
            executor.submit(run_once, run_number)
            for run_number in range(1, args.repeats + 1)
            if run_number not in completed_numbers
        ]
        for future in as_completed(futures):
            completed_runs.append(future.result())
    completed_runs.sort(key=lambda item: int(item["run"]))
    for run_payload in completed_runs:
        normalized = run_payload["final_normalized"]
        for decision in normalized.get("decisions") or []:
            candidate_id = str(decision["candidate_id"])
            decision_name = str(decision["decision"])
            all_decision_counts[candidate_id][decision_name] += 1
            if run_payload["valid"]:
                valid_decision_counts[candidate_id][decision_name] += 1

    summary = {
        "model": args.model,
        "requested_repeats": args.repeats,
        "valid_runs": sum(bool(run["valid"]) for run in completed_runs),
        "valid_candidate_decision_counts": {
            candidate_id: dict(counts)
            for candidate_id, counts in sorted(valid_decision_counts.items())
        },
        "all_candidate_decision_counts": {
            candidate_id: dict(counts)
            for candidate_id, counts in sorted(all_decision_counts.items())
        },
    }
    candidate_ids = {
        str(candidate["candidate_id"])
        for candidate in candidates.get("candidates") or []
    }
    summary["all_runs_valid"] = summary["valid_runs"] == args.repeats
    summary["unanimous_candidate_count"] = sum(
        len(valid_decision_counts[candidate_id]) == 1
        and sum(valid_decision_counts[candidate_id].values()) == args.repeats
        for candidate_id in candidate_ids
    )
    summary["disagreement_candidates"] = sorted(
        candidate_id
        for candidate_id in candidate_ids
        if len(valid_decision_counts[candidate_id]) != 1
        or sum(valid_decision_counts[candidate_id].values()) != args.repeats
    )
    summary["passed_10_of_10_gate"] = bool(
        args.repeats == 10
        and summary["all_runs_valid"]
        and not summary["disagreement_candidates"]
    )
    _write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["passed_10_of_10_gate"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
