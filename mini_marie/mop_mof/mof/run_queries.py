"""Run MOF TWA exploration queries against the remote Ontop endpoint."""

from __future__ import annotations

import argparse
import json

from mini_marie.mop_mof.mof.mof_operations import (
    format_results_as_tsv,
    get_source_database_stats,
    get_top_tobassco_co2_uptake,
    run_query_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the OntoMOFs TWA SPARQL endpoint")
    parser.add_argument(
        "--query",
        choices=["stats", "co2", "01", "02", "03", "04", "05"],
        default="stats",
        help="Which query to run",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of TSV")
    args = parser.parse_args()

    if args.query == "stats":
        results = get_source_database_stats()
    elif args.query == "co2":
        results = get_top_tobassco_co2_uptake()
    else:
        mapping = {
            "01": "01_count_tobassco_co2.sparql",
            "02": "02_top_co2_valid_pore_geometry.sparql",
            "03": "03_top_topologies_tobassco.sparql",
            "04": "04_large_pore_co2_candidates.sparql",
            "05": "05_co2_uptake_stats_tobassco.sparql",
        }
        results = run_query_file(mapping[args.query])

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_results_as_tsv(results))


if __name__ == "__main__":
    main()
