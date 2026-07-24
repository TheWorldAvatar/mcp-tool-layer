"""Atomic read operations over materialized sg-old cache."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib import error, request

from mini_marie.zaha.sg_old import local_store as store

SG_OLD_HOST = "https://sg-old.theworldavatar.io"
SG_UA = "curl/8.0"

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

CARPARK = "https://www.theworldavatar.com/kg/ontocarpark/Carpark"
AVAILABLE_LOTS = "https://www.theworldavatar.com/kg/ontocarpark/AvailableLots"
HAS_TS = "https://www.theworldavatar.com/kg/ontotimeseries/hasTimeSeries"
HAS_LOTS = "https://www.theworldavatar.com/kg/ontocarpark/hasLots"
HAS_ID = "https://www.theworldavatar.com/kg/ontocarpark/hasID"

EMISSION = "https://www.theworldavatar.com/kg/ontodispersion/Emission"
HAS_NUM = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue"

LAND_USE_REG = "https://www.theworldavatar.com/kg/ontoplanningregulation/LandUseRegulation"
APPLIES_TO = "https://www.theworldavatar.com/kg/ontoplanningregulation/appliesTo"

OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
ONTOMPANY = "http://www.theworldavatar.com/kg/ontocompany/"


def format_tsv(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No results"
    keys = list(rows[0].keys())
    lines = ["\t".join(keys)]
    for row in rows:
        lines.append("\t".join(str(row.get(k, "")) for k in keys))
    return "\n".join(lines)


def get_sg_graph_stats() -> List[Dict[str, Any]]:
    return store.graph_stats()


def get_sg_carpark_list(limit: int = 25) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in store.subjects_by_type("carpark", CARPARK, limit=limit):
        row = {
            "carpark": s,
            "label": (store.object_values("carpark", s, RDFS_LABEL, limit=1) or [""])[0],
            "lots": (store.object_values("carpark", s, HAS_LOTS, limit=1) or [""])[0],
            "id": (store.object_values("carpark", s, HAS_ID, limit=1) or [""])[0],
        }
        out.append(row)
    return out


def count_sg_carpark_with_timeseries() -> List[Dict[str, Any]]:
    n = 0
    for s in store.subjects_by_type("carpark", AVAILABLE_LOTS, limit=10000):
        if store.object_values("carpark", s, HAS_TS, limit=1):
            n += 1
    return [{"available_lots_individuals_with_timeseries": n}]


def get_sg_emission_stats() -> List[Dict[str, Any]]:
    emissions = store.subjects_by_type("kb", EMISSION, limit=100000)
    return [
        {
            "emission_individuals": len(emissions),
            "kb_triples": next(
                (r["triples"] for r in store.graph_stats() if r["ns"] == "kb"),
                0,
            ),
        }
    ]


def get_sg_emission_samples(limit: int = 10) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in store.subjects_by_type("kb", EMISSION, limit=limit):
        out.append(
            {
                "emission": s,
                "value": (store.object_values("kb", s, HAS_NUM, limit=1) or [""])[0],
            }
        )
    return out


def get_sg_plot_regulations(limit: int = 25) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in store.subjects_by_type("plot", LAND_USE_REG, limit=limit):
        out.append(
            {
                "regulation": s,
                "label": (store.object_values("plot", s, RDFS_LABEL, limit=1) or [""])[0],
                "applies_to": (store.object_values("plot", s, APPLIES_TO, limit=1) or [""])[0],
            }
        )
    return out


def get_sg_company_ontology_classes(limit: int = 25) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in store.subjects_by_type("company", OWL_CLASS, limit=limit):
        label = (store.object_values("company", s, RDFS_LABEL, limit=1) or [""])[0]
        if ONTOMPANY in s or "ontocompany" in s:
            out.append({"class": s, "label": label})
        if len(out) >= limit:
            break
    # fill with any owl classes if few ontocompany-named
    if len(out) < limit:
        for s in store.subjects_by_type("company", OWL_CLASS, limit=limit * 2):
            label = (store.object_values("company", s, RDFS_LABEL, limit=1) or [""])[0]
            if not any(r["class"] == s for r in out):
                out.append({"class": s, "label": label})
            if len(out) >= limit:
                break
    return out[:limit]


HAS_PROPERTY = "https://www.theworldavatar.com/kg/ontodispersion/hasProperty"
HAS_TS = "https://www.theworldavatar.com/kg/ontotimeseries/hasTimeSeries"
HAS_RDB = "https://www.theworldavatar.com/kg/ontotimeseries/hasRDB"
SHIP_TYPE = "https://www.theworldavatar.com/kg/ontodispersion/Ship"


def get_sg_ship_timeseries_info(mmsi: str = "563071320") -> List[Dict[str, Any]]:
    """Ship speed/location values live in PostGIS via hasTimeSeries, not as RDF numerics."""
    store.ensure_db()
    ship = f"https://www.theworldavatar.com/kg/ontodispersion/Ship{mmsi}"
    label = (store.object_values("kb", ship, RDFS_LABEL, limit=1) or [""])[0]
    speed = f"https://www.theworldavatar.com/kg/ontodispersion/Ship{mmsi}Speed"
    speed_measure = f"https://www.theworldavatar.com/kg/ontodispersion/Ship{mmsi}SpeedMeasure"
    ts = (store.object_values("kb", speed_measure, HAS_TS, limit=1) or [""])[0]
    rdb = (store.object_values("kb", ts, HAS_RDB, limit=1) or [""])[0] if ts else ""
    props = store.object_values("kb", ship, HAS_PROPERTY, limit=20)
    return [
        {
            "ship_iri": ship,
            "label": label,
            "mmsi": mmsi,
            "speed_property": speed,
            "speed_measure": speed_measure,
            "timeseries": ts,
            "timeseries_rdb": rdb,
            "has_numerical_speed_in_rdf_cache": False,
            "ship_properties": "; ".join(p.rsplit("/", 1)[-1] for p in props),
            "note": "Speed over ground is stored in external PostGIS time series, not Blazegraph triples",
        }
    ]


def get_sg_dispersion_simulations() -> List[Dict[str, Any]]:
    """Live GET /dispersion-interactor/GetDispersionSimulations (Singapore-wide metadata)."""
    url = f"{SG_OLD_HOST}/dispersion-interactor/GetDispersionSimulations"
    req = request.Request(url, headers={"User-Agent": SG_UA}, method="GET")
    try:
        with request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return [{"ok": False, "http": exc.code, "error": body[:300]}]
    except Exception as exc:
        return [{"ok": False, "error": str(exc)[:300]}]

    out: List[Dict[str, Any]] = []
    for region, meta in payload.items():
        pollutants = meta.get("pollutants") or {}
        out.append(
            {
                "region": region,
                "centroid_lon_lat": meta.get("centroid"),
                "derivation_iri": meta.get("derivationIri"),
                "pollutants": "; ".join(pollutants.values()),
                "pollutant_iris": "; ".join(pollutants.keys()),
                "simulation_times_unix": len(meta.get("time") or []),
                "z_layers": "; ".join(f"z{k}" for k in (meta.get("z") or {})),
                "weather_station_wkt": (meta.get("weatherStation") or {}).get("wkt"),
            }
        )
    return out or [{"ok": True, "note": "empty response"}]


def get_sg_dispersion_data_gaps() -> List[Dict[str, Any]]:
    store.ensure_db()
    import sqlite3
    from mini_marie.zaha.sg_old.local_store import db_path

    conn = sqlite3.connect(db_path())
    jn = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(s) LIKE '%jurong%' OR LOWER(o) LIKE '%jurong%')"
    ).fetchone()[0]
    conc = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE ns='kb' AND LOWER(o) LIKE '%concentration%'"
    ).fetchone()[0]
    conn.close()
    return [
        {
            "jurong_triples_in_kb": jn,
            "concentration_type_triples": conc,
            "get_dispersion_simulations": "HTTP 200 — Singapore-wide pollutant list + derivation IRI",
            "get_pollutant_concentrations": "HTTP 500 even with timestep+derivationIri+zIri (PostGIS backend)",
            "get_colour_bar": "HTTP 500 with correct timestep param (not time)",
            "create_virtual_sensor": "POST 403 (nginx/Cloudflare); needs viz browser session",
            "feature_info_agent": "iri+endpoint accepted; 500 class-determination query",
            "pollutant_values_in_cache": False,
            "note": "Jurong point μg/m³ need internal PostGIS or working dispersion-interactor backend; kb has 0 Jurong triples",
        }
    ]


def get_sg_concentration_timeseries_info(limit: int = 5) -> List[Dict[str, Any]]:
    """Concentration quantities in kb link to PostGIS timeseries (microgram/m3), not RDF numerics."""
    store.ensure_db()
    import sqlite3
    from mini_marie.zaha.sg_old.local_store import db_path

    conn = sqlite3.connect(db_path())
    rows = conn.execute(
        """
        SELECT q.s, q.o AS conc_type, m.o AS measure, ts.o AS timeseries, rdb.o AS rdb
        FROM triples q
        JOIN triples t ON t.ns='kb' AND t.s=q.s AND t.p LIKE '%type%' AND t.o LIKE '%Concentration%'
        LEFT JOIN triples hv ON hv.ns='kb' AND hv.s=q.s AND hv.p LIKE '%hasValue%'
        LEFT JOIN triples m ON m.ns='kb' AND m.s=hv.o
        LEFT JOIN triples ts ON ts.ns='kb' AND ts.s=hv.o AND ts.p LIKE '%hasTimeSeries%'
        LEFT JOIN triples rdb ON rdb.ns='kb' AND rdb.s=ts.o AND rdb.p LIKE '%hasRDB%'
        WHERE q.ns='kb' AND q.s LIKE '%quantity_%'
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    conn.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "quantity": r[0],
                "concentration_type": r[1],
                "measure": r[2],
                "timeseries": r[3],
                "timeseries_rdb": r[4] or "",
                "has_numerical_in_rdf_cache": False,
                "typical_unit": "microgramPerCubicmetre",
            }
        )
    if not out:
        return [{"note": "No ontoems concentration quantities in kb cache"}]
    return out


