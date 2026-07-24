"""MCP atomics over warmed sg-old Ontop SQLite cache."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from mini_marie.zaha.sg_old import local_store as bg
from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT, cache_ready, connect, land_plot_count
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"

SPECIAL_USE = "https://www.theworldavatar.com/kg/landplot/LandUseType_1115b7ad-184a-4014-9b87-ebcc3a0eee41"
BUSINESS1 = "https://www.theworldavatar.com/kg/landplot/LandUseType_618ab6ea-3d41-4841-95ef-369f000e5075"
BUSINESS2 = "https://www.theworldavatar.com/kg/landplot/LandUseType_a7fd8f8c-4cb3-4b08-b539-a64daeadd29e"
HEALTH = "https://www.theworldavatar.com/kg/landplot/LandUseType_14534f34-3cbc-40c8-9e52-30018d18c486"
AGRICULTURE = "https://www.theworldavatar.com/kg/landplot/LandUseType_a8e423e3-c628-4b08-9f63-fcf5b244873a"
COMMERCIAL = "https://www.theworldavatar.com/kg/landplot/LandUseType_f45d365c-1d59-4fda-b240-afb0066f2d61"
RESIDENTIAL = "https://www.theworldavatar.com/kg/landplot/LandUseType_6cbda899-27e3-41e9-9ad1-9d4061a5818d"


def _require_cache() -> None:
    if not cache_ready():
        raise RuntimeError(
            "Ontop cache empty. Run: python -m mini_marie.zaha.sg_old.warm_ontop_cache"
        )


def _land_use_short(iri: str) -> str:
    if not iri:
        return ""
    tail = iri.rsplit("/", 1)[-1]
    if tail.startswith("LandUseType_"):
        return tail
    return tail


def _usage_short(iri: str) -> str:
    if not iri:
        return ""
    m = re.search(r"/(Non-Domestic|Domestic|Bank|Hotel|Office|Retail|School|Warehouse)[_>]", iri, re.I)
    if m:
        return m.group(1)
    return iri.rsplit("/", 1)[-1].split("_")[0]


def format_tsv(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No results"
    keys = list(rows[0].keys())
    lines = ["\t".join(keys)]
    for row in rows:
        lines.append("\t".join(str(row.get(k, "")) for k in keys))
    return "\n".join(lines)


def get_sg_ontop_cache_status() -> List[Dict[str, Any]]:
    from mini_marie.zaha.sg_old.ontop_store import building_count, get_meta

    conn = connect()
    plots_with_max_gfa = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_land_plot WHERE max_gfa IS NOT NULL"
    ).fetchone()["n"]
    plots_with_ratio = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_land_plot WHERE plot_ratio_num IS NOT NULL"
    ).fetchone()["n"]
    plots_with_area = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_land_plot WHERE area_sqm IS NOT NULL"
    ).fetchone()["n"]
    footprints = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_building WHERE footprint_wkt IS NOT NULL AND footprint_wkt != ''"
    ).fetchone()["n"]
    name_rows = conn.execute("SELECT COUNT(*) AS n FROM facet_building_name").fetchone()["n"]
    conn.close()
    return [
        {
            "ready": cache_ready(),
            "building_rows": building_count(),
            "land_plot_rows": land_plot_count(),
            "land_plots_with_max_gfa": plots_with_max_gfa,
            "land_plots_with_plot_ratio": plots_with_ratio,
            "land_plots_with_plot_area": plots_with_area,
            "buildings_with_footprint_ref": footprints,
            "building_name_rows": name_rows,
            "warm_complete": get_meta("warm_complete"),
        }
    ]


def get_sg_building_count() -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM facet_building").fetchone()["n"]
    conn.close()
    return [{"building_count": n}]


def count_sg_office_buildings() -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_building WHERE usage LIKE '%Office%'"
    ).fetchone()["n"]
    conn.close()
    return [{"office_building_count": n}]


def get_sg_building_usage_top(limit: int = 5) -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    rows = conn.execute(
        "SELECT usage, COUNT(*) AS n FROM facet_building WHERE usage IS NOT NULL AND usage != '' GROUP BY usage"
    ).fetchall()
    conn.close()
    agg: Dict[str, int] = {}
    for r in rows:
        short = _usage_short(r["usage"])
        agg[short] = agg.get(short, 0) + int(r["n"])
    top = sorted(agg.items(), key=lambda x: -x[1])[: int(limit)]
    return [{"usage": k, "count": v} for k, v in top]


def get_sg_land_use_counts(limit: int = 25) -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    rows = conn.execute(
        """
        SELECT land_use, COUNT(*) AS n FROM facet_land_plot
        WHERE land_use IS NOT NULL AND land_use != ''
        GROUP BY land_use ORDER BY n DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    label_map = _land_use_label_map(conn)
    conn.close()
    return [
        {
            "land_use": r["land_use"],
            "land_use_label": label_map.get(r["land_use"], ""),
            "land_use_short": _land_use_short(r["land_use"]),
            "count": r["n"],
        }
        for r in rows
    ]


