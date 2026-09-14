"""Run official extraction-vs-KG attribution on s1-s4 yd30 score packs.

Pack-level reporting: Steps F1 in tables must use pre-extension-merge
scores (s*p30 0.847/0.838/0.836), not this run's merged-graph
0.843/0.840/0.843/0.853. Char graph F1 must use s*yc30, not this run's
0.788/0.738/0.771/0.809. This script's attribution/_overall.md still
records the yd30 merged graphs; that is the source of Extraction F1
upper limits, not the published Steps/Char left-hand numbers.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.kg_building.attribution.run import OFFICIAL_MODULES, attribute_run

RUNS = ROOT / "scenarios" / "mops" / "runs"
JOBS = (
    ("s1", "20260911_131245_s1yd30"),
    ("s2", "20260911_131452_s2yd30"),
    ("s3", "20260911_131705_s3yd30"),
    ("s4", "20260911_131918_s4yd30"),
)


def shards_for(pack: str) -> list[Path]:
    pattern = re.compile(rf"_{pack}yd[a-o]$")
    found = []
    for path in RUNS.iterdir():
        if path.is_dir() and pattern.search(path.name):
            found.append(path)
    return sorted(found)


def main() -> None:
    for pack, folder in JOBS:
        root = RUNS / folder
        hints = shards_for(pack)
        hashes = json.loads((root / "score_manifest.json").read_text(encoding="utf-8"))["hashes"]
        print(f"===== {pack} hints={len(hints)} papers={len(hashes)} =====", flush=True)
        if len(hints) < 15:
            raise SystemExit(f"{pack} expected 15 shards, got {len(hints)}")
        overall = attribute_run(
            hashes,
            pred_root=root / "merged",
            hint_runs=hints,
            out_dir=root / "attribution",
            scores_root=root / "scores",
            modules=OFFICIAL_MODULES,
            skip_existing=True,
            cbu_derivation=True,
        )
        print(
            pack,
            "monotonicity_ok",
            overall["monotonicity_ok"],
            flush=True,
        )
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
