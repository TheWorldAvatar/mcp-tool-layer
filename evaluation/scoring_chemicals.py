"""
Chemicals scoring evaluator

Usage:
    # Current predictions vs earlier ground truth
    python evaluation/scoring_chemicals.py

    # Current predictions vs full ground truth
    python evaluation/scoring_chemicals.py --full

    # Previous work vs earlier ground truth
    python evaluation/scoring_chemicals.py --previous

    # Previous work vs full ground truth
    python evaluation/scoring_chemicals.py --previous --full

    # Previous work anchored vs full ground truth
    python evaluation/scoring_chemicals.py --previous --anchor --full

    # Fuzzy mode (ignores procedureName and outputChemical differences)
    python evaluation/scoring_chemicals.py --full --fuzzy
    python evaluation/scoring_chemicals.py --previous --full --fuzzy
"""

import json
import os
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Any
import argparse
import sys
import re

# Robust import for running as a script or module
try:
    from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse, to_fingerprint
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse, to_fingerprint

from evaluation.utils.chemical_synonym_judge import (
    SynonymJudgeConfig,
    SynonymJudgement,
    judge_pairs,
)

TYPES = ["name", "formula", "amount", "supplier", "purity"]


def _normalize(s: Any) -> str:
    """Normalize a value to lowercase string, treating N/A as empty."""
    val = str(s or "").strip()
    low = val.lower()
    # Treat common placeholders as empty/missing
    if low in ["n/a", "na", "", "-1", "-1.0", "-1e+00", "-1e+0", "-1.00"]:
        return ""
    # Normalize Unicode primes/quotes globally to avoid spurious differences
    try:
        val = val.replace("\u2034", "\u2033")  # ‴ -> ″
        val = val.replace("\u2032", "'")       # ′ -> '
        val = val.replace("\u2033", '"')        # ″ -> "
        val = val.replace("\u2019", "'")       # ’ -> '
        val = val.replace("\u201D", '"')        # ” -> "
    except Exception:
        pass
    # Normalize whitespace
    val = re.sub(r'\s+', ' ', val)
    # Normalize comma-space patterns
    val = re.sub(r',\s*', ', ', val)
    return val.lower().strip()


def _is_valid(s: Any) -> bool:
    """Check if a value is valid (not N/A or empty)."""
    return _normalize(s) != ""


def _normalize_json_structure(obj: Any) -> Any:
    """Recursively normalize all string values in a JSON structure for display purposes."""
    if isinstance(obj, dict):
        return {k: _normalize_json_structure(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_normalize_json_structure(item) for item in obj]
    elif isinstance(obj, str):
        return _normalize(obj) if obj else obj
    else:
        return obj


def _name_sets_for_diff(gt_list: List[str], res_list: List[str]) -> Tuple[set, set]:
    from evaluation.utils.scoring_common import to_fingerprint as _fp
    gt_fp = {_fp(x) for x in gt_list if _fp(x) != '""' and _fp(x) != ''}
    res_fp = {_fp(x) for x in res_list if _fp(x) != '""' and _fp(x) != ''}
    return gt_fp, res_fp


def _collect_output_ccdcs(obj: Any) -> List[str]:
    out: set = set()
    for syn in (obj or {}).get("Synthesis", []) or []:
        c = str((syn or {}).get("productCCDCNumber") or "").strip()
        if _is_valid(c):
            out.add(_normalize(c))
    for proc in (obj or {}).get("synthesisProcedures", []) or []:
        for step in (proc or {}).get("steps", []) or []:
            for oc in (step or {}).get("outputChemical", []) or []:
                c = str((oc or {}).get("CCDCNumber") or "").strip()
                if _is_valid(c):
                    out.add(_normalize(c))
    return sorted(out)


def _apply_fuzzy_ignores(obj: Any, *, ignore_procedure_name: bool = False, ignore_output_names: bool = False, ignore_output_formula: bool = False) -> Any:
    """Return a deep-copied structure with specified fields blanked for fuzzy comparison/display.
    - procedureName set to "" when ignore_procedure_name
    - For every step.outputChemical[*]:
        - names cleared when ignore_output_names
        - chemicalFormula cleared when ignore_output_formula
    """
    import copy
    data = copy.deepcopy(obj)
    try:
        for proc in (data or {}).get("synthesisProcedures", []) or []:
            if ignore_procedure_name:
                if isinstance(proc, dict) and "procedureName" in proc:
                    proc["procedureName"] = ""
            steps = (proc or {}).get("steps", []) or []
            for step in steps:
                outs = (step or {}).get("outputChemical", []) or []
                for out in outs:
                    if ignore_output_names and isinstance(out, dict):
                        out["names"] = []
                    if ignore_output_formula and isinstance(out, dict):
                        out["chemicalFormula"] = ""
    except Exception:
        pass
    return data


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None and str(x) != ""]
    s = str(v)
    return [s] if s else []