def _land_use_label_map(conn) -> Dict[str, str]:
    rows = conn.execute(
        """
        SELECT land_use, MAX(land_use_label) AS label
        FROM facet_land_plot
        WHERE land_use_label IS NOT NULL AND land_use_label != ''
        GROUP BY land_use
        """
    ).fetchall()
    return {r["land_use"]: r["label"] for r in rows}


def get_sg_residential_commercial_percent() -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    total = conn.execute("SELECT COUNT(*) AS n FROM facet_land_plot").fetchone()["n"]
    res = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_land_plot WHERE land_use = ? OR land_use_label = 'Residential'",
        (RESIDENTIAL,),
    ).fetchone()["n"]
    com = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_land_plot WHERE land_use = ? OR land_use_label = 'Commercial'",
        (COMMERCIAL,),
    ).fetchone()["n"]
    conn.close()
    pct = lambda x: round(100.0 * x / total, 2) if total else 0.0
    return [
        {
            "land_plot_total": total,
            "residential_count": res,
            "residential_pct": pct(res),
            "commercial_count": com,
            "commercial_pct": pct(com),
        }
    ]


def get_sg_commercial_plot_count() -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM facet_land_plot WHERE land_use = ? OR land_use_label = 'Commercial'",
        (COMMERCIAL,),
    ).fetchone()["n"]
    conn.close()
    return [{"commercial_plot_count": n}]


def get_sg_gfa_compliance_by_land_use() -> List[Dict[str, Any]]:
    """Plot hasPlotArea vs hasMaximumPermittedGFA on same landplot/{id} (A-box path)."""
    _require_cache()
    conn = connect()
    rows = conn.execute(
        """
        SELECT land_use,
               COUNT(*) AS total,
               SUM(CASE WHEN area_sqm IS NOT NULL AND max_gfa IS NOT NULL THEN 1 ELSE 0 END) AS comparable,
               SUM(CASE WHEN area_sqm IS NOT NULL AND max_gfa IS NOT NULL AND area_sqm <= max_gfa THEN 1 ELSE 0 END) AS within_max,
               SUM(CASE WHEN area_sqm IS NOT NULL AND max_gfa IS NOT NULL AND area_sqm > max_gfa THEN 1 ELSE 0 END) AS exceeds_max
        FROM facet_land_plot
        GROUP BY land_use
        ORDER BY exceeds_max DESC, total DESC
        """
    ).fetchall()
    label_map = _land_use_label_map(conn)
    conn.close()
    return [
        {
            "land_use_label": label_map.get(r["land_use"], ""),
            "land_use_short": _land_use_short(r["land_use"]),
            "plot_count": r["total"],
            "plots_with_area_and_max_gfa": r["comparable"],
            "plots_within_max_gfa": r["within_max"],
            "plots_exceeding_max_gfa": r["exceeds_max"],
            "method": "ontoplot:hasPlotArea <=> ontoplot:hasMaximumPermittedGFA",
        }
        for r in rows
        if r["comparable"]
    ]


def count_sg_within_max_gfa() -> List[Dict[str, Any]]:
    """Plots where hasPlotArea <= hasMaximumPermittedGFA (both numeric on same plot)."""
    _require_cache()
    conn = connect()
    row = conn.execute(
        """
        SELECT
          SUM(CASE WHEN area_sqm IS NOT NULL AND max_gfa IS NOT NULL THEN 1 ELSE 0 END) AS comparable,
          SUM(CASE WHEN area_sqm IS NOT NULL AND max_gfa IS NOT NULL AND area_sqm <= max_gfa THEN 1 ELSE 0 END) AS within_max,
          SUM(CASE WHEN area_sqm IS NOT NULL AND max_gfa IS NOT NULL AND area_sqm > max_gfa THEN 1 ELSE 0 END) AS exceeds_max,
          SUM(CASE WHEN area_sqm IS NOT NULL AND max_gfa IS NULL THEN 1 ELSE 0 END) AS area_only,
          SUM(CASE WHEN max_gfa IS NOT NULL AND area_sqm IS NULL THEN 1 ELSE 0 END) AS max_gfa_only
        FROM facet_land_plot
        """
    ).fetchone()
    conn.close()
    return [
        {
            "plots_with_area_and_max_gfa": row["comparable"],
            "plots_within_max_gfa": row["within_max"],
            "plots_exceeding_max_gfa": row["exceeds_max"],
            "plots_area_only_no_max_gfa": row["area_only"],
            "plots_max_gfa_only_no_area": row["max_gfa_only"],
            "method": "hasPlotArea vs hasMaximumPermittedGFA on landplot/{id}",
            "note": "Building calc_gfa join not available; 80k plots lack numeric max_gfa",
        }
    ]


