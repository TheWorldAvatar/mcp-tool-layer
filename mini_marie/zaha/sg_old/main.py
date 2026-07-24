"""Singapore sg-old MCP — Blazegraph cache + Ontop buildings/land-use cache."""

from __future__ import annotations

from fastmcp import FastMCP

from mini_marie.zaha.sg_old import label_store as labels
from mini_marie.zaha.sg_old import ontop_operations as ontop
from mini_marie.zaha.sg_old import operations as op
from mini_marie.zaha.sg_old import live_api as live
from mini_marie.zaha.sg_old import partial_cq as pcq
from mini_marie.zaha.sg_old import value_chain as vc
from mini_marie.zaha.sg_old.local_store import ensure_db

mcp = FastMCP(name="sg-old")


@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "Singapore KG on sg-old.theworldavatar.io (offline SQLite caches).\n\n"
        "Blazegraph: carpark, kb (dispersion), plot (zoning T-box), company (OWL).\n"
        "Ontop: ~114k buildings + land-use/GFA (warm via warm_ontop_cache).\n\n"
        "Start with get_sg_ontop_cache_status / get_sg_building_count.\n"
        "Zoning definitions: get_sg_zoning_type_definition(special_use|business1|business2|health_medical).\n"
        "Building lookup by name: lookup_sg_buildings_by_name, get_sg_building_height_by_name."
    )


# --- Blazegraph (existing) ---

@mcp.tool(name="get_sg_graph_stats", description="Triple counts per Blazegraph namespace")
def get_sg_graph_stats() -> str:
    ensure_db()
    return op.format_tsv(op.get_sg_graph_stats())


@mcp.tool(name="get_sg_carpark_list", description="List carpark individuals with label, lots, id")
def get_sg_carpark_list(limit: int = 25) -> str:
    return op.format_tsv(op.get_sg_carpark_list(limit=min(limit, 50)))


@mcp.tool(name="count_sg_carpark_with_timeseries", description="Count AvailableLots linked to a time series")
def count_sg_carpark_with_timeseries() -> str:
    return op.format_tsv(op.count_sg_carpark_with_timeseries())


@mcp.tool(name="get_sg_emission_stats", description="Emission count and kb triple count")
def get_sg_emission_stats() -> str:
    return op.format_tsv(op.get_sg_emission_stats())


@mcp.tool(name="get_sg_plot_regulations", description="URA land-use regulations from plot namespace")
def get_sg_plot_regulations(limit: int = 25) -> str:
    return op.format_tsv(op.get_sg_plot_regulations(limit=min(limit, 50)))


@mcp.tool(name="get_sg_ship_timeseries_info", description="Ship MMSI metadata; speed values in PostGIS timeseries not RDF")
def get_sg_ship_timeseries_info(mmsi: str = "563071320") -> str:
    return op.format_tsv(op.get_sg_ship_timeseries_info(mmsi=mmsi))


@mcp.tool(name="get_sg_dispersion_data_gaps", description="Evidence for Jurong pollutant concentration data availability")
def get_sg_dispersion_data_gaps() -> str:
    return op.format_tsv(op.get_sg_dispersion_data_gaps())


@mcp.tool(name="get_sg_dispersion_simulations", description="Live dispersion simulation metadata (pollutants, derivation IRI, scope centroid)")
def get_sg_dispersion_simulations() -> str:
    return op.format_tsv(op.get_sg_dispersion_simulations())


@mcp.tool(name="get_sg_ship_speed_value_chain", description="RDF chain evidence for ship speed: where numerics drop (Measure->TimeSeries->PostGIS)")
def get_sg_ship_speed_value_chain(mmsi: str = "563071320") -> str:
    return op.format_tsv(vc.get_sg_ship_speed_value_chain(mmsi=mmsi))


@mcp.tool(name="get_sg_concentration_value_chain", description="RDF chain evidence for pollutant concentrations (DispersionMatrix->TimeSeries->PostGIS)")
def get_sg_concentration_value_chain(pollutant: str = "CO") -> str:
    return op.format_tsv(vc.get_sg_concentration_value_chain(pollutant=pollutant))


@mcp.tool(name="fuzzy_search_sg_labels", description="Fuzzy label search over carpark/kb/building local indexes (source=all|carpark|kb|building)")
def fuzzy_search_sg_labels(query: str, source: str = "all", limit: int = 15) -> str:
    return op.format_tsv(labels.fuzzy_search_labels(query, source=source, limit=min(limit, 50)))


