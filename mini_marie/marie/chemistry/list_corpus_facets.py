"""List chemistry corpus facets (query patterns × namespaces)."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from mini_marie.marie.chemistry.corpus_registry import facet_summary, list_facets


def main() -> None:
    parser = argparse.ArgumentParser(description="Chemistry corpus facet registry")
    parser.add_argument("--namespace", help="Filter by namespace")
    parser.add_argument(
        "--pattern",
        choices=["resolve", "traverse", "filter", "graph", "aggregate", "property_scan"],
    )
    parser.add_argument("--status", choices=["implemented", "partial", "planned"])
    parser.add_argument("--summary", action="store_true", help="Counts by namespace and pattern")
    args = parser.parse_args()

    if args.summary:
        print(json.dumps(facet_summary(), indent=2))
        return

    facets = list_facets(
        namespace=args.namespace,
        query_pattern=args.pattern,  # type: ignore[arg-type]
        status=args.status,  # type: ignore[arg-type]
    )
    print(json.dumps([asdict(f) for f in facets], indent=2))


if __name__ == "__main__":
    main()
