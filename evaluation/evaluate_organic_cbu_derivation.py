"""
Evaluate organic CBU derivation outputs by checking whether each derived formula
exists among the set of organic CBUs associated with the DOI in
data/ontologies/full_cbus_with_canonical_smiles_updated.csv.

Rules:
- We DO NOT attempt exact per-entity mapping; we only check membership in the
  set of organic CBUs for that DOI.
- The CSV remains unchanged. We normalize DOI formats to match CSV entries.
- We normalize formulas for robust matching (strip brackets/spaces, upper-case).

Inputs:
- data/doi_to_hash.json (DOI→hash mapping)
- data/<hash>/cbu_derivation/structured/*.txt (derived formula-only outputs)
- data/ontologies/full_cbus_with_canonical_smiles_updated.csv (ground truth)

Usage:
  python -m evaluation.organic_cbu_derivation                # evaluate all DOIs in mapping
  python -m evaluation.organic_cbu_derivation --file <DOI|hash>   # evaluate one DOI/hash
  python -m evaluation.organic_cbu_derivation --verbose

Verbose mode will additionally include:
- ground_truth_formulas_from_csv (original strings)
- ground_truth_formulas_normalized (used for matching)
- agent_derived_formulas (normalized list)
- agent_derived_details (per-species label and raw/normalized formulas)
"""

import os
import re
import csv
import json
import argparse
from typing import Dict, List, Set, Tuple


def normalize_doi_for_csv(doi_like: str) -> str:
    """Convert agent DOI-like strings to canonical '10.xxxx/...' form.
    Examples:
      '10.1021_acs.cgd.6b00306' → '10.1021/acs.cgd.6b00306'
      '10.1021.acs.chemmater.0c01965' → '10.1021/acs.chemmater.0c01965'
    """
    s = (doi_like or "").strip()
    if not s:
        return s
    # Replace first underscore between prefix and suffix as '/'
    if "_" in s:
        parts = s.split("_", 1)
        if parts[0].startswith("10."):
            return f"{parts[0]}/{parts[1]}"
    # If no '/', insert a slash after the 10.<digits> prefix separator
    m = re.match(r"^(10\.\d+)[\._](.+)$", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return s


def normalize_formula(formula: str) -> str:
    """Normalize formula strings to compare robustly.
    - Remove brackets and whitespace
    - Uppercase letters
    """
    if formula is None:
        return ""
    s = formula.strip()
    # Remove brackets and spaces
    s = re.sub(r"[\[\]\(\)\s]", "", s)
    return s.upper()


def load_mapping(path: str = os.path.join("data", "doi_to_hash.json")) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def collect_ground_truth_for_doi(csv_path: str, canonical_doi: str) -> Set[str]:
    """Return normalized set of organic formulas for the DOI from the CSV."""
    gt: Set[str] = set()
    if not os.path.exists(csv_path):
        return gt
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("category") or "").strip() != "Organic":
                continue
            doi_list = (row.get("kg_dois") or "")
            # Split by semicolon, strip spaces
            dois = [d.strip() for d in doi_list.split(";") if d.strip()]
            if canonical_doi in dois:
                formula = (row.get("formula") or "").strip()
                if formula:
                    gt.add(normalize_formula(formula))
    return gt


def collect_ground_truth_for_doi_verbose(csv_path: str, canonical_doi: str) -> Tuple[Set[str], List[str]]:
    """Return (normalized_set, original_formulas_list) for the DOI from the CSV."""
    gt_norm: Set[str] = set()
    gt_original: List[str] = []
    if not os.path.exists(csv_path):
        return gt_norm, gt_original
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("category") or "").strip() != "Organic":
                continue
            doi_list = (row.get("kg_dois") or "")
            dois = [d.strip() for d in doi_list.split(";") if d.strip()]
            if canonical_doi in dois:
                formula = (row.get("formula") or "").strip()
                if formula:
                    gt_original.append(formula)
                    gt_norm.add(normalize_formula(formula))
    return gt_norm, gt_original


def _structured_dir(hash_value: str) -> str:
    # Prefer new organic_cbu_derivation, fallback to legacy cbu_derivation
    d1 = os.path.join("data", hash_value, "organic_cbu_derivation", "structured")
    if os.path.exists(d1):
        return d1
    return os.path.join("data", hash_value, "cbu_derivation", "structured")


