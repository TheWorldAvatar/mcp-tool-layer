import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any, Set
from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse


def _normalize(s: Any) -> str:
    """Normalize a value to lowercase string, treating N/A as empty."""
    val = str(s or "").strip()
    if val.upper() in ["N/A", "NA", ""]:
        return ""
    # Normalize whitespace: replace multiple spaces/tabs with single space
    import re
    val = re.sub(r'\s+', ' ', val)
    # Normalize comma-space patterns for consistency
    val = re.sub(r',\s*', ', ', val)
    return val.lower().strip()


def _is_valid(s: Any) -> bool:
    """Check if a value is valid (not N/A or empty)."""
    return _normalize(s) != ""


def evaluate_current() -> None:
    GT_ROOT = Path("earlier_ground_truth/characterisation")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/result/characterisation")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "characterisation.json"
        if not doi or not res_path.exists():
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        gt_list: List[str] = []
        for device in gt.get("Devices", []):
            for char in device.get("Characterisation", []):
                gt_list.append(char.get("productCCDCNumber") or "")

        res_list: List[str] = []
        for ch in res.get("characterisations", []):
            res_list.append(ch.get("productCCDCNumber") or "")

        tp, fp, fn = score_lists(gt_list, res_list)
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((hv, (tp, fp, fn, prec, rec, f1)))

        report = render_report(f"Characterisation Scoring - {hv}", [(hv, (tp, fp, fn, prec, rec, f1))])
        (OUT_ROOT / f"{hv}.md").write_text(report, encoding="utf-8")

    overall = render_report("Characterisation Scoring - Overall", rows)
    (OUT_ROOT / "_overall.md").write_text(overall, encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_previous() -> None:
    GT_ROOT = Path("earlier_ground_truth/characterisation")
    PREV_ROOT = Path("previous_work/characterisation")
    OUT_ROOT = Path("evaluation/data/result/characterisation_previous")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []

    for jf in sorted(PREV_ROOT.glob("*.json")):
        doi = jf.stem
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue

        try:
            gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
            pred_obj = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Fine-grained scoring: count all fields individually
        tp = fp = fn = 0
        
        # Collect characterisations by CCDC (use product name as fallback key if CCDC is N/A)
        def _get_chars(data: Any) -> Dict[str, Any]:
            chars = {}
            for dev in (data or {}).get("Devices", []) or []:
                for ch in (dev or {}).get("Characterisation", []) or []:
                    ccdc = str((ch or {}).get("productCCDCNumber") or "").strip()
                    # Use first product name as key if CCDC is N/A
                    if not ccdc or ccdc.upper() in ["N/A", "NA"]:
                        names = (ch or {}).get("productNames") or []
                        if names and len(names) > 0:
                            ccdc = f"NAME:{str(names[0]).strip()}"
                    if ccdc and ccdc.upper() not in ["N/A", "NA"]:
                        chars[ccdc] = ch
            return chars
        
        gt_chars = _get_chars(gt_obj)
        pr_chars = _get_chars(pred_obj)
        
        all_keys = set(gt_chars.keys()) | set(pr_chars.keys())
        
        for key in all_keys:
            gt_ch = gt_chars.get(key, {})
            pr_ch = pr_chars.get(key, {})
            
            # CCDC number (or key itself)
            if key in gt_chars and key in pr_chars:
                tp += 1
            elif key in gt_chars:
                fn += 1
            elif key in pr_chars:
                fp += 1
            
            # Product names (set comparison)
            # Filter out pure CCDC numbers from product names (they're already counted as CCDC field)
            def _is_ccdc_only(name: str) -> bool:
                """Check if name is just a CCDC number (6-7 digits)."""
                import re
                return bool(re.match(r'^\d{6,7}$', str(name).strip()))
            
            gt_names = set(_normalize(n) for n in ((gt_ch or {}).get("productNames") or []) if _is_valid(n) and not _is_ccdc_only(n))
            pr_names = set(_normalize(n) for n in ((pr_ch or {}).get("productNames") or []) if _is_valid(n) and not _is_ccdc_only(n))
            
            # If both lists are empty (all N/A), count as 1 TP
            if not gt_names and not pr_names:
                tp += 1
            else:
                tp += len(gt_names & pr_names)
                fn += len(gt_names - pr_names)
                fp += len(pr_names - gt_names)
            
            # HNMR fields
            gt_hnmr = (gt_ch or {}).get("HNMR") or {}
            pr_hnmr = (pr_ch or {}).get("HNMR") or {}
            for field in ["shifts", "solvent", "temperature"]:
                gt_val = _normalize(gt_hnmr.get(field))
                pr_val = _normalize(pr_hnmr.get(field))
                if gt_val and pr_val:
                    if gt_val == pr_val:
                        tp += 1
                    else:
                        fp += 1
                        fn += 1
                elif not gt_val and not pr_val:
                    # Both N/A - counts as TP
                    tp += 1
                elif gt_val:
                    fn += 1
                elif pr_val:
                    fp += 1
            
            # ElementalAnalysis fields
            gt_ea = (gt_ch or {}).get("ElementalAnalysis") or {}
            pr_ea = (pr_ch or {}).get("ElementalAnalysis") or {}
            for field in ["weightPercentageCalculated", "weightPercentageExperimental", "chemicalFormula"]:
                gt_val = _normalize(gt_ea.get(field))
                pr_val = _normalize(pr_ea.get(field))
                if gt_val and pr_val:
                    if gt_val == pr_val:
                        tp += 1
                    else:
                        fp += 1
                        fn += 1
                elif not gt_val and not pr_val:
                    # Both N/A - counts as TP
                    tp += 1
                elif gt_val:
                    fn += 1
                elif pr_val:
                    fp += 1
            
            # InfraredSpectroscopy fields
            gt_ir = (gt_ch or {}).get("InfraredSpectroscopy") or {}
            pr_ir = (pr_ch or {}).get("InfraredSpectroscopy") or {}
            for field in ["material", "bands"]:
                gt_val = _normalize(gt_ir.get(field))
                pr_val = _normalize(pr_ir.get(field))
                if gt_val and pr_val:
                    if gt_val == pr_val:
                        tp += 1
                    else:
                        fp += 1
                        fn += 1
                elif not gt_val and not pr_val:
                    # Both N/A - counts as TP
                    tp += 1
                elif gt_val:
                    fn += 1
                elif pr_val:
                    fp += 1
        
        rows_overall.append((doi, (tp, fp, fn)))

        # Per-DOI report with GT and Pred data
        lines: List[str] = []
        lines.append(f"# Characterisation Previous Scoring - {doi}\n")
        lines.append("\n")
        prec = (tp / (tp + fp) * 100.0) if (tp + fp) else 0.0
        rec = (tp / (tp + fn) * 100.0) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.1f}% R={rec:.1f}% F1={f1:.1f}%\n")
        lines.append("\n")
        
        # Show GT and Pred data
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_obj, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_obj, indent=2))
        lines.append("\n```\n\n")

        (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# Characterisation Previous Scoring - Overall\n\n")
    lines_overall.append("| DOI | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
    
    total_tp = total_fp = total_fn = 0
    for doi, (tp, fp, fn) in rows_overall:
        prec = (tp / (tp + fp) * 100.0) if (tp + fp) else 0.0
        rec = (tp / (tp + fn) * 100.0) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        lines_overall.append(f"| {doi} | {tp} | {fp} | {fn} | {prec:.1f}% | {rec:.1f}% | {f1:.1f}% |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn
    
    # Overall summary row
    overall_prec = (total_tp / (total_tp + total_fp) * 100.0) if (total_tp + total_fp) else 0.0
    overall_rec = (total_tp / (total_tp + total_fn) * 100.0) if (total_tp + total_fn) else 0.0
    overall_f1 = (2 * overall_prec * overall_rec / (overall_prec + overall_rec)) if (overall_prec + overall_rec) else 0.0
    lines_overall.append(f"| **Overall** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{overall_prec:.1f}%** | **{overall_rec:.1f}%** | **{overall_f1:.1f}%** |\n")
    
    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Characterisation scoring evaluator")
    parser.add_argument("--previous", action="store_true", help="Evaluate previous_work/characterisation/*.json against ground truth using fine-grained field-level scoring")
    args = parser.parse_args()

    if args.previous:
        evaluate_previous()
    else:
        evaluate_current()


if __name__ == "__main__":
    main()


