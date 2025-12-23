"""
Characterisation scoring evaluator

Usage:
    # Current predictions vs earlier ground truth
    python evaluation/scoring_characterisation.py

    # Current predictions vs full ground truth
    python evaluation/scoring_characterisation.py --full

    # Previous work vs earlier ground truth
    python evaluation/scoring_characterisation.py --previous

    # Previous work vs full ground truth
    python evaluation/scoring_characterisation.py --previous --full

    # Previous work anchored vs full ground truth
    python evaluation/scoring_characterisation.py --previous --anchor --full
"""

import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any, Set
from evaluation.utils.scoring_common import precision_recall_f1, hash_map_reverse


def _is_na_string(raw: Any) -> bool:
    try:
        v = str(raw or "").strip().lower()
    except Exception:
        return False
    return v in {"n/a", "na", "n.a.", "none", "null", "-", ""}


def _normalize(s: Any) -> str:
    """Normalize a value to lowercase string; canonicalize NA-like values to empty string."""
    val = str(s or "").strip()
    # Normalize Unicode primes/quotes to ASCII (align with chemicals scoring)
    try:
        val = val.replace("\u2034", "\u2033")  # ‴ -> ″ (collapse triple prime)
        val = val.replace("\u2032", "'")       # ′ -> '
        val = val.replace("\u2033", '"')        # ″ -> "
        val = val.replace("\u2019", "'")       # ’ -> '
        val = val.replace("\u201D", '"')        # ” -> "
    except Exception:
        pass
    # Normalize whitespace: replace multiple spaces/tabs with single space
    import re
    val = re.sub(r'\s+', ' ', val)
    # Normalize spacing around commas and semicolons
    val = re.sub(r'\s*,\s*', ', ', val)
    val = re.sub(r'\s*;\s*', '; ', val)
    # Normalize Oxford comma / conjunction patterns: ", and " -> ", "
    val = re.sub(r',\s*and\s+', ', ', val)
    # Unicode normalization for micro and middle dot
    try:
        val = val.replace('\u00B5', 'µ')  # micro sign
        val = val.replace('\u03BC', 'µ')  # Greek small mu
        val = val.replace('\u22C5', '·')  # dot operator to middle dot
        val = val.replace('\u2219', '·')  # bullet operator to middle dot
    except Exception:
        pass
    val = val.lower().strip()
    # Canonical NA handling
    if _is_na_string(val):
        return ""
    return val


def _is_valid(s: Any) -> bool:
    """Check if a value is valid (not N/A or empty)."""
    return _normalize(s) != ""


def _normalize_percent(s: Any) -> str:
    """Normalize a percentage-like string for comparison; NA-like becomes empty.
    Handles both formats: 'C: 37.50; H: 4.26' and 'C 37.50, H 4.26'
    """
    v = _normalize(s)
    if not v:
        return v
    # Remove percentage symbols
    v = v.replace('%', '')
    # Normalize both separator styles to comma
    v = v.replace(';', ',')
    # Normalize both "element:" and "element " to just "element"
    import re as _re
    v = _re.sub(r'([a-z]):\s*', r'\1', v)
    # Remove all spaces for consistent comparison
    v = v.replace(' ', '')
    return v.strip()


def _normalize_display_value(s: Any) -> str:
    v = _normalize(s)
    return "N/A" if v == "" else v


