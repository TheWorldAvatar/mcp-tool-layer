"""
OntoMOFs TWA MCP Server

FastMCP server exposing atomic SPARQL-backed tools for the MOF TWA.
Query limits are hardcoded in mof_operations.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from functools import wraps

from fastmcp import FastMCP

from mini_marie.mop_mof.mof.workflow_mcp import (
    MCP_ONLINE_LIMIT,
    list_workflows_text,
    replay_workflow_offline,
    run_workflow_online,
)
from mini_marie.mop_mof.mof.competency_workflow_mcp import (
    list_competency_workflows_text,
    replay_competency_offline as replay_mof_competency_offline,
    run_competency_online as run_mof_competency_online,
)
from mini_marie.mop_mof.mof import mof_competency_operations as competency
from mini_marie.mop_mof.mof.mof_operations import (
    format_results_as_tsv,
    get_large_pore_co2_candidates,
    get_mof_properties_by_mofid_fragment,
    get_mof_total_count,
    get_mofs_by_sourcedb,
    get_source_database_stats,
    get_tobassco_co2_coverage,
    get_tobassco_co2_uptake_stats,
    get_tobassco_mofs_by_metal_node,
    get_tobassco_mofs_by_topology,
    get_tobassco_topology_counts,
    get_top_tobassco_co2_uptake,
    get_top_tobassco_co2_valid_pore_geometry,
    lookup_mof_by_mofid_fragment,
)

logger = logging.getLogger("mof_twa_mcp")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(name)s] %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def mof_twa_tool_logger(func):
    """Log MCP tool invocations."""

    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger.info("Tool call: %s args=%s kwargs=%s", func.__name__, args, kwargs)
            try:
                return await func(*args, **kwargs)
            except Exception:
                logger.exception("Tool failed: %s", func.__name__)
                raise

        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        logger.info("Tool call: %s args=%s kwargs=%s", func.__name__, args, kwargs)
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("Tool failed: %s", func.__name__)
            raise

    return sync_wrapper


mcp = FastMCP(name="mof-twa")


@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "You can query the OntoMOFs TWA (~850k MOFs) via SPARQL-backed tools.\n\n"
        "**Endpoint:** http://68.183.227.15:3840/ontop/sparql/\n"
        "**Prefix:** mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>\n\n"
        "**Corpus tools:**\n"
        "1. get_mof_total_count - total MOF count\n"
        "2. get_source_database_stats - MOF counts per source database\n\n"
        "**Tobassco / CO2 tools (P=15 bar, T=298 K):**\n"
        "3. get_tobassco_co2_coverage - count Tobassco MOFs with CO2 uptake data\n"
        "4. get_tobassco_co2_uptake_stats - avg/min/max CO2 uptake for Tobassco\n"
        "5. get_top_tobassco_co2_uptake - top Tobassco MOFs by CO2 uptake (seed query)\n"
        "6. get_top_tobassco_co2_valid_pore_geometry - top uptake with valid PLD/LCD (>0)\n"
        "7. get_large_pore_co2_candidates - PLD>=10 A, uptake below 300 mmol/g cap\n"
        "8. get_tobassco_topology_counts - MOF counts by RCSR topology\n\n"
        "**Filtered lookup tools:**\n"
        "9. get_tobassco_mofs_by_topology - filter Tobassco by topology (e.g. pcu, nbo)\n"
        "10. get_tobassco_mofs_by_metal_node - filter by metal-node SMILES (e.g. [Zn][Zn])\n"
        "11. get_mofs_by_sourcedb - top MOFs from any source database name\n"
        "12. lookup_mof_by_mofid_fragment - search MOFid-v1 substring\n"
        "13. get_mof_properties_by_mofid_fragment - full property list for first MOF match\n\n"
        "**Competency tools (hasNames MOFs: UiO-66, ZIF-8, HKUST-1, MIL-53, etc.):**\n"
        "17. get_pld_stats_by_mof_name - avg/variance PLD for a named MOF\n"
        "18. get_mofs_by_metal - Zn/Cu/Zr MOFs; use count_only, experimental_only, list_sources\n"
        "19. get_synthesis_by_mof_name - synthesis method, solvent, temperature, DOI\n"
        "20. get_mof_identity_by_name - topology, space group, refcode per source\n"
        "21. get_linkers_by_mof_name / get_publications_by_mof_name\n"
        "22. get_mofs_with_same_topology_as - peers sharing topology with ZIF-8, etc.\n"
        "23. count_hypothetical_same_topology_as / count_experimental_by_topology\n"
        "24. get_pore_metrics_by_mof_name / get_asa_by_mof_name\n"
        "25. get_mofs_by_gsa_min / get_mofs_by_lcd_max\n"
        "26. get_top_synthesis_solvents - solvent frequency (excl. DMF/DMA)\n"
        "27. get_mofs_by_metal_and_linker / get_mofs_by_topology_all_sources\n"
        "28. get_refcodes_by_mof_name / get_water_stable_mofs / get_thermal_stable_mofs\n"
        "29. get_aqueous_low_temp_syntheses / get_high_binary_gas_uptake_mofs\n\n"
        "**Notes:**\n"
        "- Result row limits are hardcoded (typically 10 rows).\n"
        "- CO2 uptake max of 363 mmol/g is often a model cap; prefer tools 6 or 7 for ranking.\n"
        "- PLD/LCD of -1 means missing pore geometry.\n"
        "- Topology property is hasRCSRSym (not hasTopology).\n"
        "- Metal node property is hasNodeSmile.\n"
        "- All tool outputs are TSV for easy parsing.\n\n"
        "**Scalable workflows (online probe → record → offline replay):**\n"
        "14. list_workflows - available multi-step MOF workflows\n"
        "15. run_workflow_online(workflow_name) - LIMIT 10 per rank step; saves recording\n"
        "16. replay_workflow_offline(recording_path) - full-scale replay without agent timeout\n"
        "17. list_competency_workflows - human competency questions as cached workflows\n"
        "18. run_competency_online(workflow_id) - online probe for competency workflow\n"
        "19. replay_competency_offline(recording_path) - full cache replay (orchestrator use)\n"
    )


def _tsv_or_message(
    results,
    empty_message: str,
    *,
    row_limit: int = MCP_ONLINE_LIMIT,
) -> str:
    if not results:
        return empty_message
    if len(results) > row_limit:
        results = results[:row_limit]
    return format_results_as_tsv(results)


@mof_twa_tool_logger
@mcp.tool(name="get_mof_total_count", description="Total number of MOFs in the OntoMOFs TWA")
async def get_mof_total_count_tool() -> str:
    return _tsv_or_message(get_mof_total_count(), "No MOF count returned")


@mof_twa_tool_logger
@mcp.tool(name="get_source_database_stats", description="MOF counts grouped by source database")
async def get_source_database_stats_tool() -> str:
    return _tsv_or_message(get_source_database_stats(), "No source database stats found")


@mof_twa_tool_logger
@mcp.tool(
    name="get_tobassco_co2_coverage",
    description="Count Tobassco MOFs with predicted CO2 uptake at 15 bar and 298 K",
)
async def get_tobassco_co2_coverage_tool() -> str:
    return _tsv_or_message(get_tobassco_co2_coverage(), "No Tobassco CO2 coverage data found")


@mof_twa_tool_logger
@mcp.tool(
    name="get_tobassco_co2_uptake_stats",
    description="Average, minimum, and maximum Tobassco CO2 uptake (mmol/g) at 15 bar, 298 K",
)
async def get_tobassco_co2_uptake_stats_tool() -> str:
    return _tsv_or_message(get_tobassco_co2_uptake_stats(), "No Tobassco CO2 stats found")


@mof_twa_tool_logger
@mcp.tool(
    name="get_top_tobassco_co2_uptake",
    description="Top Tobassco MOFs by predicted CO2 uptake; returns MOFid, PLD, LCD, uptake",
)
async def get_top_tobassco_co2_uptake_tool() -> str:
    return _tsv_or_message(get_top_tobassco_co2_uptake(), "No Tobassco CO2 uptake results found")


@mof_twa_tool_logger
@mcp.tool(
    name="get_top_tobassco_co2_valid_pore_geometry",
    description="Top Tobassco CO2 uptake where PLD and LCD are both positive",
)
async def get_top_tobassco_co2_valid_pore_geometry_tool() -> str:
    return _tsv_or_message(
        get_top_tobassco_co2_valid_pore_geometry(),
        "No Tobassco MOFs with valid pore geometry found",
    )


@mof_twa_tool_logger
@mcp.tool(
    name="get_large_pore_co2_candidates",
    description="Tobassco MOFs with PLD >= 10 A and CO2 uptake below 300 mmol/g model cap",
)
async def get_large_pore_co2_candidates_tool() -> str:
    return _tsv_or_message(get_large_pore_co2_candidates(), "No large-pore CO2 candidates found")


@mof_twa_tool_logger
@mcp.tool(
    name="get_tobassco_topology_counts",
    description="Tobassco MOF counts grouped by RCSR topology symbol (hasRCSRSym)",
)
async def get_tobassco_topology_counts_tool() -> str:
    return _tsv_or_message(get_tobassco_topology_counts(), "No Tobassco topology counts found")


@mof_twa_tool_logger
@mcp.tool(
    name="get_tobassco_mofs_by_topology",
    description="Top Tobassco MOFs for an RCSR topology symbol (e.g. pcu, nbo, sra)",
)
async def get_tobassco_mofs_by_topology_tool(topology: str) -> str:
    results = get_tobassco_mofs_by_topology(topology)
    return _tsv_or_message(results, f"No Tobassco MOFs found for topology: {topology}")


@mof_twa_tool_logger
@mcp.tool(
    name="get_tobassco_mofs_by_metal_node",
    description="Top Tobassco MOFs whose metal-node SMILES contains the given fragment (e.g. [Zn][Zn])",
)
async def get_tobassco_mofs_by_metal_node_tool(metal_smiles: str) -> str:
    results = get_tobassco_mofs_by_metal_node(metal_smiles)
    return _tsv_or_message(results, f"No Tobassco MOFs found for metal node: {metal_smiles}")


@mof_twa_tool_logger
@mcp.tool(
    name="get_mofs_by_sourcedb",
    description="Top MOFs from a named source database (exact match on hasSourcedb)",
)
async def get_mofs_by_sourcedb_tool(source_db: str) -> str:
    results = get_mofs_by_sourcedb(source_db)
    return _tsv_or_message(results, f"No MOFs found for source database: {source_db}")


@mof_twa_tool_logger
@mcp.tool(
    name="lookup_mof_by_mofid_fragment",
    description="Search MOFs by substring match on MOFid-v1 (hasMofidV1)",
)
async def lookup_mof_by_mofid_fragment_tool(mofid_fragment: str) -> str:
    results = lookup_mof_by_mofid_fragment(mofid_fragment)
    return _tsv_or_message(results, f"No MOFs found matching MOFid fragment: {mofid_fragment}")


@mof_twa_tool_logger
@mcp.tool(
    name="get_mof_properties_by_mofid_fragment",
    description="Return predicate/value pairs for the first MOF matching a MOFid-v1 fragment",
)
async def get_mof_properties_by_mofid_fragment_tool(mofid_fragment: str) -> str:
    results = get_mof_properties_by_mofid_fragment(mofid_fragment)
    return _tsv_or_message(results, f"No properties found for MOFid fragment: {mofid_fragment}")


def _competency_tsv(rows, empty: str) -> str:
    return _tsv_or_message(rows, empty)


@mof_twa_tool_logger
@mcp.tool(name="get_pld_stats_by_mof_name", description="Average and variance PLD for MOFs with hasNames (e.g. UiO-66)")
async def get_pld_stats_by_mof_name_tool(mof_name: str) -> str:
    return _competency_tsv(competency.get_pld_stats_by_mof_name(mof_name), f"No PLD data for {mof_name}")


@mof_twa_tool_logger
@mcp.tool(name="get_mofs_by_metal", description="MOFs containing a metal (hasMetal or hasNodeSmile); optional count/sources")
async def get_mofs_by_metal_tool(
    metal: str,
    count_only: bool = False,
    experimental_only: bool = False,
    list_sources: bool = False,
) -> str:
    rows = competency.get_mofs_by_metal(
        metal,
        count_only=count_only,
        experimental_only=experimental_only,
        list_sources=list_sources,
        limit=MCP_ONLINE_LIMIT,
    )
    return _competency_tsv(rows, f"No MOFs found for metal {metal}")


@mof_twa_tool_logger
@mcp.tool(name="get_synthesis_by_mof_name", description="Synthesis routes for a named MOF (method, solvent, temp, DOI)")
async def get_synthesis_by_mof_name_tool(mof_name: str) -> str:
    return _competency_tsv(
        competency.get_synthesis_by_mof_name(mof_name, limit=MCP_ONLINE_LIMIT),
        f"No synthesis for {mof_name}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_mof_identity_by_name", description="Topology, space group, refcode, sources for a MOF name")
async def get_mof_identity_by_name_tool(mof_name: str) -> str:
    return _competency_tsv(
        competency.get_mof_identity_by_name(mof_name, limit=MCP_ONLINE_LIMIT),
        f"No identity for {mof_name}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_linkers_by_mof_name", description="Linker SMILES for a named MOF (e.g. HKUST-1)")
async def get_linkers_by_mof_name_tool(mof_name: str) -> str:
    return _competency_tsv(
        competency.get_linkers_by_mof_name(mof_name, limit=MCP_ONLINE_LIMIT),
        f"No linkers for {mof_name}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_publications_by_mof_name", description="Reference DOIs for MOFs matching a name fragment")
async def get_publications_by_mof_name_tool(mof_name: str) -> str:
    return _competency_tsv(
        competency.get_publications_by_mof_name(mof_name, limit=MCP_ONLINE_LIMIT),
        f"No DOIs for {mof_name}",
    )


@mof_twa_tool_logger
@mcp.tool(
    name="get_mofs_with_same_topology_as",
    description="Sample MOFs sharing RCSR topology with a reference MOF; set count_only=true for totals",
)
async def get_mofs_with_same_topology_as_tool(
    reference_name: str,
    count_only: bool = False,
) -> str:
    return _competency_tsv(
        competency.get_mofs_with_same_topology_as(
            reference_name, count_only=count_only, limit=MCP_ONLINE_LIMIT
        ),
        f"No peers for topology of {reference_name}",
    )


@mof_twa_tool_logger
@mcp.tool(name="count_hypothetical_same_topology_as", description="Count non-experimental MOFs sharing topology with named MOF")
async def count_hypothetical_same_topology_as_tool(reference_name: str) -> str:
    return _competency_tsv(
        competency.count_hypothetical_same_topology_as(reference_name),
        f"No count for {reference_name}",
    )


@mof_twa_tool_logger
@mcp.tool(name="count_experimental_by_topology", description="Count experimental MOFs with given RCSR topology (e.g. pcu)")
async def count_experimental_by_topology_tool(topology: str) -> str:
    return _competency_tsv(
        competency.count_experimental_by_topology(topology),
        f"No experimental MOFs for topology {topology}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_pore_metrics_by_mof_name", description="Max LCD and avg PLD/density/ASA for a MOF name")
async def get_pore_metrics_by_mof_name_tool(mof_name: str) -> str:
    return _competency_tsv(competency.get_pore_metrics_by_mof_name(mof_name), f"No pore metrics for {mof_name}")


@mof_twa_tool_logger
@mcp.tool(name="get_asa_by_mof_name", description="Accessible surface area (hasASA) per source for a MOF name")
async def get_asa_by_mof_name_tool(mof_name: str) -> str:
    return _competency_tsv(
        competency.get_asa_by_mof_name(mof_name, limit=MCP_ONLINE_LIMIT),
        f"No ASA for {mof_name}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_mofs_by_gsa_min", description="MOFs with geometric surface area >= min_gsa (m2/g)")
async def get_mofs_by_gsa_min_tool(min_gsa: float = 2500) -> str:
    return _competency_tsv(
        competency.get_mofs_by_gsa_min(min_gsa, limit=MCP_ONLINE_LIMIT),
        f"No MOFs with GSA >= {min_gsa}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_mofs_by_lcd_max", description="MOFs with largest cavity diameter below max_lcd_angstrom")
async def get_mofs_by_lcd_max_tool(max_lcd_angstrom: float = 20) -> str:
    return _competency_tsv(
        competency.get_mofs_by_lcd_max(max_lcd_angstrom, limit=MCP_ONLINE_LIMIT),
        f"No MOFs with LCD < {max_lcd_angstrom}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_top_synthesis_solvents", description="Top synthesis solvents excluding DMF/DMA/HF by default")
async def get_top_synthesis_solvents_tool(exclude: str = "DMF,DMA,HF") -> str:
    return _competency_tsv(
        competency.get_top_synthesis_solvents(exclude=exclude, limit=MCP_ONLINE_LIMIT),
        "No solvent data",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_mofs_by_metal_and_linker", description="MOFs matching metal node and linker SMILES fragments")
async def get_mofs_by_metal_and_linker_tool(
    metal_fragment: str = "zr",
    linker_fragment: str = "c(=o)o",
) -> str:
    return _competency_tsv(
        competency.get_mofs_by_metal_and_linker(
            metal_fragment, linker_fragment, limit=MCP_ONLINE_LIMIT
        ),
        "No MOFs matching metal/linker filters",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_mofs_by_topology_all_sources", description="MOFs with topology across all source databases")
async def get_mofs_by_topology_all_sources_tool(topology: str) -> str:
    return _competency_tsv(
        competency.get_mofs_by_topology_all_sources(topology, limit=MCP_ONLINE_LIMIT),
        f"No MOFs for topology {topology}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_refcodes_by_mof_name", description="CSD refcodes for a MOF name (e.g. ZIF-8)")
async def get_refcodes_by_mof_name_tool(mof_name: str) -> str:
    return _competency_tsv(
        competency.get_refcodes_by_mof_name(mof_name, limit=MCP_ONLINE_LIMIT),
        f"No refcodes for {mof_name}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_water_stable_mofs", description="MOFs with predicted or Burtch water-stability signals")
async def get_water_stable_mofs_tool() -> str:
    return _competency_tsv(
        competency.get_water_stable_mofs(limit=MCP_ONLINE_LIMIT),
        "No water-stable MOFs found",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_thermal_stable_mofs", description="MOFs with experimental thermal stability above min_thermal")
async def get_thermal_stable_mofs_tool(min_thermal: float = 400) -> str:
    return _competency_tsv(
        competency.get_thermal_stable_mofs(min_thermal=min_thermal, limit=MCP_ONLINE_LIMIT),
        f"No MOFs with thermal stability > {min_thermal}",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_aqueous_low_temp_syntheses", description="Aqueous low-temperature syntheses without DMF/DMA")
async def get_aqueous_low_temp_syntheses_tool() -> str:
    return _competency_tsv(
        competency.get_aqueous_low_temp_syntheses(limit=MCP_ONLINE_LIMIT),
        "No aqueous low-temp syntheses found",
    )


@mof_twa_tool_logger
@mcp.tool(name="get_high_binary_gas_uptake_mofs", description="High CO2/N2 or CO2/H2 binary uptake (ARC_MOF predictions)")
async def get_high_binary_gas_uptake_mofs_tool() -> str:
    return _competency_tsv(
        competency.get_high_binary_gas_uptake_mofs(limit=MCP_ONLINE_LIMIT),
        "No high binary uptake MOFs found",
    )


@mof_twa_tool_logger
@mcp.tool(
    name="list_workflows",
    description="List available multi-step MOF workflows for online probe and offline replay",
)
async def list_workflows_tool() -> str:
    return list_workflows_text()


@mof_twa_tool_logger
@mcp.tool(
    name="run_workflow_online",
    description=(
        "Run a recorded MOF workflow online with LIMIT 10 on rank/list SPARQL steps. "
        "Returns compact TSV + recording_path for replay_workflow_offline."
    ),
)
async def run_workflow_online_tool(
    workflow_name: str,
    online_limit: int = MCP_ONLINE_LIMIT,
) -> str:
    if online_limit > 20:
        online_limit = MCP_ONLINE_LIMIT
    return run_workflow_online(workflow_name, online_limit=online_limit)


@mof_twa_tool_logger
@mcp.tool(
    name="replay_workflow_offline",
    description=(
        "Replay a MOF workflow from recording_path with limits removed/raised for full results."
    ),
)
async def replay_workflow_offline_tool(
    recording_path: str,
    offline_cap: int = 500_000,
    workflow_name: str = "",
    workflow_path: str = "",
) -> str:
    return replay_workflow_offline(
        recording_path,
        offline_cap=offline_cap,
        workflow_name=workflow_name or None,
        workflow_path=workflow_path or None,
    )


@mof_twa_tool_logger
@mcp.tool(
    name="list_competency_workflows",
    description="List MOF competency workflows from CompetencyQs.md (workflow_id + question)",
)
async def list_competency_workflows_tool() -> str:
    return list_competency_workflows_text()


@mof_twa_tool_logger
@mcp.tool(
    name="run_competency_online",
    description=(
        "Run a MOF competency workflow online (LIMIT 10). "
        "Returns compact TSV + recording_path for offline replay."
    ),
)
async def run_competency_online_tool(
    workflow_id: str,
    online_limit: int = MCP_ONLINE_LIMIT,
) -> str:
    if online_limit > 20:
        online_limit = MCP_ONLINE_LIMIT
    return run_mof_competency_online(workflow_id, online_limit=online_limit)


@mof_twa_tool_logger
@mcp.tool(
    name="replay_competency_offline",
    description="Replay MOF competency workflow from recording_path at full cache tier.",
)
async def replay_competency_offline_tool(recording_path: str) -> str:
    return replay_mof_competency_offline(recording_path)


if __name__ == "__main__":
    mcp.run(transport="stdio")
