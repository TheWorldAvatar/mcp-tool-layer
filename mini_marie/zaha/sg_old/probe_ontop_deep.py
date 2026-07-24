"""
Exhaustive Ontop probe for sg-old — confirm what exists with counts + samples.

Usage:
  python -m mini_marie.zaha.sg_old.probe_ontop_deep
  python -m mini_marie.zaha.sg_old.probe_ontop_deep --json-out data/mini_marie_cache/sg_old/ontop_deep_probe.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.zaha.sg_old.ontop_store import (
    ONTOP_ENDPOINT,
    P_CALC_GFA,
    P_FOOTPRINT,
    P_HEIGHT,
    P_LAND_USE,
    P_MAX_GFA,
    P_PLOT_RATIO,
    P_RATIO_NUM,
    P_USAGE,
    CITYGML_BUILDING,
)
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

OM_HAS_VALUE = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasValue"
OM_NUM = P_RATIO_NUM
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def _count(query: str, var: str = "n", timeout: int = 120) -> Optional[int]:
    try:
        rows = execute_sparql_get(query, ONTOP_ENDPOINT, timeout=timeout)
        if rows and var in rows[0]:
            return int(float(rows[0][var]))
        return 0
    except Exception as exc:
        return None


def _sample(query: str, limit: int = 3, timeout: int = 120) -> Dict[str, Any]:
    try:
        rows = execute_sparql_get(query, ONTOP_ENDPOINT, timeout=timeout)
        return {"ok": True, "rows": rows[:limit], "row_count_returned": len(rows)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


def _probe_section(name: str, counts: Dict[str, str], samples: Dict[str, str], timeout: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"counts": {}, "samples": {}}
    for key, q in counts.items():
        n = _count(q, timeout=timeout)
        out["counts"][key] = n
        print(f"  {key}: {n}", flush=True)
    for key, q in samples.items():
        s = _sample(q, timeout=timeout)
        out["samples"][key] = s
        status = "ok" if s.get("ok") else f"ERR {s.get('error', '')[:80]}"
        print(f"  sample {key}: {status}", flush=True)
    return out


def build_report(timeout: int = 120) -> Dict[str, Any]:
    t0 = time.perf_counter()
    report: Dict[str, Any] = {
        "endpoint": ONTOP_ENDPOINT,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sections": {},
    }

    print("=== Buildings ===", flush=True)
    report["sections"]["buildings"] = _probe_section(
        "buildings",
        counts={
            "citygml_buildings": f"SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a <{CITYGML_BUILDING}> }}",
            "with_height": f"SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a <{CITYGML_BUILDING}> ; <{P_HEIGHT}> ?h }}",
            "with_usage": f"SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a <{CITYGML_BUILDING}> ; <{P_USAGE}> ?u }}",
            "with_footprint": f"SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a <{CITYGML_BUILDING}> ; <{P_FOOTPRINT}> ?f }}",
            "with_calc_gfa_literal": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_CALC_GFA}> ?v .
  FILTER(isLiteral(?v))
}}""",
            "with_calc_gfa_iri": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_CALC_GFA}> ?v .
  FILTER(!isLiteral(?v))
}}""",
            "with_max_gfa_on_building": f"SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a <{CITYGML_BUILDING}> ; <{P_MAX_GFA}> ?g }}",
            "with_land_use_on_building": f"SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a <{CITYGML_BUILDING}> ; <{P_LAND_USE}> ?lu }}",
            "with_plot_ratio_on_building": f"SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a <{CITYGML_BUILDING}> ; <{P_PLOT_RATIO}> ?r }}",
            "office_usage": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_USAGE}> ?u .
  FILTER(CONTAINS(LCASE(STR(?u)), "office"))
}}""",
        },
        samples={
            "building_attrs": f"""
SELECT ?b ?height ?usage WHERE {{
  ?b a <{CITYGML_BUILDING}> .
  OPTIONAL {{ ?b <{P_HEIGHT}> ?height }}
  OPTIONAL {{ ?b <{P_USAGE}> ?usage }}
}} LIMIT 2""",
            "footprint_sample": f"""
SELECT ?b ?fp WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_FOOTPRINT}> ?fp .
}} LIMIT 1""",
            "calc_gfa_building_chain": f"""
SELECT ?b ?calc ?num WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_CALC_GFA}> ?calc .
  OPTIONAL {{
    ?calc <{OM_HAS_VALUE}> ?m .
    ?m <{OM_NUM}> ?num .
  }}
}} LIMIT 3""",
        },
        timeout=timeout,
    )

    print("=== Land plots ===", flush=True)
    report["sections"]["land_plots"] = _probe_section(
        "land_plots",
        counts={
            "landplot_subjects": """
SELECT (COUNT(?p) AS ?n) WHERE {
  ?p <https://www.theworldavatar.com/kg/ontozoning/hasLandUseType> ?lu .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}""",
            "with_land_use": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_LAND_USE}> ?lu .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "with_max_gfa_link": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_MAX_GFA}> ?gfa .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "with_max_gfa_numeric": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_MAX_GFA}> ?gfa .
  ?gfa <{OM_HAS_VALUE}> ?m .
  ?m <{OM_NUM}> ?num .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "with_calc_gfa_on_plot": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_CALC_GFA}> ?v .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "with_calc_gfa_numeric_on_plot": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_CALC_GFA}> ?calc .
  ?calc <{OM_HAS_VALUE}> ?m .
  ?m <{OM_NUM}> ?num .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "with_plot_ratio_link": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_PLOT_RATIO}> ?r .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "with_plot_ratio_numeric": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_PLOT_RATIO}> ?ratio .
  ?ratio <{OM_HAS_VALUE}> ?m .
  ?m <{OM_NUM}> ?num .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "agriculture_plots": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_LAND_USE}> <https://www.theworldavatar.com/kg/landplot/LandUseType_a8e423e3-c628-4b08-9f63-fcf5b244873a> .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "agriculture_with_max_gfa_numeric": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_LAND_USE}> <https://www.theworldavatar.com/kg/landplot/LandUseType_a8e423e3-c628-4b08-9f63-fcf5b244873a> .
  ?p <{P_MAX_GFA}> ?gfa .
  ?gfa <{OM_HAS_VALUE}> ?m .
  ?m <{OM_NUM}> ?num .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "health_plots": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_LAND_USE}> <https://www.theworldavatar.com/kg/landplot/LandUseType_14534f34-3cbc-40c8-9e52-30018d18c486> .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "health_with_plot_ratio_link": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_LAND_USE}> <https://www.theworldavatar.com/kg/landplot/LandUseType_14534f34-3cbc-40c8-9e52-30018d18c486> .
  ?p <{P_PLOT_RATIO}> ?r .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
            "with_plot_area_numeric": """
SELECT (COUNT(?p) AS ?n) WHERE {
  ?p <https://www.theworldavatar.com/kg/ontoplot/hasPlotArea> ?area .
  ?area <http://www.ontology-of-units-of-measure.org/resource/om-2/hasValue> ?m .
  ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?num .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}""",
            "planningregulation_ratio_numeric": f"""
SELECT (COUNT(?reg) AS ?n) WHERE {{
  ?reg <{P_PLOT_RATIO}> ?ratio .
  ?ratio <{OM_HAS_VALUE}> ?m .
  ?m <{OM_NUM}> ?num .
  FILTER(REGEX(STR(?reg), "/planningregulation/[0-9]+$"))
}}""",
            "footprint_literal_wkt": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_FOOTPRINT}> ?fp .
  FILTER(isLiteral(?fp))
}}""",
            "footprint_geometry_iri": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{P_FOOTPRINT}> ?fp .
  FILTER(!isLiteral(?fp))
}}""",
        },
        samples={
            "max_gfa_chain": f"""
SELECT ?p ?gfa ?m ?num WHERE {{
  ?p <{P_MAX_GFA}> ?gfa .
  ?gfa <{OM_HAS_VALUE}> ?m .
  OPTIONAL {{ ?m <{OM_NUM}> ?num }}
  FILTER(REGEX(STR(?p), "/landplot/10$"))
}} LIMIT 1""",
            "plot_without_numeric_gfa": f"""
SELECT ?p ?gfa WHERE {{
  ?p <{P_MAX_GFA}> ?gfa .
  FILTER(REGEX(STR(?p), "/landplot/1$"))
}} LIMIT 1""",
            "gfameasure_preds": """
SELECT ?p ?o WHERE {
  <https://www.theworldavatar.com/kg/landplot/gfameasure/1> ?p ?o .
}""",
            "agriculture_plot_preds": f"""
SELECT ?p ?o WHERE {{
  <https://www.theworldavatar.com/kg/landplot/1> ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "gfa") || CONTAINS(LCASE(STR(?p)), "area") || CONTAINS(LCASE(STR(?p)), "ratio"))
}} LIMIT 10""",
        },
        timeout=timeout,
    )

    print("=== Alternate ratio / GFA predicates ===", flush=True)
    report["sections"]["alternate_predicates"] = _probe_section(
        "alternate_predicates",
        counts={
            "pred_contains_grossplotratio": """
SELECT (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "grossplotratio") || CONTAINS(LCASE(STR(?p)), "plotratio"))
}""",
            "pred_contains_gpr": """
SELECT (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "gpr") && !CONTAINS(LCASE(STR(?p)), "evaluation"))
}""",
            "pred_hasGFA": """
SELECT (COUNT(?s) AS ?n) WHERE {
  ?s <https://www.theworldavatar.com/kg/ontoplot/hasGFA> ?o .
}""",
            "pred_hasCalculatedGFA_any": f"SELECT (COUNT(?s) AS ?n) WHERE {{ ?s <{P_CALC_GFA}> ?o }}",
            "pred_hasMaximumPermittedGFA_any": f"SELECT (COUNT(?s) AS ?n) WHERE {{ ?s <{P_MAX_GFA}> ?o }}",
            "pred_allowsGrossPlotRatio_any": f"SELECT (COUNT(?s) AS ?n) WHERE {{ ?s <{P_PLOT_RATIO}> ?o }}",
        },
        samples={
            "grossplotratio_preds": """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "grossplotratio") || CONTAINS(LCASE(STR(?p)), "plotratio"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 10""",
            "gfa_related_preds": """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "gfa"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 15""",
            "planning_regulation_preds": """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "ontoplanningregulation"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 15""",
        },
        timeout=timeout,
    )

    print("=== Building-landplot linkage ===", flush=True)
    report["sections"]["building_plot_link"] = _probe_section(
        "building_plot_link",
        counts={
            "building_has_landplot_ref": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b ?p ?plot .
  FILTER(CONTAINS(LCASE(STR(?p)), "landplot") || CONTAINS(LCASE(STR(?plot)), "/landplot/"))
}""",
            "building_on_landplot": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontoplot/isOnLandPlot> ?plot .
}""",
            "landplot_has_building": """
SELECT (COUNT(?p) AS ?n) WHERE {
  ?p <https://www.theworldavatar.com/kg/ontoplot/hasBuilding> ?b .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}""",
            "same_id_building_landplot": """
SELECT (COUNT(?id) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?plot <https://www.theworldavatar.com/kg/ontozoning/hasLandUseType> ?lu .
  FILTER(REGEX(STR(?b), "/Building/([^/]+)$", "i"))
  FILTER(REGEX(STR(?plot), "/landplot/([^/]+)$", "i"))
  BIND(REPLACE(STR(?b), "^.*Building/", "") AS ?bid)
  BIND(REPLACE(STR(?plot), "^.*landplot/", "") AS ?pid)
  FILTER(?bid = ?pid)
}""",
        },
        samples={
            "building_landplot_preds": """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "land") || CONTAINS(LCASE(STR(?p)), "plot"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 10""",
        },
        timeout=timeout,
    )

    print("=== Names / addresses ===", flush=True)
    report["sections"]["names"] = _probe_section(
        "names",
        counts={
            "facility_names": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
}""",
            "address_names": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <http://www.theworldavatar.com/kg/ontocompany/hasAddress> ?a .
  ?a <http://www.w3.org/2000/01/rdf-schema#label> ?l .
}""",
            "building_rdfs_label": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <{CITYGML_BUILDING}> ; <{RDFS_LABEL}> ?l .
}}""",
            "abbott_facility": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "abbott"))
}""",
        },
        samples={
            "abbott_lookup": """
SELECT ?b ?l ?h ?fp WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "abbott")) .
  OPTIONAL { ?b <http://www.opengis.net/citygml/building/2.0/measuredHeight> ?h }
  OPTIONAL { ?b <http://www.opengis.net/citygml/building/2.0/lod0FootPrint> ?fp }
} LIMIT 3""",
        },
        timeout=timeout,
    )

    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep Ontop probe with evidence")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("data/mini_marie_cache/sg_old/ontop_deep_probe.json"),
    )
    args = parser.parse_args()

    report = build_report(timeout=args.timeout)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.json_out} ({report['elapsed_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
