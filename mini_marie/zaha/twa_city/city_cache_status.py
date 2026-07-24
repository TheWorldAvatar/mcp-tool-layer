"""
Print TWA city cache progress (fast SQL counts; no full facet scans).

Usage:
  python -m mini_marie.zaha.twa_city.city_cache_status
  python -m mini_marie.zaha.twa_city.city_cache_status --city bremen
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from mini_marie.zaha.twa_city.atomic_warm_manifest import (
    CITIES,
    comprehensive_warm_specs,
    workflow_driven_warm_specs,
)
from mini_marie.zaha.twa_city.city_cache import CityCache, db_path
from mini_marie.warm_manifest import specs_missing_full_tier


def _location_batch_progress(conn: sqlite3.Connection, city: str) -> dict:
    cur = conn.execute(
        """
        SELECT COUNT(*) AS batches,
               COALESCE(SUM(row_count), 0) AS rows,
               COALESCE(SUM(elapsed_ms), 0) AS elapsed_ms,
               MAX(fetched_at) AS last_fetched
        FROM atomic_calls
        WHERE city = ? AND tool = 'fetch_building_locations' AND mode = 'full'
        """,
        (city.lower(),),
    )
    row = cur.fetchone()
    return {
        "batches_done": int(row[0] or 0),
        "rows_fetched": int(row[1] or 0),
        "elapsed_ms": int(row[2] or 0),
        "last_fetched": row[3],
    }


def print_status(*, city_filter: str | None = None) -> None:
    path = db_path()
    if not path.exists():
        print(f"No cache at {path}")
        print("Warm: python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen")
        return

    size_mb = path.stat().st_size / 1e6
    print(f"DB: {path}")
    print(f"Size: {size_mb:.1f} MB")

    conn = sqlite3.connect(path)
    cache = CityCache(path)
    try:
        specs = workflow_driven_warm_specs()
        cities = [city_filter] if city_filter else list(CITIES)
        for city in cities:
            city_lc = city.lower()
            heights = conn.execute(
                "SELECT COUNT(*) FROM facet_building_height WHERE city_lc = ?",
                (city_lc,),
            ).fetchone()[0]
            locs = conn.execute(
                "SELECT COUNT(*) FROM facet_building_location WHERE city_lc = ?",
                (city_lc,),
            ).fetchone()[0]

            print(f"\n=== {city} ===")
            print(f"  facet_building_height:   {heights:,}")
            print(f"  facet_building_location: {locs:,}")

            city_specs = [s for s in specs if (s.get("args") or {}).get("city", "").lower() == city_lc]
            missing = specs_missing_full_tier(city_specs, has_full=cache.has_full)
            print(f"  atomic specs: {len(city_specs)} total, {len(missing)} missing full tier")
            for spec in city_specs:
                tool = spec["tool"]
                args = spec.get("args") or {}
                mark = "OK" if cache.has_full(tool, args) else "MISSING"
                print(f"    [{mark}] {tool} {args}")

            loc = _location_batch_progress(conn, city)
            unique_buildings = conn.execute(
                "SELECT COUNT(DISTINCT building) FROM facet_building_height WHERE city_lc = ?",
                (city_lc,),
            ).fetchone()[0]
            expected_batches = (int(unique_buildings) + 99) // 100 if unique_buildings else 0
            pct = (
                round(100.0 * loc["batches_done"] / expected_batches, 1)
                if expected_batches
                else 0.0
            )
            avg_ms = (
                loc["elapsed_ms"] // loc["batches_done"] if loc["batches_done"] else 0
            )
            remaining = max(0, expected_batches - loc["batches_done"])
            eta_min = round(remaining * avg_ms / 60000, 1) if avg_ms else None
            print(
                f"  location batches: {loc['batches_done']:,}/{expected_batches:,} "
                f"({pct}%) rows={loc['rows_fetched']:,} avg_batch_ms={avg_ms}"
            )
            if eta_min is not None and remaining:
                print(f"  ETA (location warm): ~{eta_min} min at current avg batch time")
            if loc["last_fetched"]:
                print(f"  last location batch at: {loc['last_fetched']}")
    finally:
        cache.close()
        conn.close()

    print("\nChunked warm examples:")
    print("  python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --atomics-only")
    print("  python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --locations-only --missing-only")
    print("  python -m mini_marie.zaha.twa_city.warm_city_cache --city kaiserslautern --comprehensive")


def main() -> None:
    parser = argparse.ArgumentParser(description="TWA city cache progress")
    parser.add_argument("--city", help="Filter to one city slug")
    args = parser.parse_args()
    print_status(city_filter=args.city)


if __name__ == "__main__":
    main()
