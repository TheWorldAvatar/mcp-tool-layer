"""CLI: query building WKT from city TWA and render Leaflet HTML maps."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from mini_marie.zaha.twa_city.gis_visualization import (
    generate_building_map,
    summarize_map_buildings,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize TWA city building footprints")
    parser.add_argument("--city", required=True, choices=["bremen", "kaiserslautern", "kl"])
    parser.add_argument(
        "--mode",
        choices=["top_height", "bbox"],
        default="top_height",
        help="top_height: tallest buildings; bbox: map centre window (GeoSPARQL)",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path, help="Output HTML path")
    parser.add_argument("--open", action="store_true", help="Open map in default browser")
    args = parser.parse_args()

    city = "kaiserslautern" if args.city == "kl" else args.city
    path, rows, geojson = generate_building_map(
        city=city,
        mode=args.mode,
        limit=args.limit,
        output_path=args.output,
    )
    print(f"Map: {path.resolve()}")
    print(f"Features: {len(geojson['features'])}")
    print(summarize_map_buildings(rows))
    if args.open:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