def _normalize_json_structure(obj: Any) -> Any:
    """Recursively normalize all string values for display; NA-like -> 'N/A'."""
    if isinstance(obj, dict):
        return {k: _normalize_json_structure(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_normalize_json_structure(item) for item in obj]
    elif isinstance(obj, str):
        return _normalize_display_value(obj)
    else:
        return obj


def _normalize_ir_material(s: Any) -> str:
    """Normalize IR material names (e.g., map 'kbr pellet(s)' to 'kbr')."""
    v = _normalize(s)
    if not v:
        return v
    try:
        import re as _re
        v = _re.sub(r"\bkbr\s*pellets?\b", "kbr", v)
    except Exception:
        pass
    return v


def _normalize_parenthetical_spacing(s: Any) -> str:
    v = _normalize(s)
    if not v:
        return v
    try:
        import re as _re
        # Ensure a single space before opening parenthesis following a digit or letter
        v = _re.sub(r'(?<=[0-9a-z])\s*\(', ' (', v)
        # Ensure no extra spaces before closing parenthesis
        v = _re.sub(r'\s*\)', ')', v)
        # Ensure a single space after comma and semicolon (already handled in _normalize), re-apply lightly
        v = _re.sub(r',\s*', ', ', v)
        v = _re.sub(r';\s*', '; ', v)
        # Collapse multiple spaces
        v = _re.sub(r'\s{2,}', ' ', v)
    except Exception:
        pass
    return v.strip()


def _normalize_ir_bands(s: Any) -> str:
    """Normalize IR bands: remove material prefix, standardize units, normalize spacing."""
    v = _normalize_parenthetical_spacing(s)
    if not v:
        return v
    try:
        import re as _re
        # Remove redundant material prefixes like "KBr, cm-1:" or "kbr pellet, cm-1:"
        v = _re.sub(r'^kbr\s*(?:pellets?)?\s*,\s*', '', v)
        # Standardize all cm unit variants to "cm-1"
        # Handle: cm⁻¹ (Unicode superscript), cm^-1 (caret), cm-1 (hyphen)
        v = v.replace('cm⁻¹', 'cm-1')
        v = v.replace('cm^-1', 'cm-1')
        # Remove duplicate semicolon-separated entries (e.g., "... cm^-1 ; ... cm-1")
        parts = [p.strip() for p in v.split(';')]
        # Keep only unique normalized parts
        seen = set()
        unique_parts = []
        for p in parts:
            normalized = p.replace('cm⁻¹', 'cm-1').replace('cm^-1', 'cm-1')
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_parts.append(normalized)
        v = unique_parts[0] if unique_parts else v
    except Exception:
        pass
    return v.strip()


def _normalize_shifts(s: Any) -> str:
    return _normalize_parenthetical_spacing(s)


def _normalize_chemical_formula(s: Any) -> str:
    """Normalize chemical formulas: remove middot separators for comparison."""
    v = _normalize(s)
    if not v:
        return v
    # Remove middot/interpunct separators (·) and other dot operators
    v = v.replace('·', '')
    v = v.replace('\u00B7', '')  # middle dot
    v = v.replace('\u22C5', '')  # dot operator
    v = v.replace('\u2219', '')  # bullet operator
    # Remove all spaces for consistent comparison
    v = v.replace(' ', '')
    return v.strip()

def _collect_characterisations(data: Any) -> Dict[str, Any]:
    """Return a map of key->characterisation, where key is CCDC or NAME:<productName> fallback.
    Supports both GT schema (Devices[].Characterisation[]) and merged schema (characterisations[]).
    """
    chars: Dict[str, Any] = {}
    if not data:
        return chars
    # GT-like structure
    for dev in (data or {}).get("Devices", []) or []:
        for ch in (dev or {}).get("Characterisation", []) or []:
            ccdc = str((ch or {}).get("productCCDCNumber") or "").strip()
            if not ccdc or ccdc.lower() in ["n/a", "na", ""]:
                names = (ch or {}).get("productNames") or []
                if names:
                    ccdc = f"NAME:{str(names[0]).strip()}"
            if ccdc and ccdc.lower() not in ["n/a", "na", ""]:
                chars[ccdc] = ch
    # Merged flat list structure
    if not chars:
        for ch in (data or {}).get("characterisations", []) or []:
            ccdc = str((ch or {}).get("productCCDCNumber") or "").strip()
            if not ccdc or ccdc.lower() in ["n/a", "na", ""]:
                names = (ch or {}).get("productNames") or []
                if names:
                    ccdc = f"NAME:{str(names[0]).strip()}"
            if ccdc and ccdc.lower() not in ["n/a", "na", ""]:
                chars[ccdc] = ch
    return chars


def _pred_has_missing_ccdc(pred: Any) -> bool:
    # Check GT-like structure
    for dev in (pred or {}).get("Devices", []) or []:
        for ch in (dev or {}).get("Characterisation", []) or []:
            ccdc = str((ch or {}).get("productCCDCNumber") or "").strip()
            if not ccdc or ccdc.lower() in ["n/a", "na", ""]:
                return True
    # Check merged flat list structure
    for ch in (pred or {}).get("characterisations", []) or []:
        ccdc = str((ch or {}).get("productCCDCNumber") or "").strip()
        if not ccdc or ccdc.lower() in ["n/a", "na", ""]:
            return True
    return False


def score_characterisation_fine_grained(gt_obj: Any, pred_obj: Any) -> Tuple[int, int, int, bool]:
    tp = fp = fn = 0
    pred_missing_ccdc = _pred_has_missing_ccdc(pred_obj)

    gt_chars = _collect_characterisations(gt_obj)
    pr_chars = _collect_characterisations(pred_obj)

    all_keys: Set[str] = set(gt_chars.keys()) | set(pr_chars.keys())

    def _is_ccdc_only(name: str) -> bool:
        import re
        return bool(re.match(r'^\d{6,7}$', str(name).strip()))

    def _normalize_percent(s: Any) -> str:
        v = _normalize(s)
        return v.replace('%', '').strip()

    for key in all_keys:
        gt_ch = gt_chars.get(key, {})
        pr_ch = pr_chars.get(key, {})

        # Key match itself
        if key in gt_chars and key in pr_chars:
            tp += 1
        elif key in gt_chars:
            fn += 1
        elif key in pr_chars:
            fp += 1

        # Product names comparison (ignore FP - extra names are not errors)
        gt_names = set(_normalize(n) for n in ((gt_ch or {}).get("productNames") or []) if _is_valid(n) and not _is_ccdc_only(n))
        pr_names = set(_normalize(n) for n in ((pr_ch or {}).get("productNames") or []) if _is_valid(n) and not _is_ccdc_only(n))
        if not gt_names and not pr_names:
            tp += 1
        else:
            tp += len(gt_names & pr_names)
            fn += len(gt_names - pr_names)
            # fp += len(pr_names - gt_names)  # Ignore FP for chemical names

        # HNMR
        gt_hnmr = (gt_ch or {}).get("HNMR") or {}
        pr_hnmr = (pr_ch or {}).get("HNMR") or {}
        for field in ["shifts", "solvent", "temperature"]:
            if field == "shifts":
                gt_val = _normalize_shifts(gt_hnmr.get(field))
                pr_val = _normalize_shifts(pr_hnmr.get(field))
                # Map NA-like to empty for comparison
                gt_val = "" if _is_na_string(gt_hnmr.get(field)) else gt_val
                pr_val = "" if _is_na_string(pr_hnmr.get(field)) else pr_val
            else:
                gt_val = _normalize(gt_hnmr.get(field))
                pr_val = _normalize(pr_hnmr.get(field))
            if gt_val and pr_val:
                if gt_val == pr_val:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif not gt_val and not pr_val:
                tp += 1
            elif gt_val:
                fn += 1
            elif pr_val:
                fp += 1

        # ElementalAnalysis
        gt_ea = (gt_ch or {}).get("ElementalAnalysis") or {}
        pr_ea = (pr_ch or {}).get("ElementalAnalysis") or {}
        for field in ["weightPercentageCalculated", "weightPercentageExperimental", "chemicalFormula"]:
            if field in ("weightPercentageCalculated", "weightPercentageExperimental"):
                gt_val = _normalize_percent(gt_ea.get(field))
                pr_val = _normalize_percent(pr_ea.get(field))
            elif field == "chemicalFormula":
                gt_val = _normalize_chemical_formula(gt_ea.get(field))
                pr_val = _normalize_chemical_formula(pr_ea.get(field))
            else:
                gt_val = _normalize(gt_ea.get(field))
                pr_val = _normalize(pr_ea.get(field))
            if gt_val and pr_val:
                if gt_val == pr_val:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif not gt_val and not pr_val:
                tp += 1
            elif gt_val:
                fn += 1
            elif pr_val:
                fp += 1

        # IR
        gt_ir = (gt_ch or {}).get("InfraredSpectroscopy") or {}
        pr_ir = (pr_ch or {}).get("InfraredSpectroscopy") or {}
        for field in ["material", "bands"]:
            if field == "material":
                gt_val = _normalize_ir_material(gt_ir.get(field))
                pr_val = _normalize_ir_material(pr_ir.get(field))
            else:
                gt_val = _normalize_ir_bands(gt_ir.get(field))
                pr_val = _normalize_ir_bands(pr_ir.get(field))
            if gt_val and pr_val:
                if gt_val == pr_val:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif not gt_val and not pr_val:
                tp += 1
            elif gt_val:
                fn += 1
            elif pr_val:
                fp += 1

    return tp, fp, fn, pred_missing_ccdc


def _collect_name_union(ch_map: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    def _is_ccdc_only(name: str) -> bool:
        import re
        return bool(re.match(r'^\d{6,7}$', str(name).strip()))
    for ch in ch_map.values():
        for n in (ch or {}).get("productNames", []) or []:
            if _is_valid(n) and not _is_ccdc_only(n):
                names.add(_normalize(n))
    return names


def _collect_field_differences(gt_chars: Dict[str, Any], pr_chars: Dict[str, Any]) -> List[str]:
    diffs: List[str] = []
    all_keys: Set[str] = set(gt_chars.keys()) | set(pr_chars.keys())
    for key in sorted(all_keys):
        gt_ch = gt_chars.get(key, {})
        pr_ch = pr_chars.get(key, {})
        per_key: List[str] = []

        # HNMR fields
        gt_h = (gt_ch or {}).get("HNMR") or {}
        pr_h = (pr_ch or {}).get("HNMR") or {}
        for f in ["shifts", "solvent", "temperature"]:
            if f == "shifts":
                g = _normalize_shifts(gt_h.get(f))
                p = _normalize_shifts(pr_h.get(f))
                # Map NA-like to display 'N/A'
                g = "" if _is_na_string(gt_h.get(f)) else g
                p = "" if _is_na_string(pr_h.get(f)) else p
            else:
                g = _normalize(gt_h.get(f))
                p = _normalize(pr_h.get(f))
            if g == p:
                continue
            if g or p:
                per_key.append(f"HNMR.{f}: pred='{(p or 'N/A')}' vs gt='{(g or 'N/A')}'")

        # Elemental Analysis
        gt_e = (gt_ch or {}).get("ElementalAnalysis") or {}
        pr_e = (pr_ch or {}).get("ElementalAnalysis") or {}
        for f in ["chemicalFormula", "weightPercentageCalculated", "weightPercentageExperimental"]:
            if f in ("weightPercentageCalculated", "weightPercentageExperimental"):
                g = _normalize_percent(gt_e.get(f))
                p = _normalize_percent(pr_e.get(f))
            elif f == "chemicalFormula":
                g = _normalize_chemical_formula(gt_e.get(f))
                p = _normalize_chemical_formula(pr_e.get(f))
            else:
                g = _normalize(gt_e.get(f))
                p = _normalize(pr_e.get(f))
            if g == p:
                continue
            if g or p:
                per_key.append(f"ElementalAnalysis.{f}: pred='{(p or 'N/A')}' vs gt='{(g or 'N/A')}'")

        # Infrared Spectroscopy
        gt_ir = (gt_ch or {}).get("InfraredSpectroscopy") or {}
        pr_ir = (pr_ch or {}).get("InfraredSpectroscopy") or {}
        for f in ["material", "bands"]:
            if f == "material":
                g = _normalize_ir_material(gt_ir.get(f))
                p = _normalize_ir_material(pr_ir.get(f))
            else:
                g = _normalize_ir_bands(gt_ir.get(f))
                p = _normalize_ir_bands(pr_ir.get(f))
            if g == p:
                continue
            if g or p:
                per_key.append(f"Infrared.{f}: pred='{p or 'N/A'}' vs gt='{g or 'N/A'}'")

        if per_key:
            diffs.append(f"- [{key}] " + "; ".join(per_key))
    return diffs

def evaluate_current(use_full_gt: bool = False) -> None:
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/characterisation")
        OUT_ROOT = Path("evaluation/data/full_result/characterisation")
        allowed_files = None
    else:
        GT_ROOT = Path("full_ground_truth/characterisation")  # Always use full_ground_truth
        OUT_ROOT = Path("evaluation/data/result/characterisation")
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

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    files_with_missing_ccdc: List[str] = []

    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "characterisation.json"
        if not doi or not res_path.exists():
            continue

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue

        try:
            gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error loading GT file {gt_path}: {e}")
            continue
        
        try:
            pred_obj = json.loads(res_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error loading prediction file {res_path}: {e}")
            continue

        tp, fp, fn, pred_missing = score_characterisation_fine_grained(gt_obj, pred_obj)
        if pred_missing:
            files_with_missing_ccdc.append(f"characterisation/{hv}.json")

        rows_overall.append((hv, (tp, fp, fn)))

        # Per-item markdown report
        lines: List[str] = []
        lines.append(f"# Characterisation Scoring - {hv}\n\n")
        lines.append(f"**DOI**: `{doi}`  \\n")
        lines.append(f"**Hash**: `{hv}`  \\n")
        lines.append(f"**Prediction file**: `{res_path.as_posix()}`  \\n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n\n")
        
        # Create normalized versions
        gt_normalized = _normalize_json_structure(gt_obj)
        pred_normalized = _normalize_json_structure(pred_obj)
        
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences summary (normalized)
        gt_chars = _collect_characterisations(gt_obj)
        pr_chars = _collect_characterisations(pred_obj)
        gt_keys = set(gt_chars.keys())
        pr_keys = set(pr_chars.keys())
        fn_keys = sorted(gt_keys - pr_keys)
        fp_keys = sorted(pr_keys - gt_keys)
        gt_names = _collect_name_union(gt_chars)
        pr_names = _collect_name_union(pr_chars)
        fn_names = sorted(gt_names - pr_names)
        fp_names = sorted(pr_names - gt_names)
        lines.append("## Differences\n\n")
        lines.append(f"Keys (Prediction vs GT): {', '.join(sorted(pr_keys))} - {', '.join(sorted(gt_keys))}\n")
        lines.append(f"FN (missing keys): {', '.join(fn_keys) if fn_keys else 'None'}\n")
        lines.append(f"FP (extra keys): {', '.join(fp_keys) if fp_keys else 'None'}\n")
        lines.append(f"FN (missing product names): {', '.join(fn_names) if fn_names else 'None'}\n")
        lines.append(f"FP (extra product names): {', '.join(fp_names) if fp_names else 'None'}\n")

        # Field-level differences (HNMR, ElementalAnalysis, IR)
        field_diffs = _collect_field_differences(gt_chars, pr_chars)
        if field_diffs:
            lines.append("\n### Field-level differences\n\n")
            for d in field_diffs:
                lines.append(d + "\n")
        lines.append("\n")
        
        (OUT_ROOT / f"{hv}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# Characterisation Scoring - Overall\n\n")
    lines_overall.append("| ID | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")

    total_tp = total_fp = total_fn = 0
    for ident, (tp, fp, fn) in rows_overall:
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines_overall.append(f"| {ident} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn

    overall_prec, overall_rec, overall_f1 = precision_recall_f1(total_tp, total_fp, total_fn)
    lines_overall.append(f"| **Overall** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{overall_prec:.3f}** | **{overall_rec:.3f}** | **{overall_f1:.3f}** |\n")
    lines_overall.append(f"\n**Fine-grained Scoring:** TP={total_tp} FP={total_fp} FN={total_fn} | P={overall_prec:.3f} R={overall_rec:.3f} F1={overall_f1:.3f}\n")

    if files_with_missing_ccdc:
        lines_overall.append("\n## Files with Missing or N/A CCDC Numbers\n\n")
        for fpath in sorted(set(files_with_missing_ccdc)):
            lines_overall.append(f"- `{fpath}`\n")

    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_full() -> None:
    """Evaluate current predictions against full ground truth dataset."""
    GT_ROOT = Path("full_ground_truth/characterisation")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/full_result/characterisation")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])
    allowed_files = None

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    files_with_missing_ccdc: List[str] = []

    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "characterisation.json"
        if not doi or not res_path.exists():
            continue

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue

        try:
            gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error loading GT file {gt_path}: {e}")
            continue
        
        try:
            pred_obj = json.loads(res_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error loading prediction file {res_path}: {e}")
            continue

        tp, fp, fn, pred_missing = score_characterisation_fine_grained(gt_obj, pred_obj)
        if pred_missing:
            files_with_missing_ccdc.append(f"characterisation/{hv}.json")

        rows_overall.append((hv, (tp, fp, fn)))

        # Per-item markdown report
        lines: List[str] = []
        lines.append(f"# Characterisation Scoring (Full GT) - {hv}\n\n")
        lines.append(f"**DOI**: `{doi}`  \\n")
        lines.append(f"**Hash**: `{hv}`  \\n")
        lines.append(f"**Prediction file**: `{res_path.as_posix()}`  \\n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n\n")
        
        # Create normalized versions
        gt_normalized = _normalize_json_structure(gt_obj)
        pred_normalized = _normalize_json_structure(pred_obj)
        
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences summary (normalized)
        gt_chars = _collect_characterisations(gt_obj)
        pr_chars = _collect_characterisations(pred_obj)
        gt_keys = set(gt_chars.keys())
        pr_keys = set(pr_chars.keys())
        fn_keys = sorted(gt_keys - pr_keys)
        fp_keys = sorted(pr_keys - gt_keys)
        gt_names = _collect_name_union(gt_chars)
        pr_names = _collect_name_union(pr_chars)
        fn_names = sorted(gt_names - pr_names)
        fp_names = sorted(pr_names - gt_names)
        lines.append("## Differences\n\n")
        lines.append(f"Keys (Prediction vs GT): {', '.join(sorted(pr_keys))} - {', '.join(sorted(gt_keys))}\n")
        lines.append(f"FN (missing keys): {', '.join(fn_keys) if fn_keys else 'None'}\n")
        lines.append(f"FP (extra keys): {', '.join(fp_keys) if fp_keys else 'None'}\n")
        lines.append(f"FN (missing product names): {', '.join(fn_names) if fn_names else 'None'}\n")
        lines.append(f"FP (extra product names): {', '.join(fp_names) if fp_names else 'None'}\n")

        # Field-level differences (HNMR, ElementalAnalysis, IR)
        field_diffs = _collect_field_differences(gt_chars, pr_chars)
        if field_diffs:
            lines.append("\n### Field-level differences\n\n")
            for d in field_diffs:
                lines.append(d + "\n")
        lines.append("\n")
        
        (OUT_ROOT / f"{hv}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# Characterisation Scoring (Full GT) - Overall\n\n")
    lines_overall.append("| ID | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")

    total_tp = total_fp = total_fn = 0
    for ident, (tp, fp, fn) in rows_overall:
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines_overall.append(f"| {ident} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn

    overall_prec, overall_rec, overall_f1 = precision_recall_f1(total_tp, total_fp, total_fn)
    lines_overall.append(f"| **Overall** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{overall_prec:.3f}** | **{overall_rec:.3f}** | **{overall_f1:.3f}** |\n")
    lines_overall.append(f"\n**Fine-grained Scoring:** TP={total_tp} FP={total_fp} FN={total_fn} | P={overall_prec:.3f} R={overall_rec:.3f} F1={overall_f1:.3f}\n")

    if files_with_missing_ccdc:
        lines_overall.append("\n## Files with Missing or N/A CCDC Numbers\n\n")
        for fpath in sorted(set(files_with_missing_ccdc)):
            lines_overall.append(f"- `{fpath}`\n")

    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def evaluate_previous(use_anchored: bool = False, use_full_gt: bool = False) -> None:
    """Evaluate previous predictions.

    Args:
        use_anchored: Use previous_work_anchored instead of previous_work
        use_full_gt: Use full ground truth dataset instead of earlier ground truth
    """
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/characterisation")
        OUT_ROOT = Path("evaluation/data/full_result/characterisation_previous")
        allowed_files = None
    else:
        GT_ROOT = Path("full_ground_truth/characterisation")  # Always use full_ground_truth
        OUT_ROOT = Path("evaluation/data/result/characterisation_previous")
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
    
    PREV_ROOT = Path("previous_work_anchored/characterisation") if use_anchored else Path("previous_work/characterisation")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    files_with_missing_ccdc: List[str] = []

    # Map DOI -> hash for reporting
    doi_to_hash: Dict[str, str] = {}
    try:
        doi_to_hash = json.loads(Path("data/doi_to_hash.json").read_text(encoding="utf-8"))
    except Exception:
        doi_to_hash = {}

    missing_predictions: List[str] = []

    # Iterate over all ground truth files (not just previous work files)
    for gt_path in sorted(GT_ROOT.glob("*.json")):
        doi = gt_path.stem

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue

        prev_path = PREV_ROOT / f"{doi}.json"
        
        try:
            gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error loading GT file {gt_path}: {e}")
            continue
        
        # Check if prediction exists
        if not prev_path.exists():
            # No prediction for this ground truth - count as all FN
            gt_chars = _collect_characterisations(gt_obj)
            # Count all fields that would have been compared
            fn = 0
            for ch in gt_chars.values():
                fn += 1  # key itself
                # Product names
                def _is_ccdc_only(name: str) -> bool:
                    import re
                    return bool(re.match(r'^\d{6,7}$', str(name).strip()))
                gt_names = set(_normalize(n) for n in ((ch or {}).get("productNames") or []) if _is_valid(n) and not _is_ccdc_only(n))
                fn += len(gt_names)
                # HNMR fields
                gt_h = (ch or {}).get("HNMR") or {}
                for f in ["shifts", "solvent", "temperature"]:
                    if _is_valid(gt_h.get(f)):
                        fn += 1
                # ElementalAnalysis fields
                gt_e = (ch or {}).get("ElementalAnalysis") or {}
                for f in ["chemicalFormula", "weightPercentageCalculated", "weightPercentageExperimental"]:
                    if _is_valid(gt_e.get(f)):
                        fn += 1
                # IR fields
                gt_ir = (ch or {}).get("InfraredSpectroscopy") or {}
                for f in ["material", "bands"]:
                    if _is_valid(gt_ir.get(f)):
                        fn += 1
            
            rows_overall.append((doi, (0, 0, fn)))
            missing_predictions.append(doi)

            # Emit a placeholder per-DOI report
            lines: List[str] = []
            title_suffix = " (Full GT)" if use_full_gt else ""
            lines.append(f"# Characterisation Previous Scoring{title_suffix} - {doi}\n\n")
            lines.append("No previous_work prediction found for this DOI.\n\n")
            
            hv = doi_to_hash.get(doi, "<unknown>")
            lines.append(f"**DOI**: `{doi}`  \n")
            lines.append(f"**Hash**: `{hv}`  \n")
            lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")

            gt_normalized = _normalize_json_structure(gt_obj)
            lines.append("## Ground Truth\n")
            lines.append("```json\n")
            lines.append(json.dumps(gt_normalized, indent=2))
            lines.append("\n```\n\n")

            lines.append("## Prediction\n\n")
            lines.append("(none)\n\n")

            # Differences summary
            gt_chars = _collect_characterisations(gt_obj)
            gt_keys = set(gt_chars.keys())
            gt_names = _collect_name_union(gt_chars)
            lines.append("## Differences\n\n")
            lines.append(f"FN (missing keys): {', '.join(sorted(gt_keys))}\n")
            lines.append(f"FN (missing product names): {', '.join(sorted(gt_names)) if gt_names else 'None'}\n\n")

            (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")
            continue
        
        try:
            pred_obj = json.loads(prev_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error loading prediction file {prev_path}: {e}")
            continue

        # Fine-grained scoring (shared)
        tp, fp, fn, pred_missing = score_characterisation_fine_grained(gt_obj, pred_obj)
        if pred_missing:
            files_with_missing_ccdc.append(f"characterisation/{doi}.json")
        
        rows_overall.append((doi, (tp, fp, fn)))

        # Per-DOI report with GT and Pred data
        lines: List[str] = []
        title_suffix = " (Full GT)" if use_full_gt else ""
        lines.append(f"# Characterisation Previous Scoring{title_suffix} - {doi}\n")
        lines.append("\n")
        # Metadata block
        hv = doi_to_hash.get(doi, "<unknown>")
        lines.append(f"**DOI**: `{doi}`  \n")
        lines.append(f"**Hash**: `{hv}`  \n")
        lines.append(f"**Prediction file**: `{prev_path.as_posix()}`  \n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n\n")
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n")
        lines.append("\n")
        
        # Create normalized versions
        gt_normalized = _normalize_json_structure(gt_obj)
        pred_normalized = _normalize_json_structure(pred_obj)
        
        # Show GT and Pred data (normalized)
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences summary (normalized)
        gt_chars = _collect_characterisations(gt_obj)
        pr_chars = _collect_characterisations(pred_obj)
        gt_keys = set(gt_chars.keys())
        pr_keys = set(pr_chars.keys())
        fn_keys = sorted(gt_keys - pr_keys)
        fp_keys = sorted(pr_keys - gt_keys)
        gt_names = _collect_name_union(gt_chars)
        pr_names = _collect_name_union(pr_chars)
        fn_names = sorted(gt_names - pr_names)
        fp_names = sorted(pr_names - gt_names)
        lines.append("## Differences\n\n")
        lines.append(f"Keys (Prediction vs GT): {', '.join(sorted(pr_keys))} - {', '.join(sorted(gt_keys))}\n")
        lines.append(f"FN (missing keys): {', '.join(fn_keys) if fn_keys else 'None'}\n")
        lines.append(f"FP (extra keys): {', '.join(fp_keys) if fp_keys else 'None'}\n")
        lines.append(f"FN (missing product names): {', '.join(fn_names) if fn_names else 'None'}\n")
        lines.append(f"FP (extra product names): {', '.join(fp_names) if fp_names else 'None'}\n")

        # Field-level differences (HNMR, ElementalAnalysis, IR)
        field_diffs = _collect_field_differences(gt_chars, pr_chars)
        if field_diffs:
            lines.append("\n### Field-level differences\n\n")
            for d in field_diffs:
                lines.append(d + "\n")
        lines.append("\n")

        (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    title_suffix = " (Full GT)" if use_full_gt else ""
    lines_overall.append(f"# Characterisation Previous Scoring{title_suffix} - Overall\n\n")
    lines_overall.append("| DOI | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
    
    total_tp = total_fp = total_fn = 0
    for doi, (tp, fp, fn) in rows_overall:
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines_overall.append(f"| {doi} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn
    
    # Overall summary row
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
    
    # Report files with missing/N/A CCDC
    if files_with_missing_ccdc:
        lines_overall.append("\n## Files with Missing or N/A CCDC Numbers\n\n")
        for fpath in sorted(set(files_with_missing_ccdc)):
            lines_overall.append(f"- `{fpath}`\n")
    
    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Characterisation scoring evaluator")
    parser.add_argument("--previous", action="store_true", help="Evaluate previous_work/characterisation/*.json against ground truth using fine-grained field-level scoring")
    parser.add_argument("--anchor", action="store_true", help="Use previous_work_anchored/ instead of previous_work/ (only with --previous)")
    parser.add_argument("--full", action="store_true", help="Evaluate against full ground truth dataset (full_ground_truth/characterisation/)")
    args = parser.parse_args()

    if args.previous:
        # --full flag also applies to previous work evaluation
        evaluate_previous(use_anchored=args.anchor, use_full_gt=args.full)
    elif args.full:
        evaluate_full()
    else:
        evaluate_current(use_full_gt=args.full)


if __name__ == "__main__":
    main()


