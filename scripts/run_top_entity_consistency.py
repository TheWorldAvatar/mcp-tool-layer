from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.domain_semantic_planner import (
    plan_top_entity_semantics,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    parse_ontology_ttl,
)


def _trial(parsed: dict[str, Any], output_dir: Path, trial: int) -> dict[str, Any]:
    try:
        decision = plan_top_entity_semantics(
            parsed=parsed,
            planning_dir=output_dir / f"trial_{trial:02d}",
        )
        payload = {"trial": trial, "valid": True, "decision": decision}
    except Exception as exc:
        payload = {
            "trial": trial,
            "valid": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    (output_dir / f"trial_{trial:02d}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run independent T-Box-only top-entity consistency trials."
    )
    parser.add_argument("--tbox", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--parallelism", type=int, default=5)
    args = parser.parse_args()
    if args.trials < 1 or args.parallelism < 1:
        raise ValueError("trials and parallelism must be positive")

    parsed = parse_ontology_ttl(args.tbox)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(args.parallelism, args.trials)) as pool:
        futures = [
            pool.submit(_trial, parsed, output_dir, trial)
            for trial in range(1, args.trials + 1)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: int(item["trial"]))

    valid = [item for item in results if item["valid"]]
    identities = {
        (
            str(item["decision"]["class_iri"]),
            str(item["decision"]["class_local"]),
        )
        for item in valid
    }
    summary = {
        "schema_version": "top-entity-consistency.v1",
        "tbox": str(Path(args.tbox).resolve()),
        "requested_trials": args.trials,
        "valid_trials": len(valid),
        "all_trials_valid": len(valid) == args.trials,
        "unanimous_identity": len(identities) == 1,
        "passed_10_of_10_gate": (
            args.trials == 10 and len(valid) == 10 and len(identities) == 1
        ),
        "identity_counts": {
            f"{class_iri} [{class_local}]": sum(
                1
                for item in valid
                if (
                    str(item["decision"]["class_iri"]),
                    str(item["decision"]["class_local"]),
                )
                == (class_iri, class_local)
            )
            for class_iri, class_local in sorted(identities)
        },
        "results": results,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["passed_10_of_10_gate"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
