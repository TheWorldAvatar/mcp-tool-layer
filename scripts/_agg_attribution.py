"""Aggregate per-module attribution counts from a run folder."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    score = (2 * prec * rec / (prec + rec)) if prec + rec else 0.0
    return prec, rec, score


def aggregate(root: Path) -> None:
    mods = ["steps", "chemicals", "characterisation", "cbu"]
    agg = {
        m: {
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "ext_tp": 0,
            "ext_fp": 0,
            "ext_fn": 0,
            "kg_fn": 0,
            "ext_fn_attr": 0,
            "der": 0,
            "kg_inv": 0,
            "hall": 0,
            "atoms": 0,
            "llm": 0,
            "heur": 0,
        }
        for m in mods
    }
    papers = 0
    mono = True
    for path in sorted(root.glob("????????.json")):
        if path.stem == "_overall":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        papers += 1
        mono = mono and bool(data.get("monotonicity_ok", True))
        for row in data.get("modules") or []:
            if row.get("skipped"):
                continue
            module = row["module"]
            if module not in agg:
                continue
            official = row["official"]
            extraction = row["extraction_counterfactual"]
            attr = row["attribution"]
            bucket = agg[module]
            bucket["tp"] += official["tp"]
            bucket["fp"] += official["fp"]
            bucket["fn"] += official["fn"]
            bucket["ext_tp"] += extraction["tp"]
            bucket["ext_fp"] += extraction["fp"]
            bucket["ext_fn"] += extraction["fn"]
            bucket["kg_fn"] += attr["kg_fn"]
            bucket["ext_fn_attr"] += attr["extraction_fn"]
            bucket["der"] += attr.get("derivation_fn") or 0
            bucket["kg_inv"] += attr.get("kg_invention_fp") or 0
            bucket["hall"] += attr.get("extraction_hallucination_fp") or 0
            for atom in row.get("atoms") or []:
                bucket["atoms"] += 1
                if atom.get("judge_source") == "llm":
                    bucket["llm"] += 1
                else:
                    bucket["heur"] += 1

    print(f"root={root}")
    print(f"papers={papers} monotonicity_ok={mono}")
    header = (
        f"{'module':16} {'kg_f1':>7} {'ext_f1':>7} {'dF1':>7} "
        f"{'kg_fn':>6} {'ext_fn':>7} {'der':>5} {'hall':>5} {'invent':>6} "
        f"{'llm':>5} {'heur':>5}"
    )
    print(header)
    for module in mods:
        bucket = agg[module]
        _kp, _kr, kf = f1(bucket["tp"], bucket["fp"], bucket["fn"])
        _ep, _er, ef = f1(bucket["ext_tp"], bucket["ext_fp"], bucket["ext_fn"])
        print(
            f"{module:16} {kf:7.3f} {ef:7.3f} {ef - kf:7.3f} "
            f"{bucket['kg_fn']:6d} {bucket['ext_fn_attr']:7d} {bucket['der']:5d} "
            f"{bucket['hall']:5d} {bucket['kg_inv']:6d} {bucket['llm']:5d} "
            f"{bucket['heur']:5d}"
        )
        print(
            f"  kg  tp={bucket['tp']} fp={bucket['fp']} fn={bucket['fn']} "
            f"P={_kp:.3f} R={_kr:.3f}"
        )
        print(
            f"  ext tp={bucket['ext_tp']} fp={bucket['ext_fp']} fn={bucket['ext_fn']} "
            f"P={_ep:.3f} R={_er:.3f} atoms={bucket['atoms']}"
        )


if __name__ == "__main__":
    aggregate(Path(sys.argv[1]))
