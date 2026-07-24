"""Smoke-test TWA city TWA queries against Bremen and Kaiserslautern."""

from __future__ import annotations

import argparse
import json

from mini_marie.zaha.twa_city.twa_city_operations import (
    format_results_as_tsv,
    get_building_count,
    get_buildings_by_usage,
    get_height_stats,
    get_top_buildings_by_height,
)

TOOLS = {
    "count": get_building_count,
    "height_stats": get_height_stats,
    "top_height": get_top_buildings_by_height,
    "usage": get_buildings_by_usage,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TWA city TWA queries")
    parser.add_argument("--city", required=True, choices=["bremen", "kaiserslautern", "kl"])
    parser.add_argument(
        "--query",
        choices=list(TOOLS),
        default="count",
    )
    parser.add_argument("--usage-type", default="Domestic", help="For --query usage")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    city = "kaiserslautern" if args.city == "kl" else args.city
    fn = TOOLS[args.query]
    if args.query == "usage":
        results = fn(city, args.usage_type)
    else:
        results = fn(city)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_results_as_tsv(results))


if __name__ == "__main__":
    main()
