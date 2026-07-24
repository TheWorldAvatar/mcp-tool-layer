"""
Warm sg-old Ontop SQLite cache (buildings, land-use, GFA, names).

Usage:
  python -m mini_marie.zaha.sg_old.warm_ontop_cache
  python -m mini_marie.zaha.sg_old.warm_ontop_cache --page-size 3000 --with-footprints
  python -m mini_marie.zaha.sg_old.warm_ontop_cache --names-only
"""

from __future__ import annotations

import argparse
import time
from typing import Any, Dict, List

from mini_marie.zaha.sg_old.ontop_store import (
    CITYGML_BUILDING,
    ONTOP_ENDPOINT,
    P_ADDRESS,
    P_CALC_GFA,
    P_FACILITY,
    P_FOOTPRINT,
    P_HEIGHT,
    P_LAND_USE,
    P_MAX_GFA,
    P_PLOT_RATIO,
    P_RATIO_NUM,
    P_USAGE,
    RDFS_LABEL,
    ensure_schema,
    set_meta,
    upsert_buildings,
    upsert_land_plots,
    upsert_names,
)
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get


def _building_batch_query(limit: int, offset: int, with_footprints: bool) -> str:
    fp = (
        f"OPTIONAL {{ ?b <{P_FOOTPRINT}> ?footprint . }}"
        if with_footprints
        else ""
    )
    sel_fp = "?footprint" if with_footprints else '""'
    return f"""
SELECT ?b ?height ?usage ?calc_gfa ?max_gfa ?land_use ?plot_ratio_num {('?footprint' if with_footprints else '')}
WHERE {{
  ?b a <{CITYGML_BUILDING}> .
  OPTIONAL {{ ?b <{P_HEIGHT}> ?height }}
  OPTIONAL {{ ?b <{P_USAGE}> ?usage }}
  OPTIONAL {{ ?b <{P_CALC_GFA}> ?calc_gfa }}
  OPTIONAL {{ ?b <{P_MAX_GFA}> ?max_gfa }}
  OPTIONAL {{ ?b <{P_LAND_USE}> ?land_use }}
  OPTIONAL {{
    ?b <{P_PLOT_RATIO}> ?ratio .
    ?ratio <{P_RATIO_NUM}> ?plot_ratio_num
  }}
  {fp}
}}
ORDER BY ?b
LIMIT {int(limit)}
OFFSET {int(offset)}
"""


def _name_query(kind: str, limit: int, offset: int) -> str:
    if kind == "address":
        return f"""
SELECT ?b ?label WHERE {{
  ?b a <{CITYGML_BUILDING}> .
  ?b <{P_ADDRESS}> ?addr .
  ?addr <{RDFS_LABEL}> ?label .
}}
ORDER BY ?b
LIMIT {int(limit)} OFFSET {int(offset)}
"""
    return f"""
SELECT ?b ?label WHERE {{
  ?b a <{CITYGML_BUILDING}> .
  ?b <{P_FACILITY}> ?fac .
  ?fac <{RDFS_LABEL}> ?label .
}}
ORDER BY ?b
LIMIT {int(limit)} OFFSET {int(offset)}
"""


def warm_buildings(
    *,
    page_size: int,
    timeout: int,
    with_footprints: bool,
    max_pages: int | None,
) -> Dict[str, Any]:
    total_rows = 0
    pages = 0
    offset = 0
    t0 = time.perf_counter()
    while True:
        if max_pages is not None and pages >= max_pages:
            break
        q = _building_batch_query(page_size, offset, with_footprints)
        rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=timeout)
        pages += 1
        if not rows:
            break
        batch: List[Dict[str, Any]] = []
        for r in rows:
            item = {
                "building_iri": r.get("b", ""),
                "height": r.get("height"),
                "usage": r.get("usage"),
                "calc_gfa": r.get("calc_gfa"),
                "max_gfa": r.get("max_gfa"),
                "land_use": r.get("land_use"),
                "plot_ratio_num": r.get("plot_ratio_num"),
            }
            if with_footprints:
                item["footprint_wkt"] = r.get("footprint")
            batch.append(item)
        upsert_buildings(batch)
        total_rows += len(batch)
        print(f"  buildings page {pages}: +{len(batch)} (total {total_rows})", flush=True)
        if len(rows) < page_size:
            break
        offset += page_size
    return {
        "pages": pages,
        "rows": total_rows,
        "seconds": round(time.perf_counter() - t0, 1),
        "with_footprints": with_footprints,
    }


