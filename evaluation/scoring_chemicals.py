import json
from pathlib import Path
from typing import Dict, List, Tuple, Any
import argparse
import sys
import re

# Robust import for running as a script or module
try:
    from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse

TYPES = ["name", "formula", "amount", "supplier", "purity"]


def _normalize(s: Any) -> str:
    """Normalize a value to lowercase string, treating N/A as empty."""
    val = str(s or "").strip()
    if val.upper() in ["N/A", "NA", ""]:
        return ""
    # Normalize whitespace
    val = re.sub(r'\s+', ' ', val)
    # Normalize comma-space patterns
    val = re.sub(r',\s*', ', ', val)
    return val.lower().strip()


def _is_valid(s: Any) -> bool:
    """Check if a value is valid (not N/A or empty)."""
    return _normalize(s) != ""


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None and str(x) != ""]
    s = str(v)
    return [s] if s else []


def _extract_input_chemical_names_from_gt(gt_obj: Dict) -> List[str]:
    names: List[str] = []
    for proc in gt_obj.get("synthesisProcedures", []) or []:
        for step in proc.get("steps", []) or []:
            for input_chem in step.get("inputChemicals", []) or []:
                for chem in input_chem.get("chemical", []) or []:
                    vals = chem.get("chemicalName")
                    for s in _as_list(vals):
                        names.append(s)
    return names


def _type_presence_counts_gt(gt_obj: Dict) -> Dict[str, int]:
    counts = {k: 0 for k in TYPES}
    for proc in gt_obj.get("synthesisProcedures", []) or []:
        for step in proc.get("steps", []) or []:
            for input_chem in step.get("inputChemicals", []) or []:
                # supplier, purity on wrapper
                if _as_list(input_chem.get("supplierName")):
                    counts["supplier"] += 1
                if _as_list(input_chem.get("purity")):
                    counts["purity"] += 1
                for chem in input_chem.get("chemical", []) or []:
                    if _as_list(chem.get("chemicalName")):
                        counts["name"] += 1
                    if _as_list(chem.get("chemicalFormula")):
                        counts["formula"] += 1
                    if _as_list(chem.get("chemicalAmount")):
                        counts["amount"] += 1
    return counts


def _type_presence_counts_res(res_obj: Dict) -> Dict[str, int]:
    counts = {k: 0 for k in TYPES}
    # Prefer nested structure if present
    nested = False
    for proc in res_obj.get("synthesisProcedures", []) or []:
        nested = True
        for step in proc.get("steps", []) or []:
            for input_chem in step.get("inputChemicals", []) or []:
                if _as_list(input_chem.get("supplierName") or input_chem.get("supplier")):
                    counts["supplier"] += 1
                if _as_list(input_chem.get("purity")):
                    counts["purity"] += 1
                for chem in input_chem.get("chemical", []) or []:
                    vals_name = chem.get("chemicalName")
                    if vals_name is None:
                        vals_name = chem.get("names")
                    if vals_name is None:
                        vals_name = chem.get("name")
                    if _as_list(vals_name):
                        counts["name"] += 1
                    vals_formula = chem.get("chemicalFormula")
                    if vals_formula is None:
                        vals_formula = chem.get("formula")
                    if _as_list(vals_formula):
                        counts["formula"] += 1
                    vals_amount = chem.get("chemicalAmount")
                    if vals_amount is None:
                        vals_amount = chem.get("amount")
                    if _as_list(vals_amount):
                        counts["amount"] += 1
    if nested:
        return counts

    # Fallback to top-level chemicals list
    chems = res_obj.get("chemicals")
    if isinstance(chems, list):
        for c in chems:
            if not isinstance(c, dict):
                continue
            vals_name = c.get("chemicalName")
            if vals_name is None:
                vals_name = c.get("names")
            if vals_name is None:
                vals_name = c.get("name")
            if _as_list(vals_name):
                counts["name"] += 1
            vals_formula = c.get("chemicalFormula")
            if vals_formula is None:
                vals_formula = c.get("formula")
            if _as_list(vals_formula):
                counts["formula"] += 1
            vals_amount = c.get("chemicalAmount")
            if vals_amount is None:
                vals_amount = c.get("amount")
            if _as_list(vals_amount):
                counts["amount"] += 1
    return counts