def collect_derived_formulas(hash_value: str) -> List[Tuple[str, str]]:
    """Collect (safe_label, normalized_formula) from structured outputs for a hash."""
    out_dir = _structured_dir(hash_value)
    results: List[Tuple[str, str]] = []
    if not os.path.exists(out_dir):
        return results
    for name in os.listdir(out_dir):
        if not name.endswith(".txt"):
            continue
        p = os.path.join(out_dir, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if raw and raw != "Ignore":
                results.append((os.path.splitext(name)[0], normalize_formula(raw)))
        except Exception:
            continue
    return results


def collect_derived_details(hash_value: str) -> List[Dict[str, str]]:
    """Collect detailed derived outputs: [{safe_label, formula_raw, formula_normalized}]."""
    out_dir = _structured_dir(hash_value)
    details: List[Dict[str, str]] = []
    if not os.path.exists(out_dir):
        return details
    for name in os.listdir(out_dir):
        if not name.endswith(".txt"):
            continue
        p = os.path.join(out_dir, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = (f.read() or "").strip()
            if not raw or raw == "Ignore":
                continue
            details.append({
                "safe_label": os.path.splitext(name)[0],
                "formula_raw": raw,
                "formula_normalized": normalize_formula(raw),
            })
        except Exception:
            continue
    return details


def evaluate_one(doi_like: str, hash_value: str, csv_path: str, verbose: bool = False) -> Dict:
    canonical = normalize_doi_for_csv(doi_like)
    if verbose:
        gt, gt_original = collect_ground_truth_for_doi_verbose(csv_path, canonical)
        derived_details = collect_derived_details(hash_value)
        derived = [(d["safe_label"], d["formula_normalized"]) for d in derived_details]
    else:
        gt = collect_ground_truth_for_doi(csv_path, canonical)
        derived = collect_derived_formulas(hash_value)
    total = len(derived)
    matched = 0
    mismatches: List[Dict] = []
    for safe_label, formula in derived:
        if formula in gt:
            matched += 1
        else:
            mismatches.append({"label": safe_label, "formula": formula})
    result = {
        "doi": doi_like,
        "canonical_doi": canonical,
        "hash": hash_value,
        "derived_count": total,
        "gt_count": len(gt),
        "matched": matched,
        "accuracy": (matched / total) if total > 0 else None,
        "mismatches": mismatches if verbose else None,
    }
    if verbose:
        result["ground_truth_formulas_from_csv"] = gt_original
        result["ground_truth_formulas_normalized"] = sorted(list(gt))
        result["agent_derived_formulas"] = [f for (_lbl, f) in derived]
        result["agent_derived_details"] = derived_details
    return result


def main():
    ap = argparse.ArgumentParser(description="Evaluate organic CBU derivation outputs")
    ap.add_argument("--file", type=str, help="Specific DOI or hash to evaluate", default=None)
    ap.add_argument("--verbose", action="store_true", help="Include mismatch details")
    args = ap.parse_args()

    csv_path = os.path.join("data", "ontologies", "full_cbus_with_canonical_smiles_updated.csv")
    mapping = load_mapping()

    eval_items: List[Tuple[str, str]] = []  # (doi_like, hash)
    if args.file:
        # Resolve hash via mapping if possible; otherwise assume input is hash
        doi_like = None
        if args.file in mapping:
            doi_like = args.file
            hv = mapping[doi_like]
        else:
            # Try find DOI from mapping by hash
            hv = args.file
            for d, h in mapping.items():
                if h == hv:
                    doi_like = d
                    break
            doi_like = doi_like or hv
        eval_items.append((doi_like, hv))
    else:
        # Evaluate all in mapping
        for d, h in mapping.items():
            eval_items.append((d, h))

    reports = []
    overall_derived = 0
    overall_matched = 0
    for d, h in eval_items:
        rep = evaluate_one(d, h, csv_path, verbose=args.verbose)
        reports.append(rep)
        overall_derived += rep.get("derived_count") or 0
        overall_matched += rep.get("matched") or 0

    summary = {
        "items": reports,
        "overall_derived": overall_derived,
        "overall_matched": overall_matched,
        "overall_accuracy": (overall_matched / overall_derived) if overall_derived > 0 else None,
    }

    # Write report
    out_dir = os.path.join("evaluation")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "organic_cbu_derivation_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2))

    # Print concise summary
    print(f"Evaluated {len(eval_items)} items: derived={overall_derived}, matched={overall_matched}, accuracy={summary['overall_accuracy']}")
    print(f"Report saved to: {out_path}")


if __name__ == "__main__":
    main()