def _land_plot_batch_query(limit: int, offset: int) -> str:
    return f"""
SELECT ?plot ?land_use WHERE {{
  ?plot <{P_LAND_USE}> ?land_use .
  FILTER(REGEX(STR(?plot), "/landplot/[0-9]+$"))
}}
ORDER BY ?plot
LIMIT {int(limit)}
OFFSET {int(offset)}
"""


OM_HAS_VALUE = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasValue"


def _max_gfa_page_query(limit: int, offset: int) -> str:
    return f"""
SELECT ?plot ?num WHERE {{
  ?plot <{P_MAX_GFA}> ?gfa .
  ?gfa <{OM_HAS_VALUE}> ?m .
  ?m <{P_RATIO_NUM}> ?num .
  FILTER(REGEX(STR(?plot), "/landplot/[0-9]+$"))
}}
ORDER BY ?plot
LIMIT {int(limit)}
OFFSET {int(offset)}
"""


def _plot_ratio_page_query(limit: int, offset: int) -> str:
    # Ratio is on landplot/planningregulation/{id}, not landplot/{id}.
    return f"""
SELECT ?plot ?num WHERE {{
  ?reg <{P_PLOT_RATIO}> ?ratio .
  ?ratio <{OM_HAS_VALUE}> ?m .
  ?m <{P_RATIO_NUM}> ?num .
  FILTER(REGEX(STR(?reg), "/landplot/planningregulation/([0-9]+)$"))
  BIND(IRI(CONCAT("https://www.theworldavatar.com/kg/landplot/", REPLACE(STR(?reg), "^.*planningregulation/", ""))) AS ?plot)
}}
ORDER BY ?plot
LIMIT {int(limit)}
OFFSET {int(offset)}
"""


def _plot_area_page_query(limit: int, offset: int) -> str:
    return f"""
SELECT ?plot ?num WHERE {{
  ?plot <https://www.theworldavatar.com/kg/ontoplot/hasPlotArea> ?area .
  ?area <{OM_HAS_VALUE}> ?m .
  ?m <{P_RATIO_NUM}> ?num .
  FILTER(REGEX(STR(?plot), "/landplot/[0-9]+$"))
}}
ORDER BY ?plot
LIMIT {int(limit)}
OFFSET {int(offset)}
"""


def warm_land_plots(*, page_size: int, timeout: int) -> Dict[str, Any]:
    total = 0
    pages = 0
    offset = 0
    t0 = time.perf_counter()
    while True:
        rows = execute_sparql_get(_land_plot_batch_query(page_size, offset), ONTOP_ENDPOINT, timeout=timeout)
        pages += 1
        if not rows:
            break
        batch = [
            {"plot_iri": r.get("plot", ""), "land_use": r.get("land_use")}
            for r in rows
            if r.get("plot")
        ]
        upsert_land_plots(batch)
        total += len(batch)
        print(f"  land_plots page {pages}: +{len(batch)} (total {total})", flush=True)
        if len(rows) < page_size:
            break
        offset += page_size
    return {"land_plot_pages": pages, "land_plot_rows": total, "seconds": round(time.perf_counter() - t0, 1)}


def enrich_land_plot_max_gfa(*, page_size: int, timeout: int) -> Dict[str, Any]:
    from mini_marie.zaha.sg_old.ontop_store import connect

    updated = 0
    pages = 0
    offset = 0
    t0 = time.perf_counter()
    conn = connect()
    while True:
        rows = execute_sparql_get(_max_gfa_page_query(page_size, offset), ONTOP_ENDPOINT, timeout=timeout)
        pages += 1
        if not rows:
            break
        conn.executemany(
            "UPDATE facet_land_plot SET max_gfa=?, area_sqm=? WHERE plot_iri=?",
            [(float(r["num"]), float(r["num"]), r["plot"]) for r in rows if r.get("plot") and r.get("num")],
        )
        conn.commit()
        updated += len(rows)
        print(f"  max_gfa page {pages}: +{len(rows)} (total {updated})", flush=True)
        if len(rows) < page_size:
            break
        offset += page_size
    conn.close()
    return {"max_gfa_pages": pages, "max_gfa_rows": updated, "seconds": round(time.perf_counter() - t0, 1)}


