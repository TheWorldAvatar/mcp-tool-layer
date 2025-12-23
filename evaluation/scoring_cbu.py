import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any
import re

# Add parent directory to path to allow imports when run as script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse, to_fingerprint


def _normalize_name(s: str) -> str:
    """Normalize chemical names for matching.

    Rules:
    - Lowercase and strip
    - Convert Unicode subscript/superscript to regular characters
    - Remove all spaces (chemical names often have inconsistent spacing)
    - Remove parentheses and brackets for formula-like names
    - Remove common separators (-, _, etc.)
    """
    try:
        s = str(s or "").strip().lower()
    except Exception:
        return ""
    if not s or s in ["n/a", "na"]:
        return ""

    # Convert Unicode subscript/superscript characters to regular characters
    # Subscripts: ₀₁₂₃₄₅₆₇₈₉ → 0123456789
    # Superscripts: ⁰¹²³⁴⁵⁶⁷⁸⁹ → 0123456789
    # Other Unicode chars: · → ., etc.
    unicode_map = {
        # Subscripts
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
        # Superscripts
        '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
        '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
        # Other Unicode characters
        '·': '.', '•': '.', '⋅': '.', '×': 'x', '÷': '/',
        'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta',
        'ε': 'epsilon', 'μ': 'mu', 'ν': 'nu', 'π': 'pi',
    }

    for unicode_char, ascii_char in unicode_map.items():
        s = s.replace(unicode_char, ascii_char)

    # Remove brackets from formula-like names
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]

    # Remove all spaces, hyphens, underscores for chemical name comparison
    import re
    s = re.sub(r"[\s\-_]", "", s)

    # Remove common chemical formula artifacts
    s = re.sub(r"[()]", "", s)

    return s.strip()


def _normalize_json_structure(obj: Any, preserve_case: bool = False) -> Any:
    """Recursively normalize all string values in a JSON structure for display purposes.
    
    Args:
        obj: The object to normalize
        preserve_case: If True, preserve original case for display; if False, lowercase (default)
    """
    if isinstance(obj, dict):
        return {k: _normalize_json_structure(v, preserve_case) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_normalize_json_structure(item, preserve_case) for item in obj]
    elif isinstance(obj, str):
        if preserve_case:
            return str(obj).strip() if obj else obj
        else:
            return _normalize_name(obj) if obj else obj
    else:
        return obj


def _normalize_cbu_formula(s: str) -> str:
    """Normalize CBU formula strings for comparison.

    - Lowercase + trim
    - Component equivalences applied symmetrically on both GT and prediction:
      * phpo3 ≡ c6h5po3 (component-level replacement)
      * (c6h3)2 ≡ (c12h6) (component-level replacement)
      * v6o6(och3)9(vo4) ≡ v7o10(och3)9 (component/whole-formula replacement)
    """
    val = str(s or "").strip().lower()
    if not val:
        return val

    # Component-level replacements
    # (c6h3)2 → (c12h6)
    val = val.replace("(c6h3)2", "(c12h6)")

    # phpo3 → c6h5po3, but avoid touching larger alphanumeric tokens
    val = re.sub(r"(?<![a-z0-9])phpo3(?![a-z0-9])", "c6h5po3", val)

    # Whole/embedded equivalence for vanadium methoxide unit
    val = val.replace("v6o6(och3)9(vo4)", "v7o10(och3)9")

    return val


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


