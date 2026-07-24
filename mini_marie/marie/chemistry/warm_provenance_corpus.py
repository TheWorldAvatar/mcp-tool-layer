"""Batch-warm OntoProvenance corpus."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.provenance_corpus import ProvenanceCorpusStore, warm_provenance_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Warm OntoProvenance corpus")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--skip-health-check", action="store_true")
    args = parser.parse_args()

    if args.health_only:
        print(json.dumps(namespace_health_ok("ontoprovenance"), indent=2))
        return

    if args.status:
        store = ProvenanceCorpusStore()
        try:
            print(json.dumps(store.stats(), indent=2))
        finally:
            store.close()
        return

    print(json.dumps(warm_provenance_corpus(skip_health_check=args.skip_health_check), indent=2))


if __name__ == "__main__":
    main()
