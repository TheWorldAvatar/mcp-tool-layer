"""Build derived formula index from species names corpus."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.species_formula_corpus import SpeciesFormulaStore, build_formula_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build corpus_species_formula from names corpus")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--build", action="store_true", help="Rebuild formula index from names")
    args = parser.parse_args()

    if args.build or not args.status:
        print(json.dumps(build_formula_index(), indent=2))
        return

    store = SpeciesFormulaStore()
    try:
        print(json.dumps(store.stats(), indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