def enrich_land_plot_areas(*, page_size: int, timeout: int) -> Dict[str, Any]:
    from mini_marie.zaha.sg_old.ontop_store import connect

    updated = 0
    pages = 0
    offset = 0
    t0 = time.perf_counter()
    conn = connect()
    while True:
        rows = execute_sparql_get(_plot_area_page_query(page_size, offset), ONTOP_ENDPOINT, timeout=timeout)
        pages += 1
        if not rows:
            break
        conn.executemany(
            "UPDATE facet_land_plot SET area_sqm=? WHERE plot_iri=?",
            [(float(r["num"]), r["plot"]) for r in rows if r.get("plot") and r.get("num")],
        )
        conn.commit()
        updated += len(rows)
        print(f"  plot_area page {pages}: +{len(rows)} (total {updated})", flush=True)
        if len(rows) < page_size:
            break
        offset += page_size
    conn.close()
    return {"plot_area_pages": pages, "plot_area_rows": updated, "seconds": round(time.perf_counter() - t0, 1)}


def enrich_land_plot_ratios(*, page_size: int, timeout: int) -> Dict[str, Any]:
    from mini_marie.zaha.sg_old.ontop_store import connect

    updated = 0
    pages = 0
    offset = 0
    t0 = time.perf_counter()
    conn = connect()
    while True:
        rows = execute_sparql_get(_plot_ratio_page_query(page_size, offset), ONTOP_ENDPOINT, timeout=timeout)
        pages += 1
        if not rows:
            break
        conn.executemany(
            "UPDATE facet_land_plot SET plot_ratio_num=? WHERE plot_iri=?",
            [(float(r["num"]), r["plot"]) for r in rows if r.get("plot") and r.get("num")],
        )
        conn.commit()
        updated += len(rows)
        print(f"  plot_ratio page {pages}: +{len(rows)} (total {updated})", flush=True)
        if len(rows) < page_size:
            break
        offset += page_size
    conn.close()
    return {"plot_ratio_pages": pages, "plot_ratio_rows": updated, "seconds": round(time.perf_counter() - t0, 1)}


def enrich_land_use_labels() -> Dict[str, Any]:
    from mini_marie.zaha.sg_old import local_store as bg
    from mini_marie.zaha.sg_old.local_store import ensure_db
    from mini_marie.zaha.sg_old.ontop_store import connect

    ensure_db()
    conn = connect()
    iris = conn.execute(
        "SELECT DISTINCT land_use FROM facet_land_plot WHERE land_use IS NOT NULL AND land_use != ''"
    ).fetchall()
    mapped = 0
    for row in iris:
        label = (bg.object_values("plot", row["land_use"], RDFS_LABEL, limit=1) or [""])[0]
        if not label:
            continue
        conn.execute(
            "UPDATE facet_land_plot SET land_use_label=? WHERE land_use=?",
            (label, row["land_use"]),
        )
        mapped += 1
    conn.commit()
    conn.close()
    return {"land_use_types_mapped": mapped}


def _footprint_page_query(limit: int, offset: int) -> str:
    return f"""
SELECT ?b ?footprint WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_FOOTPRINT}> ?footprint .
}}
ORDER BY ?b
LIMIT {int(limit)}
OFFSET {int(offset)}
"""


def enrich_building_footprints(*, page_size: int, timeout: int) -> Dict[str, Any]:
    from mini_marie.zaha.sg_old.ontop_store import connect

    updated = 0
    pages = 0
    offset = 0
    t0 = time.perf_counter()
    conn = connect()
    while True:
        rows = execute_sparql_get(_footprint_page_query(page_size, offset), ONTOP_ENDPOINT, timeout=timeout)
        pages += 1
        if not rows:
            break
        conn.executemany(
            "UPDATE facet_building SET footprint_wkt=? WHERE building_iri=?",
            [(r.get("footprint", ""), r["b"]) for r in rows if r.get("b") and r.get("footprint")],
        )
        conn.commit()
        updated += len(rows)
        print(f"  footprints page {pages}: +{len(rows)} (total {updated})", flush=True)
        if len(rows) < page_size:
            break
        offset += page_size
    conn.close()
    return {"footprint_pages": pages, "footprint_rows": updated, "seconds": round(time.perf_counter() - t0, 1)}


def _calc_gfa_plot_page_query(limit: int, offset: int) -> str:
    return f"""
SELECT ?plot ?num WHERE {{
  ?plot <{P_CALC_GFA}> ?calc .
  ?calc <{OM_HAS_VALUE}> ?m .
  ?m <{P_RATIO_NUM}> ?num .
  FILTER(REGEX(STR(?plot), "/landplot/[0-9]+$"))
}}
ORDER BY ?plot
LIMIT {int(limit)}
OFFSET {int(offset)}
"""


