"""Score extraction hints against official step atoms and attribute residual errors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from evaluation.error_attribution import attribute_papers


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attribute official step-score errors to extraction vs KG building"
    )
    parser.add_argument("--hash", action="append", dest="hashes", required=True)
    parser.add_argument("--pred-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--gt-root", type=Path, default=Path("full_ground_truth/steps"))
    parser.add_argument("--doi-map", type=Path, default=Path("data/doi_to_hash.json"))
    parser.add_argument("--model", default="openai/gpt-5.6")
    parser.add_argument("--synonym-model", default="gpt-4o")
    parser.add_argument(
        "--paper-root",
        type=Path,
        default=Path("scenarios/mops/datasets/eval30_md"),
        help="Trimmed paper MD root (eval30_md/{hash}/{hash}_text.md + _si.md)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Dump atoms only; no hint-support LLM")
    args = parser.parse_args()

    payload = attribute_papers(
        args.hashes,
        pred_root=args.pred_root,
        runtime_root=args.runtime_root,
        gt_root=args.gt_root,
        doi_map=args.doi_map,
        out_root=args.out_root,
        judge_model=args.model,
        synonym_model=args.synonym_model,
        dry_run=args.dry_run,
        paper_root=args.paper_root,
    )
    official = (payload.get("overall") or {}).get("official") or {}
    share = (payload.get("overall") or {}).get("share") or {}
    print(
        json.dumps(
            {
                "out_root": str(args.out_root),
                "official": official,
                "share": share,
                "incidents": payload.get("incident_summary"),
                "extraction_informative_recall": (payload.get("overall") or {}).get(
                    "extraction_informative_recall"
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