def _remaining_raw_names(
    raw_names: List[str],
    remaining: Counter[str],
) -> List[Tuple[str, str]]:
    output: List[Tuple[str, str]] = []
    available = remaining.copy()
    for raw_name in raw_names:
        fingerprint = to_fingerprint(raw_name)
        if fingerprint != '""' and available[fingerprint] > 0:
            output.append((raw_name, fingerprint))
            available[fingerprint] -= 1
    return output


def _score_name_lists(
    gt_list: List[str],
    res_list: List[str],
    synonym_config: SynonymJudgeConfig,
) -> Tuple[int, int, int, List[SynonymJudgement], int]:
    """Score names exactly, then optionally consume LLM-validated synonym pairs."""
    gt_fp = [to_fingerprint(x) for x in gt_list if to_fingerprint(x) != '""']
    res_fp = [to_fingerprint(x) for x in res_list if to_fingerprint(x) != '""']
    gt_c = Counter(gt_fp)
    res_c = Counter(res_fp)
    all_keys = set(gt_c) | set(res_c)
    exact_tp = sum(min(gt_c[k], res_c[k]) for k in all_keys)
    gt_remaining = Counter(
        {key: max(0, count - res_c.get(key, 0)) for key, count in gt_c.items()}
    )
    res_remaining = Counter(
        {key: max(0, count - gt_c.get(key, 0)) for key, count in res_c.items()}
    )

    judgements: List[SynonymJudgement] = []
    synonym_tp = 0
    if synonym_config.enabled and sum(gt_remaining.values()) and sum(res_remaining.values()):
        gt_raw = _remaining_raw_names(gt_list, gt_remaining)
        res_raw = _remaining_raw_names(res_list, res_remaining)
        judgements = judge_pairs(
            (
                (gt_name, res_name, gt_fingerprint, res_fingerprint)
                for gt_name, gt_fingerprint in gt_raw
                for res_name, res_fingerprint in res_raw
            ),
            synonym_config,
        )
        equivalent_edges = {
            (row.ground_truth_fingerprint, row.prediction_fingerprint)
            for row in judgements
            if row.equivalent and row.status == "ok"
        }
        adjacency = {
            gt_index: [
                res_index
                for res_index, (_, res_fingerprint) in enumerate(res_raw)
                if (gt_fingerprint, res_fingerprint) in equivalent_edges
            ]
            for gt_index, (_, gt_fingerprint) in enumerate(gt_raw)
        }
        matched_gt_for_res: Dict[int, int] = {}

        def _augment(gt_index: int, seen: set[int]) -> bool:
            for res_index in adjacency[gt_index]:
                if res_index in seen:
                    continue
                seen.add(res_index)
                previous_gt = matched_gt_for_res.get(res_index)
                if previous_gt is None or _augment(previous_gt, seen):
                    matched_gt_for_res[res_index] = gt_index
                    return True
            return False

        synonym_tp = sum(
            1 for gt_index in sorted(adjacency) if _augment(gt_index, set())
        )

    tp = exact_tp + synonym_tp
    fn = max(0, len(gt_fp) - tp)
    fp = max(0, len(res_fp) - tp) if tp == 0 else 0
    return tp, fp, fn, judgements, synonym_tp


