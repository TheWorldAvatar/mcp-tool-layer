#!/usr/bin/env python3
"""
Compare CCDC numbers found in current results (merged TTL) against ground truth.

Behavior:
- For each hash under evaluation/data/merged_tll/<hash>/<hash>.ttl, extract all
  ontomops:hasCCDCNumber literals from TTL.
- Map hash -> DOI using data/doi_to_hash.json (reverse mapping).
- Aggregate ground truth CCDC numbers for that DOI by reading any available files
  under earlier_ground_truth/{chemicals1,characterisation,steps,cbu}/<doi>.json.
  Ignore missing files; ignore values "N/A" or "NA".
- Write per-hash markdown files summarizing TTL set vs GT set and differences.
- Write an overall markdown table summarizing presence/equality for all hashes.

Outputs:
- evaluation/data/result/ccdc/<hash>.md
- evaluation/data/result/ccdc/_overall.md

Run:
  python -m evaluation.extraction_performance_evaluation.ccdc_number_comparison
  python evaluation/extraction_performance_evaluation/ccdc_number_comparison.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from rdflib import Graph, URIRef, Literal


ONTOMOPS_NS = "https://www.theworldavatar.com/kg/ontomops/"
HAS_CCDC_PROP = URIRef(ONTOMOPS_NS + "hasCCDCNumber")


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_ccdc(value: str) -> Optional[str]:
    s = (value or "").strip()
    if not s:
        return None
    u = s.upper()
    if u in ("N/A", "NA"):
        return None
    # Keep as-is; TTL may provide non-numeric but we primarily expect numeric strings
    return s


def _collect_ttl_ccdcs(ttl_path: Path) -> Set[str]:
    g = Graph()
    try:
        g.parse(str(ttl_path), format="turtle")
    except Exception:
        return set()
    out: Set[str] = set()
    for _s, _p, o in g.triples((None, HAS_CCDC_PROP, None)):
        if isinstance(o, Literal):
            v = _normalize_ccdc(str(o))
            if v:
                out.add(v)
    return out


def _hash_map_reverse(doi_to_hash_path: Path) -> Dict[str, str]:
    data = _read_json(doi_to_hash_path) or {}
    # reverse map: hash -> doi
    out: Dict[str, str] = {}
    for doi, hv in (data.items() if isinstance(data, dict) else []):
        hv_str = str(hv)
        if hv_str:
            out[hv_str] = str(doi)
    return out


def _collect_gt_chemicals(path: Path) -> Set[str]:
    data = _read_json(path) or {}
    out: Set[str] = set()
    for proc in (data.get("synthesisProcedures") or []):
        for step in (proc.get("steps") or []):
            for oc in (step.get("outputChemical") or []):
                v = _normalize_ccdc(str(oc.get("CCDCNumber") or ""))
                if v:
                    out.add(v)
    return out


def _collect_gt_characterisation(path: Path) -> Set[str]:
    data = _read_json(path) or {}
    out: Set[str] = set()
    for dev in (data.get("Devices") or []):
        for ch in (dev.get("Characterisation") or []):
            v = _normalize_ccdc(str(ch.get("productCCDCNumber") or ""))
            if v:
                out.add(v)
    return out


def _collect_gt_steps(path: Path) -> Set[str]:
    data = _read_json(path) or {}
    out: Set[str] = set()
    for syn in (data.get("Synthesis") or []):
        v = _normalize_ccdc(str(syn.get("productCCDCNumber") or ""))
        if v:
            out.add(v)
    return out


def _collect_gt_cbu(path: Path) -> Set[str]:
    data = _read_json(path) or {}
    out: Set[str] = set()
    for proc in (data.get("synthesisProcedures") or []):
        v = _normalize_ccdc(str(proc.get("mopCCDCNumber") or ""))
        if v:
            out.add(v)
    return out


def _collect_gt_ccdcs_for_doi(repo_root: Path, doi: str) -> Tuple[Set[str], Dict[str, Set[str]]]:
    gt_root = repo_root / "earlier_ground_truth"
    cat_to_fn = {
        "chemicals1": gt_root / "chemicals1" / f"{doi}.json",
        "characterisation": gt_root / "characterisation" / f"{doi}.json",
        "steps": gt_root / "steps" / f"{doi}.json",
        "cbu": gt_root / "cbu" / f"{doi}.json",
    }
    per_category: Dict[str, Set[str]] = {}
    for cat, p in cat_to_fn.items():
        if not p.exists():
            continue
        if cat == "chemicals1":
            s = _collect_gt_chemicals(p)
        elif cat == "characterisation":
            s = _collect_gt_characterisation(p)
        elif cat == "steps":
            s = _collect_gt_steps(p)
        else:
            s = _collect_gt_cbu(p)
        if s:
            per_category[cat] = s
    union: Set[str] = set()
    for s in per_category.values():
        union |= s
    return union, per_category


def _render_per_hash_md(doi: str, hv: str, ttl_path: Path, ttl_ccdcs: Set[str], gt_union: Set[str], gt_by_cat: Dict[str, Set[str]]) -> str:
    ttl_sorted = sorted(ttl_ccdcs)
    gt_sorted = sorted(gt_union)
    inter = set(ttl_ccdcs) & set(gt_union)
    ttl_only = sorted(set(ttl_ccdcs) - set(gt_union))
    gt_only = sorted(set(gt_union) - set(ttl_ccdcs))
    present_in_gt = set(ttl_ccdcs).issubset(gt_union)
    equal_sets = set(ttl_ccdcs) == set(gt_union)

    tp = len(inter)
    fp = len(ttl_only)
    fn = len(gt_only)
    prec = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    rec = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

    lines: List[str] = []
    lines.append(f"# CCDC Numbers Comparison - {hv}\n\n")
    lines.append(f"**DOI**: `{doi}`  \n")
    lines.append(f"**Hash**: `{hv}`  \n")
    lines.append(f"**TTL file**: `{ttl_path.as_posix()}`\n\n")

    lines.append("## Summary\n\n")
    lines.append(f"- **TTL set size**: {len(ttl_sorted)}\n")
    lines.append(f"- **GT set size (union)**: {len(gt_sorted)}\n")
    lines.append(f"- **TP**: {tp}  |  **FP**: {fp}  |  **FN**: {fn}\n")
    lines.append(f"- **Precision**: {prec:.4f}  |  **Recall**: {rec:.4f}  |  **F1**: {f1:.4f}\n")
    lines.append(f"- **All TTL numbers present in GT?**: {'Yes' if present_in_gt else 'No'}\n")
    lines.append(f"- **Exact set match?**: {'Yes' if equal_sets else 'No'}\n\n")

    lines.append("## TTL CCDC Numbers\n\n")
    lines.append("``" + "\n")
    lines.append("\n".join(ttl_sorted) if ttl_sorted else "(none)")
    lines.append("\n" + "``" + "\n\n")

    lines.append("## Ground Truth CCDC Numbers (Union)\n\n")
    lines.append("``" + "\n")
    lines.append("\n".join(gt_sorted) if gt_sorted else "(none)")
    lines.append("\n" + "``" + "\n\n")

    if gt_by_cat:
        lines.append("## Ground Truth by Category\n\n")
        for cat in sorted(gt_by_cat.keys()):
            vals = sorted(gt_by_cat[cat])
            lines.append(f"- **{cat}**: {', '.join(vals) if vals else '(none)'}\n")
        lines.append("\n")

    lines.append("## Differences\n\n")
    lines.append(f"- **TTL only**: {', '.join(ttl_only) if ttl_only else 'None'}\n")
    lines.append(f"- **GT only**: {', '.join(gt_only) if gt_only else 'None'}\n")

    return "".join(lines)


def evaluate_current() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ttl_root = repo_root / "evaluation" / "data" / "merged_tll"
    out_root = repo_root / "evaluation" / "data" / "result" / "ccdc"
    out_root.mkdir(parents=True, exist_ok=True)

    hash_to_doi = _hash_map_reverse(repo_root / "data" / "doi_to_hash.json")

    rows: List[Tuple[str, str, List[str], List[str], int, int, int, float, float, float, bool, bool]] = []

    # Iterate hashes that have merged TTL
    for hv_dir in sorted([p for p in ttl_root.iterdir() if p.is_dir()]):
        hv = hv_dir.name
        ttl_path = hv_dir / f"{hv}.ttl"
        if not ttl_path.exists():
            continue

        doi = hash_to_doi.get(hv)
        if not doi:
            # Skip if we can't relate to GT
            continue

        ttl_ccdcs = _collect_ttl_ccdcs(ttl_path)
        gt_union, gt_by_cat = _collect_gt_ccdcs_for_doi(repo_root, doi)

        inter = set(ttl_ccdcs) & set(gt_union)
        ttl_only = set(ttl_ccdcs) - set(gt_union)
        gt_only = set(gt_union) - set(ttl_ccdcs)
        tp = len(inter)
        fp = len(ttl_only)
        fn = len(gt_only)
        prec = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

        present_in_gt = ttl_ccdcs.issubset(gt_union)
        equal_sets = ttl_ccdcs == gt_union

        # Write per-hash MD
        md = _render_per_hash_md(doi, hv, ttl_path, ttl_ccdcs, gt_union, gt_by_cat)
        per_hash_md_path = out_root / f"{hv}.md"
        per_hash_md_path.write_text(md, encoding="utf-8")
        print(f"Wrote per-hash MD: {per_hash_md_path.resolve()}\n")

        rows.append((hv, doi, sorted(ttl_ccdcs), sorted(gt_union), tp, fp, fn, prec, rec, f1, present_in_gt, equal_sets))

    # Overall MD
    lines: List[str] = []
    lines.append("# CCDC Numbers Comparison - Overall\n\n")
    lines.append("| Hash | DOI | |TTL| | |GT| | TP | FP | FN | Precision | Recall | F1 | TTL⊆GT | Equal? |\n")
    lines.append("| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n")

    total_ttl = 0
    total_gt = 0
    total_tp = 0
    total_fp = 0
    total_fn = 0
    sum_prec = 0.0
    sum_rec = 0.0
    sum_f1 = 0.0

    for hv, doi, ttl_list, gt_list, tp, fp, fn, prec, rec, f1, subset_ok, eq_ok in rows:
        ttl_disp = ", ".join(ttl_list) if ttl_list else "(none)"
        gt_disp = ", ".join(gt_list) if gt_list else "(none)"
        lines.append(
            f"| {hv} | {doi} | {len(ttl_list)} | {ttl_disp} | {len(gt_list)} | {gt_disp} | {tp} | {fp} | {fn} | {prec:.4f} | {rec:.4f} | {f1:.4f} | {'Yes' if subset_ok else 'No'} | {'Yes' if eq_ok else 'No'} |\n"
        )

        total_ttl += len(ttl_list)
        total_gt += len(gt_list)
        total_tp += tp
        total_fp += fp
        total_fn += fn
        sum_prec += prec
        sum_rec += rec
        sum_f1 += f1

    micro_prec = (total_tp / (total_tp + total_fp)) if (total_tp + total_fp) > 0 else 0.0
    micro_rec = (total_tp / (total_tp + total_fn)) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = (2 * micro_prec * micro_rec / (micro_prec + micro_rec)) if (micro_prec + micro_rec) > 0 else 0.0
    n_hashes = len(rows)
    macro_prec = (sum_prec / n_hashes) if n_hashes > 0 else 0.0
    macro_rec = (sum_rec / n_hashes) if n_hashes > 0 else 0.0
    macro_f1 = (sum_f1 / n_hashes) if n_hashes > 0 else 0.0

    lines.append("\n## Totals / Aggregates\n\n")
    lines.append(f"- Total TTL count: {total_ttl}\n")
    lines.append(f"- Total GT count (union): {total_gt}\n")
    lines.append(f"- Total TP: {total_tp}  |  Total FP: {total_fp}  |  Total FN: {total_fn}\n")
    lines.append(f"- Micro Precision: {micro_prec:.4f}  |  Micro Recall: {micro_rec:.4f}  |  Micro F1: {micro_f1:.4f}\n")
    lines.append(f"- Macro Precision: {macro_prec:.4f}  |  Macro Recall: {macro_rec:.4f}  |  Macro F1: {macro_f1:.4f}\n")

    overall_md_path = out_root / "_overall.md"
    overall_md_path.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote overall MD: {overall_md_path.resolve()}")


def main() -> int:
    evaluate_current()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