@mcp.tool(name="find_nearest_sg_carpark_to_create", description="Nearest carpark to CREATE Tower via label geocode cache (Nominatim)")
def find_nearest_sg_carpark_to_create(limit: int = 5) -> str:
    return op.format_tsv(labels.find_nearest_carpark_to_create(limit=min(limit, 20)))


@mcp.tool(name="get_sg_nearest_carpark_to_create_answer", description="Q17 answer: CREATE ref + nearest carpark IRI/label/distance with runners-up")
def get_sg_nearest_carpark_to_create_answer(limit: int = 5) -> str:
    return op.format_tsv(pcq.get_sg_nearest_carpark_to_create_answer(limit=min(limit, 10)))


@mcp.tool(name="get_sg_jurong_pollutant_status", description="Q15: Jurong vs Singapore-grid virtual sensors and CO timeseries chain")
def get_sg_jurong_pollutant_status() -> str:
    return op.format_tsv(pcq.get_sg_jurong_pollutant_status())


@mcp.tool(name="get_sg_virtual_sensor_pollutants", description="Virtual sensors reporting CO/PM/etc quantities linked to derivations")
def get_sg_virtual_sensor_pollutants(limit: int = 20) -> str:
    return op.format_tsv(pcq.get_sg_virtual_sensor_pollutants(limit=min(limit, 50)))


@mcp.tool(name="get_sg_ship_measurable_properties", description="Q16: ship static RDF numerics (MMSI, draught) vs speed PostGIS-only")
def get_sg_ship_measurable_properties(mmsi: str = "563071320") -> str:
    return op.format_tsv(pcq.get_sg_ship_measurable_properties(mmsi=mmsi))


@mcp.tool(name="get_sg_q15_jurong_answer", description="Q15 partial answer: Jurong pollutants via dispersion grid + live probe status")
def get_sg_q15_jurong_answer() -> str:
    return op.format_tsv(pcq.get_sg_q15_jurong_answer())


@mcp.tool(name="get_sg_q16_ship_speed_answer", description="Q16 partial answer: ship identity + PostGIS speed chain evidence")
def get_sg_q16_ship_speed_answer(mmsi: str = "563071320") -> str:
    return op.format_tsv(pcq.get_sg_q16_ship_speed_answer(mmsi=mmsi))


@mcp.tool(name="get_sg_postgis_registry", description="PostGIS jdbc, timeseries counts, internal agent URLs from kb cache")
def get_sg_postgis_registry() -> str:
    return op.format_tsv(live.get_sg_postgis_registry())


@mcp.tool(name="probe_sg_dispersion_point", description="Live probe GetPollutantConcentrations + GetColourBar at lat/lng (Jurong default)")
def probe_sg_dispersion_point(lat: float = 1.33, lng: float = 103.74, pollutant: str = "CO") -> str:
    return op.format_tsv(live.probe_sg_dispersion_point(lat=lat, lng=lng, pollutant=pollutant))


@mcp.tool(name="get_sg_feature_info", description="Live feature-info-agent/get for IRI (timeseries/ship/virtual sensor metadata)")
def get_sg_feature_info(iri: str, endpoint: str = "") -> str:
    ep = endpoint or None
    return op.format_tsv(live.get_sg_feature_info(iri=iri, endpoint=ep))


@mcp.tool(name="attempt_sg_create_virtual_sensor", description="POST CreateVirtualSensor at lat/lng (documents 403 vs browser session need)")
def attempt_sg_create_virtual_sensor(lat: float = 1.33, lng: float = 103.74) -> str:
    return op.format_tsv(live.attempt_sg_create_virtual_sensor(lat=lat, lng=lng))


@mcp.tool(name="get_sg_live_api_surface", description="Reachability summary for dispersion/feature-info/timeseries HTTP paths")
def get_sg_live_api_surface() -> str:
    return op.format_tsv(live.get_sg_live_api_surface())


@mcp.tool(name="get_sg_carpark_geo_gaps", description="Evidence for carpark nearest-neighbor feasibility")
def get_sg_carpark_geo_gaps() -> str:
    return op.format_tsv(op.get_sg_carpark_geo_gaps())


@mcp.tool(name="get_sg_concentration_timeseries_info", description="CO/PM concentration quantity → PostGIS timeseries chain")
def get_sg_concentration_timeseries_info(limit: int = 5) -> str:
    return op.format_tsv(op.get_sg_concentration_timeseries_info(limit=min(limit, 20)))


@mcp.tool(name="get_sg_dispersion_scope_info", description="Dispersion simulation Scope instances and derivation counts")
def get_sg_dispersion_scope_info() -> str:
    return op.format_tsv(op.get_sg_dispersion_scope_info())


