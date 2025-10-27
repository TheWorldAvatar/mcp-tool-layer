import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any
from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse


def _normalize_name(s: str) -> str:
    return str(s).strip().lower()


def _map_cbu_formula1_by_ccdc(data: Any) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for proc in (data or {}).get("synthesisProcedures", []) or []:
        ccdc = str((proc or {}).get("mopCCDCNumber") or (proc or {}).get("CCDCNumber") or (proc or {}).get("ccdc_number") or "").strip()
        if not ccdc:
            continue
        f1 = str((proc or {}).get("cbuFormula1") or "").strip()
        if f1:
            m[ccdc] = f1
    return m


def _map_cbu_species1_by_ccdc(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    for proc in (data or {}).get("synthesisProcedures", []) or []:
        ccdc = str((proc or {}).get("mopCCDCNumber") or (proc or {}).get("CCDCNumber") or (proc or {}).get("ccdc_number") or "").strip()
        if not ccdc:
            continue
        names = (proc or {}).get("cbuSpeciesNames1") or []
        if isinstance(names, list):
            vals = sorted({ _normalize_name(x) for x in names if str(x).strip() })
            m[ccdc] = vals
    return m


def _score_anchor_maps(gt_map: Dict[str, Any], pred_map: Dict[str, Any], eq_fn=None) -> Tuple[int, int, int, int, int]:
    keys = set(gt_map.keys()) | set(pred_map.keys())
    matched = 0
    for k in keys:
        if k in gt_map and k in pred_map:
            a, b = gt_map[k], pred_map[k]
            if eq_fn is None:
                matched += int(a == b)
            else:
                matched += int(eq_fn(a, b))
    gt_total = len(gt_map)
    res_total = len(pred_map)
    gt_only = gt_total - matched
    res_only = res_total - matched
    return gt_total, res_total, matched, gt_only, res_only


def evaluate_current() -> None:
    GT_ROOT = Path("earlier_ground_truth/cbu")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/result/cbu")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "cbu.json"
        if not doi or not res_path.exists():
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            # Skip if ground truth missing
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        gt_list: List[str] = []
        for proc in gt.get("synthesisProcedures", []):
            gt_list.append(proc.get("cbuFormula1") or "")
            gt_list.append(proc.get("cbuFormula2") or "")

        res_list: List[str] = []
        for c in res.get("cbus", []):
            res_list.append(c.get("cbu_formula") or "")

        tp, fp, fn = score_lists(gt_list, res_list)
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((hv, (tp, fp, fn, prec, rec, f1)))

        report = render_report(f"CBU Scoring - {hv}", [(hv, (tp, fp, fn, prec, rec, f1))])
        (OUT_ROOT / f"{hv}.md").write_text(report, encoding="utf-8")

    overall = render_report("CBU Scoring - Overall", rows)
    (OUT_ROOT / "_overall.md").write_text(overall, encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_previous() -> None:
    GT_ROOT = Path("earlier_ground_truth/cbu")
    PREV_ROOT = Path("previous_work/cbu")
    OUT_ROOT = Path("evaluation/data/result/cbu_previous")
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

        # Fine-grained scoring: count all formulas and species names individually
        tp = fp = fn = 0
        
        # Collect all CCDC numbers
        gt_procs = {str(p.get("mopCCDCNumber") or "").strip(): p for p in (gt_obj.get("synthesisProcedures") or []) if str(p.get("mopCCDCNumber") or "").strip()}
        pr_procs = {str(p.get("mopCCDCNumber") or "").strip(): p for p in (pred_obj.get("synthesisProcedures") or []) if str(p.get("mopCCDCNumber") or "").strip()}
        
        all_ccdcs = set(gt_procs.keys()) | set(pr_procs.keys())
        
        for ccdc in all_ccdcs:
            gt_proc = gt_procs.get(ccdc, {})
            pr_proc = pr_procs.get(ccdc, {})
            
            # CCDC number itself
            if ccdc in gt_procs and ccdc in pr_procs:
                tp += 1
            elif ccdc in gt_procs:
                fn += 1
            elif ccdc in pr_procs:
                fp += 1
            
            # cbuFormula1
            gt_f1 = str(gt_proc.get("cbuFormula1") or "").strip()
            pr_f1 = str(pr_proc.get("cbuFormula1") or "").strip()
            if gt_f1 and pr_f1:
                if gt_f1 == pr_f1:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif gt_f1:
                fn += 1
            elif pr_f1:
                fp += 1
            
            # cbuFormula2
            gt_f2 = str(gt_proc.get("cbuFormula2") or "").strip()
            pr_f2 = str(pr_proc.get("cbuFormula2") or "").strip()
            if gt_f2 and pr_f2:
                if gt_f2 == pr_f2:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif gt_f2:
                fn += 1
            elif pr_f2:
                fp += 1
            
            # cbuSpeciesNames1
            gt_sp1 = set(_normalize_name(x) for x in (gt_proc.get("cbuSpeciesNames1") or []) if str(x).strip())
            pr_sp1 = set(_normalize_name(x) for x in (pr_proc.get("cbuSpeciesNames1") or []) if str(x).strip())
            tp += len(gt_sp1 & pr_sp1)
            fn += len(gt_sp1 - pr_sp1)
            fp += len(pr_sp1 - gt_sp1)
            
            # cbuSpeciesNames2
            gt_sp2 = set(_normalize_name(x) for x in (gt_proc.get("cbuSpeciesNames2") or []) if str(x).strip())
            pr_sp2 = set(_normalize_name(x) for x in (pr_proc.get("cbuSpeciesNames2") or []) if str(x).strip())
            tp += len(gt_sp2 & pr_sp2)
            fn += len(gt_sp2 - pr_sp2)
            fp += len(pr_sp2 - gt_sp2)
        
        rows_overall.append((doi, (tp, fp, fn)))

        # Per-DOI report with GT and Pred data
        lines: List[str] = []
        lines.append(f"# CBU Previous Scoring - {doi}\n")
        lines.append("\n")
        prec = (tp / (tp + fp) * 100.0) if (tp + fp) else 0.0
        rec = (tp / (tp + fn) * 100.0) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.1f}% R={rec:.1f}% F1={f1:.1f}%\n")
        lines.append("\n")
        
        # Show GT and Pred data by CCDC
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        for ccdc in sorted(gt_procs.keys()):
            proc = gt_procs[ccdc]
            lines.append(f'  "{ccdc}": {{\n')
            lines.append(f'    "cbuFormula1": "{proc.get("cbuFormula1") or ""}",\n')
            lines.append(f'    "cbuFormula2": "{proc.get("cbuFormula2") or ""}",\n')
            lines.append(f'    "cbuSpeciesNames1": {json.dumps(proc.get("cbuSpeciesNames1") or [])},\n')
            lines.append(f'    "cbuSpeciesNames2": {json.dumps(proc.get("cbuSpeciesNames2") or [])}\n')
            lines.append(f'  }}\n')
        lines.append("```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        for ccdc in sorted(pr_procs.keys()):
            proc = pr_procs[ccdc]
            lines.append(f'  "{ccdc}": {{\n')
            lines.append(f'    "cbuFormula1": "{proc.get("cbuFormula1") or ""}",\n')
            lines.append(f'    "cbuFormula2": "{proc.get("cbuFormula2") or ""}",\n')
            lines.append(f'    "cbuSpeciesNames1": {json.dumps(proc.get("cbuSpeciesNames1") or [])},\n')
            lines.append(f'    "cbuSpeciesNames2": {json.dumps(proc.get("cbuSpeciesNames2") or [])}\n')
            lines.append(f'  }}\n')
        lines.append("```\n\n")

        (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# CBU Previous Scoring - Overall\n\n")
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
    parser = argparse.ArgumentParser(description="CBU scoring evaluator")
    parser.add_argument("--previous", action="store_true", help="Evaluate previous_work/cbu/*.json against ground truth using CCDC anchoring")
    args = parser.parse_args()

    if args.previous:
        evaluate_previous()
    else:
        evaluate_current()


if __name__ == "__main__":
    main()