def get_sg_dispersion_scope_info() -> List[Dict[str, Any]]:
    store.ensure_db()
    import sqlite3
    from mini_marie.zaha.sg_old.local_store import db_path

    conn = sqlite3.connect(db_path())
    rows = conn.execute(
        """
        SELECT s, MAX(CASE WHEN p LIKE '%label%' THEN o END) AS label
        FROM triples
        WHERE ns='kb' AND s IN (
          SELECT s FROM triples WHERE ns='kb' AND o LIKE '%Scope%' AND p LIKE '%type%'
        )
        GROUP BY s
        """
    ).fetchall()
    deriv = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE ns='kb' AND s LIKE '%DerivationWithTimeSeries%'"
    ).fetchone()[0]
    conn.close()
    return [
        {
            "derivation_with_timeseries_count": deriv,
            "scope_instances": [{"scope_iri": r[0], "label": r[1]} for r in rows],
            "jurong_in_scope_labels": any((r[1] or "").lower().find("jurong") >= 0 for r in rows),
            "note": "Simulation scope is 'Singapore' bbox; concentrations in PostGIS via DerivationWithTimeSeries",
        }
    ]


def get_sg_visualisation_map_entry() -> List[Dict[str, Any]]:
    return [
        {
            "url": "https://sg-old.theworldavatar.io/visualisation/",
            "name": "TWA-VF Mapbox",
            "abbott_building_iri": "https://www.theworldavatar.com/kg/Building/53ecd194-3dab-4e12-9369-cbb86007882a",
            "abbott_footprint_geometry_iri": "http://cui.unige.ch/citygml/2.0/geometry/42226638",
            "wkt_via_sparql": True,
            "wkt_path": "Building -> hasGeometry -> asWKT (lod0FootPrint is geometry IRI only)",
            "note": "TWA-VF Mapbox UI at /visualisation/; Abbott WKT via Ontop hasGeometry chain",
        }
    ]