@mcp.tool(name="get_sg_visualisation_map_entry", description="TWA-VF Mapbox URL and Abbott building/geometry refs")
def get_sg_visualisation_map_entry() -> str:
    return op.format_tsv(op.get_sg_visualisation_map_entry())


# --- Ontop buildings / land lots ---

@mcp.tool(name="get_sg_ontop_cache_status", description="Ontop SQLite warm status and row counts")
def get_sg_ontop_cache_status() -> str:
    return ontop.format_tsv(ontop.get_sg_ontop_cache_status())


@mcp.tool(
    name="get_sg_ontop_postgis_scope",
    description="Live Ontop inventory: which sg-postgis families are OBDA-mapped (buildings vs timeseries gap)",
)
def get_sg_ontop_postgis_scope() -> str:
    return ontop.format_tsv(ontop.get_sg_ontop_postgis_scope())


@mcp.tool(name="get_sg_building_count", description="Total Singapore buildings in Ontop cache")
def get_sg_building_count() -> str:
    return ontop.format_tsv(ontop.get_sg_building_count())


@mcp.tool(name="get_sg_building_usage_top", description="Top N building property usage types")
def get_sg_building_usage_top(limit: int = 5) -> str:
    return ontop.format_tsv(ontop.get_sg_building_usage_top(limit=min(limit, 20)))


@mcp.tool(name="count_sg_office_buildings", description="Count buildings with Office property usage")
def count_sg_office_buildings() -> str:
    return ontop.format_tsv(ontop.count_sg_office_buildings())


@mcp.tool(name="get_sg_land_use_counts", description="Plot counts grouped by land-use type IRI")
def get_sg_land_use_counts(limit: int = 25) -> str:
    return ontop.format_tsv(ontop.get_sg_land_use_counts(limit=min(limit, 50)))


@mcp.tool(name="get_sg_residential_commercial_percent", description="Residential vs commercial % of zoned plots")
def get_sg_residential_commercial_percent() -> str:
    return ontop.format_tsv(ontop.get_sg_residential_commercial_percent())


@mcp.tool(name="get_sg_commercial_plot_count", description="Count plots zoned commercial")
def get_sg_commercial_plot_count() -> str:
    return ontop.format_tsv(ontop.get_sg_commercial_plot_count())


@mcp.tool(name="count_sg_within_max_gfa", description="Plots within vs exceeding max permitted GFA")
def count_sg_within_max_gfa() -> str:
    return ontop.format_tsv(ontop.count_sg_within_max_gfa())


@mcp.tool(name="get_sg_gfa_compliance_by_land_use", description="Within/exceeds max GFA counts per land-use category")
def get_sg_gfa_compliance_by_land_use() -> str:
    return ontop.format_tsv(ontop.get_sg_gfa_compliance_by_land_use())


@mcp.tool(name="get_sg_lowest_plot_ratio_health_medical", description="Lowest gross plot ratio for health/medical plots")
def get_sg_lowest_plot_ratio_health_medical() -> str:
    return ontop.format_tsv(ontop.get_sg_lowest_plot_ratio_health_medical())


@mcp.tool(name="get_sg_smallest_agriculture_gfa", description="Smallest calculated GFA among agriculture plots")
def get_sg_smallest_agriculture_gfa() -> str:
    return ontop.format_tsv(ontop.get_sg_smallest_agriculture_gfa())


@mcp.tool(name="lookup_sg_buildings_by_name", description="Find buildings by address/facility name fragment")
def lookup_sg_buildings_by_name(name_fragment: str, limit: int = 10) -> str:
    return ontop.format_tsv(ontop.lookup_sg_buildings_by_name(name_fragment, limit=min(limit, 25)))


@mcp.tool(name="get_sg_building_height_by_name", description="Building height by name fragment (e.g. New Tech Park)")
def get_sg_building_height_by_name(name_fragment: str) -> str:
    return ontop.format_tsv(ontop.get_sg_building_height_by_name(name_fragment))


@mcp.tool(name="get_sg_building_footprint_by_name", description="Building footprint WKT by facility name via hasGeometry->asWKT (live Ontop fallback)")
def get_sg_building_footprint_by_name(name_fragment: str) -> str:
    return ontop.format_tsv(ontop.get_sg_building_footprint_by_name(name_fragment))


@mcp.tool(
    name="get_sg_zoning_type_definition",
    description="Zoning definition from plot T-box: special_use, business1, business2, health_medical, commercial",
)
def get_sg_zoning_type_definition(zoning_key: str) -> str:
    return ontop.format_tsv(ontop.get_sg_zoning_type_definition(zoning_key))


if __name__ == "__main__":
    mcp.run()
