"""Smoke-test competency MCP operations against human-made questions."""

from __future__ import annotations

import sys

from mini_marie.mop_mof.mof import mof_competency_operations as co


def check(label: str, rows: list, min_rows: int = 1) -> None:
    if len(rows) >= min_rows:
        print(f"PASS {label}: {len(rows)} rows")
    else:
        print(f"FAIL {label}: empty")
        raise AssertionError(label)


def main() -> None:
    check("Q1 UiO-66 PLD", co.get_pld_stats_by_mof_name("UiO-66"))
    check("Q2 Zn by source", co.get_mofs_by_metal("Zn", list_sources=True))
    check("Q3 Cu experimental count", co.get_mofs_by_metal("Cu", count_only=True, experimental_only=True))
    check("Q4 UiO-66 synthesis", co.get_synthesis_by_mof_name("uio-66"))
    check("Q5 DUT-67 identity", co.get_mof_identity_by_name("DUT-67"))
    check("Q6 ZIF-8 identity", co.get_mof_identity_by_name("ZIF-8"))
    check("Q7 ZIF-8 topo count", co.get_mofs_with_same_topology_as("ZIF-8", count_only=True))
    check("Q7 ZIF-8 topo sample", co.get_mofs_with_same_topology_as("ZIF-8"))
    check("Q8 HKUST-1 linker", co.get_linkers_by_mof_name("HKUST-1"))
    check("Q9 MIL-53 hypo count", co.count_hypothetical_same_topology_as("MIL-53"))
    check("Q10 exp pcu", co.count_experimental_by_topology("pcu"))
    check("Q11 HKUST pore", co.get_pore_metrics_by_mof_name("HKUST-1"))
    check("Q12 GSA", co.get_mofs_by_gsa_min(2500))
    check("Q13 MIL-101 DOI", co.get_publications_by_mof_name("mil-101"))
    check("Q14 MIL-53 ASA", co.get_asa_by_mof_name("mil-53"))
    check("Q15 DUT-67 density", co.get_pore_metrics_by_mof_name("DUT-67"))
    check("Q16 LCD<20", co.get_mofs_by_lcd_max(20))
    check("Q17 solvents", co.get_top_synthesis_solvents())
    check("David Zr COOH", co.get_mofs_by_metal_and_linker())
    check("David pcu all", co.get_mofs_by_topology_all_sources("pcu"))
    check("David ZIF refcodes", co.get_refcodes_by_mof_name("ZIF-8"))
    print("\nALL COMPETENCY MCP SMOKE TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise
