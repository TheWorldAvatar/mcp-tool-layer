"""Mine reviewable alias candidates from existing LLM judgement caches.

This script never modifies the reviewed registries. It aggregates positive and
negative evidence into separate chemical and field-value candidate files for
human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.utils.fast_field_match_judge import deterministic_species_match
from evaluation.utils.step_equivalence_judge import _normalize_text

DEFAULT_CACHE_DIRS = (
    Path("evaluation/cache/step_equivalence_judge"),
    Path("evaluation/cache/fast_field_match_judge"),
    Path("evaluation/cache/chemical_synonym_judge"),
)
SUPPORTED_FIELD_KINDS = {"chemical_name", "atmosphere"}


@dataclass
class Evidence:
    field_kind: str
    field_names: set[str] = field(default_factory=set)
    raw_values: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    positive_models: set[str] = field(default_factory=set)
    negative_models: set[str] = field(default_factory=set)
    positive_observations: int = 0
    negative_observations: int = 0
    max_positive_confidence: float = 0.0
    relations: Counter[str] = field(default_factory=Counter)
    policy_versions: set[str] = field(default_factory=set)


def _extract_pair(payload: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return ``field_kind, field_name, left, right`` for known cache schemas."""
    pair = payload.get("pair")
    if isinstance(pair, dict):
        left = pair.get("ground_truth_name")
        right = pair.get("prediction_name")
        if isinstance(left, str) and isinstance(right, str):
            return "chemical_name", "chemicalName", left, right

    left = payload.get("ground_truth_value")
    right = payload.get("prediction_value")
    if not isinstance(left, str) or not isinstance(right, str):
        return None
    field_kind = str(payload.get("field_kind") or "")
    field_name = str(payload.get("field_name") or field_kind)
    if field_kind not in SUPPORTED_FIELD_KINDS:
        return None
    return field_kind, field_name, left, right


def _pair_key(field_kind: str, left: str, right: str) -> tuple[str, str, str]:
    normalized = sorted(
        (
            _normalize_text(left, field_kind),
            _normalize_text(right, field_kind),
        )
    )
    return field_kind, normalized[0], normalized[1]


def _already_deterministic(field_kind: str, left: str, right: str) -> bool:
    if field_kind == "chemical_name":
        return deterministic_species_match(left, right)
    return _normalize_text(left, field_kind) == _normalize_text(right, field_kind)


