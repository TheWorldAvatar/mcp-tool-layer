"""
MOPs TWA MCP Server Package

This package provides MCP tools for querying the MOPs synthesis TWA.
"""

from mini_marie.mop_mof.mops.twa_operations import (
    load_twa_graph,
    ensure_twa_loaded,
    execute_sparql,
    format_results_as_tsv,
    # Lookup functions
    lookup_synthesis_iri,
    lookup_mop_iri,
    lookup_by_ccdc,
    # General queries
    get_all_mops,
    get_twa_statistics,
    # Synthesis queries
    get_synthesis_recipe,
    get_synthesis_steps,
    get_synthesis_temperatures,
    get_synthesis_durations,
    get_synthesis_products,
    # MOP queries
    get_mop_building_units,
    # Corpus queries
    get_common_chemicals,
)

__all__ = [
    "load_twa_graph",
    "ensure_twa_loaded",
    "execute_sparql",
    "format_results_as_tsv",
    "lookup_synthesis_iri",
    "lookup_mop_iri",
    "lookup_by_ccdc",
    "get_all_mops",
    "get_twa_statistics",
    "get_synthesis_recipe",
    "get_synthesis_steps",
    "get_synthesis_temperatures",
    "get_synthesis_durations",
    "get_synthesis_products",
    "get_mop_building_units",
    "get_common_chemicals",
]

