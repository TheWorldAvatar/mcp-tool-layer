"""Offline smoke: exercise corpus-backed MCP operations without live SPARQL."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from mini_marie.marie.chemistry import competency_operations as cq


def _ok(label: str, result: str, *, min_len: int = 1) -> Dict[str, Any]:
    bad = result.startswith("No ") or result.startswith("No local") or len(result.strip()) < min_len
    return {"check": label, "ok": not bad, "preview": result.splitlines()[0][:120]}


def run_smoke() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    checks.append(_ok("search_species_names", cq.search_species_names("glycol", limit=3)))
    checks.append(_ok("list_species_by_formula", cq.list_species_by_formula("C6H8O6")))
    checks.append(_ok("search_species_uses", cq.search_species_uses("solvent", limit=5)))
    checks.append(_ok("query_species_physprops", cq.query_species_physprops(property_local="hasHydrogenBondDonorCount", limit=5)))
    checks.append(_ok("query_species_pka", cq.query_species_pka(limit=5)))
    checks.append(_ok("search_mechanisms", cq.search_mechanisms("GRI", limit=5)))
    checks.append(
        _ok(
            "traverse_mechanism_reactions",
            cq.traverse_mechanism_reactions("ontokin", reaction_fragment="H2"),
            min_len=10,
        )
    )
    checks.append(_ok("query_calculation_results", cq.query_calculation_results("ontocompchem", "663e4fd4")))
    checks.append(_ok("search_zeolite_materials", cq.search_zeolite_materials("FAU", limit=5)))
    checks.append(
        _ok(
            "query_zeolite_property_framework",
            cq.query_zeolite_property("ontozeolite", framework_code="AEN"),
        )
    )
    checks.append(_ok("search_authors", cq.search_authors("Smooke", limit=5)))
    checks.append(
        _ok(
            "filter_by_literal_formula",
            cq.filter_by_literal("ontospecies", "Species", "hasMolecularFormula", "C6H8O6", match="equals"),
        )
    )
    failed = [c for c in checks if not c["ok"]]
    return {"passed": len(checks) - len(failed), "failed": len(failed), "checks": checks}


def main() -> None:
    summary = run_smoke()
    print(json.dumps(summary, indent=2))
    if summary["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