def get_sg_carpark_geo_gaps() -> List[Dict[str, Any]]:
    store.ensure_db()
    import sqlite3
    from mini_marie.zaha.sg_old.local_store import db_path

    conn = sqlite3.connect(db_path())
    geo = conn.execute(
        """
        SELECT COUNT(*) FROM triples WHERE ns='carpark' AND (
          LOWER(p) LIKE '%lat%' OR LOWER(p) LIKE '%lon%' OR LOWER(p) LIKE '%coord%'
          OR LOWER(p) LIKE '%wkt%' OR LOWER(p) LIKE '%geometry%' OR LOWER(p) LIKE '%location%'
        )
        """
    ).fetchone()[0]
    create_hits = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE ns='carpark' AND (LOWER(o) LIKE '%create%' OR LOWER(s) LIKE '%create%')"
    ).fetchone()[0]
    conn.close()
    return [
        {
            "carpark_geo_predicates": geo,
            "create_tower_in_carpark_cache": create_hits,
            "create_tower_in_building_names": 0,
            "create_tower_in_kb_labels": 0,
            "nearest_carpark_feasible": True,
            "method": "fuzzy_search_sg_labels + Nominatim geocode cache -> find_nearest_sg_carpark_to_create",
            "note": "No geo predicates in RDF; CREATE not in labels — use external geocode on carpark address literals",
        }
    ]


def count_sg_company_instances() -> List[Dict[str, Any]]:
    company_type = ONTOMPANY + "Company"
    n = len(store.subjects_by_type("company", company_type, limit=10000))
    owl_classes = len(store.subjects_by_type("company", OWL_CLASS, limit=10000))
    return [{"ontocompany_company_instances": n, "owl_classes": owl_classes}]
