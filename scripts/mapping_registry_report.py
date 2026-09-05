"""Build an eval30 identity-pair inventory and deterministic coverage report."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import evaluation.scoring_steps as scoring
from evaluation.normalize_steps import normalize_json_structure
from evaluation.utils.chemical_name_aliases import (
    CHEMICAL_NAME_ALIAS_MAP,
    FIELD_VALUE_ALIAS_MAPS,
    chemical_identity_key,
)
from evaluation.utils.fast_field_match_judge import deterministic_species_match
from evaluation.utils.scoring_common import hash_map_reverse
from evaluation.utils.step_equivalence_judge import (
    StepEquivalenceConfig,
    StepEquivalenceJudge,
    _normalize_text,
)

DEFAULT_PRED_ROOT = Path(
    "scenarios/mops/runs/20260822_eval30_os_om/evaluation/merged_tll"
)


class PairSink:
    config = SimpleNamespace(enabled=True, fast_match_enabled=False)

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str, str, str]] = []

    def prefetch(self, pairs) -> int:
        self.pairs.extend(sorted(pairs))
        return 0

    def cached_equivalent(self, *args) -> None:
        return None

    def same_product(self, ground_truth_names, prediction_names) -> bool:
        return False


def _prepared_pair(gt_path: Path, pred_path: Path, hash_value: str) -> tuple[dict, dict]:
    gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
    pred_obj = json.loads(pred_path.read_text(encoding="utf-8"))
    gt_obj = scoring._filter_out_product(gt_obj, "H4PBPTA")
    pred_obj = scoring._filter_out_product(pred_obj, "H4PBPTA")
    gt_obj = scoring._convert_air_to_na(gt_obj, hash_value)
    gt_obj = scoring._expand_add_steps_in_obj(gt_obj)
    pred_obj = scoring._expand_add_steps_in_obj(pred_obj)
    return normalize_json_structure(gt_obj), normalize_json_structure(pred_obj)


def _resolver(field_kind: str, left: str, right: str) -> str:
    if left == right:
        return "R0_exact_raw"
    if field_kind == "chemical_name":
        left_key = chemical_identity_key(left)
        right_key = chemical_identity_key(right)
        left_canonical = CHEMICAL_NAME_ALIAS_MAP.get(left_key, left_key)
        right_canonical = CHEMICAL_NAME_ALIAS_MAP.get(right_key, right_key)
        if left_canonical == right_canonical:
            return "R2_reviewed_alias"
        if deterministic_species_match(left, right):
            return "R4_deterministic_species"
    elif field_kind == "atmosphere":
        aliases = FIELD_VALUE_ALIAS_MAPS.get("atmosphere", {})
        left_key = " ".join(left.casefold().split())
        right_key = " ".join(right.casefold().split())
        if aliases.get(left_key, left_key) == aliases.get(right_key, right_key):
            return "R2_reviewed_alias"
    if _normalize_text(left, field_kind) == _normalize_text(right, field_kind):
        return "R1_normalize"
    deterministic = StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=False, fast_match_enabled=False)
    )
    if deterministic.equivalent(field_kind, "", left, right):
        return "R3_quantity_rules"
    return "R5_llm_required"


def build_report(pred_root: Path, gt_root: Path) -> dict[str, Any]:
    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    inventory: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    per_paper: dict[str, Counter[str]] = defaultdict(Counter)
    per_field: dict[str, Counter[str]] = defaultdict(Counter)
    papers: list[str] = []
    for hash_value in sorted(path.name for path in pred_root.iterdir() if path.is_dir()):
        doi = hash_to_doi.get(hash_value)
        pred_path = pred_root / hash_value / "steps.json"
        gt_path = gt_root / f"{doi}.json" if doi else Path()
        if not doi or not pred_path.exists() or not gt_path.exists():
            continue
        papers.append(hash_value)
        gt_obj, pred_obj = _prepared_pair(gt_path, pred_path, hash_value)
        sink = PairSink()
        previous = scoring._ACTIVE_STEP_EQUIVALENCE
        try:
            scoring._ACTIVE_STEP_EQUIVALENCE = sink
            scoring._prefetch_score_equivalence(
                gt_obj,
                pred_obj,
                ignore_vessel=True,
                skip_order=True,
            )
        finally:
            scoring._ACTIVE_STEP_EQUIVALENCE = previous
        for field_kind, field_name, left, right in sink.pairs:
            resolver = _resolver(field_kind, left, right)
            key = (
                field_kind,
                field_name,
                _normalize_text(left, field_kind),
                _normalize_text(right, field_kind),
            )
            row = inventory.setdefault(
                key,
                {
                    "field_kind": field_kind,
                    "field_name": field_name,
                    "ground_truth_value": left,
                    "prediction_value": right,
                    "resolver": resolver,
                    "source_hashes": [],
                    "occurrences": 0,
                },
            )
            row["occurrences"] += 1
            if hash_value not in row["source_hashes"]:
                row["source_hashes"].append(hash_value)
            per_paper[hash_value][resolver] += 1
            per_field[field_name][resolver] += 1

    resolver_counts = Counter(row["resolver"] for row in inventory.values())
    llm_pairs = resolver_counts["R5_llm_required"]
    return {
        "schema_version": "mapping-registry-coverage.v1",
        "benchmark_policy": "development/all-30-derived",
        "pred_root": pred_root.as_posix(),
        "papers_scored": len(papers),
        "paper_hashes": papers,
        "unique_pairs": len(inventory),
        "resolver_counts": dict(sorted(resolver_counts.items())),
        "deterministic_coverage_rate": round(
            (len(inventory) - llm_pairs) / len(inventory), 6
        )
        if inventory
        else 0.0,
        "llm_required_pairs": llm_pairs,
        "estimated_step_batches": sum(
            math.ceil(counts["R5_llm_required"] / 40)
            for counts in per_paper.values()
            if counts["R5_llm_required"]
        ),
        "per_paper": {
            key: dict(sorted(value.items())) for key, value in sorted(per_paper.items())
        },
        "per_field": {
            key: dict(sorted(value.items())) for key, value in sorted(per_field.items())
        },
        "pair_inventory": sorted(
            inventory.values(),
            key=lambda row: (
                row["resolver"],
                row["field_name"],
                -row["occurrences"],
                row["ground_truth_value"],
            ),
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pred-root", type=Path, default=DEFAULT_PRED_ROOT)
    parser.add_argument("--gt-root", type=Path, default=Path("full_ground_truth/steps"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/reports/mapping_registry_coverage_eval30.json"),
    )
    args = parser.parse_args()
    report = build_report(args.pred_root, args.gt_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"papers={report['papers_scored']} pairs={report['unique_pairs']} "
        f"deterministic={report['deterministic_coverage_rate']:.1%} "
        f"llm_pairs={report['llm_required_pairs']}"
    )


if __name__ == "__main__":
    main()
