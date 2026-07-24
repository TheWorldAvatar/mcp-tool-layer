"""Batch-warm OntoSpecies corpus (all species + searchable names)."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS
from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.species_corpus import (
    DEFAULT_BATCH_SIZE,
    SpeciesCorpusStore,
    count_species_remote,
    warm_species_corpus,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Warm OntoSpecies corpus (species + labels/IUPAC/SMILES/…) in batches"
    )
    parser.add_argument("--status", action="store_true", help="Show corpus stats and warm progress")
    parser.add_argument("--count-remote", action="store_true", help="COUNT species on live endpoint")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--max-batches",
        type=int,
        default=1,
        help="Batches to run this invocation (default 1; use 0 for until complete)",
    )
    parser.add_argument("--offset", type=int, help="Start offset (default: resume from warm state)")
    parser.add_argument("--delay", type=float, default=WARM_DELAY_SECONDS)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Probe ontospecies endpoint health and exit",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Skip ASK health probe before each batch",
    )
    args = parser.parse_args()

    if args.health_only:
        print(json.dumps(namespace_health_ok("ontospecies"), indent=2))
        return

    if args.count_remote:
        print(json.dumps({"species_count": count_species_remote()}, indent=2))
        return

    if args.status:
        store = SpeciesCorpusStore()
        try:
            print(json.dumps(store.stats(), indent=2))
        finally:
            store.close()
        return

    max_batches = args.max_batches
    if max_batches == 0:
        max_batches = 10_000

    summary = warm_species_corpus(
        batch_size=args.batch_size,
        max_batches=max_batches,
        delay_seconds=args.delay,
        offset=args.offset,
        show_progress=not args.no_progress,
        skip_health_check=args.skip_health_check,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