def get_sg_lowest_plot_ratio_health_medical() -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    row = conn.execute(
        """
        SELECT plot_iri, plot_ratio_num, max_gfa, land_use, land_use_label
        FROM facet_land_plot
        WHERE (land_use = ? OR land_use_label LIKE '%Health%')
          AND plot_ratio_num IS NOT NULL
        ORDER BY plot_ratio_num ASC
        LIMIT 1
        """,
        (HEALTH,),
    ).fetchone()
    conn.close()
    if not row:
        return [{"note": "No health/medical plots with plot ratio in cache"}]
    return [dict(row)]


def get_sg_smallest_agriculture_gfa() -> List[Dict[str, Any]]:
    _require_cache()
    conn = connect()
    row = conn.execute(
        """
        SELECT plot_iri, area_sqm, max_gfa, calc_gfa, land_use, land_use_label
        FROM facet_land_plot
        WHERE (land_use = ? OR land_use_label = 'Agriculture')
          AND area_sqm IS NOT NULL
        ORDER BY area_sqm ASC
        LIMIT 1
        """,
        (AGRICULTURE,),
    ).fetchone()
    conn.close()
    if not row:
        return [{"note": "No agriculture plots with hasPlotArea in cache"}]
    out = dict(row)
    out["source"] = "ontoplot:hasPlotArea"
    return [out]


