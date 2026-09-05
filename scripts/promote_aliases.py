"""Promote explicitly approved alias candidates into a frozen registry release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _identity_key(value: str) -> str:
    text = value.casefold().replace("·", "-").replace("•", "-").replace("⋅", "-")
    text = re.sub(r"\bn,\s*n'\s*-", "n,n-", text)
    text = re.sub(r"\bn,\s*n\s*-", "n,n-", text)
    text = re.sub(r"\s*([,;:()\[\]{}+])\s*", r"\1", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return " ".join(text.split())


def _hydrate_count(value: str) -> str | None:
    match = re.search(r"(?:-|·)(\d+(?:\.\d+)?)h2o\b", value, re.I)
    return match.group(1) if match else None


def _acid_ion_mismatch(values: list[str]) -> bool:
    forms = [
        (
            bool(re.search(r"\bacid\b", value, re.I)),
            bool(re.search(r"(?:carboxylate|benzoate|terephthalate)\b", value, re.I)),
        )
        for value in values
    ]
    return any(acid for acid, _ in forms) and any(anion for _, anion in forms)


def promote(
    registry: dict[str, Any],
    review_payload: dict[str, Any],
    clusters_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    groups = list(registry.get("species") or [])
    by_id = {str(group["canonical_id"]): group for group in groups}
    clean_candidates = {
        candidate_id
        for cluster in clusters_payload.get("clusters") or []
        if cluster.get("status") == "clean"
        for candidate_id in cluster.get("candidate_ids") or []
    }
    promoted: list[str] = []
    for candidate in review_payload.get("candidates") or []:
        review = candidate.get("review") or {}
        if review.get("decision") != "approve":
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id not in clean_candidates:
            raise ValueError(f"Candidate {candidate_id} is not in a clean cluster")
        if candidate.get("evidence", {}).get("level") == "conflicted":
            raise ValueError(f"Candidate {candidate_id} has conflicting evidence")
        canonical_id = str(review.get("canonical_id") or "").strip()
        reviewer = str(review.get("reviewer") or "").strip()
        if not canonical_id or not reviewer:
            raise ValueError(f"Candidate {candidate_id} lacks canonical_id/reviewer")
        values = [str(value) for value in candidate.get("values") or []]
        counts = {count for value in values if (count := _hydrate_count(value))}
        if len(counts) > 1:
            raise ValueError(f"Candidate {candidate_id} mixes hydrate counts")
        if _acid_ion_mismatch(values):
            raise ValueError(f"Candidate {candidate_id} mixes acid and anion forms")
        group = by_id.get(canonical_id)
        if group is None:
            group = {
                "canonical_id": canonical_id,
                "canonical": str(review.get("canonical_value") or values[0]),
                "aliases": [],
                "status": "reviewed",
                "derived_from": ["eval30-all-dev"],
            }
            groups.append(group)
            by_id[canonical_id] = group
        aliases = list(group.get("aliases") or [])
        canonical_key = _identity_key(str(group["canonical"]))
        for value in values:
            if _identity_key(value) != canonical_key and value not in aliases:
                aliases.append(value)
        group["aliases"] = aliases
        promoted.append(candidate_id)

    owner_by_alias: dict[str, str] = {}
    for group in groups:
        canonical_id = str(group["canonical_id"])
        for value in [group["canonical"], *(group.get("aliases") or [])]:
            key = _identity_key(str(value))
            owner = owner_by_alias.get(key)
            if owner is not None and owner != canonical_id:
                raise ValueError(f"Ambiguous alias {value!r}: {owner} vs {canonical_id}")
            owner_by_alias[key] = canonical_id
    result = dict(registry)
    result["registry_status"] = "frozen"
    result["species"] = groups
    return result, promoted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("evaluation/resources/chemical_species_aliases.json"),
    )
    parser.add_argument(
        "--review",
        type=Path,
        default=Path("evaluation/resources/staging/chemical_species_alias_review.json"),
    )
    parser.add_argument(
        "--clusters",
        type=Path,
        default=Path("evaluation/resources/chemical_species_alias_clusters.json"),
    )
    parser.add_argument("--release-id", required=True)
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=Path("evaluation/resources/releases"),
    )
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    clusters = json.loads(args.clusters.read_text(encoding="utf-8"))
    released, promoted = promote(registry, review, clusters)
    args.release_dir.mkdir(parents=True, exist_ok=True)
    registry_path = args.release_dir / f"{args.release_id}.json"
    encoded = (
        json.dumps(released, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    registry_path.write_bytes(encoded)
    manifest = {
        "schema_version": "alias-registry-release-manifest.v1",
        "release_id": args.release_id,
        "benchmark_policy": "development/all-30-derived",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "registry_file": registry_path.name,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "species_count": len(released.get("species") or []),
        "promoted_candidate_ids": promoted,
    }
    manifest_path = args.release_dir / f"{args.release_id}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"release={args.release_id} promoted={len(promoted)}")


if __name__ == "__main__":
    main()
