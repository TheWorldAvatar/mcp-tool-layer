#!/usr/bin/env python3
"""
Scan all hash folders under data/, inspect ontomops_output/*.ttl, and report files
that have no CCDC number or have CCDC number set to "N/A".

Outputs:
  - Prints a tabular summary to stdout
  - Writes CSV to evaluation/ccdc_number_result.csv with columns:
      hash, ttl_file, status

Usage:
  python -m evaluation.ccdc_number_result
  python evaluation/ccdc_number_result.py
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from typing import List, Tuple

from rdflib import Graph, URIRef, Literal


ONTOMOPS_NS = "https://www.theworldavatar.com/kg/ontomops/"
HAS_CCDC_PROP = URIRef(ONTOMOPS_NS + "hasCCDCNumber")


def find_hash_dirs(data_dir: Path) -> List[Path]:
    """Return candidate hash directories under data/. Skip known non-hash folders."""
    if not data_dir.exists():
        return []
    skip = {"ontologies", "log"}
    out: List[Path] = []
    for child in sorted(data_dir.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith(".") or name in skip:
            continue
        # Hash directories are typically 8-char hex strings, but allow all for robustness
        out.append(child)
    return out


def extract_ccdc_numbers(ttl_path: Path) -> List[str]:
    """Parse a TTL file and return all literal values for ontomops:hasCCDCNumber."""
    g = Graph()
    try:
        g.parse(str(ttl_path), format="turtle")
    except Exception as e:
        print(f"[WARN] Failed to parse {ttl_path}: {e}")
        return []

    values: List[str] = []
    for s, p, o in g.triples((None, HAS_CCDC_PROP, None)):
        if isinstance(o, Literal):
            values.append(str(o).strip())
    return values


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = repo_root / "data"
    report_path = repo_root / "evaluation" / "ccdc_number_result.csv"

    missing: List[Tuple[str, str, str]] = []  # (hash, ttl_file, status)

    for hash_dir in find_hash_dirs(data_dir):
        ontomops_out = hash_dir / "ontomops_output"
        if not ontomops_out.exists() or not ontomops_out.is_dir():
            continue
        for ttl_file in sorted(ontomops_out.glob("*.ttl")):
            values = extract_ccdc_numbers(ttl_file)
            if not values:
                missing.append((hash_dir.name, ttl_file.name, "missing"))
                continue
            # If any value is not N/A, consider it present
            normalized = [v.strip().upper() for v in values]
            if all(v == "N/A" for v in normalized):
                missing.append((hash_dir.name, ttl_file.name, "N/A"))

    # Print summary
    if not missing:
        print("All ontomops_output TTLs contain a non-N/A CCDC number.")
    else:
        print("hash\tttl_file\tstatus")
        for h, f, s in missing:
            print(f"{h}\t{f}\t{s}")

    # Write CSV
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["hash", "ttl_file", "status"])  # header
            for row in missing:
                w.writerow(list(row))
        print(f"\nWrote report: {report_path}")
    except Exception as e:
        print(f"[WARN] Failed to write CSV report: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


