from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _iteration_projection(payload: dict[str, Any]) -> dict[int, dict[str, list[str]]]:
    return {
        int(item["iteration_number"]): {
            "classes": sorted(
                str(value)
                for value in (item.get("responsibilities") or {}).get("classes") or []
            ),
            "object_properties": sorted(
                str(value)
                for value in (item.get("responsibilities") or {}).get(
                    "object_properties"
                )
                or []
            ),
        }
        for item in payload.get("iterations") or []
    }


def _diff_sets(
    baseline: dict[int, dict[str, list[str]]],
    derived: dict[int, dict[str, list[str]]],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for iteration in sorted(set(baseline) | set(derived)):
        for field in ("classes", "object_properties"):
            old = set((baseline.get(iteration) or {}).get(field) or [])
            new = set((derived.get(iteration) or {}).get(field) or [])
            if old != new:
                differences.append(
                    {
                        "iteration_number": iteration,
                        "field": field,
                        "added_by_derivation": sorted(new - old),
                        "absent_from_derivation": sorted(old - new),
                    }
                )
    return differences


def _reuse_projection(payload: dict[str, Any]) -> dict[str, tuple[bool, str]]:
    projection: dict[str, tuple[bool, str]] = {}
    for item in payload.get("classes") or []:
        if not isinstance(item, dict) or not str(item.get("class_iri") or ""):
            continue
        reusable = bool(item.get("reusable"))
        projection[str(item["class_iri"])] = (
            reusable,
            str(item.get("reuse_scope") or "") if reusable else "non_reusable",
        )
    return projection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare generated semantic config with the legacy baseline."
    )
    parser.add_argument("--baseline-blueprint", required=True)
    parser.add_argument("--derived-blueprint", required=True)
    parser.add_argument("--top-summary", required=True)
    parser.add_argument("--baseline-top-local", required=True)
    parser.add_argument("--baseline-reuse-policy")
    parser.add_argument("--derived-reuse-candidate")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    baseline = json.loads(Path(args.baseline_blueprint).read_text(encoding="utf-8"))
    derived = json.loads(Path(args.derived_blueprint).read_text(encoding="utf-8"))
    top = json.loads(Path(args.top_summary).read_text(encoding="utf-8"))
    selected_top = str(top["results"][0]["decision"]["class_local"])
    differences = _diff_sets(
        _iteration_projection(baseline),
        _iteration_projection(derived),
    )
    reuse_report: dict[str, Any] | None = None
    if args.baseline_reuse_policy and args.derived_reuse_candidate:
        baseline_reuse = _reuse_projection(
            json.loads(
                Path(args.baseline_reuse_policy).read_text(encoding="utf-8")
            )
        )
        derived_reuse = _reuse_projection(
            json.loads(
                Path(args.derived_reuse_candidate).read_text(encoding="utf-8")
            )
        )
        all_classes = sorted(derived_reuse)
        reuse_differences = [
            {
                "class_iri": class_iri,
                "baseline": baseline_reuse.get(class_iri),
                "derived": derived_reuse.get(class_iri),
            }
            for class_iri in all_classes
            if baseline_reuse.get(class_iri) != derived_reuse.get(class_iri)
        ]
        reuse_report = {
            "matches": not reuse_differences,
            "differences": reuse_differences,
            "match_basis_excluded_from_structural_parity": True,
            "match_basis_review_status": str(
                json.loads(
                    Path(args.derived_reuse_candidate).read_text(encoding="utf-8")
                ).get("status")
                or "unknown"
            ),
        }
    reuse_matches = reuse_report is None or reuse_report["matches"]
    report = {
        "schema_version": "config-derivation-parity.v1",
        "top_entity": {
            "baseline": args.baseline_top_local,
            "derived": selected_top,
            "matches": selected_top == args.baseline_top_local,
        },
        "iteration_semantic_surface": {
            "matches": not differences,
            "differences": differences,
        },
        "reuse_decision_surface": reuse_report,
        "passed": (
            selected_top == args.baseline_top_local
            and not differences
            and reuse_matches
        ),
        "interpretation": (
            "Parity failure is fail-closed evidence requiring review; it does not "
            "authorize copying the legacy semantic fields into generated config."
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
