"""One-shot backfill: pKa provenance, zeolite guest props, species references."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.provenance_corpus import warm_provenance_corpus
from mini_marie.marie.chemistry.species_pka_corpus import refresh_provenance_corpus
from mini_marie.marie.chemistry.zeolite_corpus import backfill_zeolite_properties


def main() -> None:
    parser = argparse.ArgumentParser(description="Run chemistry corpus backfills")
    parser.add_argument("--pka-provenance", action="store_true")
    parser.add_argument("--zeolite-props", action="store_true")
    parser.add_argument("--provenance-refs", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    run_all = args.all or not (args.pka_provenance or args.zeolite_props or args.provenance_refs)
    report = {}

    if run_all or args.provenance_refs:
        report["provenance_refs"] = warm_provenance_corpus()
    if run_all or args.pka_provenance:
        report["pka_provenance"] = refresh_provenance_corpus(
            batch_size=args.batch_size, max_batches=0, delay_seconds=args.delay
        )
    if run_all or args.zeolite_props:
        report["zeolite_props"] = backfill_zeolite_properties(
            batch_size=min(args.batch_size, 25), max_batches=0, delay_seconds=args.delay
        )

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