def _render_synonym_judgements(
    judgements: List[SynonymJudgement],
    synonym_tp: int,
) -> str:
    if not judgements:
        return ""
    lines = [
        "\n## LLM Chemical Synonym Judgements\n\n",
        f"Validated one-to-one synonym matches added to TP: **{synonym_tp}**\n\n",
        "| GT name | Prediction name | Equivalent | Confidence | Relation | Source | Reason |\n",
        "| --- | --- | --- | ---: | --- | --- | --- |\n",
    ]
    for row in judgements:
        reason = row.reason.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row.ground_truth_name} | {row.prediction_name} | "
            f"{str(row.equivalent).lower()} | {row.confidence:.2f} | "
            f"{row.relation} | {row.source} | {reason} |\n"
        )
    return "".join(lines)


def _extract_input_chemical_names_from_gt(gt_obj: Dict) -> List[str]:
    names: List[str] = []
    for syn in gt_obj.get("Synthesis", []) or []:
        for step in syn.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            for step_payload in step.values():
                if not isinstance(step_payload, dict):
                    continue
                for input_chem in step_payload.get("addedChemical", []) or []:
                    vals = input_chem.get("chemicalName")
                    for s in _as_list(vals):
                        names.append(s)
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
    for syn in gt_obj.get("Synthesis", []) or []:
        for step in syn.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            for step_payload in step.values():
                if not isinstance(step_payload, dict):
                    continue
                for chem in step_payload.get("addedChemical", []) or []:
                    if _as_list(chem.get("chemicalName")):
                        counts["name"] += 1
                    if _as_list(chem.get("chemicalAmount")):
                        counts["amount"] += 1
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


