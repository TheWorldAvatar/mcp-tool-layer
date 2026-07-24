"""Merge probe evidence + local cache counts into one report."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.ontop_store import db_path as ontop_db


def _ontop_counts() -> dict:
    conn = sqlite3.connect(ontop_db())
    conn.row_factory = sqlite3.Row

    def c(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    out = {
        "buildings": c("SELECT COUNT(*) FROM facet_building"),
        "buildings_with_footprint_ref": c(
            "SELECT COUNT(*) FROM facet_building WHERE footprint_wkt IS NOT NULL AND footprint_wkt != ''"
        ),
        "buildings_footprint_wkt_literal": c(
            "SELECT COUNT(*) FROM facet_building WHERE footprint_wkt LIKE 'POLYGON%' OR footprint_wkt LIKE 'MULTIPOLYGON%'"
        ),
        "building_names": c("SELECT COUNT(*) FROM facet_building_name"),
        "land_plots": c("SELECT COUNT(*) FROM facet_land_plot"),
        "land_plots_with_max_gfa": c("SELECT COUNT(*) FROM facet_land_plot WHERE max_gfa IS NOT NULL"),
        "land_plots_with_plot_area": c("SELECT COUNT(*) FROM facet_land_plot WHERE area_sqm IS NOT NULL"),
        "land_plots_with_plot_ratio": c("SELECT COUNT(*) FROM facet_land_plot WHERE plot_ratio_num IS NOT NULL"),
        "land_plots_with_calc_gfa": c("SELECT COUNT(*) FROM facet_land_plot WHERE calc_gfa IS NOT NULL"),
    }
    conn.close()
    return out


def _blazegraph_geo() -> dict:
    from mini_marie.zaha.sg_old.local_store import ensure_db, db_path

    ensure_db()
    conn = sqlite3.connect(db_path())
    out: dict = {"namespaces": {}}
    for ns in ("kb", "carpark", "plot", "company"):
        rows = conn.execute(
            """
            SELECT p, COUNT(*) AS n FROM triples
            WHERE ns=? AND (
              LOWER(p) LIKE '%latitude%' OR LOWER(p) LIKE '%longitude%'
              OR LOWER(p) LIKE '%location%' OR LOWER(p) LIKE '%wkt%'
              OR LOWER(p) LIKE '%geometry%' OR LOWER(p) LIKE '%coordinate%'
            )
            GROUP BY p ORDER BY n DESC
            """,
            (ns,),
        ).fetchall()
        out["namespaces"][ns] = [{"predicate": r[0], "count": r[1]} for r in rows]
    conn.close()
    return out


def main() -> int:
    root = mini_marie_cache_root() / "sg_old"
    deep = root / "ontop_deep_probe.json"
    probe = json.loads(deep.read_text(encoding="utf-8")) if deep.exists() else {}

    report = {
        "summary": {
            "ontop_endpoint": "https://sg-old.theworldavatar.io/ontop/sparql/",
            "blazegraph_host": "https://sg-old.theworldavatar.io/blazegraph/namespace/{ns}/sparql",
            "confirmed_findings": [
                "114553 buildings; all have measuredHeight and lod0FootPrint (as geometry IRI, not WKT literal).",
                "113672 land plots with hasLandUseType.",
                "113672 land plots with hasPlotArea numeric (sqm) — use for agriculture smallest lot.",
                "113672 land plots with hasMaximumPermittedGFA link; only 33641 have numeric max GFA via OM chain.",
                "allowsGrossPlotRatio exists on landplot/planningregulation/{id} (not landplot/{id}); 33641 numeric.",
                "hasCalculatedGFA on buildings is a literal on all 114553; on land plots: 0.",
                "Building↔landplot join predicates: none found on Ontop.",
                "Facility names: 5152; address names via hasAddress: 0.",
                "Abbott Manufacturing: 1 facility match; footprint is geometry IRI cui.unige.ch/citygml/2.0/geometry/42226638.",
            ],
        },
        "local_cache_counts": _ontop_counts(),
        "blazegraph_geo_predicates": _blazegraph_geo(),
        "ontop_deep_probe": probe,
    }

    out_path = root / "data_existence_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