def lookup_sg_buildings_by_name(name_fragment: str, limit: int = 10) -> List[Dict[str, Any]]:
    _require_cache()
    frag = f"%{name_fragment.lower()}%"
    conn = connect()
    rows = conn.execute(
        """
        SELECT n.building_iri, n.name, n.source, b.height, b.usage, b.calc_gfa
        FROM facet_building_name n
        LEFT JOIN facet_building b ON b.building_iri = n.building_iri
        WHERE n.name_lc LIKE ?
        LIMIT ?
        """,
        (frag, int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sg_building_height_by_name(name_fragment: str) -> List[Dict[str, Any]]:
    rows = lookup_sg_buildings_by_name(name_fragment, limit=5)
    return [
        {
            "name": r.get("name"),
            "building_iri": r.get("building_iri"),
            "height": r.get("height"),
            "source": r.get("source"),
        }
        for r in rows
        if r.get("height") is not None
    ]


def _is_wkt_literal(value: str) -> bool:
    v = (value or "").lstrip()
    return v.upper().startswith(("POLYGON", "MULTIPOLYGON", "POINT", "LINESTRING", "<HTTP"))


def _footprint_wkt_live(name_fragment: str) -> List[Dict[str, Any]]:
    q = f"""
SELECT ?name ?b ?wkt WHERE {{
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?name .
  FILTER(CONTAINS(LCASE(STR(?name)), "{name_fragment.lower().replace('"', '')}")) .
  ?b <http://www.opengis.net/ont/geosparql#hasGeometry> ?g .
  ?g <http://www.opengis.net/ont/geosparql#asWKT> ?wkt .
}} LIMIT 3"""
    try:
        rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=120)
    except Exception as exc:
        return [{"error": str(exc)[:300], "source": "ontop_live_hasGeometry"}]
    return [
        {
            "name": r.get("name"),
            "building_iri": r.get("b"),
            "footprint_wkt": r.get("wkt"),
            "footprint_kind": "wkt",
            "source": "ontop_live_hasGeometry",
        }
        for r in rows
        if r.get("wkt")
    ]


def get_sg_building_footprint_by_name(name_fragment: str) -> List[Dict[str, Any]]:
    _require_cache()
    frag = f"%{name_fragment.lower()}%"
    conn = connect()
    rows = conn.execute(
        """
        SELECT n.name, b.building_iri, b.footprint_wkt
        FROM facet_building_name n
        JOIN facet_building b ON b.building_iri = n.building_iri
        WHERE n.name_lc LIKE ?
        LIMIT 3
        """,
        (frag,),
    ).fetchall()
    conn.close()
    if rows:
        out = []
        for r in rows:
            item = dict(r)
            fp = item.get("footprint_wkt") or ""
            if _is_wkt_literal(fp):
                item["footprint_kind"] = "wkt"
                item["source"] = "cache"
                out.append(item)
            else:
                live = _footprint_wkt_live(name_fragment)
                if live and live[0].get("footprint_wkt"):
                    out.extend(live)
                else:
                    item["footprint_kind"] = "geometry_iri"
                    item["lod0_geometry_iri"] = fp or None
                    item["note"] = "lod0FootPrint is geometry IRI; use hasGeometry->asWKT (live query failed or empty)"
                    out.append(item)
        return out
    live = _footprint_wkt_live(name_fragment)
    if live:
        return live
    return [{"note": "No building name match in cache; live hasGeometry query also empty"}]


def get_sg_zoning_type_definition(zoning_key: str) -> List[Dict[str, Any]]:
    key_map = {
        "special_use": SPECIAL_USE,
        "business1": BUSINESS1,
        "business2": BUSINESS2,
        "health_medical": HEALTH,
        "commercial": COMMERCIAL,
    }
    subject = key_map.get(zoning_key.strip().lower().replace(" ", "_"))
    if not subject:
        return [{"error": f"Unknown key. Use one of: {list(key_map)}"}]
    label = (bg.object_values("plot", subject, RDFS_LABEL, limit=1) or [""])[0]
    comment = (bg.object_values("plot", subject, RDFS_COMMENT, limit=1) or [""])[0]
    return [{"zoning_iri": subject, "label": label, "definition": comment}]


def get_sg_ontop_postgis_scope() -> List[Dict[str, Any]]:
    """Live Ontop inventory: which sg-postgis table families are OBDA-mapped (vs kb-only timeseries)."""
    def _count(q: str) -> str:
        try:
            rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=90)
            if rows:
                for k in ("n", "c"):
                    if k in rows[0]:
                        return str(rows[0][k])
            return "0"
        except Exception as exc:
            return f"err:{str(exc)[:80]}"

    dispersion_sample = ""
    try:
        rows = execute_sparql_get(
            """
            SELECT ?val WHERE {
              ?dp a <https://www.theworldavatar.com/kg/ontodispersion/DispersionPolygon> .
              ?dp <https://www.theworldavatar.com/kg/ontodispersion/hasValue> ?val .
            } LIMIT 1
            """,
            ONTOP_ENDPOINT,
            timeout=60,
        )
        dispersion_sample = rows[0]["val"] if rows else ""
    except Exception:
        pass

    return [
        {
            "endpoint": ONTOP_ENDPOINT,
            "postgis_confirmed_via_ontop": True,
            "buildings": _count(
                "SELECT (COUNT(?b) AS ?n) WHERE { ?b a <http://www.opengis.net/citygml/building/2.0/Building> }"
            ),
            "om_measures": _count(
                "SELECT (COUNT(?m) AS ?n) WHERE { ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> }"
            ),
            "gfameasure_with_numerics": _count(
                """
                SELECT (COUNT(?m) AS ?n) WHERE {
                  ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> .
                  ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?v .
                  FILTER(CONTAINS(STR(?m), "gfameasure"))
                }"""
            ),
            "dispersion_polygons": _count(
                "SELECT (COUNT(?dp) AS ?n) WHERE { ?dp a <https://www.theworldavatar.com/kg/ontodispersion/DispersionPolygon> }"
            ),
            "dispersion_hasValue_sample": dispersion_sample,
            "co2_emissions": _count(
                "SELECT (COUNT(?e) AS ?n) WHERE { ?e a <http://www.theworldavatar.com/kg/ontochemplant/IndividualCO2Emission> }"
            ),
            "ships_on_ontop": _count(
                "SELECT (COUNT(?s) AS ?n) WHERE { ?s a <https://www.theworldavatar.com/kg/ontodispersion/Ship> }"
            ),
            "timeseries_on_ontop": _count(
                "SELECT (COUNT(?t) AS ?n) WHERE { ?t a <https://www.theworldavatar.com/kg/ontotimeseries/TimeSeries> }"
            ),
            "hasTimeSeries_links": _count(
                f"SELECT (COUNT(?m) AS ?n) WHERE {{ ?m <https://www.theworldavatar.com/kg/ontotimeseries/hasTimeSeries> ?ts }}"
            ),
            "ontoems_measures_on_ontop": _count(
                """
                SELECT (COUNT(?m) AS ?n) WHERE {
                  ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> .
                  FILTER(CONTAINS(STR(?m), "ontoems/measure_"))
                }"""
            ),
            "note": (
                "Ontop maps PostGIS for city/plot + dispersion colour-bar bins; "
                "ship speed and CO time-series tables are kb-metadata only (hasRDB), not in this OBDA"
            ),
        }
    ]
