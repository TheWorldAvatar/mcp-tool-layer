"""
TWA City TWA MCP Server

FastMCP server exposing SPARQL-backed tools for Bremen and Kaiserslautern building graphs.
Query limits are hardcoded in twa_city_operations.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from functools import wraps

from fastmcp import FastMCP

from mini_marie.zaha.twa_city.twa_city_operations import (
    CITY_ENDPOINTS,
    format_results_as_tsv,
    get_building_count,
    get_building_properties,
    get_buildings_by_usage,
    get_height_stats,
    get_property_coverage,
    get_top_buildings_by_height,
    get_usage_type_counts,
    lookup_building_by_uuid_fragment,
)
from mini_marie.zaha.twa_city.gis_visualization import (
    generate_building_map,
    summarize_map_buildings,
)
from mini_marie.zaha.twa_city.workflow_mcp import (
    MCP_ONLINE_LIMIT,
    list_workflows_text,
    replay_workflow_offline,
    run_workflow_online,
)

logger = logging.getLogger("twa_city_mcp")
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


def twa_city_tool_logger(func):
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


mcp = FastMCP(name="twa-city")


@mcp.prompt(name="instruction")
def instruction_prompt():
    cities = ", ".join(sorted(CITY_ENDPOINTS))
    return (
        "Query TWA city building TWAs (CityGML + ontobuiltenv) via SPARQL tools.\n\n"
        f"**Cities:** {cities}\n"
        "**Building IRI:** https://theworldavatar.io/kg/Building/{{uuid}}\n"
        "**Usage prefix:** https://www.theworldavatar.com/kg/ontobuiltenv/ "
        "(e.g. Domestic, Office, IndustrialFacility)\n\n"
        "**Corpus tools:**\n"
        "1. get_building_count(city) - total buildings\n"
        "2. get_property_coverage(city) - counts with height, address, usage, geometry, label\n"
        "3. get_usage_type_counts(city) - buildings per ontobuiltenv usage type\n"
        "4. get_height_stats(city) - min/max/avg measuredHeight (metres)\n\n"
        "**Ranking / filter tools:**\n"
        "5. get_top_buildings_by_height(city) - tallest buildings\n"
        "6. get_buildings_by_usage(city, usage_type) - e.g. usage_type='Domestic' or 'Office'\n"
        "7. lookup_building_by_uuid_fragment(city, uuid_fragment) - search by UUID substring\n"
        "8. get_building_properties(city, uuid_fragment) - predicates for first match (no WKT)\n\n"
        "**GIS visualization:**\n"
        "9. generate_building_map(city, mode, limit) - query footprint WKT and write Leaflet HTML map\n"
        "   - mode='top_height' (default): tallest buildings with polygons\n"
        "   - mode='bbox': buildings in city centre window (falls back to top_height)\n\n"
        "**Scalable workflows (online probe → record → offline replay):**\n"
        "10. list_workflows - available workflow definitions\n"
        "11. run_workflow_online(workflow_name) - execute with LIMIT 10, save recording, compact TSV response\n"
        "12. replay_workflow_offline(recording_path) - rerun without online limits, save full results\n\n"
        "**Workflow pattern:**\n"
        "- Online (agent): small LIMIT (~10) validates call chain; recording saved under workflow_runs/\n"
        "- Offline: replay_workflow_offline on recording_path for full-scale SPARQL\n\n"
        "**Notes:**\n"
        "- Pass city as 'bremen' or 'kaiserslautern' (aliases: kl).\n"
        "- Row limits are hardcoded (typically 10).\n"
        "- measuredHeight is in metres; not all buildings have height in KL.\n"
        "- Do not expect footprint area via hasMetricArea (often empty).\n"
        "- All outputs are TSV.\n"
        "- GIS maps saved under mini_marie/zaha/twa_city/maps/ as HTML (open in browser).\n"
    )


def _tsv_or_message(results, empty_message: str) -> str:
    if not results:
        return empty_message
    return format_results_as_tsv(results)


@twa_city_tool_logger
@mcp.tool(name="get_building_count", description="Total number of CityGML buildings in the city TWA")
async def get_building_count_tool(city: str) -> str:
    return _tsv_or_message(get_building_count(city), f"No building count for city: {city}")


@twa_city_tool_logger
@mcp.tool(
    name="get_property_coverage",
    description="How many buildings have height, storeys, address, usage, geometry, or label",
)
async def get_property_coverage_tool(city: str) -> str:
    return _tsv_or_message(get_property_coverage(city), f"No property coverage for city: {city}")


@twa_city_tool_logger
@mcp.tool(
    name="get_usage_type_counts",
    description="Building counts per ontobuiltenv usage type (Domestic, Office, etc.)",
)
async def get_usage_type_counts_tool(city: str) -> str:
    return _tsv_or_message(get_usage_type_counts(city), f"No usage type counts for city: {city}")


@twa_city_tool_logger
@mcp.tool(
    name="get_height_stats",
    description="Min, max, and average measuredHeight (metres) for buildings with height data",
)
async def get_height_stats_tool(city: str) -> str:
    return _tsv_or_message(get_height_stats(city), f"No height stats for city: {city}")


@twa_city_tool_logger
@mcp.tool(
    name="get_top_buildings_by_height",
    description="Top buildings by measuredHeight with optional storeys, usage type, and label",
)
async def get_top_buildings_by_height_tool(city: str) -> str:
    return _tsv_or_message(
        get_top_buildings_by_height(city),
        f"No buildings with height found for city: {city}",
    )


@twa_city_tool_logger
@mcp.tool(
    name="get_buildings_by_usage",
    description="Top buildings for an ontobuiltenv usage type (e.g. Domestic, Office, IndustrialFacility)",
)
async def get_buildings_by_usage_tool(city: str, usage_type: str) -> str:
    results = get_buildings_by_usage(city, usage_type)
    return _tsv_or_message(
        results,
        f"No buildings found for city={city} usage_type={usage_type}",
    )


@twa_city_tool_logger
@mcp.tool(
    name="lookup_building_by_uuid_fragment",
    description="Search buildings by substring in the Building IRI (UUID fragment)",
)
async def lookup_building_by_uuid_fragment_tool(city: str, uuid_fragment: str) -> str:
    results = lookup_building_by_uuid_fragment(city, uuid_fragment)
    return _tsv_or_message(
        results,
        f"No buildings matching UUID fragment '{uuid_fragment}' in {city}",
    )


@twa_city_tool_logger
@mcp.tool(
    name="get_building_properties",
    description="Predicate/value pairs for the first building matching a UUID fragment (excludes geo:asWKT)",
)
async def get_building_properties_tool(city: str, uuid_fragment: str) -> str:
    results = get_building_properties(city, uuid_fragment)
    return _tsv_or_message(
        results,
        f"No properties for UUID fragment '{uuid_fragment}' in {city}",
    )


@twa_city_tool_logger
@mcp.tool(
    name="generate_building_map",
    description=(
        "Query building footprint polygons (GeoSPARQL WKT) and write an interactive Leaflet HTML map. "
        "Returns map file path and building summary TSV."
    ),
)
async def generate_building_map_tool(
    city: str,
    mode: str = "top_height",
    limit: int = 10,
) -> str:
    if limit > 20:
        limit = 20
    path, rows, geojson = generate_building_map(city=city, mode=mode, limit=limit)
    summary = summarize_map_buildings(rows)
    return (
        f"map_html_path\t{path.resolve()}\n"
        f"feature_count\t{len(geojson['features'])}\n"
        f"mode\t{mode}\n\n"
        f"{summary}"
    )


@twa_city_tool_logger
@mcp.tool(
    name="list_workflows",
    description="List available multi-step SPARQL workflows for online probe and offline replay",
)
async def list_workflows_tool() -> str:
    return list_workflows_text()


@twa_city_tool_logger
@mcp.tool(
    name="run_workflow_online",
    description=(
        "Run a recorded workflow online with LIMIT 10 per SPARQL step. "
        "Returns compact TSV + recording_path for replay_workflow_offline. "
        "Workflow names from list_workflows."
    ),
)
async def run_workflow_online_tool(
    workflow_name: str,
    online_limit: int = MCP_ONLINE_LIMIT,
) -> str:
    if online_limit > 20:
        online_limit = MCP_ONLINE_LIMIT
    return run_workflow_online(workflow_name, online_limit=online_limit)


@twa_city_tool_logger
@mcp.tool(
    name="replay_workflow_offline",
    description=(
        "Replay a workflow from recording_path with online limits removed/raised for full results. "
        "Use recording_path returned by run_workflow_online."
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
