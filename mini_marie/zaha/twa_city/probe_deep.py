"""
Deep probe: building schema, property coverage, usage, height, addresses, geo.

Usage:
  python -m mini_marie.zaha.twa_city.probe_deep
  python -m mini_marie.zaha.twa_city.probe_deep --city bremen
  python -m mini_marie.zaha.twa_city.probe_deep --md-out mini_marie/zaha/twa_city/BUILDING_SCHEMA.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from mini_marie.zaha.twa_city.twa_city_operations import (
    CITY_ENDPOINTS,
    format_results_as_tsv,
    run_deep_discovery,
)

PREFIX_NOTES = {
    "bldg": "http://www.opengis.net/citygml/building/2.0/",
    "gml": "http://www.opengis.net/citygml/2.0/",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "be": "https://www.theworldavatar.com/kg/ontobuiltenv/",
    "building_iri": "https://theworldavatar.io/kg/Building/{uuid}",
}


def _short_iri(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri


def _rows_or_error(block: Any) -> tuple[List[Dict[str, Any]], str | None]:
    if isinstance(block, dict) and "error" in block:
        return [], block["error"]
    return block, None


def render_city_section(city: str, discovery: Dict[str, Any]) -> str:
    lines = [f"## {city.title()}", ""]
    endpoint = CITY_ENDPOINTS[city]
    lines.append(f"- **Endpoint:** `{endpoint}`")
    lines.append("")

    cov_rows, cov_err = _rows_or_error(discovery.get("15_property_coverage"))
    if cov_rows:
        lines.append("### Property coverage (distinct buildings)")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|------:|")
        for row in cov_rows:
            lines.append(f"| {row.get('metric', '')} | {row.get('count', '')} |")
        lines.append("")
    elif cov_err:
        lines.append(f"### Property coverage — failed: `{cov_err[:120]}`")
        lines.append("")

    for key in (
        "12_height_stats",
        "14_usage_type_counts",
        "17_footprint_area_stats",
        "13_top_buildings_by_height",
    ):
        rows, err = _rows_or_error(discovery.get(key))
        if err:
            lines.append(f"### {key} — failed")
            lines.append(f"`{err[:200]}`")
            lines.append("")
            continue
        if not rows:
            continue
        lines.append(f"### {key.replace('_', ' ')}")
        lines.append("")
        lines.append("```tsv")
        lines.append(format_results_as_tsv(rows))
        lines.append("```")
        lines.append("")

    props, err = _rows_or_error(discovery.get("10_one_building_properties"))
    if props:
        lines.append("### Example building — all properties (one record)")
        lines.append("")
        lines.append("| Predicate | Object |")
        lines.append("|-----------|--------|")
        for row in props[:35]:
            p = _short_iri(str(row.get("p", "")))
            o = str(row.get("o", ""))
            if len(o) > 80:
                o = o[:77] + "..."
            lines.append(f"| {p} | {o} |")
        lines.append("")

    preds, err = _rows_or_error(discovery.get("11_building_predicates_on_sample"))
    if preds:
        lines.append("### Predicates on 500-building sample")
        lines.append("")
        lines.append("| Predicate | Buildings (of 500) |")
        lines.append("|-----------|-------------------:|")
        for row in preds:
            lines.append(f"| {_short_iri(str(row.get('p', '')))} | {row.get('buildings', '')} |")
        lines.append("")

    addr, _ = _rows_or_error(discovery.get("16_buildings_with_address_sample"))
    if addr:
        lines.append("### Address structure sample")
        lines.append("")
        lines.append("```tsv")
        lines.append(format_results_as_tsv(addr[:15]))
        lines.append("```")
        lines.append("")

    wkt, _ = _rows_or_error(discovery.get("18_wkt_length_sample"))
    if wkt:
        lines.append("### Geo WKT sample (truncated in report)")
        lines.append("")
        for row in wkt:
            w = str(row.get("wkt", ""))
            lines.append(f"- `{row.get('building', '')}`: WKT length {len(w)} chars")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep probe TWA city building VKGs")
    parser.add_argument(
        "--city",
        choices=list(CITY_ENDPOINTS.keys()),
        help="Probe one city only (default: both)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("mini_marie/zaha/twa_city/probe_results_deep.json"),
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=Path("mini_marie/zaha/twa_city/BUILDING_SCHEMA.md"),
    )
    args = parser.parse_args()

    cities = [args.city] if args.city else list(CITY_ENDPOINTS.keys())
    report: Dict[str, Any] = {"prefix_notes": PREFIX_NOTES, "cities": {}}

    md_parts = [
        "# TWA City — building data schema (from deep probe)",
        "",
        "Generated by `python -m mini_marie.zaha.twa_city.probe_deep`.",
        "",
        "## Prefixes",
        "",
    ]
    for k, v in PREFIX_NOTES.items():
        md_parts.append(f"- `{k}`: `{v}`")
    md_parts.append("")

    for city in cities:
        print(f"Deep probe: {city} ...", file=sys.stderr)
        endpoint = CITY_ENDPOINTS[city]
        discovery = run_deep_discovery(endpoint)
        report["cities"][city] = {"endpoint": endpoint, "discovery": discovery}
        md_parts.append(render_city_section(city, discovery))
        print(f"  done {city}", file=sys.stderr)

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.md_out.write_text("\n".join(md_parts), encoding="utf-8")
    print(f"Wrote {args.json_out}")
    print(f"Wrote {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
