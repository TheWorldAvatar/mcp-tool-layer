#!/usr/bin/env python3
"""
Build evaluation/medical/medical_cases_latest.csv by discovering one TTL per Ground-Truth case under data/<hash>/.

Priority (newest mtime wins within each tier):
  1) data/<hash>/medical_output/*.ttl excluding top.ttl
  2) data/<hash>/exports/MedicalCase*.ttl (prefer filenames without ``[...]``)
  3) data/<hash>/memory/MedicalCase*.ttl (same preference)
  4) data/<hash>/iteration_1.ttl
  5) data/<hash>/medical_output/top.ttl or memory/top.ttl (fallback; row will be sparse)

Uses the same extraction as medical_ttl_to_csv_sparql (2-hop literals, schema -> column map).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

# Repo root = parent of scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import medical_ttl_to_csv_sparql as _ttl_csv  # noqa: E402

MED_NS = _ttl_csv.MED_NS
RDF_NS = _ttl_csv.RDF_NS
_build_pred_to_reference_headers = _ttl_csv._build_pred_to_reference_headers
_load_headers_from_reference_csv = _ttl_csv._load_headers_from_reference_csv
extract_row_from_ttl = _ttl_csv.extract_row_from_ttl
write_csv = _ttl_csv.write_csv


def _pick_ttl_for_hash(case_root: Path) -> Optional[Path]:
    if not case_root.is_dir():
        return None

    def newest(paths: List[Path]) -> Optional[Path]:
        existing = [p for p in paths if p.is_file()]
        if not existing:
            return None
        return max(existing, key=lambda p: p.stat().st_mtime)

    mo = case_root / "medical_output"
    if mo.is_dir():
        cands = [p for p in mo.glob("*.ttl") if p.name.lower() != "top.ttl"]
        hit = newest(cands)
        if hit:
            return hit

    # Prefer live memory graph over exports (exports may lack MedicalCase typing).
    for sub, glob_pat, prefer_no_bracket in (
        (case_root / "memory", "MedicalCase*.ttl", True),
        (case_root / "exports", "MedicalCase*.ttl", True),
    ):
        if not sub.is_dir():
            continue
        cands = list(sub.glob(glob_pat))
        if prefer_no_bracket:
            plain = [p for p in cands if "[" not in p.name and not p.name.lower().startswith("top_")]
            hit = newest(plain)
            if hit:
                return hit
        hit = newest([p for p in cands if not p.name.lower().startswith("top_")])
        if hit:
            return hit

    it1 = case_root / "iteration_1.ttl"
    if it1.is_file():
        return it1

    for fallback in (mo / "top.ttl", case_root / "memory" / "top.ttl"):
        if fallback.is_file():
            return fallback

    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Build medical_cases_latest.csv from data/<hash> TTL discovery.")
    ap.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    ap.add_argument(
        "--ground-truth",
        type=Path,
        default=REPO_ROOT / "medical_case" / "2026-03-18_SR_Testcase1_GroundTruth.csv",
        help="Used for _doi_hash list (row 1..n) and reference headers (row 0).",
    )
    ap.add_argument(
        "--schema-ttl",
        type=Path,
        default=REPO_ROOT / "medical_case" / "medical_case_schema_de_non_flat_v3.ttl",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "evaluation" / "medical" / "medical_cases_latest.csv",
    )
    ap.add_argument(
        "--case-literal-hops",
        type=int,
        default=2,
        help="Pass-through to extract_row_from_ttl (default 2 for non-flat).",
    )
    args = ap.parse_args()

    gt_path = args.ground_truth
    if not gt_path.is_file():
        print(f"Missing ground truth: {gt_path}", file=sys.stderr)
        return 2
    if not args.schema_ttl.is_file():
        print(f"Missing schema: {args.schema_ttl}", file=sys.stderr)
        return 2

    reference_headers = _load_headers_from_reference_csv(gt_path, header_row_index=0)
    pred_to_header, canonical_columns = _build_pred_to_reference_headers(
        args.schema_ttl, reference_headers
    )

    case_query = f"""
PREFIX rdf: <{RDF_NS}>
SELECT ?case WHERE {{
  ?case rdf:type <{MED_NS}MedicalCase> .
}}
"""

    data_dir = args.data_dir.resolve()
    rows = []
    missing = []

    import csv as csv_module

    with gt_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv_module.DictReader(f)
        for gt_row in reader:
            h = (gt_row.get("_doi_hash") or "").strip()
            if not h:
                continue
            case_root = data_dir / h
            ttl_path = _pick_ttl_for_hash(case_root)
            if ttl_path is None:
                missing.append(h)
                print(f"[WARN] No TTL found for hash {h} under {case_root}", file=sys.stderr)
                continue
            try:
                rows.append(
                    extract_row_from_ttl(
                        ttl_path,
                        data_dir=data_dir,
                        case_query=case_query,
                        pred_to_header=pred_to_header,
                        canonical_columns=canonical_columns,
                        missing_placeholder="-",
                        case_literal_hops=max(1, int(args.case_literal_hops)),
                    )
                )
                print(f"[OK] {h} <- {ttl_path.relative_to(data_dir)}")
            except Exception as e:
                print(f"[ERR] {h} {ttl_path}: {e}", file=sys.stderr)
                missing.append(h)

    if not rows:
        print("No rows extracted.", file=sys.stderr)
        return 3

    write_csv(rows, output_path=args.output, canonical_columns=canonical_columns)
    print(f"[OK] Wrote {len(rows)} row(s) -> {args.output}")
    if missing:
        print(f"[WARN] Missing or failed hashes: {missing}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
