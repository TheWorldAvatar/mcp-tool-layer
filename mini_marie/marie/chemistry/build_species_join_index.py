"""Build OntoSpecies competency join tables from warmed corpus facets (no SPARQL)."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore, build_species_join_tables


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build corpus_species_*_enriched join tables for offline MQ joins"
    )
    parser.add_argument("--status", action="store_true", help="Show join table row counts")
    parser.add_argument(
        "--build",
        action="store_true",
        help="Rebuild all join tables from corpus_species_* source tables",
    )
    args = parser.parse_args()

    if args.build or not args.status:
        print(json.dumps(build_species_join_tables(), indent=2))
        return

    store = SpeciesJoinStore()
    try:
        print(json.dumps(store.stats(), indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
