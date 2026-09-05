"""Run independent real-LLM stability trials for generic entity reuse."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .reuse_pair_judge import ReuseJudgeConfig, judge_reuse_pairs


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("reuse judge fixture requires a non-empty cases array")
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError("every reuse judge case must be an object")
        pair_id = str(item.get("pair_id") or "").strip()
        expected = item.get("expected_reuse")
        request = item.get("request")
        if (
            not pair_id
            or pair_id in ids
            or not isinstance(expected, bool)
            or not isinstance(request, dict)
        ):
            raise ValueError("case requires unique pair_id, expected_reuse, and request")
        ids.add(pair_id)
        normalized.append(
            {
                "pair_id": pair_id,
                "expected_reuse": expected,
                "critical_negative": bool(item.get("critical_negative")),
                "request": {**request, "pair_id": pair_id},
            }
        )
    return normalized


def _run_trial(
    *,
    trial: int,
    cases: list[dict[str, Any]],
    model: str,
    output_dir: Path,
) -> dict[str, Any]:
    trial_dir = output_dir / f"trial_{trial:02d}"
    judgements = judge_reuse_pairs(
        [item["request"] for item in cases],
        ReuseJudgeConfig(
            model=model,
            cache_dir=trial_dir / "cache",
            audit_dir=trial_dir / "audit",
            required=True,
        ),
    )
    by_id = {str(item["pair_id"]): item for item in judgements}
    rows = []
    for case in cases:
        judgement = by_id[case["pair_id"]]
        actual = judgement["reuse_authorized"] is True
        expected = case["expected_reuse"]
        rows.append(
            {
                "pair_id": case["pair_id"],
                "expected_reuse": expected,
                "actual_reuse": actual,
                "critical_negative": case["critical_negative"],
                "correct": actual == expected,
                "judgement": judgement,
            }
        )
    result = {
        "trial": trial,
        "valid": True,
        "false_positives": sum(
            1 for row in rows if not row["expected_reuse"] and row["actual_reuse"]
        ),
        "false_negatives": sum(
            1 for row in rows if row["expected_reuse"] and not row["actual_reuse"]
        ),
        "correct": sum(1 for row in rows if row["correct"]),
        "total": len(rows),
        "rows": rows,
    }
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def run_experiment(
    *,
    fixture_path: Path,
    output_dir: Path,
    model: str,
    trials: int,
    parallelism: int,
) -> dict[str, Any]:
    cases = _load_cases(fixture_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(parallelism, trials))) as executor:
        futures = {
            executor.submit(
                _run_trial,
                trial=trial,
                cases=cases,
                model=model,
                output_dir=output_dir,
            ): trial
            for trial in range(1, trials + 1)
        }
        for future in as_completed(futures):
            trial = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append(
                    {
                        "trial": trial,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    results.sort(key=lambda item: int(item["trial"]))
    decisions = {
        case["pair_id"]: [
            next(
                row["actual_reuse"]
                for row in result["rows"]
                if row["pair_id"] == case["pair_id"]
            )
            for result in results
        ]
        for case in cases
    }
    unanimous = {
        pair_id: len(set(values)) == 1 and len(values) == trials
        for pair_id, values in decisions.items()
    }
    total_decisions = len(cases) * len(results)
    total_correct = sum(int(result["correct"]) for result in results)
    summary = {
        "schema_version": "entity-reuse-pair-judge-experiment.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture_path": str(fixture_path.resolve()),
        "model": model,
        "requested_trials": trials,
        "valid_trials": len(results),
        "failures": failures,
        "false_positives": sum(
            int(result["false_positives"]) for result in results
        ),
        "false_negatives": sum(
            int(result["false_negatives"]) for result in results
        ),
        "accuracy": total_correct / total_decisions if total_decisions else 0.0,
        "unanimous_pairs": sum(unanimous.values()),
        "total_pairs": len(cases),
        "pair_unanimity": unanimous,
        "passed": (
            len(results) == trials
            and not failures
            and all(int(result["false_positives"]) == 0 for result in results)
            and total_correct / total_decisions >= 0.95
            and all(unanimous.values())
        )
        if total_decisions
        else False,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--parallelism", type=int, default=5)
    args = parser.parse_args()
    summary = run_experiment(
        fixture_path=args.fixture,
        output_dir=args.output_dir,
        model=args.model,
        trials=args.trials,
        parallelism=args.parallelism,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
