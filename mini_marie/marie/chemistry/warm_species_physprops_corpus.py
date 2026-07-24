"""Batch-warm OntoSpecies physical properties corpus."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS
from mini_marie.marie.chemistry.species_physprops_corpus import (
    DEFAULT_BATCH_SIZE,
    SpeciesPhyspropsCorpusStore,
    warm_physprops_corpus,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Warm OntoSpecies physprops corpus")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-batches", type=int, default=1)
    parser.add_argument("--delay", type=float, default=WARM_DELAY_SECONDS)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--skip-health-check", action="store_true")
    args = parser.parse_args()

    if args.health_only:
        print(json.dumps(namespace_health_ok("ontospecies"), indent=2))
        return

    if args.status:
        store = SpeciesPhyspropsCorpusStore()
        try:
            print(json.dumps(store.stats(), indent=2))
        finally:
            store.close()
        return

    max_batches = args.max_batches
    if max_batches == 0:
        max_batches = 10_000

    print(
        json.dumps(
            warm_physprops_corpus(
                batch_size=args.batch_size,
                max_batches=max_batches,
                delay_seconds=args.delay,
                show_progress=not args.no_progress,
                skip_health_check=args.skip_health_check,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
