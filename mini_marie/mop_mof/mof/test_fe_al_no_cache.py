"""
Fe / Al — Q2 by source database, remote SPARQL only (no cache read/write).

Uses mof_competency_operations directly, not CompetencyCache.
"""

from __future__ import annotations

import sys
import time

from mini_marie.mop_mof.mof import mof_competency_operations as co


def run_metal(metal: str, *, limit: int | None) -> int:
    label = "probe LIMIT 10" if limit == 10 else "full (no LIMIT)"
    print(f"\n--- {metal} | {label} | remote only ---", flush=True)
    t0 = time.perf_counter()
    try:
        rows = co.get_mofs_by_metal(metal, list_sources=True, limit=limit)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
        return 1
    wall = round((time.perf_counter() - t0) * 1000)
    print(f"status=pass rows={len(rows)} wall_ms={wall}", flush=True)
    if not rows:
        print("(empty result)", flush=True)
        return 0
    total = 0
    for row in rows:
        c = int(row.get("count") or 0)
        total += c
        print(f"  {row.get('sourcedb')}: {row.get('count')}", flush=True)
    print(f"  sum(count)={total}", flush=True)
    return 0


def main() -> int:
    print("MOF Q2 by database — Fe & Al — NO CACHE (direct execute_sparql)")
    rc = 0
    for metal in ("Fe", "Al"):
        # Full remote answer (authoritative for list_sources; ~7 groups)
        rc |= run_metal(metal, limit=None)
        # What online probe tier would do with LIMIT 10 on the grouped query
        rc |= run_metal(metal, limit=10)
    return rc


if __name__ == "__main__":
    sys.exit(main())