def enrich_land_plot_calc_gfa(*, page_size: int, timeout: int) -> Dict[str, Any]:
    from mini_marie.zaha.sg_old.ontop_store import connect

    updated = 0
    pages = 0
    offset = 0
    t0 = time.perf_counter()
    conn = connect()
    while True:
        rows = execute_sparql_get(_calc_gfa_plot_page_query(page_size, offset), ONTOP_ENDPOINT, timeout=timeout)
        pages += 1
        if not rows:
            break
        # store in area_sqm when max_gfa absent — also track via meta
        conn.executemany(
            "UPDATE facet_land_plot SET calc_gfa=? WHERE plot_iri=?",
            [(float(r["num"]), r["plot"]) for r in rows if r.get("plot") and r.get("num")],
        )
        conn.commit()
        updated += len(rows)
        print(f"  calc_gfa page {pages}: +{len(rows)} (total {updated})", flush=True)
        if len(rows) < page_size:
            break
        offset += page_size
    conn.close()
    return {"calc_gfa_pages": pages, "calc_gfa_rows": updated, "seconds": round(time.perf_counter() - t0, 1)}


def enrich_land_plots(*, page_size: int, timeout: int) -> Dict[str, Any]:
    gfa = enrich_land_plot_max_gfa(page_size=page_size, timeout=timeout)
    area = enrich_land_plot_areas(page_size=page_size, timeout=timeout)
    calc = enrich_land_plot_calc_gfa(page_size=page_size, timeout=timeout)
    ratio = enrich_land_plot_ratios(page_size=page_size, timeout=timeout)
    labels = enrich_land_use_labels()
    return {"max_gfa": gfa, "plot_area": area, "calc_gfa": calc, "plot_ratio": ratio, "labels": labels}


def warm_names(*, page_size: int, timeout: int) -> Dict[str, Any]:
    total = 0
    for kind in ("address", "facility"):
        offset = 0
        pages = 0
        while True:
            rows = execute_sparql_get(_name_query(kind, page_size, offset), ONTOP_ENDPOINT, timeout=timeout)
            pages += 1
            if not rows:
                break
            upsert_names(
                [
                    {"building_iri": r.get("b", ""), "name": r.get("label", ""), "source": kind}
                    for r in rows
                    if r.get("b") and r.get("label")
                ]
            )
            total += len(rows)
            print(f"  names {kind} page {pages}: +{len(rows)}", flush=True)
            if len(rows) < page_size:
                break
            offset += page_size
    return {"name_rows": total}


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm sg-old Ontop cache")
    parser.add_argument("--page-size", type=int, default=3000)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--with-footprints", action="store_true")
    parser.add_argument("--names-only", action="store_true")
    parser.add_argument("--land-plots-only", action="store_true")
    parser.add_argument("--enrich-measures", action="store_true", help="Fill max_gfa, plot_ratio, land_use_label on cached plots")
    parser.add_argument("--footprints-only", action="store_true", help="Download lod0FootPrint WKT for all buildings")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()

    ensure_schema()
    print(f"Warming Ontop from {ONTOP_ENDPOINT}")

    if args.names_only:
        stats = warm_names(page_size=args.page_size, timeout=args.timeout)
        print(stats)
        return 0

    if args.footprints_only:
        stats = enrich_building_footprints(page_size=args.page_size, timeout=args.timeout)
        print(stats)
        return 0

    if args.enrich_measures:
        stats = enrich_land_plots(page_size=args.page_size, timeout=args.timeout)
        print(stats)
        return 0

    if args.land_plots_only:
        stats = warm_land_plots(page_size=args.page_size, timeout=args.timeout)
        estats = enrich_land_plots(page_size=args.page_size, timeout=args.timeout)
        print(stats, estats)
        return 0

    bstats = warm_buildings(
        page_size=args.page_size,
        timeout=args.timeout,
        with_footprints=args.with_footprints,
        max_pages=args.max_pages,
    )
    lpstats = warm_land_plots(page_size=args.page_size, timeout=args.timeout)
    estats = enrich_land_plots(page_size=args.page_size, timeout=args.timeout)
    nstats = warm_names(page_size=args.page_size, timeout=args.timeout)
    set_meta("warm_complete", "1")
    set_meta("building_rows", str(bstats["rows"]))
    set_meta("land_plot_rows", str(lpstats["land_plot_rows"]))
    print("Done:", bstats, lpstats, estats, nstats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
