#!/usr/bin/env python3
"""Dump gold-vs-pred mismatches for medical scoring analysis."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def _norm(v: str | None) -> str:
    text = (v or "").strip()
    if text in {"", "-", "–", "—", "nan", "None"}:
        return ""
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    gold = {
        r["_doi_hash"]: r
        for r in csv.DictReader(args.gold.open(encoding="utf-8-sig"))
    }
    pred = {
        r["_doi_hash"]: r
        for r in csv.DictReader(args.pred.open(encoding="utf-8-sig"))
    }
    sample_g = next(iter(gold.values()))
    sample_p = next(iter(pred.values()))
    cols = [
        c
        for c in sample_g
        if c not in {"_ttl_file", "_doi_hash"} and c in sample_p
    ]

    miss: list[tuple[str, str, str, str, str]] = []
    for h, g in gold.items():
        p = pred.get(h)
        name = (g.get("Name") or "").strip()
        if not p:
            miss.append((h, name, "MISSING_PRED", "", ""))
            continue
        for c in cols:
            gv, pv = _norm(g.get(c)), _norm(p.get(c))
            if gv != pv:
                miss.append((h, name, c, gv, pv))

    lines = [f"strict_mismatches\t{len(miss)}", "BY_COLUMN"]
    for c, k in Counter(m[2] for m in miss).most_common():
        lines.append(f"{k}\t{c}")
    lines.append("ALL")
    for h, name, c, gv, pv in miss:
        lines.append(f"{name}\t{h}\t{c}\tgold={gv}\tpred={pv}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ({len(miss)} mismatches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
