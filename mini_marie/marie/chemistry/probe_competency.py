"""Live LIMIT-5 smoke probes for chemistry competency SPARQL."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, List

from mini_marie.marie.chemistry import competency_operations as cq
from mini_marie.marie.chemistry.limits import ONLINE_PROBE_LIMIT

ProbeFn = Callable[[], str]


def _row_count(tsv: str) -> int:
    if tsv == "No results":
        return 0
    lines = [ln for ln in tsv.strip().splitlines() if ln.strip()]
    return max(0, len(lines) - 1)


def _assert_limit(tsv: str) -> None:
    assert _row_count(tsv) <= ONLINE_PROBE_LIMIT, f"Exceeded limit: {_row_count(tsv)} rows"


PROBES: Dict[str, List[tuple[str, ProbeFn]]] = {
    "ontospecies": [
        (
            "formula C6H8O6",
            lambda: cq.filter_by_literal(
                "ontospecies", "Species", "hasMolecularFormula", "C6H8O6", "equals"
            ),
        ),
        (
            "pKa propenoic SMILES",
            lambda: cq.get_linked_values(
                "ontospecies",
                "Species",
                "C=CC(=O)O",
                "hasDissociationConstants",
                identifier_type="smiles",
            ),
        ),
        (
            "ethylene glycol H-bonds SMILES C(CO)O",
            lambda: cq.get_linked_values(
                "ontospecies",
                "Species",
                "C(CO)O",
                "hasHydrogenBondDonorCount",
                ["hasHydrogenBondAcceptorCount"],
                identifier_type="smiles",
            ),
        ),
    ],
    "ontokin": [
        ("list mechanisms", lambda: cq.lookup_individuals("ontokin", "ReactionMechanism")),
        (
            "reaction H2O2 equation",
            lambda: cq.traverse_mechanism_reactions("ontokin", reaction_fragment="H2O2"),
        ),
    ],
    "ontocompchem": [
        (
            "rotational constants species 798ae810",
            lambda: cq.query_calculation_results(
                "ontocompchem", "798ae810", ["rotational"]
            ),
        ),
        (
            "HOMO LUMO species 663e4fd4",
            lambda: cq.query_calculation_results(
                "ontocompchem", "663e4fd4", ["homo", "lumo"]
            ),
        ),
    ],
    "ontozeolite": [
        (
            "framework AEN materials",
            lambda: cq.filter_by_literal("ontozeolite", "ZeoliticMaterial", "hasFrameworkCode", "AEN"),
        ),
        (
            "reference SFN",
            lambda: cq.query_zeolite_property(
                "ontozeolite", framework_code="SFN", property_local="isReferenceZeolite"
            ),
        ),
    ],
    "ontoprovenance": [
        ("Person Marinov", lambda: cq.lookup_individuals("ontoprovenance", "Person", "Marinov")),
    ],
}


def run_probes(namespaces: List[str] | None = None) -> Dict[str, Any]:
    selected = namespaces or list(PROBES.keys())
    report: Dict[str, Any] = {"online_probe_limit": ONLINE_PROBE_LIMIT, "namespaces": {}}
    failed = 0

    for ns in selected:
        ns_report: List[Dict[str, Any]] = []
        for name, fn in PROBES.get(ns, []):
            item: Dict[str, Any] = {"probe": name}
            try:
                tsv = fn()
                _assert_limit(tsv)
                rows = _row_count(tsv)
                item["rows"] = rows
                item["ok"] = rows > 0
                item["preview"] = tsv.splitlines()[:4]
                if rows == 0:
                    failed += 1
            except Exception as exc:
                item["ok"] = False
                item["error"] = str(exc)[:300]
                failed += 1
            ns_report.append(item)
        report["namespaces"][ns] = ns_report

    report["failed"] = failed
    report["passed"] = sum(
        1 for ns in report["namespaces"].values() for p in ns if p.get("ok")
    )
    return report


def main() -> None:
    report = run_probes()
    print(json.dumps(report, indent=2))
    if report["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
