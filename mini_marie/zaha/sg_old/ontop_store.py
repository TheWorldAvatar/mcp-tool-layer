"""SQLite cache for sg-old Ontop (buildings + land-use/GFA facets)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.cache_paths import mini_marie_cache_root

ONTOP_ENDPOINT = "https://sg-old.theworldavatar.io/ontop/sparql/"

CITYGML_BUILDING = "http://www.opengis.net/citygml/building/2.0/Building"
P_HEIGHT = "http://www.opengis.net/citygml/building/2.0/measuredHeight"
P_FOOTPRINT = "http://www.opengis.net/citygml/building/2.0/lod0FootPrint"
P_USAGE = "https://www.theworldavatar.com/kg/ontobuiltenv/hasPropertyUsage"
P_CALC_GFA = "https://www.theworldavatar.com/kg/ontoplot/hasCalculatedGFA"
P_MAX_GFA = "https://www.theworldavatar.com/kg/ontoplot/hasMaximumPermittedGFA"
P_LAND_USE = "https://www.theworldavatar.com/kg/ontozoning/hasLandUseType"
P_PLOT_RATIO = "https://www.theworldavatar.com/kg/ontoplanningregulation/allowsGrossPlotRatio"
P_RATIO_NUM = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue"
P_ADDRESS = "http://www.theworldavatar.com/kg/ontocompany/hasAddress"
P_FACILITY = "https://www.theworldavatar.com/kg/ontobim/hasFacility"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def cache_dir() -> Path:
    d = mini_marie_cache_root() / "sg_old"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return cache_dir() / "ontop_cache.sqlite"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        CREATE TABLE IF NOT EXISTS facet_building (
          building_iri TEXT PRIMARY KEY,
          height REAL,
          usage TEXT,
          calc_gfa REAL,
          max_gfa REAL,
          land_use TEXT,
          plot_ratio_num REAL,
          footprint_wkt TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fb_usage ON facet_building(usage);
        CREATE INDEX IF NOT EXISTS idx_fb_land_use ON facet_building(land_use);
        CREATE TABLE IF NOT EXISTS facet_building_name (
          building_iri TEXT,
          name TEXT,
          name_lc TEXT,
          source TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fbn_lc ON facet_building_name(name_lc);
        CREATE TABLE IF NOT EXISTS facet_land_plot (
          plot_iri TEXT PRIMARY KEY,
          land_use TEXT,
          land_use_label TEXT,
          max_gfa REAL,
          calc_gfa REAL,
          plot_ratio_num REAL,
          area_sqm REAL
        );
        CREATE INDEX IF NOT EXISTS idx_flp_land_use ON facet_land_plot(land_use);
        """
    )
    for col, typ in [("land_use_label", "TEXT"), ("calc_gfa", "REAL")]:
        try:
            conn.execute(f"ALTER TABLE facet_land_plot ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def set_meta(key: str, value: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_meta(key: str) -> Optional[str]:
    conn = connect()
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def upsert_buildings(rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    conn = connect()
    conn.executemany(
        """
        INSERT INTO facet_building
          (building_iri, height, usage, calc_gfa, max_gfa, land_use, plot_ratio_num, footprint_wkt)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(building_iri) DO UPDATE SET
          height=excluded.height,
          usage=excluded.usage,
          calc_gfa=excluded.calc_gfa,
          max_gfa=excluded.max_gfa,
          land_use=excluded.land_use,
          plot_ratio_num=excluded.plot_ratio_num,
          footprint_wkt=COALESCE(excluded.footprint_wkt, facet_building.footprint_wkt)
        """,
        [
            (
                r.get("building_iri", ""),
                _f(r.get("height")),
                r.get("usage") or "",
                _f(r.get("calc_gfa")),
                _f(r.get("max_gfa")),
                r.get("land_use") or "",
                _f(r.get("plot_ratio_num")),
                r.get("footprint_wkt"),
            )
            for r in rows
        ],
    )
    conn.commit()
    n = conn.total_changes
    conn.close()
    return n


def upsert_names(rows: List[Dict[str, str]]) -> int:
    if not rows:
        return 0
    conn = connect()
    conn.executemany(
        "INSERT INTO facet_building_name(building_iri,name,name_lc,source) VALUES(?,?,?,?)",
        [(r["building_iri"], r["name"], r["name"].lower(), r["source"]) for r in rows],
    )
    conn.commit()
    conn.close()
    return len(rows)


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def building_count() -> int:
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM facet_building").fetchone()["n"]
    conn.close()
    return int(n)


def upsert_land_plots(rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    conn = connect()
    conn.executemany(
        """
        INSERT INTO facet_land_plot(plot_iri, land_use, land_use_label, max_gfa, calc_gfa, plot_ratio_num, area_sqm)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(plot_iri) DO UPDATE SET
          land_use=excluded.land_use,
          land_use_label=COALESCE(excluded.land_use_label, facet_land_plot.land_use_label),
          max_gfa=COALESCE(excluded.max_gfa, facet_land_plot.max_gfa),
          calc_gfa=COALESCE(excluded.calc_gfa, facet_land_plot.calc_gfa),
          plot_ratio_num=COALESCE(excluded.plot_ratio_num, facet_land_plot.plot_ratio_num),
          area_sqm=COALESCE(excluded.area_sqm, facet_land_plot.area_sqm)
        """,
        [
            (
                r.get("plot_iri", ""),
                r.get("land_use") or "",
                r.get("land_use_label") or "",
                _f(r.get("max_gfa")),
                _f(r.get("calc_gfa")),
                _f(r.get("plot_ratio_num")),
                _f(r.get("area_sqm")),
            )
            for r in rows
        ],
    )
    conn.commit()
    conn.close()
    return len(rows)


def land_plot_count() -> int:
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM facet_land_plot").fetchone()["n"]
    conn.close()
    return int(n)


def cache_ready() -> bool:
    return get_meta("warm_complete") == "1" and building_count() > 0
