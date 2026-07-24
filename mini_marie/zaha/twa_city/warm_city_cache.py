"""
Pre-cache full-tier atomic SPARQL results for TWA city workflows.

Use --comprehensive to warm every city plan tool (all cities), then batch all WKT locations.

Chunked / resumable:
  --atomics-only       height/usage pools only (fast per city)
  --locations-only     WKT batches only (slow; skips batches already in full tier)
  --missing-only       skip atomics that already have full-tier cache
  --status             print progress and exit (see city_cache_status.py)

Progress:
  python -m mini_marie.zaha.twa_city.city_cache_status --city bremen
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from mini_marie.zaha.twa_city.atomic_warm_manifest import (
    cities_for_comprehensive_warm,
    comprehensive_warm_specs,
    workflow_driven_warm_specs,
)
from mini_marie.zaha.twa_city.city_cache import (
    CityCache,
    warm_atomic,
    warm_locations_for_city,
    warm_locations_top_n,
)
from mini_marie.warm_manifest import specs_missing_full_tier


def warm_city(
    city: str,
    specs: List[Dict[str, Any]],
    *,
    include_atomics: bool = True,
    include_locations: bool = True,
    locations_top_n: int | None = None,
    locations_usage_type: str | None = None,
    force: bool = False,
    missing_only: bool = False,
    batch_size: int = 100,
    skip_cached_batches: bool = True,
) -> Dict[str, Any]:
    ck = CityCache()
    try:
        city_specs = [s for s in specs if (s.get("args") or {}).get("city", "").lower() == city.lower()]
        if missing_only and not force:
            before = len(city_specs)
            city_specs = specs_missing_full_tier(city_specs, has_full=ck.has_full)
            print(
                f"  atomics to warm: {len(city_specs)}/{before} (missing full tier)",
                flush=True,
            )

        warmed: List[Dict[str, Any]] = []
        if include_atomics:
            for i, spec in enumerate(city_specs, 1):
                print(
                    f"  atomic [{i}/{len(city_specs)}] {spec['tool']} {spec['args']} ...",
                    flush=True,
                )
                rows, meta = warm_atomic(
                    spec["tool"],
                    spec["args"],
                    force=force,
                    cache=ck,
                )
                print(f"    -> {len(rows)} rows in {meta.get('elapsed_ms', '?')}ms", flush=True)
                warmed.append(
                    {
                        "tool": spec["tool"],
                        "args": spec["args"],
                        "row_count": len(rows),
                        **meta,
                    }
                )
        elif city_specs:
            print(f"  atomics skipped ({len(city_specs)} specs not run)", flush=True)

        loc_stats = None
        if include_locations:
            if locations_top_n is not None:
                loc_stats = warm_locations_top_n(
                    city,
                    locations_top_n,
                    usage_type=locations_usage_type,
                    batch_size=batch_size,
                    force=force,
                    skip_cached=skip_cached_batches,
                    cache=ck,
                )
            else:
                print(f"  locations full-city (batch_size={batch_size}) ...", flush=True)
                loc_stats = warm_locations_for_city(
                    city,
                    batch_size=batch_size,
                    force=force,
                    skip_cached=skip_cached_batches,
                    cache=ck,
                )
    finally:
        ck.close()

    return {"city": city, "atomics": warmed, "locations": loc_stats}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-cache full-tier TWA city atomics")
    parser.add_argument("--city", action="append", help="City slug (repeatable)")
    parser.add_argument(
        "--comprehensive",
        action="store_true",
        help="Warm all plan tools for all configured cities + full location facets",
    )
    parser.add_argument("--all", action="store_true", help="Alias for --comprehensive")
    parser.add_argument(
        "--atomics-only",
        action="store_true",
        help="Warm list/rank/usage atomics only (no WKT location batches)",
    )
    parser.add_argument(
        "--locations-only",
        action="store_true",
        help="Warm WKT location batches only (requires height facet; resumes cached batches)",
    )
    parser.add_argument(
        "--locations-top-n",
        type=int,
        metavar="N",
        help="Warm WKT only for tallest N buildings (recommended for workflows; minutes not hours)",
    )
    parser.add_argument(
        "--locations-usage-type",
        help="With --locations-top-n: filter pool by usage (e.g. Non-Domestic)",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Skip atomics already in full-tier cache (locations always skip cached batches)",
    )
    parser.add_argument(
        "--no-locations",
        action="store_true",
        help="Skip WKT location batches (same as --atomics-only)",
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch even if full cache exists")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Buildings per fetch_building_locations batch (default 100)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print cache progress and exit",
    )
    args = parser.parse_args()

    if args.status:
        from mini_marie.zaha.twa_city.city_cache_status import print_status

        print_status(city_filter=(args.city or [None])[0] if args.city else None)
        return

    comprehensive = args.comprehensive or args.all
    if comprehensive:
        cities = cities_for_comprehensive_warm()
        specs = workflow_driven_warm_specs()
    else:
        cities = args.city or []
        specs = comprehensive_warm_specs() if cities else []

    if not cities:
        raise SystemExit("Provide --city <slug>, --comprehensive, or --status")

    include_atomics = not args.locations_only
    include_locations = not (args.no_locations or args.atomics_only)
    if args.atomics_only:
        include_locations = False
    if args.locations_only:
        include_atomics = False
    locations_top_n = args.locations_top_n
    if locations_top_n is not None and locations_top_n < 1:
        raise SystemExit("--locations-top-n must be >= 1")
    if locations_top_n is not None:
        include_locations = True
        if not args.comprehensive and not args.locations_only:
            include_atomics = False  # height facet already warmed; avoid full-city location grind

    results = []
    for ci, city in enumerate(cities, 1):
        loc_mode = (
            f"top_n={locations_top_n}"
            if locations_top_n is not None
            else ("full" if include_locations else "off")
        )
        print(
            f"[{ci}/{len(cities)}] Warming {city} "
            f"({'comprehensive' if comprehensive else 'city'}) "
            f"atomics={include_atomics} locations={loc_mode} ...",
            flush=True,
        )
        city_specs = specs if comprehensive else [s for s in specs if s["args"].get("city") == city]
        if not city_specs and comprehensive:
            city_specs = [s for s in comprehensive_warm_specs() if s["args"].get("city") == city]
        out = warm_city(
            city,
            city_specs or comprehensive_warm_specs(),
            include_atomics=include_atomics,
            include_locations=include_locations,
            locations_top_n=locations_top_n,
            locations_usage_type=args.locations_usage_type,
            force=args.force,
            missing_only=args.missing_only,
            batch_size=max(1, int(args.batch_size)),
        )
        results.append(out)
        print(json.dumps(out, indent=2)[:4000], flush=True)

    print(json.dumps({"comprehensive": comprehensive, "cities": results}, indent=2), flush=True)


if __name__ == "__main__":
    main()