def collect_evidence(
    cache_dirs: Iterable[Path],
    *,
    minimum_confidence: float = 0.9,
    max_workers: int = 16,
) -> dict[tuple[str, str, str], Evidence]:
    aggregates: dict[tuple[str, str, str], Evidence] = {}
    paths = [
        path
        for cache_dir in cache_dirs
        if cache_dir.is_dir()
        for path in cache_dir.glob("*.json")
    ]

    def read_payload(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        payloads = executor.map(read_payload, paths)
        for payload in payloads:
            if payload is None:
                continue
            pair = _extract_pair(payload)
            judgement = payload.get("judgement")
            if pair is None or not isinstance(judgement, dict):
                continue
            field_kind, field_name, left, right = pair
            if not left.strip() or not right.strip() or left == right:
                continue
            key = _pair_key(field_kind, left, right)
            evidence = aggregates.setdefault(key, Evidence(field_kind=field_kind))
            evidence.field_names.add(field_name)
            evidence.raw_values[_normalize_text(left, field_kind)][left] += 1
            evidence.raw_values[_normalize_text(right, field_kind)][right] += 1
            model = str(payload.get("model") or "unknown")
            relation = str(judgement.get("relation") or "unknown")
            evidence.relations[relation] += 1
            policy = str(payload.get("policy_version") or "")
            if policy:
                evidence.policy_versions.add(policy)
            confidence = float(judgement.get("confidence") or 0.0)
            if bool(judgement.get("equivalent")) and confidence >= minimum_confidence:
                evidence.positive_models.add(model)
                evidence.positive_observations += 1
                evidence.max_positive_confidence = max(
                    evidence.max_positive_confidence,
                    confidence,
                )
            else:
                evidence.negative_models.add(model)
                evidence.negative_observations += 1
    return aggregates


def _representative(counter: Counter[str]) -> str:
    return sorted(counter, key=lambda value: (-counter[value], len(value), value))[0]


def _candidate(
    key: tuple[str, str, str],
    evidence: Evidence,
) -> dict[str, Any] | None:
    if evidence.positive_observations == 0:
        return None
    left = _representative(evidence.raw_values[key[1]])
    right = _representative(evidence.raw_values[key[2]])
    if _already_deterministic(evidence.field_kind, left, right):
        return None
    if evidence.negative_models:
        evidence_level = "conflicted"
    elif len(evidence.positive_models) >= 2:
        evidence_level = "cross_model_consensus"
    else:
        evidence_level = "single_model"
    candidate_id = hashlib.sha256(
        "\0".join(key).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "candidate_id": candidate_id,
        "field_kind": evidence.field_kind,
        "field_names": sorted(evidence.field_names),
        "values": [left, right],
        "normalized_values": [key[1], key[2]],
        "evidence": {
            "level": evidence_level,
            "positive_models": sorted(evidence.positive_models),
            "negative_models": sorted(evidence.negative_models),
            "positive_observations": evidence.positive_observations,
            "negative_observations": evidence.negative_observations,
            "max_positive_confidence": evidence.max_positive_confidence,
            "relations": dict(sorted(evidence.relations.items())),
            "policy_versions": sorted(evidence.policy_versions),
        },
        "review": {
            "status": "pending_review",
            "decision": None,
            "canonical_id": None,
            "reviewer": None,
            "notes": None,
        },
    }


def mine_candidates(
    cache_dirs: Iterable[Path],
    *,
    minimum_confidence: float = 0.9,
    max_workers: int = 16,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chemical: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    aggregates = collect_evidence(
        cache_dirs,
        minimum_confidence=minimum_confidence,
        max_workers=max_workers,
    )
    for key, evidence in aggregates.items():
        candidate = _candidate(key, evidence)
        if candidate is None:
            continue
        target = chemical if evidence.field_kind == "chemical_name" else fields
        target.append(candidate)
    def sort_key(item: dict[str, Any]) -> tuple[bool, int, str]:
        return (
            item["evidence"]["level"] != "cross_model_consensus",
            -item["evidence"]["positive_observations"],
            item["candidate_id"],
        )

    chemical.sort(key=sort_key)
    fields.sort(key=sort_key)
    return chemical, fields


def enrich_from_inventory(
    candidates: list[dict[str, Any]],
    inventory_path: Path,
) -> None:
    if not inventory_path.exists():
        return
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    rows = payload.get("pair_inventory") or []
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        kind = str(row.get("field_kind") or "")
        values = sorted(
            (
                _normalize_text(str(row.get("ground_truth_value") or ""), kind),
                _normalize_text(str(row.get("prediction_value") or ""), kind),
            )
        )
        index[(kind, values[0], values[1])].append(row)
    for candidate in candidates:
        normalized = sorted(candidate["normalized_values"])
        matches = index.get(
            (candidate["field_kind"], normalized[0], normalized[1]),
            [],
        )
        hashes = sorted(
            {
                hash_value
                for row in matches
                for hash_value in row.get("source_hashes") or []
            }
        )
        occurrences = sum(int(row.get("occurrences") or 0) for row in matches)
        candidate["provenance"] = {
            "benchmark_policy": "development/all-30-derived",
            "source_hashes": hashes,
            "cross_paper_support": len(hashes),
            "eval30_occurrences": occurrences,
        }
        evidence = candidate["evidence"]
        evidence["priority_score"] = (
            (20 if evidence["level"] == "cross_model_consensus" else 0)
            + min(10, len(hashes) * 2)
            + min(20, occurrences)
            + min(10, int(evidence["positive_observations"]))
            - (30 if evidence["level"] == "conflicted" else 0)
        )


def build_clusters(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for candidate in candidates:
        left, right = candidate["normalized_values"]
        union(left, right)
    members: dict[str, set[str]] = defaultdict(set)
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        root = find(candidate["normalized_values"][0])
        members[root].update(candidate["normalized_values"])
        edges[root].append(candidate)
    clusters = []
    hydrate_re = re.compile(r"(?:-|·)(\d+(?:\.\d+)?)h2o\b", re.I)
    for root, values in members.items():
        cluster_edges = edges[root]
        hydrate_counts = {
            match.group(1)
            for value in values
            for match in [hydrate_re.search(value)]
            if match
        }
        conflicted = any(
            edge["evidence"]["level"] == "conflicted" for edge in cluster_edges
        )
        status = (
            "hydrate_risk"
            if len(hydrate_counts) > 1
            else "conflicted"
            if conflicted
            else "clean"
        )
        hashes = sorted(
            {
                hash_value
                for edge in cluster_edges
                for hash_value in edge.get("provenance", {}).get("source_hashes", [])
            }
        )
        clusters.append(
            {
                "cluster_id": hashlib.sha256(
                    "\0".join(sorted(values)).encode("utf-8")
                ).hexdigest()[:16],
                "status": status,
                "members": sorted(values),
                "candidate_ids": sorted(edge["candidate_id"] for edge in cluster_edges),
                "hydrate_counts": sorted(hydrate_counts),
                "source_hashes": hashes,
                "cross_paper_support": len(hashes),
            }
        )
    return sorted(
        clusters,
        key=lambda row: (
            row["status"] != "clean",
            -row["cross_paper_support"],
            -len(row["members"]),
            row["cluster_id"],
        ),
    )


def _write_candidates(path: Path, schema_version: str, candidates: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": schema_version,
        "registry_status": "generated_candidates",
        "promotion_policy": (
            "Never promote automatically. Verify identity and approve manually "
            "before copying an alias into a reviewed registry."
        ),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        action="append",
        type=Path,
        dest="cache_dirs",
        help="Cache directory to scan; repeat as needed.",
    )
    parser.add_argument("--minimum-confidence", type=float, default=0.9)
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("evaluation/reports/mapping_registry_coverage_eval30.json"),
    )
    parser.add_argument(
        "--chemical-output",
        type=Path,
        default=Path("evaluation/resources/chemical_species_alias_candidates.json"),
    )
    parser.add_argument(
        "--field-output",
        type=Path,
        default=Path("evaluation/resources/field_value_alias_candidates.json"),
    )
    parser.add_argument(
        "--cluster-output",
        type=Path,
        default=Path("evaluation/resources/chemical_species_alias_clusters.json"),
    )
    args = parser.parse_args()
    cache_dirs = args.cache_dirs or list(DEFAULT_CACHE_DIRS)
    chemical, fields = mine_candidates(
        cache_dirs,
        minimum_confidence=args.minimum_confidence,
        max_workers=args.max_workers,
    )
    enrich_from_inventory(chemical, args.inventory)
    enrich_from_inventory(fields, args.inventory)
    chemical.sort(
        key=lambda item: (
            -item["evidence"].get("priority_score", 0),
            item["candidate_id"],
        )
    )
    clusters = build_clusters(chemical)
    _write_candidates(
        args.chemical_output,
        "chemical-species-alias-candidates.v1",
        chemical,
    )
    _write_candidates(
        args.field_output,
        "field-value-alias-candidates.v1",
        fields,
    )
    args.cluster_output.write_text(
        json.dumps(
            {
                "schema_version": "chemical-species-alias-clusters.v1",
                "benchmark_policy": "development/all-30-derived",
                "cluster_count": len(clusters),
                "clusters": clusters,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(chemical)} chemical and {len(fields)} field-value candidates."
    )


if __name__ == "__main__":
    main()
