"""
Comprehensive full-tier warm specs for every MOF competency atomic tool.

Each entry is one (tool, args) pair whose remote result is stored uncapped in SQLite.
Offline replay reads these full-tier rows; online probe only validates call sequences.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mini_marie.mop_mof.mof.competency_cache import TOOL_REGISTRY
from mini_marie.warm_manifest import merge_warm_specs

# MOF names / references used across competency workflows (hasNames lookups)
MOF_NAMES: List[str] = [
    "UiO-66",
    "uio-66",
    "DUT-67",
    "ZIF-8",
    "HKUST-1",
    "MIL-53",
    "mil-53",
    "mil-101",
    "MIL-101",
]

REFERENCE_NAMES: List[str] = ["ZIF-8", "MIL-53"]

METALS: List[str] = ["Zn", "Cu", "Zr"]

TOPOLOGIES: List[str] = ["pcu", "sod"]


def comprehensive_warm_specs() -> List[Dict[str, Any]]:
    """
    Full cache for every registered atomic tool (not workflow-selective).

    Name/reference tools: one full warm per known name.
    Corpus tools: single warm with default args (uncapped LIMIT).
    """
    specs: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(tool: str, args: Dict[str, Any]) -> None:
        if tool not in TOOL_REGISTRY:
            return
        key = f"{tool}:{sorted(args.items())}"
        if key in seen:
            return
        seen.add(key)
        specs.append({"tool": tool, "args": args})

    for name in MOF_NAMES:
        add("get_pld_stats_by_mof_name", {"mof_name": name})
        add("get_synthesis_by_mof_name", {"mof_name": name})
        add("get_mof_identity_by_name", {"mof_name": name})
        add("get_linkers_by_mof_name", {"mof_name": name})
        add("get_publications_by_mof_name", {"mof_name": name})
        add("get_pore_metrics_by_mof_name", {"mof_name": name})
        add("get_asa_by_mof_name", {"mof_name": name})
        add("get_refcodes_by_mof_name", {"mof_name": name})

    for ref in REFERENCE_NAMES:
        add("get_mofs_with_same_topology_as", {"reference_name": ref})
        add("count_hypothetical_same_topology_as", {"reference_name": ref})

    for metal in METALS:
        add("get_mofs_by_metal", {"metal": metal})

    for topo in TOPOLOGIES:
        add("count_experimental_by_topology", {"topology": topo})
        add("get_mofs_by_topology_all_sources", {"topology": topo})

    add("get_mofs_by_gsa_min", {"min_gsa": 2500})
    add("get_mofs_by_lcd_max", {"max_lcd_angstrom": 20})
    add("get_top_synthesis_solvents", {})
    add("get_mofs_by_metal_and_linker", {})
    add("get_aqueous_low_temp_syntheses", {})
    add("get_water_stable_mofs", {})
    add("get_thermal_stable_mofs", {})
    add("get_high_binary_gas_uptake_mofs", {})
    add("get_nist_exp_adsorption_rows", {})
    add("get_core_name_chemistry_rows", {})
    add("get_tobassco_func_groups_by_mofid", {})

    # Ensure every registered tool appears at least once
    covered = {s["tool"] for s in specs}
    for tool in sorted(TOOL_REGISTRY):
        if tool not in covered:
            add(tool, {})

    return specs


def workflow_driven_warm_specs() -> List[Dict[str, Any]]:
    """
    Comprehensive atomics plus every (tool, args) from competency_suite.json.

    Ensures arg variants (count_only, list_sources, experimental_only, …) are warmed.
    """
    from mini_marie.mop_mof.mof.competency_workflow_engine import load_manifest
    from mini_marie.mop_mof.mof.competency_engine import resolve_value
    from mini_marie.warm_manifest import collect_atomic_specs_from_suite_manifest

    manifest = load_manifest()
    workflow_specs = collect_atomic_specs_from_suite_manifest(
        manifest, resolve=resolve_value
    )
    return merge_warm_specs(comprehensive_warm_specs(), workflow_specs)