def _map_cbu_species2_by_ccdc(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    for proc in (data or {}).get("synthesisProcedures", []) or []:
        ccdc = str((proc or {}).get("mopCCDCNumber") or (proc or {}).get("CCDCNumber") or (proc or {}).get("ccdc_number") or "").strip()
        if not ccdc:
            continue
        names = (proc or {}).get("cbuSpeciesNames2") or []
        if isinstance(names, list):
            vals = sorted({ _normalize_name(x) for x in names if str(x).strip() })
            m[ccdc] = vals
    return m


def _score_species_maps(gt_map: Dict[str, List[str]], res_map: Dict[str, List[str]]) -> Tuple[int, int, int]:
    """Score species-name lists per CCDC with fairness: if any match in the list, do not count FP for that CCDC group.

    Returns (tp, fp, fn) across all groups. Names are normalized via to_fingerprint.
    """
    tp_total = fp_total = fn_total = 0
    keys = set(gt_map.keys()) | set(res_map.keys())
    for k in keys:
        gt_names = gt_map.get(k, [])
        res_names = res_map.get(k, [])
        gt_set = {to_fingerprint(x) for x in gt_names if to_fingerprint(x) not in ("", '""')}
        res_set = {to_fingerprint(x) for x in res_names if to_fingerprint(x) not in ("", '""')}

        if not gt_set and not res_set:
            continue
        inter = gt_set & res_set
        tp_total += len(inter)
        # Missing names still count as FN
        fn_total += len(gt_set - res_set)
        # Fairness: only count FP when no match exists in the group
        if len(inter) == 0:
            fp_total += len(res_set - gt_set)
    return tp_total, fp_total, fn_total


def _extract_procedures(data: Any) -> List[Dict[str, Any]]:
    """Extract all synthesis procedures from the data."""
    procedures = []
    for proc in (data or {}).get("synthesisProcedures", []) or []:
        if not isinstance(proc, dict):
            continue

        # Extract CCDC and formulas
        ccdc = str((proc or {}).get("mopCCDCNumber") or (proc or {}).get("CCDCNumber") or (proc or {}).get("ccdc_number") or "").strip()
        f1 = _normalize_cbu_formula(str((proc or {}).get("cbuFormula1") or "").strip())
        f2 = _normalize_cbu_formula(str((proc or {}).get("cbuFormula2") or "").strip())

        # Extract names
        names1 = (proc or {}).get("cbuSpeciesNames1") or []
        if isinstance(names1, list):
            names1_norm = sorted({_normalize_name(x) for x in names1 if str(x).strip()})
        else:
            names1_norm = []

        names2 = (proc or {}).get("cbuSpeciesNames2") or []
        if isinstance(names2, list):
            names2_norm = sorted({_normalize_name(x) for x in names2 if str(x).strip()})
        else:
            names2_norm = []

        procedures.append({
            'ccdc': ccdc,
            'formula1': f1,
            'formula2': f2,
            'names1': names1_norm,
            'names2': names2_norm,
            'all_formulas': {f1, f2} - {''},  # Set of non-empty formulas
            'all_names': set(names1_norm + names2_norm)  # Set of all names
        })

    return procedures


def _score_procedures_flexible(gt_procedures: List[Dict[str, Any]], pred_procedures: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """Score CBU procedures using flexible matching with optimal bipartite assignment (formulas only).

    Uses a simple greedy approach: match each prediction to its best available GT,
    prioritizing higher-scoring matches first. Only considers formula matching.

    Returns (tp, fp, fn) where:
    - TP: Correctly matched formula pairs
    - FP: Extra/incorrect formula pairs in predictions
    - FN: Missing formula pairs in predictions
    """
    tp_total = fp_total = fn_total = 0

    # Calculate all pairwise match scores (formulas only)
    match_scores = []
    for pred_idx, pred_proc in enumerate(pred_procedures):
        for gt_idx, gt_proc in enumerate(gt_procedures):
            # Calculate match score based on formula criteria only
            score = 0

            # CCDC match (if both have CCDC) - strongest signal
            if pred_proc['ccdc'] and gt_proc['ccdc'] and pred_proc['ccdc'] == gt_proc['ccdc']:
                score += 20  # Very strong CCDC match

            # Formula overlap - primary scoring criterion
            formula_overlap = len(pred_proc['all_formulas'] & gt_proc['all_formulas'])
            score += formula_overlap * 10  # Each matching formula adds significant points

            # Individual formula matches (exact)
            if pred_proc['formula1'] and pred_proc['formula1'] == gt_proc['formula1']:
                score += 5
            if pred_proc['formula2'] and pred_proc['formula2'] == gt_proc['formula2']:
                score += 5

            # Only add if there's some formula overlap
            if formula_overlap > 0:
                match_scores.append((score, pred_idx, gt_idx))

    # Sort by score descending (highest scores first)
    match_scores.sort(reverse=True)

    # Greedily assign matches
    matched_gt_indices = set()
    matched_pred_indices = set()

    for score, pred_idx, gt_idx in match_scores:
        if pred_idx in matched_pred_indices or gt_idx in matched_gt_indices:
            continue  # Already matched

        # Make the match
        matched_pred_indices.add(pred_idx)
        matched_gt_indices.add(gt_idx)

        gt_proc = gt_procedures[gt_idx]
        pred_proc = pred_procedures[pred_idx]

        # Score formulas
        gt_formulas = gt_proc['all_formulas']
        pred_formulas = pred_proc['all_formulas']
        tp_total += len(gt_formulas & pred_formulas)
        fn_total += len(gt_formulas - pred_formulas)
        fp_total += len(pred_formulas - gt_formulas)

    # Handle unmatched predictions - all their formulas are FP
    for pred_idx, pred_proc in enumerate(pred_procedures):
        if pred_idx not in matched_pred_indices:
            fp_total += len(pred_proc['all_formulas'])

    # Handle unmatched GT procedures - all their formulas are FN
    for gt_idx, gt_proc in enumerate(gt_procedures):
        if gt_idx not in matched_gt_indices:
            fn_total += len(gt_proc['all_formulas'])

    return tp_total, fp_total, fn_total


def _score_procedures_combined(gt_procedures: List[Dict[str, Any]], pred_procedures: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """Score CBU procedures using flexible matching with optimal bipartite assignment (formulas + species names with fairness).

    Uses a greedy approach prioritizing matches with both formula and species name overlap.
    Implements fairness rule: if there's at least one matching species name in a matched pair,
    don't count extra formulas or species names as FP.

    Returns (tp, fp, fn) where:
    - TP: Correctly matched formula pairs (with fairness for species names)
    - FP: Extra/incorrect formula pairs in predictions (reduced by fairness rule)
    - FN: Missing formula pairs in predictions
    """
    tp_total = fp_total = fn_total = 0

    # Calculate all pairwise match scores (formulas + species names)
    match_scores = []
    for pred_idx, pred_proc in enumerate(pred_procedures):
        for gt_idx, gt_proc in enumerate(gt_procedures):
            # Calculate match score based on multiple criteria
            score = 0

            # CCDC match (if both have CCDC) - strongest signal
            if pred_proc['ccdc'] and gt_proc['ccdc'] and pred_proc['ccdc'] == gt_proc['ccdc']:
                score += 30  # Very strong CCDC match

            # Name overlap - important for distinguishing similar procedures
            name_overlap = len(pred_proc['all_names'] & gt_proc['all_names'])
            score += name_overlap * 12  # Each matching name adds significant points

            # Formula overlap - still important
            formula_overlap = len(pred_proc['all_formulas'] & gt_proc['all_formulas'])
            score += formula_overlap * 8  # Each matching formula adds points

            # Individual formula matches (exact)
            if pred_proc['formula1'] and pred_proc['formula1'] == gt_proc['formula1']:
                score += 4
            if pred_proc['formula2'] and pred_proc['formula2'] == gt_proc['formula2']:
                score += 4

            # Bonus for having both formula and name overlap
            if formula_overlap > 0 and name_overlap > 0:
                score += 10

            match_scores.append((score, pred_idx, gt_idx, name_overlap > 0))

    # Sort by score descending (highest scores first)
    match_scores.sort(reverse=True)

    # Greedily assign matches
    matched_gt_indices = set()
    matched_pred_indices = set()
    matched_pairs_info = []  # Store (pred_idx, gt_idx, has_name_match) for fairness rule

    for score, pred_idx, gt_idx, has_name_match in match_scores:
        if pred_idx in matched_pred_indices or gt_idx in matched_gt_indices:
            continue  # Already matched

        # Make the match
        matched_pred_indices.add(pred_idx)
        matched_gt_indices.add(gt_idx)
        matched_pairs_info.append((pred_idx, gt_idx, has_name_match))

        gt_proc = gt_procedures[gt_idx]
        pred_proc = pred_procedures[pred_idx]

        # Score formulas with fairness rule
        gt_formulas = gt_proc['all_formulas']
        pred_formulas = pred_proc['all_formulas']

        # Apply fairness rule: if there's name overlap, don't count extra formulas as FP
        if has_name_match:
            # Only count truly matching formulas as TP, missing ones as FN
            # Don't count extra formulas in prediction as FP
            tp_total += len(gt_formulas & pred_formulas)
            fn_total += len(gt_formulas - pred_formulas)
            # No FP added for extra formulas when names match
        else:
            # Normal scoring when no name match
            tp_total += len(gt_formulas & pred_formulas)
            fn_total += len(gt_formulas - pred_formulas)
            fp_total += len(pred_formulas - gt_formulas)

        # Also score species names
        gt_names = gt_proc['all_names']
        pred_names = pred_proc['all_names']

        # Apply fairness rule: if there's name overlap, don't count extra names as FP
        if has_name_match:
            # Only count truly matching names as TP, missing ones as FN
            # Don't count extra names in prediction as FP
            tp_total += len(gt_names & pred_names)
            fn_total += len(gt_names - pred_names)
            # No FP added for extra names when names match
        else:
            # Normal scoring when no name match
            tp_total += len(gt_names & pred_names)
            fn_total += len(gt_names - pred_names)
            fp_total += len(pred_names - gt_names)

    # Handle unmatched predictions - all their formulas and names are FP (unless they would match with fairness, but since they're unmatched, no fairness applies)
    for pred_idx, pred_proc in enumerate(pred_procedures):
        if pred_idx not in matched_pred_indices:
            fp_total += len(pred_proc['all_formulas'])
            fp_total += len(pred_proc['all_names'])

    # Handle unmatched GT procedures - all their formulas and names are FN
    for gt_idx, gt_proc in enumerate(gt_procedures):
        if gt_idx not in matched_gt_indices:
            fn_total += len(gt_proc['all_formulas'])
            fn_total += len(gt_proc['all_names'])

    return tp_total, fp_total, fn_total

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




def evaluate_current(use_full_gt: bool = False) -> None:
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/cbu")
        OUT_ROOT = Path("evaluation/data/full_result/cbu")
        allowed_files = None
    else:
        GT_ROOT = Path("full_ground_truth/cbu")  # Always use full_ground_truth
        OUT_ROOT = Path("evaluation/data/result/cbu")
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

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float, int, int, int, float, float, float]]] = []
    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "cbu.json"
        if not doi or not res_path.exists():
            continue

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue

        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            # Skip if ground truth missing
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        # Extract procedures
        gt_procedures = _extract_procedures(gt)
        pred_procedures = _extract_procedures(res)

        # Formula-only scoring
        tp_formula, fp_formula, fn_formula = _score_procedures_flexible(gt_procedures, pred_procedures)
        prec_formula, rec_formula, f1_formula = precision_recall_f1(tp_formula, fp_formula, fn_formula)

        # Combined formula + species names scoring
        tp_combined, fp_combined, fn_combined = _score_procedures_combined(gt_procedures, pred_procedures)
        prec_combined, rec_combined, f1_combined = precision_recall_f1(tp_combined, fp_combined, fn_combined)

        # Keep flat lists for reporting (backward compatibility)
        gt_list: List[str] = []
        for proc in gt_procedures:
            gt_list.extend(sorted(proc['all_formulas']))

        res_list: List[str] = []
        for proc in pred_procedures:
            res_list.extend(sorted(proc['all_formulas']))

        # Store both scoring results (use combined as primary for overall table)
        rows.append((hv, (tp_combined, fp_combined, fn_combined, prec_combined, rec_combined, f1_combined, tp_formula, fp_formula, fn_formula, prec_formula, rec_formula, f1_formula)))

        # Individual report
        lines: List[str] = []
        lines.append(f"# CBU Scoring - {hv}\n\n")
        lines.append(f"**DOI**: `{doi}`  \n")
        lines.append(f"**Hash**: `{hv}`  \n")
        lines.append(f"**Prediction file**: `{res_path.as_posix()}`  \n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")
        lines.append(f"**Combined Scoring (Formulas + Species Names with Fairness):** TP={tp_combined} FP={fp_combined} FN={fn_combined} | P={prec_combined:.3f} R={rec_combined:.3f} F1={f1_combined:.3f}\n")
        lines.append(f"**Formula-only Scoring:** TP={tp_formula} FP={fp_formula} FN={fn_formula} | P={prec_formula:.3f} R={rec_formula:.3f} F1={f1_formula:.3f}\n\n")

        # Species-name scoring per CCDC with fairness (no FP within a list if any TP)
        gt_names1 = _map_cbu_species1_by_ccdc(gt)
        res_names1 = _map_cbu_species1_by_ccdc(res)
        s1_tp, s1_fp, s1_fn = _score_species_maps(gt_names1, res_names1)
        s1_p, s1_r, s1_f1 = precision_recall_f1(s1_tp, s1_fp, s1_fn)

        gt_names2 = _map_cbu_species2_by_ccdc(gt)
        res_names2 = _map_cbu_species2_by_ccdc(res)
        s2_tp, s2_fp, s2_fn = _score_species_maps(gt_names2, res_names2)
        s2_p, s2_r, s2_f1 = precision_recall_f1(s2_tp, s2_fp, s2_fn)

        lines.append(f"**Species Names (list1):** TP={s1_tp} FP={s1_fp} FN={s1_fn} | P={s1_p:.3f} R={s1_r:.3f} F1={s1_f1:.3f}\n")
        lines.append(f"**Species Names (list2):** TP={s2_tp} FP={s2_fp} FN={s2_fn} | P={s2_p:.3f} R={s2_r:.3f} F1={s2_f1:.3f}\n\n")
        
        # Create normalized versions (preserve case for readability in reports)
        gt_normalized = _normalize_json_structure(gt, preserve_case=True)
        res_normalized = _normalize_json_structure(res, preserve_case=True)
        
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(res_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences summary (normalized)
        gt_set = set(gt_list)
        res_set = set(res_list)
        fn_items = sorted(gt_set - res_set)
        fp_items = sorted(res_set - gt_set)
        lines.append("## Differences\n\n")
        lines.append(f"Formulas (Prediction vs GT): {', '.join(sorted(res_set))} - {', '.join(sorted(gt_set))}\n")
        lines.append(f"FN (missing formulas): {', '.join(fn_items) if fn_items else 'None'}\n")
        lines.append(f"FP (extra formulas): {', '.join(fp_items) if fp_items else 'None'}\n\n")
        
        (OUT_ROOT / f"{hv}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report with two tables
    lines_overall: List[str] = []
    lines_overall.append("# CBU Scoring - Overall\n\n")

    # Combined scoring table (primary)
    lines_overall.append("## Combined Scoring (Formulas + Species Names with Fairness)\n\n")
    lines_overall.append("Hash | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("|------|-----|-----|-----|-----------|--------|-----\n")

    total_tp_combined = total_fp_combined = total_fn_combined = 0
    total_tp_formula = total_fp_formula = total_fn_formula = 0

    for hv, (tp_c, fp_c, fn_c, prec_c, rec_c, f1_c, tp_f, fp_f, fn_f, prec_f, rec_f, f1_f) in rows:
        lines_overall.append(f"{hv} | {tp_c} | {fp_c} | {fn_c} | {prec_c:.3f} | {rec_c:.3f} | {f1_c:.3f} |\n")
        total_tp_combined += tp_c
        total_fp_combined += fp_c
        total_fn_combined += fn_c
        total_tp_formula += tp_f
        total_fp_formula += fp_f
        total_fn_formula += fn_f

    overall_prec_combined, overall_rec_combined, overall_f1_combined = precision_recall_f1(total_tp_combined, total_fp_combined, total_fn_combined)
    lines_overall.append(f"**Overall** | **{total_tp_combined}** | **{total_fp_combined}** | **{total_fn_combined}** | **{overall_prec_combined:.3f}** | **{overall_rec_combined:.3f}** | **{overall_f1_combined:.3f}** |\n\n")

    # Formula-only scoring table
    lines_overall.append("## Formula-only Scoring\n\n")
    lines_overall.append("Hash | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("|------|-----|-----|-----|-----------|--------|-----\n")

    for hv, (tp_c, fp_c, fn_c, prec_c, rec_c, f1_c, tp_f, fp_f, fn_f, prec_f, rec_f, f1_f) in rows:
        lines_overall.append(f"{hv} | {tp_f} | {fp_f} | {fn_f} | {prec_f:.3f} | {rec_f:.3f} | {f1_f:.3f} |\n")

    overall_prec_formula, overall_rec_formula, overall_f1_formula = precision_recall_f1(total_tp_formula, total_fp_formula, total_fn_formula)
    lines_overall.append(f"| **Overall** | **{total_tp_formula}** | **{total_fp_formula}** | **{total_fn_formula}** | **{overall_prec_formula:.3f}** | **{overall_rec_formula:.3f}** | **{overall_f1_formula:.3f}** |\n\n")

    lines_overall.append(f"**Combined Scoring Summary:** TP={total_tp_combined} FP={total_fp_combined} FN={total_fn_combined} | P={overall_prec_combined:.3f} R={overall_rec_combined:.3f} F1={overall_f1_combined:.3f}\n")
    lines_overall.append(f"**Formula-only Scoring Summary:** TP={total_tp_formula} FP={total_fp_formula} FN={total_fn_formula} | P={overall_prec_formula:.3f} R={overall_rec_formula:.3f} F1={overall_f1_formula:.3f}\n")
    
    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_full() -> None:
    GT_ROOT = Path("full_ground_truth/cbu")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/full_result/cbu")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float, int, int, int, float, float, float]]] = []
    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "cbu.json"
        if not doi or not res_path.exists():
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        # Extract procedures
        gt_procedures = _extract_procedures(gt)
        pred_procedures = _extract_procedures(res)

        # Formula-only scoring
        tp_formula, fp_formula, fn_formula = _score_procedures_flexible(gt_procedures, pred_procedures)
        prec_formula, rec_formula, f1_formula = precision_recall_f1(tp_formula, fp_formula, fn_formula)

        # Combined formula + species names scoring
        tp_combined, fp_combined, fn_combined = _score_procedures_combined(gt_procedures, pred_procedures)
        prec_combined, rec_combined, f1_combined = precision_recall_f1(tp_combined, fp_combined, fn_combined)

        rows.append((hv, (tp_combined, fp_combined, fn_combined, prec_combined, rec_combined, f1_combined, tp_formula, fp_formula, fn_formula, prec_formula, rec_formula, f1_formula)))

        # Keep flat lists for reporting
        gt_list: List[str] = []
        for proc in gt_procedures:
            gt_list.extend(sorted(proc['all_formulas']))

        res_list: List[str] = []
        for proc in pred_procedures:
            res_list.extend(sorted(proc['all_formulas']))

        lines: List[str] = []
        lines.append(f"# CBU Scoring (Full GT) - {hv}\n\n")
        lines.append(f"**DOI**: `{doi}`  \n")
        lines.append(f"**Hash**: `{hv}`  \n")
        lines.append(f"**Prediction file**: `{res_path.as_posix()}`  \n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")
        lines.append(f"**Combined Scoring (Formulas + Species Names with Fairness):** TP={tp_combined} FP={fp_combined} FN={fn_combined} | P={prec_combined:.3f} R={rec_combined:.3f} F1={f1_combined:.3f}\n")
        lines.append(f"**Formula-only Scoring:** TP={tp_formula} FP={fp_formula} FN={fn_formula} | P={prec_formula:.3f} R={rec_formula:.3f} F1={f1_formula:.3f}\n\n")

        gt_names1 = _map_cbu_species1_by_ccdc(gt)
        res_names1 = _map_cbu_species1_by_ccdc(res)
        s1_tp, s1_fp, s1_fn = _score_species_maps(gt_names1, res_names1)
        s1_p, s1_r, s1_f1 = precision_recall_f1(s1_tp, s1_fp, s1_fn)

        gt_names2 = _map_cbu_species2_by_ccdc(gt)
        res_names2 = _map_cbu_species2_by_ccdc(res)
        s2_tp, s2_fp, s2_fn = _score_species_maps(gt_names2, res_names2)
        s2_p, s2_r, s2_f1 = precision_recall_f1(s2_tp, s2_fp, s2_fn)

        lines.append(f"**Species Names (list1):** TP={s1_tp} FP={s1_fp} FN={s1_fn} | P={s1_p:.3f} R={s1_r:.3f} F1={s1_f1:.3f}\n")
        lines.append(f"**Species Names (list2):** TP={s2_tp} FP={s2_fp} FN={s2_fn} | P={s2_p:.3f} R={s2_r:.3f} F1={s2_f1:.3f}\n\n")

        gt_normalized = _normalize_json_structure(gt, preserve_case=True)
        res_normalized = _normalize_json_structure(res, preserve_case=True)
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(res_normalized, indent=2))
        lines.append("\n```\n\n")

        gt_set = set(gt_list)
        res_set = set(res_list)
        fn_items = sorted(gt_set - res_set)
        fp_items = sorted(res_set - gt_set)
        lines.append("## Differences\n\n")
        lines.append(f"Formulas (Prediction vs GT): {', '.join(sorted(res_set))} - {', '.join(sorted(gt_set))}\n")
        lines.append(f"FN (missing formulas): {', '.join(fn_items) if fn_items else 'None'}\n")
        lines.append(f"FP (extra formulas): {', '.join(fp_items) if fp_items else 'None'}\n\n")
        (OUT_ROOT / f"{hv}.md").write_text("".join(lines), encoding="utf-8")

    lines_overall: List[str] = []
    lines_overall.append("# CBU Scoring (Full GT) - Overall\n\n")

    # Combined scoring table (primary)
    lines_overall.append("## Combined Scoring (Formulas + Species Names with Fairness)\n\n")
    lines_overall.append("Hash | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("|------|-----|-----|-----|-----------|--------|-----\n")

    total_tp_combined = total_fp_combined = total_fn_combined = 0
    total_tp_formula = total_fp_formula = total_fn_formula = 0

    for hv, (tp_c, fp_c, fn_c, prec_c, rec_c, f1_c, tp_f, fp_f, fn_f, prec_f, rec_f, f1_f) in rows:
        lines_overall.append(f"{hv} | {tp_c} | {fp_c} | {fn_c} | {prec_c:.3f} | {rec_c:.3f} | {f1_c:.3f} |\n")
        total_tp_combined += tp_c
        total_fp_combined += fp_c
        total_fn_combined += fn_c
        total_tp_formula += tp_f
        total_fp_formula += fp_f
        total_fn_formula += fn_f

    overall_prec_combined, overall_rec_combined, overall_f1_combined = precision_recall_f1(total_tp_combined, total_fp_combined, total_fn_combined)
    lines_overall.append(f"**Overall** | **{total_tp_combined}** | **{total_fp_combined}** | **{total_fn_combined}** | **{overall_prec_combined:.3f}** | **{overall_rec_combined:.3f}** | **{overall_f1_combined:.3f}** |\n\n")

    # Formula-only scoring table
    lines_overall.append("## Formula-only Scoring\n\n")
    lines_overall.append("Hash | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("|------|-----|-----|-----|-----------|--------|-----\n")

    for hv, (tp_c, fp_c, fn_c, prec_c, rec_c, f1_c, tp_f, fp_f, fn_f, prec_f, rec_f, f1_f) in rows:
        lines_overall.append(f"{hv} | {tp_f} | {fp_f} | {fn_f} | {prec_f:.3f} | {rec_f:.3f} | {f1_f:.3f} |\n")

    overall_prec_formula, overall_rec_formula, overall_f1_formula = precision_recall_f1(total_tp_formula, total_fp_formula, total_fn_formula)
    lines_overall.append(f"| **Overall** | **{total_tp_formula}** | **{total_fp_formula}** | **{total_fn_formula}** | **{overall_prec_formula:.3f}** | **{overall_rec_formula:.3f}** | **{overall_f1_formula:.3f}** |\n\n")

    lines_overall.append(f"**Combined Scoring Summary:** TP={total_tp_combined} FP={total_fp_combined} FN={total_fn_combined} | P={overall_prec_combined:.3f} R={overall_rec_combined:.3f} F1={overall_f1_combined:.3f}\n")
    lines_overall.append(f"**Formula-only Scoring Summary:** TP={total_tp_formula} FP={total_fp_formula} FN={total_fn_formula} | P={overall_prec_formula:.3f} R={overall_rec_formula:.3f} F1={overall_f1_formula:.3f}\n")

    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_previous(use_anchored: bool = False, *, use_full_gt: bool = False) -> None:
    """Use the SAME evaluation as current mode (list-based formulas across procedures)."""
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/cbu")
        OUT_ROOT = Path("evaluation/data/full_result/cbu_previous")
        allowed_files = None
    else:
        GT_ROOT = Path("full_ground_truth/cbu")  # Always use full_ground_truth
        OUT_ROOT = Path("evaluation/data/result/cbu_previous")
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
    PREV_ROOT = Path("previous_work_anchored/cbu") if use_anchored else Path("previous_work/cbu")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []

    # Iterate over ALL GT DOIs to ensure one report per DOI
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

        # Flexible procedure-based scoring
        gt_procedures = _extract_procedures(gt)
        
        res = None
        pred_procedures = []
        if prev_path.exists():
            try:
                res = json.loads(prev_path.read_text(encoding="utf-8"))
                pred_procedures = _extract_procedures(res)
            except Exception:
                res = None

        # Score
        tp, fp, fn = _score_procedures_flexible(gt_procedures, pred_procedures)
        
        # Keep flat lists for reporting
        gt_list: List[str] = []
        for proc in gt_procedures:
            gt_list.extend(sorted(proc['all_formulas']))
        
        res_list: List[str] = []
        for proc in pred_procedures:
            res_list.extend(sorted(proc['all_formulas']))
        rows_overall.append((doi, (tp, fp, fn)))

        # Per-DOI report (normalized)
        lines: List[str] = []
        suffix = " (Full GT)" if use_full_gt else ""
        lines.append(f"# CBU Previous Scoring{suffix} - {doi}\n\n")
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines.append(f"**Fine-grained Scoring (Formulas):** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n\n")

        # Species-name scoring per CCDC
        gt_names1 = _map_cbu_species1_by_ccdc(gt)
        gt_names2 = _map_cbu_species2_by_ccdc(gt)
        if res:
            res_names1 = _map_cbu_species1_by_ccdc(res)
            res_names2 = _map_cbu_species2_by_ccdc(res)
            s1_tp, s1_fp, s1_fn = _score_species_maps(gt_names1, res_names1)
            s2_tp, s2_fp, s2_fn = _score_species_maps(gt_names2, res_names2)
        else:
            # No prediction: all FNs for names
            s1_tp = s2_tp = 0
            s1_fp = s2_fp = 0
            s1_fn = sum(len(v) for v in gt_names1.values())
            s2_fn = sum(len(v) for v in gt_names2.values())
        s1_p, s1_r, s1_f1 = precision_recall_f1(s1_tp, s1_fp, s1_fn)
        s2_p, s2_r, s2_f1 = precision_recall_f1(s2_tp, s2_fp, s2_fn)
        lines.append(f"**Species Names (list1):** TP={s1_tp} FP={s1_fp} FN={s1_fn} | P={s1_p:.3f} R={s1_r:.3f} F1={s1_f1:.3f}\n")
        lines.append(f"**Species Names (list2):** TP={s2_tp} FP={s2_fp} FN={s2_fn} | P={s2_p:.3f} R={s2_r:.3f} F1={s2_f1:.3f}\n\n")

        gt_normalized = _normalize_json_structure(gt, preserve_case=True)
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")

        lines.append("## Prediction\n")
        if res:
            pred_normalized = _normalize_json_structure(res, preserve_case=True)
            lines.append("```json\n")
            lines.append(json.dumps(pred_normalized, indent=2))
            lines.append("\n```\n\n")
        else:
            lines.append("(none)\n\n")

        gt_set = set(gt_list)
        res_set = set(res_list)
        fn_items = sorted(gt_set - res_set)
        fp_items = sorted(res_set - gt_set)
        lines.append("## Differences\n\n")
        lines.append(f"Formulas (Prediction vs GT): {', '.join(sorted(res_set)) or 'N/A'} - {', '.join(sorted(gt_set)) or 'N/A'}\n")
        lines.append(f"FN (missing formulas): {', '.join(fn_items) if fn_items else 'None'}\n")
        lines.append(f"FP (extra formulas): {', '.join(fp_items) if fp_items else 'None'}\n\n")
        (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    suffix = " (Full GT)" if use_full_gt else ""
    lines_overall.append(f"# CBU Previous Scoring{suffix} - Overall\n\n")
    lines_overall.append("| DOI | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("|------|-----|-----|-----|-----------|--------|-----\n")

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

    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="CBU scoring evaluator")
    parser.add_argument("--previous", action="store_true", help="Evaluate previous_work/cbu/*.json against ground truth using CCDC anchoring")
    parser.add_argument("--anchor", action="store_true", help="Use previous_work_anchored/ instead of previous_work/ (only with --previous)")
    parser.add_argument("--full", action="store_true", help="Use full ground truth set and write to evaluation/data/full_result/*")
    args = parser.parse_args()

    if args.previous:
        evaluate_previous(use_anchored=args.anchor, use_full_gt=args.full)
    elif args.full:
        evaluate_full()
    else:
        evaluate_current(use_full_gt=args.full)


if __name__ == "__main__":
    main()