def _extract_chemical_names_flexible(res_obj: Dict) -> List[str]:
    out: List[str] = []
    chems = res_obj.get("chemicals")
    if isinstance(chems, list):
        for c in chems:
            if not isinstance(c, dict):
                continue
            vals = c.get("chemicalName")
            if vals is None:
                vals = c.get("names")
            if vals is None:
                vals = c.get("name")
            for s in _as_list(vals):
                out.append(s)
        if out:
            return out

    for proc in res_obj.get("synthesisProcedures", []) or []:
        for step in proc.get("steps", []) or []:
            for input_chem in step.get("inputChemicals", []) or []:
                for chem in input_chem.get("chemical", []) or []:
                    vals = chem.get("chemicalName")
                    if vals is None:
                        vals = chem.get("names")
                    if vals is None:
                        vals = chem.get("name")
                    for s in _as_list(vals):
                        out.append(s)
    return out


def evaluate_current() -> None:
    GT_ROOT = Path("earlier_ground_truth/chemicals1")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/result/chemicals")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "chemicals.json"
        if not doi or not res_path.exists():
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        gt_list = _extract_input_chemical_names_from_gt(gt)
        res_list = _extract_chemical_names_flexible(res)

        tp, fp, fn = score_lists(gt_list, res_list)
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((hv, (tp, fp, fn, prec, rec, f1)))

        report = render_report(f"Chemicals Scoring - {hv}", [(hv, (tp, fp, fn, prec, rec, f1))])
        (OUT_ROOT / f"{hv}.md").write_text(report, encoding="utf-8")

    overall = render_report("Chemicals Scoring - Overall", rows)
    (OUT_ROOT / "_overall.md").write_text(overall, encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_previous() -> None:
    GT_ROOT = Path("earlier_ground_truth/chemicals1")
    PREV_ROOT = Path("previous_work")
    OUT_ROOT = Path("evaluation/data/result/chemicals_previous")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    overall_gt_types = {k: 0 for k in TYPES}
    overall_res_types = {k: 0 for k in TYPES}

    for jf in sorted(PREV_ROOT.glob("*.json")):
        doi = jf.stem
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        try:
            gt = json.loads(gt_path.read_text(encoding="utf-8"))
            res = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue

        gt_list = _extract_input_chemical_names_from_gt(gt)
        res_list = _extract_chemical_names_flexible(res)

        tp, fp, fn = score_lists(gt_list, res_list)
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((doi, (tp, fp, fn, prec, rec, f1)))

        # Type-level presence
        gt_types = _type_presence_counts_gt(gt)
        res_types = _type_presence_counts_res(res)
        for k in TYPES:
            overall_gt_types[k] += gt_types.get(k, 0)
            overall_res_types[k] += res_types.get(k, 0)

        # Build per-DOI report with type table
        report = render_report(f"Chemicals Scoring - {doi}", [(doi, (tp, fp, fn, prec, rec, f1))])
        lines = [report, "", "### Attribute Presence Differences (type-only)", "", "| Type | GT | Pred | TP | FN | FP |", "|---|---:|---:|---:|---:|---:|"]
        for k in TYPES:
            g = gt_types.get(k, 0)
            p = res_types.get(k, 0)
            tpp = min(g, p)
            fnn = max(g - p, 0)
            fpp = max(p - g, 0)
            lines.append(f"| {k} | {g} | {p} | {tpp} | {fnn} | {fpp} |")
        (OUT_ROOT / f"{doi}.md").write_text("\n".join(lines), encoding="utf-8")

    overall_report = render_report("Chemicals Scoring - Previous Work (Overall)", rows)
    # Overall type table
    lines = [overall_report, "", "### Overall Attribute Presence Differences (type-only)", "", "| Type | GT | Pred | TP | FN | FP |", "|---|---:|---:|---:|---:|---:|"]
    for k in TYPES:
        g = overall_gt_types.get(k, 0)
        p = overall_res_types.get(k, 0)
        tpp = min(g, p)
        fnn = max(g - p, 0)
        fpp = max(p - g, 0)
        lines.append(f"| {k} | {g} | {p} | {tpp} | {fnn} | {fpp} |")
    (OUT_ROOT / "_overall.md").write_text("\n".join(lines), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())



def main() -> None:
    parser = argparse.ArgumentParser(description="Chemicals scoring evaluator")
    parser.add_argument("--previous", action="store_true", help="Evaluate previous_work/*.json against ground truth")
    args = parser.parse_args()

    if args.previous:
        evaluate_previous()
    else:
        evaluate_current()


if __name__ == "__main__":
    main()


