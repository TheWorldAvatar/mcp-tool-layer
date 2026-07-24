"""Report chemistry cache status and full-space coverage."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.chemistry_cache import ChemistryCache, db_path
from mini_marie.marie.chemistry.warm_option_catalog import coverage_report, list_dimensions


def main() -> None:
    parser = argparse.ArgumentParser(description="Chemistry cache status")
    parser.add_argument("--coverage", action="store_true", help="Full-space option coverage")
    parser.add_argument("--list-dimensions", action="store_true", help="List warm catalog dimensions")
    parser.add_argument("--namespace", help="Filter coverage to namespace")
    parser.add_argument("--tool", help="Filter coverage to tool")
    parser.add_argument("--dimension", help="Filter coverage to catalog dimension id")
    args = parser.parse_args()

    if args.list_dimensions:
        print(json.dumps(list_dimensions(), indent=2))
        return

    cache = ChemistryCache()
    try:
        if args.coverage:
            report = coverage_report(
                has_full=cache.has_full,
                namespace=args.namespace,
                tool=args.tool,
                dimension_id=args.dimension,
            )
            report["db_path"] = str(db_path())
            print(json.dumps(report, indent=2))
            return

        stats = cache.stats()
        cur = cache._conn.execute(
            """
            SELECT namespace, mode, status, COUNT(*) AS n, SUM(row_count) AS rows
            FROM atomic_calls GROUP BY namespace, mode, status ORDER BY namespace, mode
            """
        )
        by_ns = [dict(r) for r in cur]
        report = {"db_path": str(db_path()), "stats": stats, "by_namespace": by_ns}
        print(json.dumps(report, indent=2))
    finally:
        cache.close()


if __name__ == "__main__":
    main()