def evaluate_current(
    *,
    fuzzy: bool = False,
    use_full_gt: bool = False,
    synonym_config: SynonymJudgeConfig = SynonymJudgeConfig(),
    selected_hashes: set[str] | None = None,
) -> None:
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/chemicals")
        OUT_ROOT = Path("evaluation/data/full_result/chemicals")
        allowed_files = None
    else:
        GT_ROOT = Path("full_ground_truth/chemicals")  # Always use full_ground_truth
        OUT_ROOT = Path("evaluation/data/result/chemicals")
        # Only evaluate these 9 files in default mode
        allowed_files = {
            "10.1002_anie.201811027.json",
            "10.1002_anie.202010824.json",
            "10.1021_acs.inorgchem.4c02394.json",
            "10.1021_ic402428m.json",
            "10.1021_ja042802q.json",
            "10.1039_C6CC04583A.json",
            "10.1039_C6DT02764D.json",
            "10.1039_C8DT02580K.json",
            "10.1039_D3QI01501G.json"
        }
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    RES_ROOT = Path("evaluation/data/merged_tll")

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    # Load all in-scope docs first; optionally prewarm synonym judgements in one
    # chunked/parallel LLM pass so per-hash scoring mostly hits cache.
    prepared: List[Tuple[str, str, Path, List[str], List[str]]] = []
    for hv in hashes:
        if selected_hashes is not None and hv not in selected_hashes:
            continue
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "chemicals.json"
        if not doi or not res_path.exists():
            continue

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue

        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))
        gt_list = _extract_input_chemical_names_from_gt(gt)
        res_list = _extract_chemical_names_flexible(res)
        prepared.append((hv, doi, res_path, gt_list, res_list))

    if synonym_config.enabled and prepared:
        warm_pairs: List[Tuple[str, str, str, str]] = []
        for _, _, _, gt_list, res_list in prepared:
            gt_fp = [to_fingerprint(x) for x in gt_list if to_fingerprint(x) != '""']
            res_fp = [to_fingerprint(x) for x in res_list if to_fingerprint(x) != '""']
            gt_c = Counter(gt_fp)
            res_c = Counter(res_fp)
            gt_remaining = Counter(
                {key: max(0, count - res_c.get(key, 0)) for key, count in gt_c.items()}
            )
            res_remaining = Counter(
                {key: max(0, count - gt_c.get(key, 0)) for key, count in res_c.items()}
            )
            if not sum(gt_remaining.values()) or not sum(res_remaining.values()):
                continue
            gt_raw = _remaining_raw_names(gt_list, gt_remaining)
            res_raw = _remaining_raw_names(res_list, res_remaining)
            warm_pairs.extend(
                (gt_name, res_name, gt_fingerprint, res_fingerprint)
                for gt_name, gt_fingerprint in gt_raw
                for res_name, res_fingerprint in res_raw
            )
        if warm_pairs:
            print(
                f"[chemicals] prewarming {len(warm_pairs)} unresolved name-pairs "
                f"across {len(prepared)} document(s)",
                flush=True,
            )
            judge_pairs(warm_pairs, synonym_config)

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    for hv, doi, res_path, gt_list, res_list in prepared:
        gt_path = GT_ROOT / f"{doi}.json"
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        tp, fp, fn, synonym_rows, synonym_tp = _score_name_lists(
            gt_list,
            res_list,
            synonym_config,
        )
        
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((hv, (tp, fp, fn, prec, rec, f1)))

        # Individual report
        lines: List[str] = []
        lines.append(f"# Chemicals Scoring - {hv}\n\n")
        lines.append(f"**DOI**: `{doi}`  \n")
        lines.append(f"**Hash**: `{hv}`  \n")
        lines.append(f"**Prediction file**: `{res_path.as_posix()}`  \n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n\n")
        lines.append(_render_synonym_judgements(synonym_rows, synonym_tp))
        
        # Fuzzy ignores for display if requested
        gt_display = _apply_fuzzy_ignores(gt, ignore_procedure_name=fuzzy, ignore_output_names=fuzzy, ignore_output_formula=fuzzy) if fuzzy else gt
        res_display = _apply_fuzzy_ignores(res, ignore_procedure_name=fuzzy, ignore_output_names=fuzzy, ignore_output_formula=fuzzy) if fuzzy else res
        # Create normalized versions
        gt_normalized = _normalize_json_structure(gt_display)
        pred_normalized = _normalize_json_structure(res_display)
        
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences section
        gt_set, res_set = _name_sets_for_diff(gt_list, res_list)
        missing = sorted(gt_set - res_set)
        extra = sorted(res_set - gt_set)
        pr_ccdcs = _collect_output_ccdcs(res)
        gt_ccdcs = _collect_output_ccdcs(gt)
        lines.append("## Differences\n\n")
        lines.append(f"CCDC Numbers: {', '.join(pr_ccdcs) or 'N/A'} - {', '.join(gt_ccdcs) or 'N/A'}\n")
        lines.append(f"FN (missing names): {', '.join(missing) or 'None'}\n")
        lines.append(f"FP (extra names): {', '.join(extra) or 'None'}\n\n")
        
        (OUT_ROOT / f"{hv}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# Chemicals Scoring - Overall\n\n")
    lines_overall.append("| Hash | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
    
    total_tp = total_fp = total_fn = 0
    for hv, (tp, fp, fn, prec, rec, f1) in rows:
        lines_overall.append(f"| {hv} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn
    
    overall_prec, overall_rec, overall_f1 = precision_recall_f1(total_tp, total_fp, total_fn)
    lines_overall.append(f"| **Overall** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{overall_prec:.3f}** | **{overall_rec:.3f}** | **{overall_f1:.3f}** |\n")
    lines_overall.append(f"\n**Fine-grained Scoring:** TP={total_tp} FP={total_fp} FN={total_fn} | P={overall_prec:.3f} R={overall_rec:.3f} F1={overall_f1:.3f}\n")
    
    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_full(
    *,
    fuzzy: bool = False,
    synonym_config: SynonymJudgeConfig = SynonymJudgeConfig(),
    selected_hashes: set[str] | None = None,
    pred_root: Path | None = None,
    out_root: Path | None = None,
) -> None:
    """Evaluate current predictions against full ground truth dataset."""
    GT_ROOT = Path("full_ground_truth/chemicals")
    RES_ROOT = Path(pred_root) if pred_root is not None else Path("evaluation/data/merged_tll")
    OUT_ROOT = Path(out_root) if out_root is not None else Path("evaluation/data/full_result/chemicals")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])
    allowed_files = None

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    for hv in hashes:
        if selected_hashes is not None and hv not in selected_hashes:
            continue
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "chemicals.json"
        if not doi or not res_path.exists():
            continue

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue

        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        
        try:
            gt = json.loads(gt_path.read_text(encoding="utf-8"))
            res = json.loads(res_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error loading {hv}: {e}")
            continue

        gt_list = _extract_input_chemical_names_from_gt(gt)
        res_list = _extract_chemical_names_flexible(res)

        tp, fp, fn, synonym_rows, synonym_tp = _score_name_lists(
            gt_list,
            res_list,
            synonym_config,
        )
        
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((hv, (tp, fp, fn, prec, rec, f1)))

        # Individual report
        lines: List[str] = []
        lines.append(f"# Chemicals Scoring (Full GT) - {hv}\n\n")
        lines.append(f"**DOI**: `{doi}`  \n")
        lines.append(f"**Hash**: `{hv}`  \n")
        lines.append(f"**Prediction file**: `{res_path.as_posix()}`  \n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n\n")
        lines.append(_render_synonym_judgements(synonym_rows, synonym_tp))
        
        # Fuzzy ignores for display if requested
        gt_display = _apply_fuzzy_ignores(gt, ignore_procedure_name=fuzzy, ignore_output_names=fuzzy, ignore_output_formula=fuzzy) if fuzzy else gt
        res_display = _apply_fuzzy_ignores(res, ignore_procedure_name=fuzzy, ignore_output_names=fuzzy, ignore_output_formula=fuzzy) if fuzzy else res
        # Create normalized versions
        gt_normalized = _normalize_json_structure(gt_display)
        pred_normalized = _normalize_json_structure(res_display)
        
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences section
        gt_set, res_set = _name_sets_for_diff(gt_list, res_list)
        missing = sorted(gt_set - res_set)
        extra = sorted(res_set - gt_set)
        pr_ccdcs = _collect_output_ccdcs(res)
        gt_ccdcs = _collect_output_ccdcs(gt)
        lines.append("## Differences\n\n")
        lines.append(f"CCDC Numbers: {', '.join(pr_ccdcs) or 'N/A'} - {', '.join(gt_ccdcs) or 'N/A'}\n")
        lines.append(f"FN (missing names): {', '.join(missing) or 'None'}\n")
        lines.append(f"FP (extra names): {', '.join(extra) or 'None'}\n\n")
        
        (OUT_ROOT / f"{hv}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# Chemicals Scoring (Full GT) - Overall\n\n")
    lines_overall.append("| Hash | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
    
    total_tp = total_fp = total_fn = 0
    for hv, (tp, fp, fn, prec, rec, f1) in rows:
        lines_overall.append(f"| {hv} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn
    
    overall_prec, overall_rec, overall_f1 = precision_recall_f1(total_tp, total_fp, total_fn)
    lines_overall.append(f"| **Overall** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{overall_prec:.3f}** | **{overall_rec:.3f}** | **{overall_f1:.3f}** |\n")
    lines_overall.append(f"\n**Fine-grained Scoring:** TP={total_tp} FP={total_fp} FN={total_fn} | P={overall_prec:.3f} R={overall_rec:.3f} F1={overall_f1:.3f}\n")
    
    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_previous(
    use_anchored: bool = False,
    *,
    fuzzy: bool = False,
    use_full_gt: bool = False,
    synonym_config: SynonymJudgeConfig = SynonymJudgeConfig(),
) -> None:
    """Evaluate previous predictions using the SAME standard as current mode.
    The only extra step: compute CCDC anchoring gain by comparing previous_work vs previous_work_anchored.
    
    Args:
        use_anchored: Use previous_work_anchored instead of previous_work
        fuzzy: Ignore procedureName and outputChemical differences
        use_full_gt: Use full ground truth dataset instead of earlier ground truth
    """
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/chemicals")
        OUT_ROOT = Path("evaluation/data/full_result/chemicals_previous")
        allowed_files = None
    else:
        GT_ROOT = Path("full_ground_truth/chemicals")  # Always use full_ground_truth
        OUT_ROOT = Path("evaluation/data/result/chemicals_previous")
        # Only evaluate these 9 files in default mode
        allowed_files = {
            "10.1002_anie.201811027.json",
            "10.1002_anie.202010824.json",
            "10.1021_acs.inorgchem.4c02394.json",
            "10.1021_ic402428m.json",
            "10.1021_ja042802q.json",
            "10.1039_C6CC04583A.json",
            "10.1039_C6DT02764D.json",
            "10.1039_C8DT02580K.json",
            "10.1039_D3QI01501G.json"
        }
    
    PREV_ROOT = Path("previous_work_anchored/chemicals") if use_anchored else Path("previous_work/chemicals")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    missing_predictions: List[str] = []

    # Iterate over all ground truth files
    for gt_path in sorted(GT_ROOT.glob("*.json")):
        doi = gt_path.stem

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue

        prev_path = PREV_ROOT / f"{doi}.json"
        
        try:
            gt = json.loads(gt_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        
        # Check if prediction exists
        if not prev_path.exists():
            # No prediction for this ground truth - count as all FN
            gt_list = _extract_input_chemical_names_from_gt(gt)
            gt_fp = [to_fingerprint(x) for x in gt_list if to_fingerprint(x) != '""']
            fn = len(gt_fp)
            rows_overall.append((doi, (0, 0, fn)))
            missing_predictions.append(doi)

            # Emit a placeholder per-DOI report indicating missing prediction
            lines: List[str] = []
            title_suffix = " (Full GT)" if use_full_gt else ""
            lines.append(f"# Chemicals Previous Scoring{title_suffix} - {doi}\n\n")
            lines.append("No previous_work prediction found for this DOI.\n\n")

            gt_display = _apply_fuzzy_ignores(gt, ignore_procedure_name=fuzzy, ignore_output_names=fuzzy, ignore_output_formula=fuzzy) if fuzzy else gt
            gt_normalized = _normalize_json_structure(gt_display)

            # Show GT context
            lines.append("## Ground Truth\n")
            lines.append("```json\n")
            lines.append(json.dumps(gt_normalized, indent=2))
            lines.append("\n```\n\n")

            # Prediction placeholder
            lines.append("## Prediction\n\n")
            lines.append("(none)\n\n")

            # Differences summary (all GT names counted as FN)
            pr_ccdcs = []
            gt_ccdcs = _collect_output_ccdcs(gt)
            lines.append("## Differences\n\n")
            lines.append(f"CCDC Numbers: {', '.join(pr_ccdcs) or 'N/A'} - {', '.join(gt_ccdcs) or 'N/A'}\n")
            # Extract fingerprints outside f-string to avoid backslash in f-string expression
            empty_vals = ('""', '')
            missing_fps = sorted({to_fingerprint(x) for x in gt_list if to_fingerprint(x) not in empty_vals})
            lines.append(f"FN (missing names): {', '.join(missing_fps) or 'None'}\n")
            lines.append("FP (extra names): None\n\n")

            (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")
            continue
        
        try:
            res = json.loads(prev_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # SAME MECHANISM AS evaluate_current()
        gt_list = _extract_input_chemical_names_from_gt(gt)
        res_list = _extract_chemical_names_flexible(res)

        tp, fp, fn, synonym_rows, synonym_tp = _score_name_lists(
            gt_list,
            res_list,
            synonym_config,
        )

        rows_overall.append((doi, (tp, fp, fn)))

        # Per-DOI report (normalized JSON only)
        lines: List[str] = []
        title_suffix = " (Full GT)" if use_full_gt else ""
        lines.append(f"# Chemicals Previous Scoring{title_suffix} - {doi}\n\n")
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n\n")
        lines.append(_render_synonym_judgements(synonym_rows, synonym_tp))
        gt_display = _apply_fuzzy_ignores(gt, ignore_procedure_name=fuzzy, ignore_output_names=fuzzy, ignore_output_formula=fuzzy) if fuzzy else gt
        res_display = _apply_fuzzy_ignores(res, ignore_procedure_name=fuzzy, ignore_output_names=fuzzy, ignore_output_formula=fuzzy) if fuzzy else res
        gt_normalized = _normalize_json_structure(gt_display)
        pred_normalized = _normalize_json_structure(res_display)
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences section
        gt_set, res_set = _name_sets_for_diff(gt_list, res_list)
        missing = sorted(gt_set - res_set)
        extra = sorted(res_set - gt_set)
        pr_ccdcs = _collect_output_ccdcs(res)
        gt_ccdcs = _collect_output_ccdcs(gt)
        lines.append("## Differences\n\n")
        lines.append(f"CCDC Numbers: {', '.join(pr_ccdcs) or 'N/A'} - {', '.join(gt_ccdcs) or 'N/A'}\n")
        lines.append(f"FN (missing names): {', '.join(missing) or 'None'}\n")
        lines.append(f"FP (extra names): {', '.join(extra) or 'None'}\n\n")
        (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    title_suffix = " (Full GT)" if use_full_gt else ""
    lines_overall.append(f"# Chemicals Previous Scoring{title_suffix} - Overall\n\n")
    lines_overall.append("| DOI | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")

    total_tp = total_fp = total_fn = 0
    for doi, (tp, fp, fn) in rows_overall:
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines_overall.append(f"| {doi} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn

    overall_prec, overall_rec, overall_f1 = precision_recall_f1(total_tp, total_fp, total_fn)
    lines_overall.append(f"| **Overall** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{overall_prec:.3f}** | **{overall_rec:.3f}** | **{overall_f1:.3f}** |\n")
    lines_overall.append(f"\n**Fine-grained Scoring:** TP={total_tp} FP={total_fp} FN={total_fn} | P={overall_prec:.3f} R={overall_rec:.3f} F1={overall_f1:.3f}\n")

    # List missing predictions
    if missing_predictions:
        lines_overall.append(f"\n## Missing Predictions ({len(missing_predictions)} DOIs)\n\n")
        lines_overall.append("The following DOIs in ground truth have no corresponding predictions:\n\n")
        for doi in missing_predictions:
            lines_overall.append(f"- `{doi}`\n")
        lines_overall.append("\n")

    # Deduce CCDC TP points by comparing previous_work vs previous_work_anchored
    def _count_ccdc_gain() -> Tuple[int, Dict[str, int]]:
        base = Path("previous_work/chemicals")
        anchored = Path("previous_work_anchored/chemicals")
        total_gain = 0
        per_doi: Dict[str, int] = {}
        if not base.exists() or not anchored.exists():
            return (0, {})
        for b in sorted(base.glob("*.json")):
            doi = b.stem
            a = anchored / f"{doi}.json"
            if not a.exists():
                continue
            try:
                jb = json.loads(b.read_text(encoding="utf-8"))
                ja = json.loads(a.read_text(encoding="utf-8"))
            except Exception:
                continue
            def _count_valid_ccdc(obj: Any) -> int:
                cnt = 0
                for proc in (obj or {}).get("synthesisProcedures", []) or []:
                    for step in (proc or {}).get("steps", []) or []:
                        for out_chem in (step or {}).get("outputChemical", []) or []:
                            ccdc = str((out_chem or {}).get("CCDCNumber") or "").strip().lower()
                            if ccdc not in ("", "n/a", "na"):
                                cnt += 1
                return cnt
            vb = _count_valid_ccdc(jb)
            va = _count_valid_ccdc(ja)
            gain = max(0, va - vb)
            if gain > 0:
                per_doi[doi] = gain
            total_gain += gain
        return (total_gain, per_doi)

    total_gain, per_doi_gain = _count_ccdc_gain()
    lines_overall.append("\n## CCDC Anchoring Gain (previous_work → previous_work_anchored)\n\n")
    lines_overall.append(f"Total additional valid CCDC entries: **{total_gain}**\n\n")
    if per_doi_gain:
        for doi, g in sorted(per_doi_gain.items()):
            lines_overall.append(f"- `{doi}`: +{g}\n")

    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())



def main() -> None:
    parser = argparse.ArgumentParser(description="Chemicals scoring evaluator")
    parser.add_argument("--previous", action="store_true", help="Evaluate previous_work/*.json against ground truth")
    parser.add_argument("--anchor", action="store_true", help="Use previous_work_anchored/ instead of previous_work/ (only with --previous)")
    parser.add_argument("--full", action="store_true", help="Evaluate against full ground truth dataset (full_ground_truth/chemicals/)")
    parser.add_argument("--fuzzy", action="store_true", help="Ignore differences in procedureName and outputChemical (names, chemicalFormula) while displaying and scoring context")
    parser.add_argument(
        "--llm-synonyms",
        action="store_true",
        help="Use strict cached LLM judgements for unresolved chemical-name pairs",
    )
    parser.add_argument(
        "--llm-synonym-model",
        default=os.getenv("CHEMICAL_SYNONYM_JUDGE_MODEL", "gpt-4o"),
        help="Model used by the chemical synonym judge",
    )
    parser.add_argument(
        "--llm-synonym-cache-dir",
        type=Path,
        default=Path("evaluation/cache/chemical_synonym_judge"),
        help="Persistent per-pair synonym judgement cache",
    )
    parser.add_argument(
        "--llm-synonyms-required",
        action="store_true",
        help="Fail scoring if an LLM synonym judgement cannot be validated",
    )
    parser.add_argument(
        "--llm-synonym-batch-size",
        type=int,
        default=int(os.getenv("CHEMICAL_SYNONYM_BATCH_SIZE", "20")),
        help="Pairs per chemical-synonym LLM call (default 20)",
    )
    parser.add_argument(
        "--llm-synonym-workers",
        type=int,
        default=int(os.getenv("CHEMICAL_SYNONYM_MAX_WORKERS", "8")),
        help="Parallel chemical-synonym LLM chunk workers (default 8)",
    )
    parser.add_argument(
        "--hash",
        action="append",
        dest="selected_hashes",
        help="Restrict current-result scoring to this hash; may be repeated",
    )
    parser.add_argument("--pred-root", type=Path, default=None, help="Merged prediction root (default evaluation/data/merged_tll)")
    parser.add_argument("--out-root", type=Path, default=None, help="Write chemical reports here")
    args = parser.parse_args()
    synonym_config = SynonymJudgeConfig(
        enabled=args.llm_synonyms,
        model=args.llm_synonym_model,
        cache_dir=args.llm_synonym_cache_dir,
        required=args.llm_synonyms_required,
        batch_size=max(1, args.llm_synonym_batch_size),
        max_workers=max(1, args.llm_synonym_workers),
    )

    if args.previous:
        # --full flag also applies to previous work evaluation
        evaluate_previous(
            use_anchored=args.anchor,
            fuzzy=args.fuzzy,
            use_full_gt=args.full,
            synonym_config=synonym_config,
        )
    elif args.full:
        evaluate_full(
            fuzzy=args.fuzzy,
            synonym_config=synonym_config,
            selected_hashes=set(args.selected_hashes) if args.selected_hashes else None,
            pred_root=args.pred_root,
            out_root=args.out_root,
        )
    else:
        evaluate_current(
            fuzzy=args.fuzzy,
            use_full_gt=args.full,
            synonym_config=synonym_config,
            selected_hashes=set(args.selected_hashes) if args.selected_hashes else None,
        )


if __name__ == "__main__":
    main()


