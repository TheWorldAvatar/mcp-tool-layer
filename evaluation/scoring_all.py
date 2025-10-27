import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple, Any

# Reuse existing per-category scorers for default (our results)
from evaluation.scoring_cbu import main as score_cbu_current
from evaluation.scoring_characterisation import main as score_char_current
from evaluation.scoring_steps import main as score_steps_current
from evaluation.scoring_chemicals import evaluate_current as score_chems_current, evaluate_previous as score_chems_previous

# Reuse anchor mappers and report renderer
from evaluation.merged_result_scoring import (
    map_char_by_ccdc_gt,
    map_steps_by_ccdc_gt,
    render_report,
)


def _canonicalize_json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def _score_steps_order(gt_map: Dict[str, List[str]], pred_map: Dict[str, List[str]]) -> Tuple[int, int, int, int, int]:
    tp = fp = fn = 0
    keys = set(gt_map.keys()) | set(pred_map.keys())
    for k in keys:
        g = gt_map.get(k, [])
        p = pred_map.get(k, [])
        n = min(len(g), len(p))
        eq = sum(1 for i in range(n) if g[i] == p[i])
        tp += eq
        fp += (len(p) - eq)
        fn += (len(g) - eq)
    # Map to (gt_total, res_total, matched, gt_only, res_only) for common report renderer
    gt_total = tp + fn
    res_total = tp + fp
    matched = tp
    gt_only = fn
    res_only = fp
    return gt_total, res_total, matched, gt_only, res_only


def run_previous_all() -> None:
    GT_BASE = Path("earlier_ground_truth")
    PREV_BASE = Path("previous_work")
    OUT_BASE = Path("evaluation/data/result")
    OUT_BASE.mkdir(parents=True, exist_ok=True)

    # ---------- CBU (CCDC-anchored): cbuFormula1 equality and cbuSpeciesNames1 set-equality ----------
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

    def _normalize_name(s: str) -> str:
        return str(s).strip().lower()

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

    cbu_prev = PREV_BASE / "cbu"
    cbu_gt = GT_BASE / "cbu"
    cbu_out = OUT_BASE / "cbu_previous"
    cbu_out.mkdir(parents=True, exist_ok=True)
    cbu_rows_formula: List[Tuple[str, Tuple[int, int, int, int, int]]] = []
    cbu_rows_species: List[Tuple[str, Tuple[int, int, int, int, int]]] = []
    for jf in sorted(cbu_prev.glob("*.json")):
        doi = jf.stem
        gt_path = cbu_gt / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt_obj = _canonicalize_json_load(gt_path)
        pred_obj = _canonicalize_json_load(jf)

        gt_f1 = _map_cbu_formula1_by_ccdc(gt_obj)
        pr_f1 = _map_cbu_formula1_by_ccdc(pred_obj)
        row_f1 = (doi, _score_anchor_maps(gt_f1, pr_f1, eq_fn=lambda a, b: str(a).strip() == str(b).strip()))
        cbu_rows_formula.append(row_f1)

        gt_sp1 = _map_cbu_species1_by_ccdc(gt_obj)
        pr_sp1 = _map_cbu_species1_by_ccdc(pred_obj)
        def _eq_set(a: List[str], b: List[str]) -> bool:
            return set(map(_normalize_name, a or [])) == set(map(_normalize_name, b or []))
        row_sp1 = (doi, _score_anchor_maps(gt_sp1, pr_sp1, eq_fn=_eq_set))
        cbu_rows_species.append(row_sp1)

        # Per-DOI report combining both aspects
        lines: List[str] = []
        lines.append(render_report(f"CBU Previous Scoring - cbuFormula1 - {doi}", [(doi, row_f1[1])]))
        lines.append("")
        lines.append(render_report(f"CBU Previous Scoring - cbuSpeciesNames1 - {doi}", [(doi, row_sp1[1])]))
        (cbu_out / f"{doi}.md").write_text("\n".join(lines), encoding="utf-8")

    # Overall report with two sections
    lines_overall: List[str] = []
    lines_overall.append(render_report("CBU Previous Scoring - Overall (cbuFormula1)", cbu_rows_formula))
    lines_overall.append("")
    lines_overall.append(render_report("CBU Previous Scoring - Overall (cbuSpeciesNames1)", cbu_rows_species))
    (cbu_out / "_overall.md").write_text("\n".join(lines_overall), encoding="utf-8")

    # Characterisation
    ch_prev = PREV_BASE / "characterisation"
    ch_gt = GT_BASE / "characterisation"
    ch_out = OUT_BASE / "characterisation_previous"
    ch_out.mkdir(parents=True, exist_ok=True)
    ch_rows: List[Tuple[str, Tuple[int, int, int, int, int]]] = []
    for jf in sorted(ch_prev.glob("*.json")):
        doi = jf.stem
        gt_path = ch_gt / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt_map = map_char_by_ccdc_gt(_canonicalize_json_load(gt_path))
        pred_map = map_char_by_ccdc_gt(_canonicalize_json_load(jf))
        ch_rows.append((doi, _score_anchor_maps(gt_map, pred_map)))
        (ch_out / f"{doi}.md").write_text(render_report(f"Characterisation Previous Scoring - {doi}", [(doi, ch_rows[-1][1])]), encoding="utf-8")
    (ch_out / "_overall.md").write_text(render_report("Characterisation Previous Scoring - Overall", ch_rows), encoding="utf-8")

    # Steps (order-sensitive by CCDC)
    st_prev = PREV_BASE / "steps"
    st_gt = GT_BASE / "steps"
    st_out = OUT_BASE / "steps_previous"
    st_out.mkdir(parents=True, exist_ok=True)
    st_rows: List[Tuple[str, Tuple[int, int, int, int, int]]] = []
    for jf in sorted(st_prev.glob("*.json")):
        doi = jf.stem
        gt_path = st_gt / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt_map = map_steps_by_ccdc_gt(_canonicalize_json_load(gt_path))
        pred_map = map_steps_by_ccdc_gt(_canonicalize_json_load(jf))
        st_rows.append((doi, _score_steps_order(gt_map, pred_map)))
        (st_out / f"{doi}.md").write_text(render_report(f"Steps Previous Scoring (Order) - {doi}", [(doi, st_rows[-1][1])]), encoding="utf-8")
    (st_out / "_overall.md").write_text(render_report("Steps Previous Scoring (Order) - Overall", st_rows), encoding="utf-8")

    # Chemicals (delegate to existing previous evaluator)
    score_chems_previous()


def run_current_all() -> None:
    # Use existing current evaluators that write reports under evaluation/data/result
    score_cbu_current()
    score_char_current()
    score_steps_current()
    score_chems_current()


def main() -> None:
    p = argparse.ArgumentParser(description="Run scoring for all categories (current default or previous work)")
    p.add_argument("--previous", action="store_true", help="Score previous_work/* against earlier_ground_truth using CCDC anchoring and order-sensitive steps")
    args = p.parse_args()

    if args.previous:
        run_previous_all()
    else:
        run_current_all()


if __name__ == "__main__":
    main()


