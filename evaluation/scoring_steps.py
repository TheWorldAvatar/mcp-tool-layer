import json
import argparse
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any, Set, Optional
from evaluation.utils.scoring_common import precision_recall_f1, hash_map_reverse
from evaluation.normalize_steps import normalize_json_structure

# Import step-type-only scoring logic from evaluation.py
import sys
_eval_path = Path(__file__).parent.parent / "tests" / "step_extraction"
if str(_eval_path) not in sys.path:
    sys.path.insert(0, str(_eval_path))

# Import with try-except to handle different execution contexts
try:
    from evaluation import _gt_step_names, _compare_steps
except ImportError:
    # If running as module, try absolute import
    import importlib.util
    eval_file = _eval_path / "evaluation.py"
    spec = importlib.util.spec_from_file_location("eval_module", eval_file)
    eval_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval_module)
    _gt_step_names = eval_module._gt_step_names
    _compare_steps = eval_module._compare_steps


# Chemical synonymy mapping: define canonical species -> list of equivalent names.
# Currently empty; populate with entries like {"canonical_name": ["synonym1", "synonym2"]}
chemical_synomy_dict: Dict[str, List[str]] = {}

def _normalize(s: Any) -> str:
    """Identity normalization: assume inputs are pre-normalized via external script."""
    return str(s) if s is not None else ""


def _normalize_product_name(name: Any) -> str:
    """Normalize product names for matching.

    Rules:
    - Lowercase and strip
    - Convert Unicode subscript/superscript to regular characters
    - Replace underscores and multiple spaces with single space
    - This allows 'cu_oet-bdc cage' and 'cu_oet-bdc_cage' to match
    """
    try:
        s = str(name or "").strip().lower()
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

    # Replace underscores with spaces and collapse multiple spaces
    s = re.sub(r"_", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalize_ccdc(ccdc: Any) -> str:
    """Canonicalize CCDC numbers so variants like '974183' and '97418 3' match.

    Rules:
    - Treat 'n/a', 'na', '' as empty
    - Strip whitespace and lowercase
    - If the value is digits and length > 5, insert a space before the final digit
      (e.g., '974183' -> '97418 3', '1835131' -> '183513 1')
    - If the value already has spaces, remove spaces and re-insert one before the final digit
    - Otherwise, return as-is
    """
    try:
        s = str(ccdc or "").strip().lower()
    except Exception:
        return ""
    if s in ["n/a", "na", ""]:
        return ""
    # Remove all spaces for processing
    digits_only = re.sub(r"\s+", "", s)
    if digits_only.isdigit() and len(digits_only) > 5:
        return f"{digits_only[:-1]} {digits_only[-1]}"
    # If it's already something like '12345 6', normalize spacing to single space
    m = re.match(r"^(\d+)\s+(\d)$", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return s


def _is_valid(s: Any) -> bool:
    """Basic validity: non-empty after str() conversion."""
    try:
        return str(s) != ""
    except Exception:
        return False


def _get_step_type(step: Dict) -> str:
    """Extract step type from a step dict (the key is the step type)."""
    for key in step.keys():
        if key in ["Add", "HeatChill", "Filter", "Sonicate", "Dissolve", "Stir", "Transfer", "Wash", "Dry", "Collect", "PH", "Degas", "Evaporate", "Extract", "Quench", "Purify", "Concentrate", "Reflux", "Crystallize", "Distill", "Grind", "Mix", "Separate", "Centrifuge"]:
            return key
    return ""


def _expand_add_steps(raw_steps: List[Dict]) -> List[Tuple[str, Dict]]:
    """Expand Add steps that contain multiple addedChemical entries into
    multiple Add steps (one per chemical), preserving order.

    Returns a list of (step_type, step_data) tuples with expansions applied.
    
    Uses evaluation.py _gt_step_names() for step type extraction.
    """
    # Create a temporary synthesis object to use with _gt_step_names
    temp_syn = {"steps": raw_steps}
    step_names = _gt_step_names(temp_syn)
    
    # Build result with step data
    expanded: List[Tuple[str, Dict]] = []
    step_idx = 0
    for step_object in raw_steps or []:
        if not isinstance(step_object, dict) or not step_object:
            continue
        if not step_object.keys():
            continue
        step_name_key = next(iter(step_object.keys()))
        step_name = str(step_name_key)
        data = step_object.get(step_name_key, {}) or {}
        
        # Check if this is an Add step with multiple chemicals
        if step_name.lower() == "add":
            added_chemicals = data.get("addedChemical")
            num_add_events = 1
            if isinstance(added_chemicals, list):
                num_add_events = max(1, len(added_chemicals))
            elif added_chemicals:
                num_add_events = 1
            
            for _ in range(num_add_events):
                if step_idx < len(step_names):
                    expanded.append((step_names[step_idx], data))
                    step_idx += 1
        else:
            if step_idx < len(step_names):
                expanded.append((step_names[step_idx], data))
                step_idx += 1
    
    return expanded


def _type_counts_for_objs(gt_obj: Any, pred_obj: Any) -> Tuple[int, int, int]:
    """Compute positional step-type-only counts (tp, fp, fn) after Add-splitting.

    Uses evaluation.py functions directly: _gt_step_names() and _compare_steps()
    Exactly mirrors evaluation.py _evaluate_previous logic.
    """
    tp_t = fp_t = fn_t = 0
    
    # Get all GT syntheses
    gt_synths_all = (gt_obj or {}).get("Synthesis", []) or []
    
    # Track which GT syntheses have been matched
    matched_gt: Set[int] = set()
    
    # For each pred synthesis, match to GT and compare
    for pred_synth in (pred_obj or {}).get("Synthesis", []) or []:
        pred_ccdc = str((pred_synth or {}).get("productCCDCNumber") or "").strip()
        # Canonicalize CCDC for matching/reporting
        pred_ccdc = _normalize_ccdc(pred_ccdc)
        # Normalize CCDC: treat empty as missing
        if pred_ccdc == "":
            pred_ccdc = ""
        
        # Extract pred step types using evaluation.py _gt_step_names()
        pred_types = _gt_step_names(pred_synth)
        
        gt_synth = None
        # Try name-based matching FIRST (prioritize product name over CCDC)
        pred_names = [str(x) for x in (pred_synth or {}).get("productNames", []) or []]
        if pred_names:
            # Normalize ALL prediction names
            pred_names_norm = [_normalize_product_name(n) for n in pred_names if _normalize_product_name(n)]
            
            # Filter candidates by name matching (check ALL pred names against ALL candidate names)
            name_candidates = []
            for candidate in gt_synths_all:
                if id(candidate) in matched_gt:
                    continue
                cand_names = [str(x) for x in (candidate or {}).get("productNames", []) or []]
                cand_names_norm = [_normalize_product_name(n) for n in cand_names if _normalize_product_name(n)]
                
                # Check if ANY pred name matches ANY candidate name
                matched = False
                for pred_name_norm in pred_names_norm:
                    # Exact match
                    if pred_name_norm in cand_names_norm:
                        matched = True
                        break
                    # Substring match (either way)
                    # Only allow substring matching if both names are reasonably long (>= 5 chars)
                    # to avoid false matches like "i" matching "nanocapsule ii"
                    for cn in cand_names_norm:
                        if (pred_name_norm and len(pred_name_norm) >= 5 and len(cn) >= 5 and 
                            (pred_name_norm in cn or cn in pred_name_norm)):
                            matched = True
                            break
                    if matched:
                        break
                
                if matched:
                    name_candidates.append(candidate)
            
            # Pick best match by positional step comparison
            if len(name_candidates) == 1:
                gt_synth = name_candidates[0]
                matched_gt.add(id(gt_synth))
            elif len(name_candidates) > 1:
                best_match = None
                best_score = -1
                for candidate in name_candidates:
                    # Use evaluation.py _gt_step_names() and _compare_steps()
                    cand_types = _gt_step_names(candidate)
                    matches, _, _ = _compare_steps(cand_types, pred_types)
                    if matches > best_score:
                        best_score = matches
                        best_match = candidate
                if best_match is not None:
                    gt_synth = best_match
                    matched_gt.add(id(best_match))
        
        # Fall back to CCDC matching only if no name match was found
        if gt_synth is None and pred_ccdc:
            # Find all unmatched GT syntheses with this CCDC
            ccdc_candidates = []
            for candidate in gt_synths_all:
                if id(candidate) in matched_gt:
                    continue
                gt_ccdc = _normalize_ccdc(str((candidate or {}).get("productCCDCNumber") or "").strip())
                if gt_ccdc != "" and gt_ccdc == pred_ccdc:
                    ccdc_candidates.append(candidate)
            
            # If multiple candidates (or one), pick best match by step comparison
            if len(ccdc_candidates) == 1:
                gt_synth = ccdc_candidates[0]
                matched_gt.add(id(gt_synth))
            elif len(ccdc_candidates) > 1:
                best_match = None
                best_score = -1
                for candidate in ccdc_candidates:
                    # Use evaluation.py _gt_step_names() and _compare_steps()
                    cand_types = _gt_step_names(candidate)
                    matches, _, _ = _compare_steps(cand_types, pred_types)
                    if matches > best_score:
                        best_score = matches
                        best_match = candidate
                if best_match is not None:
                    gt_synth = best_match
                    matched_gt.add(id(best_match))
        
        if gt_synth:
            # Extract GT step types using evaluation.py _gt_step_names()
            gt_types = _gt_step_names(gt_synth)
            
            # Use evaluation.py _compare_steps() for positional comparison
            tp, fp, fn = _compare_steps(gt_types, pred_types)
            tp_t += tp
            fp_t += fp
            fn_t += fn
        else:
            # No GT match: all pred steps are FP
            fp_t += len(pred_types)
    
    # Handle unmatched GT syntheses: all GT steps are FN
    for gt_synth in gt_synths_all:
        if id(gt_synth) not in matched_gt:
            # Use evaluation.py _gt_step_names()
            gt_types = _gt_step_names(gt_synth)
            fn_t += len(gt_types)
    
    return tp_t, fp_t, fn_t


def _compare_step_fields(gt_step_data: Dict, pr_step_data: Dict, step_type: str, ignore_vessel: bool = False) -> Tuple[int, int, int]:
    """
    Compare all fields within a step and return (tp, fp, fn).
    This does fine-grained field-level comparison.
    
    Args:
        gt_step_data: Ground truth step data
        pr_step_data: Predicted step data
        step_type: Type of the step
        ignore_vessel: If True, ignore differences in vessel-related fields
    """
    tp = fp = fn = 0
    
    # Get all possible field keys from both GT and prediction
    all_keys = set(gt_step_data.keys()) | set(pr_step_data.keys())
    
    # Fields to skip when ignore_vessel is True
    vessel_fields = {"usedVesselName", "usedVesselType"}
    
    for key in all_keys:
        # Skip comment and stepNumber fields - don't measure them
        if key in ("comment", "stepNumber"):
            continue
        
        # Skip vessel fields if ignore_vessel is True
        if ignore_vessel and key in vessel_fields:
            continue
            
        gt_val = gt_step_data.get(key)
        pr_val = pr_step_data.get(key)
        
        # Special handling for chemical lists (addedChemical, solvent, washingSolvent, etc.)
        if key in ["addedChemical", "solvent", "washingSolvent"] and (isinstance(gt_val, list) or isinstance(pr_val, list)):
            gt_chems = gt_val if isinstance(gt_val, list) else []
            pr_chems = pr_val if isinstance(pr_val, list) else []
            
            # Collect all chemical names and amounts
            gt_names = set()
            pr_names = set()
            gt_amounts = set()
            pr_amounts = set()
            
            for chem in gt_chems:
                if isinstance(chem, dict):
                    names = chem.get("chemicalName", []) or []
                    if not isinstance(names, list):
                        names = [names]
                    for n in names:
                        if _is_valid(n):
                            gt_names.add(_normalize(n))
                    amount = chem.get("chemicalAmount")
                    if _is_valid(amount):
                        gt_amounts.add(_normalize(amount))
            
            for chem in pr_chems:
                if isinstance(chem, dict):
                    names = chem.get("chemicalName", []) or chem.get("names", []) or []
                    if not isinstance(names, list):
                        names = [names]
                    for n in names:
                        if _is_valid(n):
                            pr_names.add(_normalize(n))
                    amount = chem.get("chemicalAmount") or chem.get("amount")
                    if _is_valid(amount):
                        pr_amounts.add(_normalize(amount))
            
            # Compare names (ignore FP - extra chemical names are not errors)
            if not gt_names and not pr_names:
                tp += 1
            else:
                tp += len(gt_names & pr_names)
                fn += len(gt_names - pr_names)
                # fp += len(pr_names - gt_names)  # Ignore FP for chemical names
            
            # Compare amounts
            if not gt_amounts and not pr_amounts:
                tp += 1
            else:
                tp += len(gt_amounts & pr_amounts)
                fn += len(gt_amounts - pr_amounts)
                fp += len(pr_amounts - gt_amounts)
        
        # Special normalization for targetPH: treat -1/NA/"N/A"/empty as the same canonical "n/a"
        elif key == "targetPH":
            # Normalize both values
            def _norm_ph(v: Any) -> str:
                s = str(v if v is not None else "").strip().lower()
                if s in ["-1", "-1.0", "n/a", "na", ""]:
                    return "n/a"
                return s
            
            gt_norm = _norm_ph(gt_val)
            pr_norm = _norm_ph(pr_val)
            
            if gt_norm == pr_norm:
                tp += 1
            else:
                # Count as error only if at least one has a meaningful value (not "n/a")
                if gt_norm != "n/a":
                    fn += 1
                if pr_norm != "n/a":
                    fp += 1

        # Handle boolean and numeric fields
        elif isinstance(gt_val, (bool, int, float)) or isinstance(pr_val, (bool, int, float)):
            if gt_val is not None and pr_val is not None:
                if gt_val == pr_val:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif gt_val is None and pr_val is None:
                tp += 1
            elif gt_val is not None:
                fn += 1
            elif pr_val is not None:
                fp += 1
        
        # Handle string fields
        else:
            if gt_val is not None and pr_val is not None:
                if gt_val == pr_val:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif gt_val is None and pr_val is None:
                tp += 1
            elif gt_val is not None:
                fn += 1
            elif pr_val is not None:
                fp += 1
    
    return tp, fp, fn


def _get_synths_by_ccdc(data: Any) -> Dict[str, Any]:
    synths: Dict[str, Any] = {}
    for synth in (data or {}).get("Synthesis", []) or []:
        ccdc = _normalize_ccdc(str((synth or {}).get("productCCDCNumber") or "").strip())
        # Use first product name as fallback key if CCDC is N/A
        if not ccdc:
            names = (synth or {}).get("productNames") or []
            if names and len(names) > 0:
                ccdc = f"NAME:{str(names[0]).strip()}"
        if ccdc:
            if ccdc not in synths:
                synths[ccdc] = []
            synths[ccdc].append(synth)
    return synths


def _extract_chemical_names_from_step(step_data: Dict) -> Set[str]:
    """Extract all normalized chemical names from a step's addedChemical field."""
    names: Set[str] = set()
    for chem in step_data.get("addedChemical", []) or []:
        if isinstance(chem, dict):
            chem_names = chem.get("chemicalName") or chem.get("names") or []
            if not isinstance(chem_names, list):
                chem_names = [chem_names]
            for name in chem_names:
                if _is_valid(name):
                    names.add(_normalize(name))
    return names


def _find_best_add_match(gt_data: Dict, pr_add_steps: List[Tuple[int, Dict]]) -> Tuple[int, int]:
    """
    Find the best matching Add step from pr_add_steps based on chemical name overlap.
    Returns: (best_index_in_pr_add_steps, overlap_count)
    """
    gt_names = _extract_chemical_names_from_step(gt_data)
    if not gt_names:
        return -1, 0
    
    best_idx = -1
    best_overlap = 0
    
    for idx, (_, pr_data) in enumerate(pr_add_steps):
        pr_names = _extract_chemical_names_from_step(pr_data)
        overlap = len(gt_names & pr_names)
        if overlap > best_overlap:
            best_overlap = overlap
            best_idx = idx
    
    return best_idx, best_overlap


def score_steps_fine_grained(gt_obj: Any, pred_obj: Any, ignore_vessel: bool = False, skip_order: bool = False) -> Tuple[int, int, int, bool]:
    """Fine-grained CCDC-anchored scoring used by both current and previous evaluators.
    
    Uses CCDC matching with productNames-based fallback when CCDC is not available.

    Args:
        gt_obj: Ground truth object
        pred_obj: Prediction object
        ignore_vessel: If True, ignore differences in vessel-related fields
        skip_order: If True, use best-match by type instead of positional matching

    Returns: (tp, fp, fn, pred_missing_ccdc)
    """
    tp = fp = fn = 0
    pred_missing_ccdc = False

    # Check for missing/N/A CCDC in prediction
    for synth in (pred_obj or {}).get("Synthesis", []) or []:
        ccdc = str((synth or {}).get("productCCDCNumber") or "").strip()
        if not ccdc or ccdc.lower() in ["n/a", "na", ""]:
            pred_missing_ccdc = True
            break

    # Get flat lists of syntheses
    gt_synths_all = (gt_obj or {}).get("Synthesis", []) or []
    pr_synths_all = (pred_obj or {}).get("Synthesis", []) or []
    
    # Track which pred syntheses have been matched
    matched_pr_ids: Set[int] = set()
    
    # Match each GT synthesis to best pred synthesis using name priority + CCDC fallback
    for gt_synth in gt_synths_all:
        gt_ccdc = str((gt_synth or {}).get("productCCDCNumber") or "").strip()
        gt_names = [str(x) for x in (gt_synth or {}).get("productNames", []) or []]
        
        # Normalize CCDC
        if gt_ccdc.lower() in ["n/a", "na", ""]:
            gt_ccdc = ""
        
        # Get GT steps
        gt_steps: List[Tuple[str, Dict]] = []
        raw = (gt_synth or {}).get("steps", []) or []
        gt_steps.extend(_expand_add_steps(raw))
        
        # Find best matching pred synthesis
        best_pr_synth = None
        best_pr_idx = -1
        
        # Try product name matching FIRST (prioritize product name over CCDC)
        if gt_names:
            gt_names_norm = [_normalize_product_name(n) for n in gt_names if _normalize_product_name(n)]
            for idx, pr_synth in enumerate(pr_synths_all):
                if idx in matched_pr_ids:
                    continue
                pr_names = [str(x) for x in (pr_synth or {})
                            .get("productNames", []) or []]
                pr_names_norm = [_normalize_product_name(n) for n in pr_names if _normalize_product_name(n)]
                # ANY-to-ANY name match (exact or substring)
                matched_by_name = False
                for gt_n in gt_names_norm:
                    if gt_n in pr_names_norm:
                        matched_by_name = True
                        break
                    # Only allow substring matching if both names are reasonably long (>= 5 chars)
                    # to avoid false matches like "i" matching "nanocapsule ii"
                    for pr_n in pr_names_norm:
                        if gt_n and len(gt_n) >= 5 and len(pr_n) >= 5 and (gt_n in pr_n or pr_n in gt_n):
                            matched_by_name = True
                            break
                    if matched_by_name:
                        break
                if matched_by_name:
                    best_pr_synth = pr_synth
                    best_pr_idx = idx
                    break
        
        # Fall back to CCDC matching only if no name match was found
        if best_pr_synth is None and gt_ccdc:
            for idx, pr_synth in enumerate(pr_synths_all):
                if idx in matched_pr_ids:
                    continue
                pr_ccdc = str((pr_synth or {}).get("productCCDCNumber") or "").strip()
                if pr_ccdc.lower() not in ["n/a", "na", ""] and pr_ccdc == gt_ccdc:
                    # Found CCDC match
                    best_pr_synth = pr_synth
                    best_pr_idx = idx
                    break
        
        # Get pred steps
        pr_steps: List[Tuple[str, Dict]] = []
        if best_pr_synth is not None:
            matched_pr_ids.add(best_pr_idx)
            raw = (best_pr_synth or {}).get("steps", []) or []
            pr_steps.extend(_expand_add_steps(raw))
        
        # Track which prediction steps have been matched within this synthesis pair
        pr_matched: Set[int] = set()
        
        if skip_order:
            # Non-positional matching: match by step type first, then find best field match
            for i in range(len(gt_steps)):
                gt_type, gt_data = gt_steps[i]
                
                # For Add steps, use chemical-name-based matching
                if gt_type == "Add":
                    pr_add_steps_all = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                       if pr_steps[idx][0] == "Add" and idx not in pr_matched]
                    if pr_add_steps_all:
                        best_match_idx, _overlap = _find_best_add_match(gt_data, pr_add_steps_all)
                        if best_match_idx >= 0:
                            pr_global_idx, pr_add_data = pr_add_steps_all[best_match_idx]
                            pr_matched.add(pr_global_idx)
                            step_tp, step_fp, step_fn = _compare_step_fields(
                                gt_data, pr_add_data, "Add", ignore_vessel)
                            tp += step_tp
                            fp += step_fp
                            fn += step_fn
                        else:
                            _, _, step_fn = _compare_step_fields(gt_data, {}, "Add", ignore_vessel)
                            fn += step_fn
                    else:
                        _, _, step_fn = _compare_step_fields(gt_data, {}, "Add", ignore_vessel)
                        fn += step_fn
                else:
                    # For non-Add steps, find best match by step type
                    pr_same_type_steps = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                         if _normalize(pr_steps[idx][0]) == _normalize(gt_type) and idx not in pr_matched]
                    if pr_same_type_steps:
                        # Find best match by comparing fields
                        best_idx = -1
                        best_tp = -1
                        best_result = None
                        for idx, pr_data in pr_same_type_steps:
                            step_tp, step_fp, step_fn = _compare_step_fields(gt_data, pr_data, gt_type, ignore_vessel)
                            if step_tp > best_tp:
                                best_tp = step_tp
                                best_idx = idx
                                best_result = (step_tp, step_fp, step_fn)
                        if best_idx >= 0 and best_result:
                            pr_matched.add(best_idx)
                            tp += best_result[0]
                            fp += best_result[1]
                            fn += best_result[2]
                    else:
                        # No prediction step of this type found
                        _, _, step_fn = _compare_step_fields(gt_data, {}, gt_type, ignore_vessel)
                        fn += step_fn
            
            # Handle remaining unmatched prediction steps
            for pr_idx in range(len(pr_steps)):
                if pr_idx not in pr_matched:
                    pr_type, pr_data = pr_steps[pr_idx]
                    _, step_fp, _ = _compare_step_fields({}, pr_data, pr_type, ignore_vessel)
                    fp += step_fp
        else:
            # Original positional matching logic
            i = 0
            while i < len(gt_steps):
                gt_type, gt_data = gt_steps[i]

                # Special matching for Add: do non-positional best-match by chemical names
                if gt_type == "Add":
                    # Collect all unmatched prediction Add steps across the remainder
                    pr_add_steps_all = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                       if pr_steps[idx][0] == "Add" and idx not in pr_matched]
                    if pr_add_steps_all:
                        best_match_idx, _overlap = _find_best_add_match(gt_data, pr_add_steps_all)
                        if best_match_idx >= 0:
                            pr_global_idx, pr_add_data = pr_add_steps_all[best_match_idx]
                            pr_matched.add(pr_global_idx)
                            step_tp, step_fp, step_fn = _compare_step_fields(
                                gt_data, pr_add_data, "Add", ignore_vessel)
                            tp += step_tp
                            fp += step_fp
                            fn += step_fn
                        else:
                            # No matching Add found in prediction
                            _, _, step_fn = _compare_step_fields(gt_data, {}, "Add", ignore_vessel)
                            fn += step_fn
                    else:
                        # No prediction Adds left
                        _, _, step_fn = _compare_step_fields(gt_data, {}, "Add", ignore_vessel)
                        fn += step_fn
                    i += 1
                    continue

                # Non-Add steps: use positional matching
                # Find next unmatched prediction step
                pr_idx = len([idx for idx in pr_matched if idx < len(pr_steps)])
                while pr_idx in pr_matched and pr_idx < len(pr_steps):
                    pr_idx += 1
                
                if pr_idx < len(pr_steps):
                    pr_type, pr_data = pr_steps[pr_idx]
                    pr_matched.add(pr_idx)
                    
                    if _normalize(gt_type) == _normalize(pr_type):
                        step_tp, step_fp, step_fn = _compare_step_fields(gt_data, pr_data, gt_type, ignore_vessel)
                        tp += step_tp
                        fp += step_fp
                        fn += step_fn
                    else:
                        _, _, gt_fn = _compare_step_fields(gt_data, {}, gt_type, ignore_vessel)
                        _, pr_fp, _ = _compare_step_fields({}, pr_data, pr_type, ignore_vessel)
                        fn += gt_fn
                        fp += pr_fp
                else:
                    # GT step with no corresponding prediction step
                    _, _, step_fn = _compare_step_fields(gt_data, {}, gt_type, ignore_vessel)
                    fn += step_fn
                
                i += 1
            
            # Handle remaining unmatched prediction steps within this synthesis pair
            for pr_idx in range(len(pr_steps)):
                if pr_idx not in pr_matched:
                    pr_type, pr_data = pr_steps[pr_idx]
                    _, step_fp, _ = _compare_step_fields({}, pr_data, pr_type, ignore_vessel)
                    fp += step_fp
    
    # Handle unmatched prediction syntheses (those not matched to any GT synthesis)
    for idx, pr_synth in enumerate(pr_synths_all):
        if idx not in matched_pr_ids:
            # All steps in this unmatched pred synthesis are FP
            raw = (pr_synth or {}).get("steps", []) or []
            pr_steps_unmatched = _expand_add_steps(raw)
            for pr_type, pr_data in pr_steps_unmatched:
                _, step_fp, _ = _compare_step_fields({}, pr_data, pr_type, ignore_vessel)
                fp += step_fp

    return tp, fp, fn, pred_missing_ccdc


def _analyze_errors_by_field(gt_obj: Any, pr_obj: Any, ignore_vessel: bool = False, skip_order: bool = False, 
                            collect_details: bool = False, hash_value: str = "", doi: str = "") -> Tuple[Dict[str, Dict[str, int]], Dict[str, List[Dict[str, Any]]]]:
    """Analyze errors by field type to understand which fields contribute most to errors.
    
    Uses CCDC matching with productNames-based fallback when CCDC is not available.
    
    Args:
        gt_obj: Ground truth object
        pr_obj: Prediction object
        ignore_vessel: If True, ignore vessel fields
        skip_order: If True, use best-match by type instead of positional matching
        collect_details: If True, collect detailed error information for reporting
        hash_value: Hash value for error reporting
        doi: DOI for error reporting
    
    Returns:
        Tuple of (field_errors, detailed_errors)
        - field_errors: dict mapping field names to {'fp': count, 'fn': count}
        - detailed_errors: dict mapping field names to list of detailed error dicts
    """
    field_errors: Dict[str, Dict[str, int]] = {}
    detailed_errors: Dict[str, List[Dict[str, Any]]] = {}
    vessel_fields = {"usedVesselName", "usedVesselType"}
    
    def _track_field_error(field_name: str, error_type: str, count: int = 1, 
                          ccdc: str = "", step_idx: int = -1, step_type: str = "",
                          gt_value: Any = None, pr_value: Any = None):
        if field_name not in field_errors:
            field_errors[field_name] = {'fp': 0, 'fn': 0}
        field_errors[field_name][error_type] += count
        
        if collect_details:
            if field_name not in detailed_errors:
                detailed_errors[field_name] = []
            detailed_errors[field_name].append({
                'hash': hash_value,
                'doi': doi,
                'ccdc': ccdc,
                'step_idx': step_idx,
                'step_type': step_type,
                'error_type': error_type,
                'gt_value': gt_value,
                'pr_value': pr_value,
                'count': count
            })
    
    def _norm_ph(v: Any) -> str:
        s = str(v if v is not None else "").strip().lower()
        if s in ["-1", "-1.0", "n/a", "na", ""]:
            return "n/a"
        return s
    
    # Get flat lists of syntheses
    gt_synths_all = (gt_obj or {}).get("Synthesis", []) or []
    pr_synths_all = (pr_obj or {}).get("Synthesis", []) or []
    
    # Track which pred syntheses have been matched
    matched_pr_ids: Set[int] = set()
    
    # Match each GT synthesis to best pred synthesis using name priority + CCDC fallback
    for gt_synth in gt_synths_all:
        gt_ccdc = str((gt_synth or {}).get("productCCDCNumber") or "").strip()
        gt_names = [str(x) for x in (gt_synth or {}).get("productNames", []) or []]
        
        # Normalize CCDC
        if gt_ccdc.lower() in ["n/a", "na", ""]:
            gt_ccdc = ""
        
        # Get GT steps
        gt_steps: List[Tuple[str, Dict]] = []
        raw = (gt_synth or {}).get("steps", []) or []
        gt_steps.extend(_expand_add_steps(raw))
        
        # Find best matching pred synthesis
        best_pr_synth = None
        best_pr_idx = -1
        
        # Try product name matching FIRST (prioritize product name over CCDC)
        if gt_names:
            gt_names_norm = [_normalize_product_name(n) for n in gt_names if _normalize_product_name(n)]
            for idx, pr_synth in enumerate(pr_synths_all):
                if idx in matched_pr_ids:
                    continue
                pr_names = [str(x) for x in (pr_synth or {})
                            .get("productNames", []) or []]
                pr_names_norm = [_normalize_product_name(n) for n in pr_names if _normalize_product_name(n)]
                # ANY-to-ANY name match (exact or substring)
                matched_by_name = False
                for gt_n in gt_names_norm:
                    if gt_n in pr_names_norm:
                        matched_by_name = True
                        break
                    # Only allow substring matching if both names are reasonably long (>= 5 chars)
                    # to avoid false matches like "i" matching "nanocapsule ii"
                    for pr_n in pr_names_norm:
                        if gt_n and len(gt_n) >= 5 and len(pr_n) >= 5 and (gt_n in pr_n or pr_n in gt_n):
                            matched_by_name = True
                            break
                    if matched_by_name:
                        break
                if matched_by_name:
                    best_pr_synth = pr_synth
                    best_pr_idx = idx
                    break
        
        # Fall back to CCDC matching only if no name match was found
        if best_pr_synth is None and gt_ccdc:
            for idx, pr_synth in enumerate(pr_synths_all):
                if idx in matched_pr_ids:
                    continue
                pr_ccdc = str((pr_synth or {}).get("productCCDCNumber") or "").strip()
                if pr_ccdc.lower() not in ["n/a", "na", ""] and pr_ccdc == gt_ccdc:
                    # Found CCDC match
                    best_pr_synth = pr_synth
                    best_pr_idx = idx
                    break
        
        # Get pred steps
        pr_steps: List[Tuple[str, Dict]] = []
        if best_pr_synth is not None:
            matched_pr_ids.add(best_pr_idx)
            raw = (best_pr_synth or {}).get("steps", []) or []
            pr_steps.extend(_expand_add_steps(raw))
        
        pr_matched: Set[int] = set()
        
        # Determine match key for reporting
        match_key = gt_ccdc if gt_ccdc else (f"NAME:{gt_names[0]}" if gt_names else "NAME:<unnamed>")
        
        if skip_order:
            # Non-positional matching
            for i in range(len(gt_steps)):
                gt_type, gt_data = gt_steps[i]
                step_idx = i + 1
                
                if gt_type == "Add":
                    pr_add_steps_all = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                       if pr_steps[idx][0] == "Add" and idx not in pr_matched]
                    if pr_add_steps_all:
                        best_match_idx, _overlap = _find_best_add_match(gt_data, pr_add_steps_all)
                        if best_match_idx >= 0:
                            pr_global_idx, pr_data = pr_add_steps_all[best_match_idx]
                            pr_matched.add(pr_global_idx)
                            # Only count field errors for successfully matched steps (type matches)
                            _count_field_errors(gt_data, pr_data, gt_type, ignore_vessel, vessel_fields, _track_field_error, _norm_ph, match_key, step_idx)
                        # Skip counting field errors for missing steps - only count type-level errors
                    # Skip counting field errors for missing steps - only count type-level errors
                else:
                    pr_same_type_steps = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                         if _normalize(pr_steps[idx][0]) == _normalize(gt_type) and idx not in pr_matched]
                    if pr_same_type_steps:
                        best_idx = -1
                        best_tp = -1
                        best_pr_data = None
                        for idx, pr_data in pr_same_type_steps:
                            step_tp, _, _ = _compare_step_fields(gt_data, pr_data, gt_type, ignore_vessel)
                            if step_tp > best_tp:
                                best_tp = step_tp
                                best_idx = idx
                                best_pr_data = pr_data
                        if best_idx >= 0 and best_pr_data is not None:
                            pr_matched.add(best_idx)
                            # Only count field errors for successfully matched steps (type matches)
                            _count_field_errors(gt_data, best_pr_data, gt_type, ignore_vessel, vessel_fields, _track_field_error, _norm_ph, match_key, step_idx)
                    # Skip counting field errors for missing steps - only count type-level errors
            
            # Skip counting field errors for extra steps - only count type-level errors
        else:
            # Positional matching
            i = 0
            while i < len(gt_steps):
                gt_type, gt_data = gt_steps[i]
                step_idx = i + 1
                
                if gt_type == "Add":
                    pr_add_steps_all = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                       if pr_steps[idx][0] == "Add" and idx not in pr_matched]
                    if pr_add_steps_all:
                        best_match_idx, _overlap = _find_best_add_match(gt_data, pr_add_steps_all)
                        if best_match_idx >= 0:
                            pr_global_idx, pr_data = pr_add_steps_all[best_match_idx]
                            pr_matched.add(pr_global_idx)
                            # Only count field errors for successfully matched steps (type matches)
                            _count_field_errors(gt_data, pr_data, "Add", ignore_vessel, vessel_fields, _track_field_error, _norm_ph, match_key, step_idx)
                        # Skip counting field errors for missing steps - only count type-level errors
                    # Skip counting field errors for missing steps - only count type-level errors
                    i += 1
                    continue
                
                pr_idx = len([idx for idx in pr_matched if idx < len(pr_steps)])
                while pr_idx in pr_matched and pr_idx < len(pr_steps):
                    pr_idx += 1
                
                if pr_idx < len(pr_steps):
                    pr_type, pr_data = pr_steps[pr_idx]
                    pr_matched.add(pr_idx)
                    
                    if _normalize(gt_type) == _normalize(pr_type):
                        # Only count field errors for successfully matched steps (type matches)
                        _count_field_errors(gt_data, pr_data, gt_type, ignore_vessel, vessel_fields, _track_field_error, _norm_ph, match_key, step_idx)
                    # Skip counting field errors for type mismatches - only count type-level errors
                # Skip counting field errors for missing steps - only count type-level errors
                
                i += 1
            
            # Skip counting field errors for extra steps - only count type-level errors
    
    # Handle unmatched prediction syntheses (those not matched to any GT synthesis)
    # Skip counting field errors for entirely unmatched syntheses - only count synthesis-level errors
    
    return field_errors, detailed_errors


def _count_field_errors(gt_data: Dict, pr_data: Dict, step_type: str, ignore_vessel: bool, 
                       vessel_fields: Set[str], track_fn, norm_ph_fn, 
                       ccdc: str = "", step_idx: int = -1) -> None:
    """Helper to count field-level errors for error analysis.
    
    Args:
        gt_data: Ground truth step data
        pr_data: Prediction step data
        step_type: Type of the step
        ignore_vessel: If True, ignore vessel fields
        vessel_fields: Set of vessel field names
        track_fn: Function to call to track errors
        norm_ph_fn: Function to normalize pH values
        ccdc: CCDC number for error reporting
        step_idx: Step index for error reporting
    """
    all_keys = set(gt_data.keys()) | set(pr_data.keys())
    
    for key in all_keys:
        if key in ("comment", "stepNumber"):
            continue
        if ignore_vessel and key in vessel_fields:
            continue
        
        gt_val = gt_data.get(key)
        pr_val = pr_data.get(key)
        
        # Chemical lists
        if key in ["addedChemical", "solvent", "washingSolvent"] and (isinstance(gt_val, list) or isinstance(pr_val, list)):
            gt_chems = gt_val if isinstance(gt_val, list) else []
            pr_chems = pr_val if isinstance(pr_val, list) else []
            
            gt_names = set()
            pr_names = set()
            gt_amounts = set()
            pr_amounts = set()
            
            for chem in gt_chems:
                if isinstance(chem, dict):
                    names = chem.get("chemicalName", []) or []
                    if not isinstance(names, list):
                        names = [names]
                    for n in names:
                        if _is_valid(n):
                            gt_names.add(_normalize(n))
                    amount = chem.get("chemicalAmount")
                    if _is_valid(amount):
                        gt_amounts.add(_normalize(amount))
            
            for chem in pr_chems:
                if isinstance(chem, dict):
                    names = chem.get("chemicalName", []) or chem.get("names", []) or []
                    if not isinstance(names, list):
                        names = [names]
                    for n in names:
                        if _is_valid(n):
                            pr_names.add(_normalize(n))
                    amount = chem.get("chemicalAmount") or chem.get("amount")
                    if _is_valid(amount):
                        pr_amounts.add(_normalize(amount))
            
            # Track name errors
            fn_names = len(gt_names - pr_names)
            if fn_names > 0:
                missing_names = gt_names - pr_names
                track_fn(f"{key}.names", "fn", fn_names, ccdc, step_idx, step_type, 
                        list(missing_names), list(pr_names))
            
            # Track amount errors
            fn_amounts = len(gt_amounts - pr_amounts)
            fp_amounts = len(pr_amounts - gt_amounts)
            if fn_amounts > 0:
                missing_amounts = gt_amounts - pr_amounts
                track_fn(f"{key}.amounts", "fn", fn_amounts, ccdc, step_idx, step_type,
                        list(missing_amounts), list(pr_amounts))
            if fp_amounts > 0:
                extra_amounts = pr_amounts - gt_amounts
                track_fn(f"{key}.amounts", "fp", fp_amounts, ccdc, step_idx, step_type,
                        list(gt_amounts), list(extra_amounts))
        
        # Scalar fields
        elif key == "targetPH":
            # Normalize targetPH for consistent comparison (treat -1, "n/a", empty as equivalent)
            g_n = norm_ph_fn(gt_val)
            p_n = norm_ph_fn(pr_val)
            if g_n != p_n:
                if g_n != "n/a":
                    track_fn(key, "fn", 1, ccdc, step_idx, step_type, gt_val, pr_val)
                if p_n != "n/a":
                    track_fn(key, "fp", 1, ccdc, step_idx, step_type, gt_val, pr_val)
        elif isinstance(gt_val, (bool, int, float)) or isinstance(pr_val, (bool, int, float)):
            if gt_val is not None and pr_val is not None:
                if gt_val != pr_val:
                    track_fn(key, "fn", 1, ccdc, step_idx, step_type, gt_val, pr_val)
                    track_fn(key, "fp", 1, ccdc, step_idx, step_type, gt_val, pr_val)
            elif gt_val is not None:
                track_fn(key, "fn", 1, ccdc, step_idx, step_type, gt_val, None)
            elif pr_val is not None:
                track_fn(key, "fp", 1, ccdc, step_idx, step_type, None, pr_val)
        else:
            if gt_val is not None and pr_val is not None:
                if gt_val != pr_val:
                    track_fn(key, "fn", 1, ccdc, step_idx, step_type, gt_val, pr_val)
                    track_fn(key, "fp", 1, ccdc, step_idx, step_type, gt_val, pr_val)
            elif gt_val is not None:
                track_fn(key, "fn", 1, ccdc, step_idx, step_type, gt_val, None)
            elif pr_val is not None:
                track_fn(key, "fp", 1, ccdc, step_idx, step_type, None, pr_val)


def _collect_step_field_differences(gt_synths: Dict[str, List[Dict]], pr_synths: Dict[str, List[Dict]], ignore_vessel: bool = False, short_mode: bool = False, skip_order: bool = False) -> List[str]:
    diffs: List[str] = []
    all_ccdcs: Set[str] = set(gt_synths.keys()) | set(pr_synths.keys())
    
    # Fields to skip when ignore_vessel is True
    vessel_fields = {"usedVesselName", "usedVesselType"}

    def _norm_ph(v: Any) -> str:
        s = str(v if v is not None else "").strip().lower()
        if s in ["-1", "-1.0", "n/a", "na", ""]:
            return "n/a"
        return s
    
    def _report_step_differences(diffs: List[str], ccdc: str, step_idx: int, gt_type: str, gt_data: Dict, 
                                 pr_type: Optional[str], pr_data: Dict, types_match: bool, 
                                 ignore_vessel: bool, vessel_fields: Set[str], _norm_ph) -> None:
        """Helper to report differences for a matched step pair."""
        if pr_type is None:
            # GT step with no corresponding prediction step
            diffs.append(f"- [{ccdc}] Step {step_idx} missing in prediction (gt has '{gt_type}')")
            return
        
        if not types_match:
            diffs.append(f"- [{ccdc}] Step {step_idx} type: pred='{pr_type}' vs gt='{gt_type}'")
            return
        
        # Compare chemical lists
        for list_key in ["addedChemical", "solvent", "washingSolvent"]:
            gt_list = gt_data.get(list_key) or []
            pr_list = pr_data.get(list_key) or []
            if isinstance(gt_list, list) or isinstance(pr_list, list):
                def _collect(lst: List[Dict]) -> Tuple[Set[str], Set[str]]:
                    names: Set[str] = set()
                    amounts: Set[str] = set()
                    for chem in lst or []:
                        if not isinstance(chem, dict):
                            continue
                        vals = chem.get("chemicalName") or chem.get("names") or []
                        if not isinstance(vals, list):
                            vals = [vals]
                        for v in vals:
                            if _is_valid(v):
                                names.add(_normalize(v))
                        amt = chem.get("chemicalAmount") or chem.get("amount")
                        if _is_valid(amt):
                            amounts.add(_normalize(amt))
                    return names, amounts
                gt_names, gt_amts = _collect(gt_list)
                pr_names, pr_amts = _collect(pr_list)
                miss_names = sorted(gt_names - pr_names)
                miss_amts = sorted(gt_amts - pr_amts)
                extra_amts = sorted(pr_amts - gt_amts)
                # Only report differences that matter: skip extra_names since they're ignored in scoring
                if miss_names or miss_amts or extra_amts:
                    parts: List[str] = []
                    if miss_names:
                        parts.append(f"{list_key}.names FN: {', '.join(miss_names)}")
                    if miss_amts:
                        parts.append(f"{list_key}.amounts FN: {', '.join(miss_amts)}")
                    if extra_amts:
                        parts.append(f"{list_key}.amounts FP: {', '.join(extra_amts)}")
                    diffs.append(f"- [{ccdc}] Step {step_idx} {gt_type}: " + "; ".join(parts))
        
        # Compare scalar fields
        keys = set(gt_data.keys()) | set(pr_data.keys())
        for k in sorted(keys):
            if k in ("addedChemical", "solvent", "washingSolvent", "comment", "stepNumber"):
                continue
            if ignore_vessel and k in vessel_fields:
                continue
            g = gt_data.get(k)
            p = pr_data.get(k)
            if k == "targetPH":
                g_n = _norm_ph(g)
                p_n = _norm_ph(p)
            elif isinstance(g, (bool, int, float)) or isinstance(p, (bool, int, float)):
                g_n = str(g).lower() if g is not None else ""
                p_n = str(p).lower() if p is not None else ""
            else:
                g_n = _normalize(g)
                p_n = _normalize(p)
            if g_n != p_n:
                if g_n or p_n:
                    p_display = p_n if p_n else "(missing)"
                    g_display = g_n if g_n else "(missing)"
                    diffs.append(f"- [{ccdc}] Step {step_idx} {gt_type}.{k}: pred='{p_display}' vs gt='{g_display}'")

    for ccdc in sorted(all_ccdcs):
        gt_synth_list = gt_synths.get(ccdc, [])
        pr_synth_list = pr_synths.get(ccdc, [])

        gt_steps: List[Tuple[str, Dict]] = []
        pr_steps: List[Tuple[str, Dict]] = []
        for synth in gt_synth_list:
            raw = (synth or {}).get("steps", []) or []
            gt_steps.extend(_expand_add_steps(raw))
        for synth in pr_synth_list:
            raw = (synth or {}).get("steps", []) or []
            pr_steps.extend(_expand_add_steps(raw))

        # Track which prediction steps have been matched
        pr_matched: Set[int] = set()
        
        # Use the same matching logic as score_steps_fine_grained
        i = 0
        step_idx = 1  # For reporting
        
        if skip_order:
            # Non-positional matching by type
            for i in range(len(gt_steps)):
                gt_type, gt_data = gt_steps[i]
                
                # For Add steps, use chemical-name-based matching
                if gt_type == "Add":
                    pr_add_steps_all = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                       if pr_steps[idx][0] == "Add" and idx not in pr_matched]
                    if pr_add_steps_all:
                        best_match_idx, _overlap = _find_best_add_match(gt_data, pr_add_steps_all)
                        if best_match_idx >= 0:
                            pr_global_idx, pr_data = pr_add_steps_all[best_match_idx]
                            pr_matched.add(pr_global_idx)
                            pr_type = "Add"
                            types_match = True
                        else:
                            pr_type = None
                            pr_data = {}
                            types_match = False
                    else:
                        pr_type = None
                        pr_data = {}
                        types_match = False
                else:
                    # For non-Add steps, find best match by step type
                    pr_same_type_steps = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                         if _normalize(pr_steps[idx][0]) == _normalize(gt_type) and idx not in pr_matched]
                    if pr_same_type_steps:
                        # Find best match by comparing fields
                        best_idx = -1
                        best_tp = -1
                        for idx, pr_data_cand in pr_same_type_steps:
                            step_tp, _, _ = _compare_step_fields(gt_data, pr_data_cand, gt_type, ignore_vessel)
                            if step_tp > best_tp:
                                best_tp = step_tp
                                best_idx = idx
                                best_pr_data = pr_data_cand
                        if best_idx >= 0 and best_pr_data is not None:
                            pr_matched.add(best_idx)
                            pr_type = gt_type
                            types_match = True
                        else:
                            pr_type = None
                            pr_data = {}
                            types_match = False
                    else:
                        pr_type = None
                        pr_data = {}
                        types_match = False
                
                # Report differences for this matched pair
                _report_step_differences(diffs, ccdc, step_idx, gt_type, gt_data, pr_type, pr_data, 
                                        types_match, ignore_vessel, vessel_fields, _norm_ph)
                step_idx += 1
            
            # Handle remaining unmatched prediction steps
            for pr_idx in range(len(pr_steps)):
                if pr_idx not in pr_matched:
                    pr_type, _ = pr_steps[pr_idx]
                    diffs.append(f"- [{ccdc}] Step {step_idx} extra in prediction ('{pr_type}')")
                    step_idx += 1
        else:
            # Original positional matching
            while i < len(gt_steps):
                gt_type, gt_data = gt_steps[i]
                
                # Special matching for Add: do non-positional best-match by chemical names
                if gt_type == "Add":
                    # Collect all unmatched prediction Add steps
                    pr_add_steps_all = [(idx, pr_steps[idx][1]) for idx in range(len(pr_steps))
                                       if pr_steps[idx][0] == "Add" and idx not in pr_matched]
                    if pr_add_steps_all:
                        best_match_idx, _overlap = _find_best_add_match(gt_data, pr_add_steps_all)
                        if best_match_idx >= 0:
                            pr_global_idx, pr_data = pr_add_steps_all[best_match_idx]
                            pr_matched.add(pr_global_idx)
                            pr_type = "Add"
                            types_match = True
                        else:
                            # No matching Add found in prediction
                            pr_type = None
                            pr_data = {}
                            types_match = False
                    else:
                        # No prediction Adds left
                        pr_type = None
                        pr_data = {}
                        types_match = False
                else:
                    # Non-Add steps: use positional matching
                    # Find next unmatched prediction step
                    pr_idx = i
                    while pr_idx < len(pr_steps) and pr_idx in pr_matched:
                        pr_idx += 1
                    
                    if pr_idx < len(pr_steps):
                        pr_type, pr_data = pr_steps[pr_idx]
                        pr_matched.add(pr_idx)
                        types_match = _normalize(gt_type) == _normalize(pr_type)
                    else:
                        pr_type = None
                        pr_data = {}
                        types_match = False
                
                # Report differences for this matched pair
                _report_step_differences(diffs, ccdc, step_idx, gt_type, gt_data, pr_type, pr_data, 
                                        types_match, ignore_vessel, vessel_fields, _norm_ph)
                i += 1
                step_idx += 1
            
            # Handle remaining unmatched prediction steps
            for pr_idx in range(len(pr_steps)):
                if pr_idx not in pr_matched:
                    pr_type, _ = pr_steps[pr_idx]
                    diffs.append(f"- [{ccdc}] Step {step_idx} extra in prediction ('{pr_type}')")
                    step_idx += 1

    return diffs

def _get_iter3_results(hash_value: str, gt_obj: Any, pred_obj: Any) -> Dict[str, List[List[str]]]:
    """
    Load iter3 step hints from data/{hash}/mcp_run/iter3_results/.
    
    Args:
        hash_value: The hash value (e.g., '0e299eb4')
        gt_obj: The ground truth object containing Synthesis data
        pred_obj: The prediction object containing Synthesis data
    
    Returns:
        Dictionary mapping CCDC numbers to lists of iter3 step type sequences
        (one sequence per iter3 hint file for that CCDC)
    """
    iter3_steps: Dict[str, List[List[str]]] = {}
    
    # Look for iter3 results in data/{hash}/mcp_run/iter3_results/
    iter3_dir = Path("data") / hash_value / "mcp_run" / "iter3_results"
    if not iter3_dir.exists():
        return iter3_steps
    
    # Build a mapping from CCDC to all entity names (from both GT and prediction)
    ccdc_to_names: Dict[str, List[str]] = {}
    
    # Add entity names from GT
    for synth in (gt_obj or {}).get("Synthesis", []) or []:
        ccdc = str((synth or {}).get("productCCDCNumber") or "").strip()
        if ccdc and ccdc.lower() not in ["n/a", "na", ""]:
            if ccdc not in ccdc_to_names:
                ccdc_to_names[ccdc] = []
            for name in (synth or {}).get("productNames", []) or []:
                if name:
                    name_str = str(name).strip()
                    if name_str not in ccdc_to_names[ccdc]:
                        ccdc_to_names[ccdc].append(name_str)
    
    # Add entity names from prediction
    for synth in (pred_obj or {}).get("Synthesis", []) or []:
        ccdc = str((synth or {}).get("productCCDCNumber") or "").strip()
        if ccdc and ccdc.lower() not in ["n/a", "na", ""]:
            if ccdc not in ccdc_to_names:
                ccdc_to_names[ccdc] = []
            for name in (synth or {}).get("productNames", []) or []:
                if name:
                    name_str = str(name).strip()
                    if name_str not in ccdc_to_names[ccdc]:
                        ccdc_to_names[ccdc].append(name_str)
    
    # Read all iter3_hints_*.txt files
    for hints_file in iter3_dir.glob("iter3_hints_*.txt"):
        try:
            content = hints_file.read_text(encoding="utf-8")
            data = json.loads(content)
            entity_label = data.get("entity_label", "")
            steps = data.get("steps", [])
            
            if not entity_label:
                continue
            
            # Extract step types
            step_types = [step.get("step", "") for step in steps if isinstance(step, dict)]
            
            # Match entity_label to CCDC using substring matching (both directions)
            entity_label_lower = entity_label.strip().lower()
            matched_ccdc = None
            
            # Try exact match first
            for ccdc, names in ccdc_to_names.items():
                for name in names:
                    if entity_label.strip() == name:
                        matched_ccdc = ccdc
                        break
                if matched_ccdc:
                    break
            
            # If no exact match, try substring matching (both directions)
            if not matched_ccdc:
                for ccdc, names in ccdc_to_names.items():
                    for name in names:
                        name_lower = name.lower()
                        # Check if either is substring of the other
                        if entity_label_lower and name_lower and (
                            entity_label_lower in name_lower or name_lower in entity_label_lower
                        ):
                            matched_ccdc = ccdc
                            break
                    if matched_ccdc:
                        break
            
            if matched_ccdc:
                if matched_ccdc not in iter3_steps:
                    iter3_steps[matched_ccdc] = []
                iter3_steps[matched_ccdc].append(step_types)
        except Exception:
            continue
    
    return iter3_steps


def _get_entity_text_files(hash_value: str, pred_obj: Any) -> Dict[str, str]:
    """
    Find and read entity text files for a given hash.
    
    Args:
        hash_value: The hash value (e.g., '1b9180ec')
        pred_obj: The prediction object containing Synthesis data
    
    Returns:
        Dictionary mapping entity names to their text content
    """
    entity_texts: Dict[str, str] = {}
    
    # Look for entity text files in data/{hash}/llm_based_results/
    llm_results_dir = Path("data") / hash_value / "llm_based_results"
    if not llm_results_dir.exists():
        return entity_texts
    
    # Collect entity names from prediction
    entity_names: Set[str] = set()
    for synth in (pred_obj or {}).get("Synthesis", []) or []:
        # Get product names
        for name in (synth or {}).get("productNames", []) or []:
            if name:
                entity_names.add(str(name).strip())
        # Also try CCDC if available
        ccdc = str((synth or {}).get("productCCDCNumber") or "").strip()
        if ccdc and ccdc.lower() not in ["n/a", "na", ""]:
            entity_names.add(ccdc)
    
    # Look for entity_text_*.txt files
    for entity_file in llm_results_dir.glob("entity_text_*.txt"):
        try:
            content = entity_file.read_text(encoding="utf-8")
            entity_name = entity_file.stem.replace("entity_text_", "")
            entity_texts[entity_name] = content
        except Exception:
            continue
    
    return entity_texts


def _collect_step_names_union(synth_list: List[Dict]) -> Set[str]:
    names: Set[str] = set()
    for synth in synth_list or []:
        for step in (synth or {}).get("steps", []) or []:
            step_type = _get_step_type(step)
            data = (step or {}).get(step_type, {}) if step_type else {}
            # addedChemical
            for chem in (data.get("addedChemical") or []):
                if isinstance(chem, dict):
                    vals = chem.get("chemicalName") or chem.get("names") or []
                    if not isinstance(vals, list):
                        vals = [vals]
                    for v in vals:
                        if _is_valid(v):
                            names.add(_normalize(v))
            # solvent
            for chem in (data.get("solvent") or []):
                if isinstance(chem, dict):
                    vals = chem.get("chemicalName") or chem.get("names") or []
                    if not isinstance(vals, list):
                        vals = [vals]
                    for v in vals:
                        if _is_valid(v):
                            names.add(_normalize(v))
            # washingSolvent
            for chem in (data.get("washingSolvent") or []):
                if isinstance(chem, dict):
                    vals = chem.get("chemicalName") or chem.get("names") or []
                    if not isinstance(vals, list):
                        vals = [vals]
                    for v in vals:
                        if _is_valid(v):
                            names.add(_normalize(v))
    return names


def _find_gt_file_new(doi: str) -> Optional[Path]:
    """Find GT file from the newer ground truth folders.
    
    Searches in order:
    1. newer_ground_truth_gao/prepared/steps/{doi}.json
    2. newer_ground_truth_lu/steps/ (multiple files per DOI)
    3. newer_ground_truth_sun/prepared/steps/{doi}.json
    
    Returns the first found path, or None if not found.
    """
    # Try newer_ground_truth_gao first
    gao_path = Path("newer_ground_truth_gao/prepared/steps") / f"{doi}.json"
    if gao_path.exists():
        return gao_path
    
    # Try newer_ground_truth_lu (has multiple files per DOI, try to find the main one)
    lu_dir = Path("newer_ground_truth_lu/steps")
    if lu_dir.exists():
        # Look for files matching the DOI pattern
        for file in lu_dir.glob(f"{doi}_*.json"):
            return file
    
    # Try newer_ground_truth_sun
    sun_path = Path("newer_ground_truth_sun/prepared/steps") / f"{doi}.json"
    if sun_path.exists():
        return sun_path
    
    return None


def evaluate_current(ignore_vessel: bool = False, short_mode: bool = False, skip_order: bool = False, ignore_mode: bool = False, use_new_gt: bool = False, use_full_gt: bool = False) -> None:
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/steps")
        OUT_ROOT = Path("evaluation/data/full_result/steps")
        # Use all available files
        allowed_files = None
    elif use_new_gt:
        GT_ROOT = None  # Will use _find_gt_file_new() instead
        OUT_ROOT = Path("evaluation/data/result/steps")
        allowed_files = None
    else:
        GT_ROOT = Path("full_ground_truth/steps")  # Always use full_ground_truth
        OUT_ROOT = Path("evaluation/data/result/steps")
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
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    rows_types_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    files_with_missing_ccdc: List[str] = []
    
    # Collect all entity scores across all hashes for step-type-only overall table
    all_entity_scores: List[Dict[str, Any]] = []
    
    # Track missing GT CCDCs (GT CCDCs not found in predictions)
    missing_gt_ccdcs: List[Dict[str, Any]] = []
    
    # Aggregate field errors across all files for overall analysis
    aggregated_field_errors: Dict[str, Dict[str, int]] = {}
    
    # Collect detailed error information for separate markdown files
    aggregated_detailed_errors: Dict[str, List[Dict[str, Any]]] = {}
    
    # In ignore_mode, also ignore usedVesselName (not just usedVesselType)
    effective_ignore_vessel = ignore_vessel or ignore_mode

    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "steps.json"
        if not doi or not res_path.exists():
            continue

        # Filter to allowed files in default mode
        if allowed_files is not None and f"{doi}.json" not in allowed_files:
            continue

        # Find GT file based on mode
        if use_full_gt:
            # In full mode, skip if GT doesn't exist
            gt_path = GT_ROOT / f"{doi}.json"
            if not gt_path.exists():
                continue
        elif use_new_gt:
            gt_path = _find_gt_file_new(doi)
            if gt_path is None:
                continue
        else:
            gt_path = GT_ROOT / f"{doi}.json"
            if not gt_path.exists():
                continue

        gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
        pred_obj = json.loads(res_path.read_text(encoding="utf-8"))
        
        # Apply ignore_mode transformations if enabled
        if ignore_mode:
            # Filter out H4PBPTA product
            gt_obj = _filter_out_product(gt_obj, "H4PBPTA")
            pred_obj = _filter_out_product(pred_obj, "H4PBPTA")

            # Convert atmosphere "air" to "n/a" in GT for specific hashes
            gt_obj = _convert_air_to_na(gt_obj, hv)

        # Expand Add steps with multiple chemicals in both GT and predictions
        gt_obj = _expand_add_steps_in_obj(gt_obj)
        pred_obj = _expand_add_steps_in_obj(pred_obj)

        # Normalize BOTH GT and prediction first, then score
        # Normalize once for scoring and reporting
        gt_normalized = normalize_json_structure(gt_obj)
        pred_normalized = normalize_json_structure(pred_obj)

        tp, fp, fn, pred_missing_ccdc = score_steps_fine_grained(gt_normalized, pred_normalized, effective_ignore_vessel, skip_order)
        # Step-type-only scoring
        ttp, tfp, tfn = _type_counts_for_objs(gt_normalized, pred_normalized)
        if pred_missing_ccdc:
            files_with_missing_ccdc.append(f"steps/{hv}.json")

        rows_overall.append((hv, (tp, fp, fn)))
        rows_types_overall.append((hv, (ttp, tfp, tfn)))
        
        # Compute per-entity step-type-only scores for this hash
        matched_gt_ids_entity: Set[int] = set()
        for pred_synth in (pred_obj or {}).get("Synthesis", []) or []:
            pred_ccdc = _normalize_ccdc(str((pred_synth or {}).get("productCCDCNumber") or "").strip())
            if pred_ccdc == "":
                pred_ccdc = ""
            
            pred_types = _gt_step_names(pred_synth)
            
            gt_synth = None
            # Try name-based matching FIRST (prioritize product name over CCDC)
            pred_names = [str(x) for x in (pred_synth or {}).get("productNames", []) or []]
            if pred_names:
                # Normalize ALL prediction names
                pred_names_norm = [_normalize_product_name(n) for n in pred_names if _normalize_product_name(n)]
                
                name_candidates = []
                for candidate in (gt_obj or {}).get("Synthesis", []) or []:
                    if id(candidate) in matched_gt_ids_entity:
                        continue
                    cand_names = [str(x) for x in (candidate or {}).get("productNames", []) or []]
                    cand_names_norm = [_normalize_product_name(n) for n in cand_names if _normalize_product_name(n)]
                    
                    # Check if ANY pred name matches ANY candidate name
                    matched = False
                    for pred_name_norm in pred_names_norm:
                        if pred_name_norm in cand_names_norm:
                            matched = True
                            break
                        # Only allow substring matching if both names are reasonably long (>= 5 chars)
                        # to avoid false matches like "i" matching "nanocapsule ii"
                        for cn in cand_names_norm:
                            if (pred_name_norm and len(pred_name_norm) >= 5 and len(cn) >= 5 and 
                                (pred_name_norm in cn or cn in pred_name_norm)):
                                matched = True
                                break
                        if matched:
                            break
                    
                    if matched:
                        name_candidates.append(candidate)
                
                if len(name_candidates) == 1:
                    gt_synth = name_candidates[0]
                    matched_gt_ids_entity.add(id(gt_synth))
                elif len(name_candidates) > 1:
                    best_match = None
                    best_score = -1
                    for candidate in name_candidates:
                        cand_types = _gt_step_names(candidate)
                        matches, _, _ = _compare_steps(cand_types, pred_types)
                        if matches > best_score:
                            best_score = matches
                            best_match = candidate
                    if best_match is not None:
                        gt_synth = best_match
                        matched_gt_ids_entity.add(id(best_match))
            
            # Fall back to CCDC matching only if no name match was found
            if gt_synth is None and pred_ccdc:
                ccdc_candidates = []
                for candidate in (gt_obj or {}).get("Synthesis", []) or []:
                    if id(candidate) in matched_gt_ids_entity:
                        continue
                    gt_ccdc = _normalize_ccdc(str((candidate or {}).get("productCCDCNumber") or "").strip())
                    if gt_ccdc != "" and gt_ccdc == pred_ccdc:
                        ccdc_candidates.append(candidate)
                
                if len(ccdc_candidates) == 1:
                    gt_synth = ccdc_candidates[0]
                    matched_gt_ids_entity.add(id(gt_synth))
                elif len(ccdc_candidates) > 1:
                    best_match = None
                    best_score = -1
                    for candidate in ccdc_candidates:
                        cand_types = _gt_step_names(candidate)
                        matches, _, _ = _compare_steps(cand_types, pred_types)
                        if matches > best_score:
                            best_score = matches
                            best_match = candidate
                    if best_match is not None:
                        gt_synth = best_match
                        matched_gt_ids_entity.add(id(best_match))
            
            if gt_synth:
                gt_types = _gt_step_names(gt_synth)
                e_tp, e_fp, e_fn = _compare_steps(gt_types, pred_types)
            else:
                gt_types = []
                e_tp = 0
                e_fp = len(pred_types)
                e_fn = 0
            
            # Prioritize product names over CCDC for display to show what was actually matched
            entity_label = pred_names[0] if pred_names else (pred_ccdc if pred_ccdc else "<unnamed>")
            all_entity_scores.append({
                "doi": doi,
                "hash": hv,
                "entity": entity_label,
                "gt_len": len(gt_types),
                "pred_len": len(pred_types),
                "tp": e_tp,
                "fp": e_fp,
                "fn": e_fn,
            })
        
        # Handle unmatched GT syntheses
        for gt_synth in (gt_obj or {}).get("Synthesis", []) or []:
            if id(gt_synth) not in matched_gt_ids_entity:
                gt_types_unmatched = _gt_step_names(gt_synth)
                gt_ccdc_unmatched = _normalize_ccdc(str((gt_synth or {}).get("productCCDCNumber") or "").strip())
                if gt_ccdc_unmatched == "":
                    gt_ccdc_unmatched = ""
                gt_names_unmatched = [str(x) for x in (gt_synth.get("productNames", []) or [])]
                # Prioritize product names over CCDC for display
                entity_label_unmatched = gt_names_unmatched[0] if gt_names_unmatched else (gt_ccdc_unmatched if gt_ccdc_unmatched else "<unnamed>")
                
                all_entity_scores.append({
                    "doi": doi,
                    "hash": hv,
                    "entity": entity_label_unmatched + " (GT only)",
                    "gt_len": len(gt_types_unmatched),
                    "pred_len": 0,
                    "tp": 0,
                    "fp": 0,
                    "fn": len(gt_types_unmatched),
                })
                
                # Record this as a missing GT CCDC/entity
                missing_gt_ccdcs.append({
                    "doi": doi,
                    "hash": hv,
                    "gt_file": gt_path.as_posix(),
                    "pred_file": res_path.as_posix(),
                    "ccdc": gt_ccdc_unmatched if gt_ccdc_unmatched else "N/A",
                    "entity_names": gt_names_unmatched,
                    "num_steps": len(gt_types_unmatched),
                })

        # Build GT-to-Pred matching map for detailed reporting
        # When multiple GT syntheses have the same CCDC, match each to best prediction
        gt_synths_flat = (gt_normalized or {}).get("Synthesis", []) or []
        pr_synths_flat = (pred_normalized or {}).get("Synthesis", []) or []
        
        # Create GT-to-Pred matching: for each GT synthesis, find best matching pred
        gt_to_pred_matches: List[Tuple[Dict, Optional[Dict], str]] = []  # (gt_synth, pred_synth, match_key)
        matched_pred_ids: Set[int] = set()
        
        for gt_synth in gt_synths_flat:
            gt_ccdc = _normalize_ccdc(str((gt_synth or {}).get("productCCDCNumber") or "").strip())
            gt_names = [str(x) for x in (gt_synth.get("productNames", []) or [])]
            
            # Normalize CCDC
            if not gt_ccdc:
                if gt_names:
                    match_key = f"NAME:{gt_names[0]}"
                else:
                    match_key = "NAME:<unnamed>"
            else:
                match_key = gt_ccdc
            
            # Get GT step types for matching
            gt_steps_raw = (gt_synth or {}).get("steps", []) or []
            gt_types = [t for (t, _d) in _expand_add_steps(gt_steps_raw)]
            
            # Find best matching pred synthesis
            best_pred = None
            best_score = -1
            
            for pred_synth in pr_synths_flat:
                if id(pred_synth) in matched_pred_ids:
                    continue
                
                pred_ccdc = _normalize_ccdc(str((pred_synth or {}).get("productCCDCNumber") or "").strip())
                pred_names = [str(x) for x in (pred_synth.get("productNames", []) or [])]
                
                # Check if CCDCs match
                ccdc_match = False
                if gt_ccdc and pred_ccdc:
                    ccdc_match = (gt_ccdc == pred_ccdc)
                
                # Check if ANY-to-ANY product names match (normalized)
                name_match = False
                gt_names_norm = [_normalize_product_name(n) for n in gt_names if _normalize_product_name(n)]
                pred_names_norm = [_normalize_product_name(n) for n in pred_names if _normalize_product_name(n)]
                if gt_names_norm and pred_names_norm:
                    for gn in gt_names_norm:
                        if gn in pred_names_norm:
                            name_match = True
                            break
                        for pn in pred_names_norm:
                            if gn and (gn in pn or pn in gn):
                                name_match = True
                                break
                        if name_match:
                            break
                
                # Only consider this pred if CCDC or name matches
                if not (ccdc_match or name_match):
                    continue
                
                # Score the match based on step types
                pred_steps_raw = (pred_synth or {}).get("steps", []) or []
                pred_types = [t for (t, _d) in _expand_add_steps(pred_steps_raw)]
                matches, _, _ = _compare_steps(gt_types, pred_types)
                
                if matches > best_score:
                    best_score = matches
                    best_pred = pred_synth
            
            if best_pred is not None:
                matched_pred_ids.add(id(best_pred))
            
            gt_to_pred_matches.append((gt_synth, best_pred, match_key))
        
        # Add unmatched predictions
        for pred_synth in pr_synths_flat:
            if id(pred_synth) not in matched_pred_ids:
                pred_ccdc = _normalize_ccdc(str((pred_synth or {}).get("productCCDCNumber") or "").strip())
                pred_names = [str(x) for x in (pred_synth.get("productNames", []) or [])]
                
                if not pred_ccdc:
                    if pred_names:
                        match_key = f"NAME:{pred_names[0]}"
                    else:
                        match_key = "NAME:<unnamed>"
                else:
                    match_key = pred_ccdc
                
                gt_to_pred_matches.append((None, pred_synth, match_key))
        
        # Per-item report
        lines: List[str] = []
        lines.append(f"# Steps Scoring - {hv}\n\n")
        # Metadata block
        lines.append(f"**DOI**: `{doi}`  \n")
        lines.append(f"**Hash**: `{hv}`  \n")
        lines.append(f"**Prediction file**: `{res_path.as_posix()}`  \n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n")
        if ignore_mode:
            lines.append(f"**Ignore mode**: Yes (vessel names ignored, H4PBPTA product filtered out)\n")
        elif ignore_vessel:
            lines.append(f"**Ignore vessel**: Yes (vessel names and types excluded from scoring)\n")
        lines.append("\n")
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n\n")

        # Step-type-only scoring table
        tprec, trec, tf1 = precision_recall_f1(ttp, tfp, tfn)
        lines.append("## Step type-only scoring\n\n")
        lines.append("| TP | FP | FN | Precision | Recall | F1 |\n")
        lines.append("| ---: | ---: | ---: | ---: | ---: | ---: |\n")
        lines.append(f"| {ttp} | {tfp} | {tfn} | {tprec:.3f} | {trec:.3f} | {tf1:.3f} |\n\n")
        
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_normalized, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_normalized, indent=2))
        lines.append("\n```\n\n")

        # Differences summary (normalized)
        gt_synths = _get_synths_by_ccdc(gt_normalized)
        pr_synths = _get_synths_by_ccdc(pred_normalized)
        
        # Error analysis by field (using normalized objects for proper matching)
        field_errors, detailed_errors = _analyze_errors_by_field(gt_normalized, pred_normalized, effective_ignore_vessel, skip_order, 
                                                                 collect_details=True, hash_value=hv, doi=doi)
        
        # Aggregate field errors for overall report
        for field_name, errors in field_errors.items():
            if field_name not in aggregated_field_errors:
                aggregated_field_errors[field_name] = {'fn': 0, 'fp': 0}
            aggregated_field_errors[field_name]['fn'] += errors['fn']
            aggregated_field_errors[field_name]['fp'] += errors['fp']
        
        # Aggregate detailed errors for separate markdown files
        for field_name, error_list in detailed_errors.items():
            if field_name not in aggregated_detailed_errors:
                aggregated_detailed_errors[field_name] = []
            aggregated_detailed_errors[field_name].extend(error_list)
        
        if field_errors:
            lines.append("## Error Analysis by Field\n\n")
            lines.append("This table shows which fields contribute most to errors **in successfully matched steps** (where step types match). Type mismatches and missing/extra steps are excluded from this analysis.\n\n")
            lines.append("| Field | FN | FP | Total Errors | % of Total FN | % of Total FP | Hypothetical P | Hypothetical R | Hypothetical F1 |\n")
            lines.append("|-------|---:|---:|-------------:|--------------:|--------------:|---------------:|---------------:|----------------:|\n")
            
            # Calculate totals for percentage
            total_fn_errors = sum(e['fn'] for e in field_errors.values())
            total_fp_errors = sum(e['fp'] for e in field_errors.values())
            
            # Sort by total errors (FN + FP) descending
            sorted_fields = sorted(field_errors.items(), key=lambda x: x[1]['fn'] + x[1]['fp'], reverse=True)
            
            for field_name, errors in sorted_fields:
                field_fn = errors['fn']
                field_fp = errors['fp']
                total_field_errors = field_fn + field_fp
                
                # Calculate percentages
                pct_fn = (field_fn / total_fn_errors * 100) if total_fn_errors > 0 else 0
                pct_fp = (field_fp / total_fp_errors * 100) if total_fp_errors > 0 else 0
                
                # Calculate hypothetical scores if this field were perfect
                hyp_tp = tp + field_fn  # Recovered FNs become TPs
                hyp_fp = max(0, fp - field_fp)  # Removed FPs
                hyp_fn = max(0, fn - field_fn)  # Removed FNs
                hyp_prec, hyp_rec, hyp_f1 = precision_recall_f1(hyp_tp, hyp_fp, hyp_fn)
                
                lines.append(f"| {field_name} | {field_fn} | {field_fp} | {total_field_errors} | {pct_fn:.1f}% | {pct_fp:.1f}% | {hyp_prec:.3f} | {hyp_rec:.3f} | {hyp_f1:.3f} |\n")
            
            lines.append("\n")
            lines.append(f"**Note**: Hypothetical scores assume fixing only that specific field while keeping all other errors.\n\n")
        
        gt_keys = set(gt_synths.keys())
        pr_keys = set(pr_synths.keys())
        fn_keys = sorted(gt_keys - pr_keys)
        fp_keys = sorted(pr_keys - gt_keys)
        gt_names = _collect_step_names_union([s for lst in gt_synths.values() for s in lst])
        pr_names = _collect_step_names_union([s for lst in pr_synths.values() for s in lst])
        fn_names = sorted(gt_names - pr_names)
        fp_names = sorted(pr_names - gt_names)
        lines.append("## Differences\n\n")
        lines.append(f"Keys (Prediction vs GT): {', '.join(sorted(pr_keys))} - {', '.join(sorted(gt_keys))}\n")
        lines.append(f"FN (missing keys): {', '.join(fn_keys) if fn_keys else 'None'}\n")
        lines.append(f"FP (extra keys): {', '.join(fp_keys) if fp_keys else 'None'}\n")
        lines.append(f"FN (missing chemical names): {', '.join(fn_names) if fn_names else 'None'}\n")
        lines.append(f"FP (extra chemical names): {', '.join(fp_names) if fp_names else 'None'}\n\n")
        # Field-level differences per CCDC and step
        field_diffs = _collect_step_field_differences(gt_synths, pr_synths, effective_ignore_vessel, short_mode, skip_order)
        if field_diffs:
            lines.append("### Field-level differences\n\n")
            for d in field_diffs:
                lines.append(d + "\n")
            lines.append("\n")

        # Side-by-side step types listing (GT vs Pred) per matched synthesis
        # When multiple GT syntheses have the same CCDC, show separate tables
        lines.append("## Step types (GT vs Pred)\n\n")
        lines.append("Each table shows a GT synthesis matched to its best prediction. When multiple GT syntheses have the same CCDC, they are shown separately.\n\n")
        
        # Group matches by CCDC to add indices when needed
        ccdc_to_matches: Dict[str, List[Tuple[Dict, Optional[Dict], str]]] = {}
        for gt_synth, pred_synth, match_key in gt_to_pred_matches:
            if match_key not in ccdc_to_matches:
                ccdc_to_matches[match_key] = []
            ccdc_to_matches[match_key].append((gt_synth, pred_synth, match_key))
        
        for match_key in sorted(ccdc_to_matches.keys()):
            matches_for_key = ccdc_to_matches[match_key]
            
            for idx, (gt_synth, pred_synth, _) in enumerate(matches_for_key, 1):
                # Add index if multiple entries for same CCDC
                if len(matches_for_key) > 1:
                    header = f"### [{match_key}] (Entry {idx}/{len(matches_for_key)})"
                else:
                    header = f"### [{match_key}]"
                
                lines.append(f"{header}\n")
                
                # Add GT synthesis name if available
                if gt_synth:
                    gt_names = [str(x) for x in (gt_synth.get("productNames", []) or [])]
                    if gt_names:
                        lines.append(f"**GT**: {', '.join(gt_names)}  \n")
                
                # Add Pred synthesis name if available
                if pred_synth:
                    pred_names = [str(x) for x in (pred_synth.get("productNames", []) or [])]
                    if pred_names:
                        lines.append(f"**Pred**: {', '.join(pred_names)}  \n")
                
                if not gt_synth and not pred_synth:
                    lines.append("(no data)\n\n")
                    continue
                
                # Get step types
                gt_types: List[str] = []
                if gt_synth:
                    raw_steps = (gt_synth or {}).get("steps", []) or []
                    gt_types = [t for (t, _d) in _expand_add_steps(raw_steps)]
                
                pr_types: List[str] = []
                if pred_synth:
                    raw_steps = (pred_synth or {}).get("steps", []) or []
                    pr_types = [t for (t, _d) in _expand_add_steps(raw_steps)]
                
                max_len = max(len(gt_types), len(pr_types))
                if max_len == 0:
                    lines.append("(no steps)\n\n")
                    continue
                
                for i in range(max_len):
                    gt_t = gt_types[i] if i < len(gt_types) else "-"
                    pr_t = pr_types[i] if i < len(pr_types) else "-"
                    lines.append(f"{i+1}. {gt_t} — {pr_t}\n")
                lines.append("\n")
        
        # Load iter3 results and add comparison table (GT vs Iter3 vs Pred)
        iter3_results = _get_iter3_results(hv, gt_obj, pred_obj)
        if iter3_results:
            lines.append("## Step types comparison (GT vs Iter3 vs Pred)\n\n")
            lines.append("This table compares the ground truth, intermediate iter3 results, and final predictions. When multiple GT syntheses have the same CCDC, they are shown separately.\n\n")
            
            for match_key in sorted(ccdc_to_matches.keys()):
                matches_for_key = ccdc_to_matches[match_key]
                
                for idx, (gt_synth, pred_synth, _) in enumerate(matches_for_key, 1):
                    # Add index if multiple entries for same CCDC
                    if len(matches_for_key) > 1:
                        header = f"### [{match_key}] (Entry {idx}/{len(matches_for_key)})"
                    else:
                        header = f"### [{match_key}]"
                    
                    lines.append(f"{header}\n\n")
                    
                    # Add GT synthesis name if available
                    if gt_synth:
                        gt_names = [str(x) for x in (gt_synth.get("productNames", []) or [])]
                        if gt_names:
                            lines.append(f"**GT**: {', '.join(gt_names)}  \n")
                    
                    # Add Pred synthesis name if available
                    if pred_synth:
                        pred_names = [str(x) for x in (pred_synth.get("productNames", []) or [])]
                        if pred_names:
                            lines.append(f"**Pred**: {', '.join(pred_names)}  \n")
                    
                    lines.append("\n")
                    
                    # Get GT types
                    gt_types: List[str] = []
                    if gt_synth:
                        raw_steps = (gt_synth or {}).get("steps", []) or []
                        gt_types = [t for (t, _d) in _expand_add_steps(raw_steps)]
                    
                    # Get Iter3 types - find best match using step type similarity
                    iter3_types: List[str] = []
                    iter3_candidates = iter3_results.get(match_key, [])
                    if iter3_candidates and gt_types:
                        # Find best matching iter3 result for this GT
                        best_iter3 = []
                        best_score = -1
                        for candidate in iter3_candidates:
                            matches, _, _ = _compare_steps(gt_types, candidate)
                            if matches > best_score:
                                best_score = matches
                                best_iter3 = candidate
                        iter3_types = best_iter3
                    elif iter3_candidates:
                        # If no GT, just take the first iter3 result
                        iter3_types = iter3_candidates[0] if iter3_candidates else []
                    
                    # Get Pred types
                    pr_types: List[str] = []
                    if pred_synth:
                        raw_steps = (pred_synth or {}).get("steps", []) or []
                        pr_types = [t for (t, _d) in _expand_add_steps(raw_steps)]
                    
                    max_len = max(len(gt_types), len(iter3_types), len(pr_types))
                    if max_len == 0:
                        lines.append("(no steps)\n\n")
                        continue
                    
                    # Create table header
                    lines.append("| Step | GT | Iter3 | Pred |\n")
                    lines.append("|-----:|:---|:------|:-----|\n")
                    
                    # Fill table rows
                    for i in range(max_len):
                        step_num = i + 1
                        gt_t = gt_types[i] if i < len(gt_types) else "-"
                        iter3_t = iter3_types[i] if i < len(iter3_types) else "-"
                        pr_t = pr_types[i] if i < len(pr_types) else "-"
                        lines.append(f"| {step_num} | {gt_t} | {iter3_t} | {pr_t} |\n")
                    
                    lines.append("\n")
        
        # Append entity text files if available
        entity_texts = _get_entity_text_files(hv, pred_obj)
        if entity_texts:
            lines.append("## Entity Text Files\n\n")
            lines.append("The following are the extracted entity text files used in the extraction process:\n\n")
            for entity_name in sorted(entity_texts.keys()):
                lines.append(f"### {entity_name}\n\n")
                lines.append("```\n")
                lines.append(entity_texts[entity_name])
                if not entity_texts[entity_name].endswith("\n"):
                    lines.append("\n")
                lines.append("```\n\n")
        
        (OUT_ROOT / f"{hv}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# Steps Scoring - Overall\n\n")
    lines_overall.append("| # | ID | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")

    total_tp = total_fp = total_fn = 0
    for idx, (ident, (tp, fp, fn)) in enumerate(rows_overall, 1):
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines_overall.append(f"| {idx} | {ident} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        total_tp += tp
        total_fp += fp
        total_fn += fn

    overall_prec, overall_rec, overall_f1 = precision_recall_f1(total_tp, total_fp, total_fn)
    lines_overall.append(f"| - | **Overall** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{overall_prec:.3f}** | **{overall_rec:.3f}** | **{overall_f1:.3f}** |\n")

    # Step-type-only overall table - per-entity scores
    lines_overall.append("\n## Step type-only — Overall (Per-Entity)\n\n")
    lines_overall.append("| # | Hash | DOI | Entity | GT | Pred | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("| ---:|------|-----|--------|---:|-----:|---:|---:|---:|----------:|-------:|----:|\n")
    
    # Calculate overall aggregates from per-entity scores
    t_total_tp = 0
    t_total_gt = 0
    t_total_pred = 0
    for idx, entity_score in enumerate(all_entity_scores, 1):
        e_prec = (entity_score["tp"] / entity_score["pred_len"]) if entity_score["pred_len"] > 0 else 0.0
        e_rec = (entity_score["tp"] / entity_score["gt_len"]) if entity_score["gt_len"] > 0 else 0.0
        e_f1 = (2 * e_prec * e_rec / (e_prec + e_rec)) if (e_prec + e_rec) > 0 else 0.0
        
        lines_overall.append(
            f"| {idx} | {entity_score['hash']} | {entity_score['doi']} | {entity_score['entity']} | "
            f"{entity_score['gt_len']} | {entity_score['pred_len']} | "
            f"{entity_score['tp']} | {entity_score['fp']} | {entity_score['fn']} | "
            f"{e_prec:.3f} | {e_rec:.3f} | {e_f1:.3f} |\n"
        )
        
        t_total_tp += entity_score["tp"]
        t_total_gt += entity_score["gt_len"]
        t_total_pred += entity_score["pred_len"]
    
    # Overall aggregate row - calculate P/R/F1 from totals
    t_overall_prec = (t_total_tp / t_total_pred) if t_total_pred > 0 else 0.0
    t_overall_rec = (t_total_tp / t_total_gt) if t_total_gt > 0 else 0.0
    t_overall_f1 = (2 * t_overall_prec * t_overall_rec / (t_overall_prec + t_overall_rec)) if (t_overall_prec + t_overall_rec) > 0 else 0.0
    lines_overall.append(
        f"| **Overall** | **All** | **All** | **{t_total_gt}** | **{t_total_pred}** | "
        f"**{t_total_tp}** | **{t_total_pred - t_total_tp}** | **{t_total_gt - t_total_tp}** | "
        f"**{t_overall_prec:.3f}** | **{t_overall_rec:.3f}** | **{t_overall_f1:.3f}** |\n"
    )

    if files_with_missing_ccdc:
        lines_overall.append("\n## Files with Missing or N/A CCDC Numbers\n\n")
        for fpath in sorted(set(files_with_missing_ccdc)):
            lines_overall.append(f"- `{fpath}`\n")
    
    # Top 5 error sources across all files
    if aggregated_field_errors:
        lines_overall.append("\n## All Error Sources (Aggregated)\n\n")
        lines_overall.append("This table shows all fields contributing to errors **in successfully matched steps** (where step types match). Type mismatches and missing/extra steps are excluded from this analysis.\n\n")
        lines_overall.append("**Detailed error breakdown**: See separate markdown files in `error_details/` directory. Click field names below to navigate.\n\n")
        lines_overall.append("| Rank | Field | FN | FP | Total Errors | % of Total FN | % of Total FP | Hypothetical P | Hypothetical R | Hypothetical F1 |\n")
        lines_overall.append("|-----:|-------|---:|---:|-------------:|--------------:|--------------:|---------------:|---------------:|----------------:|\n")
        
        # Calculate totals for percentage
        total_fn_all = sum(e['fn'] for e in aggregated_field_errors.values())
        total_fp_all = sum(e['fp'] for e in aggregated_field_errors.values())
        
        # Sort by total errors (FN + FP) descending - show all fields
        sorted_fields_all_complete = sorted(aggregated_field_errors.items(), key=lambda x: x[1]['fn'] + x[1]['fp'], reverse=True)
        # Show all error types (no limit)
        sorted_fields_all = sorted_fields_all_complete
        
        for rank, (field_name, errors) in enumerate(sorted_fields_all, 1):
            field_fn = errors['fn']
            field_fp = errors['fp']
            total_field_errors = field_fn + field_fp
            
            # Calculate percentages
            pct_fn = (field_fn / total_fn_all * 100) if total_fn_all > 0 else 0
            pct_fp = (field_fp / total_fp_all * 100) if total_fp_all > 0 else 0
            
            # Calculate hypothetical scores if this field were perfect
            hyp_tp = total_tp + field_fn  # Recovered FNs become TPs
            hyp_fp = max(0, total_fp - field_fp)  # Removed FPs
            hyp_fn = max(0, total_fn - field_fn)  # Removed FNs
            hyp_prec, hyp_rec, hyp_f1 = precision_recall_f1(hyp_tp, hyp_fp, hyp_fn)
            
            # Create link to detailed error file
            safe_field_name = field_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            field_link = f"[{field_name}](error_details/{safe_field_name}.md)"
            
            lines_overall.append(f"| {rank} | {field_link} | {field_fn} | {field_fp} | {total_field_errors} | {pct_fn:.1f}% | {pct_fp:.1f}% | {hyp_prec:.3f} | {hyp_rec:.3f} | {hyp_f1:.3f} |\n")
        
        lines_overall.append("\n")
        lines_overall.append(f"**Current Overall**: P={overall_prec:.3f} R={overall_rec:.3f} F1={overall_f1:.3f}  \n")
        lines_overall.append(f"**Note**: Hypothetical scores show potential improvements if only that specific field were fixed across all files.\n\n")
        
        # Cumulative hypothetical improvements table
        lines_overall.append("### Cumulative Hypothetical Improvements\n\n")
        lines_overall.append("This table shows the potential improvements if we fix the top N error sources cumulatively.\n\n")
        lines_overall.append("| Top N Fixed | Cumulative FN Recovered | Cumulative FP Removed | Hypothetical P | Hypothetical R | Hypothetical F1 | F1 Gain |\n")
        lines_overall.append("|------------:|------------------------:|----------------------:|---------------:|---------------:|----------------:|--------:|\n")
        
        # Calculate cumulative improvements for all error sources
        for n in range(1, len(sorted_fields_all_complete) + 1):
            cumulative_fn = sum(sorted_fields_all_complete[i][1]['fn'] for i in range(n))
            cumulative_fp = sum(sorted_fields_all_complete[i][1]['fp'] for i in range(n))
            
            cum_hyp_tp = total_tp + cumulative_fn
            cum_hyp_fp = max(0, total_fp - cumulative_fp)
            cum_hyp_fn = max(0, total_fn - cumulative_fn)
            cum_hyp_prec, cum_hyp_rec, cum_hyp_f1 = precision_recall_f1(cum_hyp_tp, cum_hyp_fp, cum_hyp_fn)
            
            f1_gain = cum_hyp_f1 - overall_f1
            
            lines_overall.append(f"| Top {n} | {cumulative_fn} | {cumulative_fp} | {cum_hyp_prec:.3f} | {cum_hyp_rec:.3f} | {cum_hyp_f1:.3f} | +{f1_gain:.3f} |\n")
        
        lines_overall.append("\n")

    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())
    
    # Generate separate report for missing GT CCDCs
    if missing_gt_ccdcs:
        lines_missing: List[str] = []
        lines_missing.append("# Missing GT Entities in Predictions\n\n")
        lines_missing.append("This report lists synthesis entities that appear in ground truth but were not found in predictions.\n\n")
        
        # Overall summary table
        lines_missing.append("## Summary\n\n")
        lines_missing.append(f"**Total missing entities**: {len(missing_gt_ccdcs)}\n")
        lines_missing.append(f"**Total missing steps**: {sum(e['num_steps'] for e in missing_gt_ccdcs)}\n\n")
        
        # Overall table
        lines_missing.append("## Overall Table\n\n")
        lines_missing.append("| Hash | DOI | CCDC | Entity Names | GT Steps | GT File | Pred File |\n")
        lines_missing.append("|------|-----|------|--------------|:--------:|---------|-----------|\n")
        
        for entry in missing_gt_ccdcs:
            entity_names_str = "; ".join(entry["entity_names"]) if entry["entity_names"] else "(none)"
            lines_missing.append(
                f"| {entry['hash']} | {entry['doi']} | {entry['ccdc']} | {entity_names_str} | "
                f"{entry['num_steps']} | `{entry['gt_file']}` | `{entry['pred_file']}` |\n"
            )
        
        lines_missing.append("\n")
        
        # Grouped by hash/DOI
        lines_missing.append("## Details by Hash/DOI\n\n")
        
        # Group entries by hash
        by_hash: Dict[str, List[Dict[str, Any]]] = {}
        for entry in missing_gt_ccdcs:
            if entry['hash'] not in by_hash:
                by_hash[entry['hash']] = []
            by_hash[entry['hash']].append(entry)
        
        for hash_val in sorted(by_hash.keys()):
            entries = by_hash[hash_val]
            doi_val = entries[0]['doi']
            lines_missing.append(f"### {hash_val} ({doi_val})\n\n")
            lines_missing.append(f"**GT File**: `{entries[0]['gt_file']}`  \n")
            lines_missing.append(f"**Pred File**: `{entries[0]['pred_file']}`  \n")
            lines_missing.append(f"**Missing entities**: {len(entries)}\n\n")
            
            for i, entry in enumerate(entries, 1):
                lines_missing.append(f"#### Entity {i}\n\n")
                lines_missing.append(f"- **CCDC**: {entry['ccdc']}\n")
                if entry['entity_names']:
                    lines_missing.append(f"- **Names**: {', '.join(entry['entity_names'])}\n")
                else:
                    lines_missing.append(f"- **Names**: (none)\n")
                lines_missing.append(f"- **GT Steps**: {entry['num_steps']}\n")
                lines_missing.append("\n")
        
        (OUT_ROOT / "_missing_gt_entities.md").write_text("".join(lines_missing), encoding="utf-8")
        print((OUT_ROOT / "_missing_gt_entities.md").resolve())
    
    # Generate separate markdown files for all error fields
    if aggregated_detailed_errors and aggregated_field_errors:
        # Sort fields by total errors
        sorted_fields_for_details = sorted(aggregated_field_errors.items(), 
                                          key=lambda x: x[1]['fn'] + x[1]['fp'], 
                                          reverse=True)
        
        error_details_dir = OUT_ROOT / "error_details"
        error_details_dir.mkdir(parents=True, exist_ok=True)
        
        for field_name, _field_counts in sorted_fields_for_details:
            if field_name not in aggregated_detailed_errors:
                continue
            
            details = aggregated_detailed_errors[field_name]
            
            # Create markdown file for this field
            field_lines: List[str] = []
            field_lines.append(f"# Error Details: {field_name}\n\n")
            field_lines.append(f"**Total errors**: {len(details)} occurrences\n")
            field_lines.append(f"**Total FN**: {_field_counts['fn']}\n")
            field_lines.append(f"**Total FP**: {_field_counts['fp']}\n\n")
            
            # Group errors by error type (FN/FP)
            fn_errors = [e for e in details if e['error_type'] == 'fn']
            fp_errors = [e for e in details if e['error_type'] == 'fp']
            
            if fn_errors:
                field_lines.append(f"## False Negatives (FN): {len(fn_errors)} occurrences\n\n")
                field_lines.append("These are values present in ground truth but missing or incorrect in predictions.\n\n")
                field_lines.append("| Hash | DOI | CCDC | Step | Step Type | GT Value | Pred Value |\n")
                field_lines.append("|------|-----|------|:----:|-----------|----------|------------|\n")
                
                for error in fn_errors:
                    gt_val_str = str(error['gt_value']) if error['gt_value'] is not None else "(none)"
                    pr_val_str = str(error['pr_value']) if error['pr_value'] is not None else "(none)"
                    field_lines.append(
                        f"| {error['hash']} | {error['doi']} | {error['ccdc']} | "
                        f"{error['step_idx']} | {error['step_type']} | "
                        f"`{gt_val_str}` | `{pr_val_str}` |\n"
                    )
                
                field_lines.append("\n")
            
            if fp_errors:
                field_lines.append(f"## False Positives (FP): {len(fp_errors)} occurrences\n\n")
                field_lines.append("These are values present in predictions but missing or incorrect compared to ground truth.\n\n")
                field_lines.append("| Hash | DOI | CCDC | Step | Step Type | GT Value | Pred Value |\n")
                field_lines.append("|------|-----|------|:----:|-----------|----------|------------|\n")
                
                for error in fp_errors:
                    gt_val_str = str(error['gt_value']) if error['gt_value'] is not None else "(none)"
                    pr_val_str = str(error['pr_value']) if error['pr_value'] is not None else "(none)"
                    field_lines.append(
                        f"| {error['hash']} | {error['doi']} | {error['ccdc']} | "
                        f"{error['step_idx']} | {error['step_type']} | "
                        f"`{gt_val_str}` | `{pr_val_str}` |\n"
                    )
                
                field_lines.append("\n")
            
            # Write the field-specific error details file
            safe_field_name = field_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            output_file = error_details_dir / f"{safe_field_name}.md"
            output_file.write_text("".join(field_lines), encoding="utf-8")
            print(f"  Generated error details: {output_file.resolve()}")


def evaluate_previous(use_anchored: bool = True, ignore_vessel: bool = False, short_mode: bool = False, skip_order: bool = False, ignore_mode: bool = False, use_new_gt: bool = False, use_full_gt: bool = False) -> None:
    if use_full_gt:
        GT_ROOT = Path("full_ground_truth/steps")
        OUT_ROOT = Path("evaluation/data/full_result/steps_previous")
    elif use_new_gt:
        GT_ROOT = None  # Will use _find_gt_file_new() instead
        OUT_ROOT = Path("evaluation/data/result/steps_previous")
    else:
        GT_ROOT = Path("earlier_ground_truth/steps")
        OUT_ROOT = Path("evaluation/data/result/steps_previous")
    PREV_ROOT = Path("previous_work_anchored/steps") if use_anchored else Path("previous_work/steps")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    rows_types_overall: List[Tuple[str, Tuple[int, int, int]]] = []
    files_with_missing_ccdc: List[str] = []
    
    # Collect all entity scores across all DOIs for step-type-only overall table
    all_entity_scores: List[Dict[str, Any]] = []
    
    # Track missing GT CCDCs (GT CCDCs not found in predictions)
    missing_gt_ccdcs: List[Dict[str, Any]] = []
    
    # Aggregate field errors across all files for overall analysis
    aggregated_field_errors: Dict[str, Dict[str, int]] = {}
    
    # Collect detailed error information for separate markdown files
    aggregated_detailed_errors: Dict[str, List[Dict[str, Any]]] = {}
    
    # In ignore_mode, also ignore usedVesselName (not just usedVesselType)
    effective_ignore_vessel = ignore_vessel or ignore_mode

    # Map DOI -> hash for reporting
    doi_to_hash: Dict[str, str] = {}
    try:
        doi_to_hash = json.loads(Path("data/doi_to_hash.json").read_text(encoding="utf-8"))
    except Exception:
        doi_to_hash = {}

    for jf in sorted(PREV_ROOT.glob("*.json")):
        doi = jf.stem
        
        # Find GT file based on mode
        if use_new_gt:
            gt_path = _find_gt_file_new(doi)
            if gt_path is None:
                continue
        else:
            gt_path = GT_ROOT / f"{doi}.json"
            if not gt_path.exists():
                continue
        
        try:
            gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
            pred_obj = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        
        # Apply ignore_mode transformations if enabled
        if ignore_mode:
            # Filter out H4PBPTA product
            gt_obj = _filter_out_product(gt_obj, "H4PBPTA")
            pred_obj = _filter_out_product(pred_obj, "H4PBPTA")

            # Convert atmosphere "air" to "n/a" in GT for specific hashes (use doi_to_hash mapping)
            hv = doi_to_hash.get(doi, doi)
            gt_obj = _convert_air_to_na(gt_obj, hv)

        # For previous predictions: convert Dissolve to Add and expand multi-chemical Add steps
        pred_obj = _convert_dissolve_to_add(pred_obj)
        pred_obj = _expand_add_steps_in_obj(pred_obj)

        # Normalize BOTH GT and prediction first, then score
        # Normalize once for scoring and reporting
        gt_normalized = normalize_json_structure(gt_obj)
        pred_normalized = normalize_json_structure(pred_obj)

        tp, fp, fn, pred_missing_ccdc = score_steps_fine_grained(gt_normalized, pred_normalized, effective_ignore_vessel, skip_order)
        # Step-type-only scoring
        ttp, tfp, tfn = _type_counts_for_objs(gt_normalized, pred_normalized)
        if pred_missing_ccdc:
            files_with_missing_ccdc.append(f"steps/{doi}.json")
        
        rows_overall.append((doi, (tp, fp, fn)))
        rows_types_overall.append((doi, (ttp, tfp, tfn)))

        # Compute per-entity (per-synthesis) step-type-only scores
        # Use evaluation.py functions directly
        entity_scores: List[Dict[str, Any]] = []
        matched_gt_ids_entity: Set[int] = set()
        
        for pred_synth in (pred_obj or {}).get("Synthesis", []) or []:
            pred_ccdc = _normalize_ccdc(str((pred_synth or {}).get("productCCDCNumber") or "").strip())
            if pred_ccdc == "":
                pred_ccdc = ""
            
            # Use evaluation.py _gt_step_names()
            pred_types = _gt_step_names(pred_synth)
            
            gt_synth = None
            # Try name-based matching FIRST (prioritize product name over CCDC)
            pred_names = [str(x) for x in (pred_synth or {}).get("productNames", []) or []]
            if pred_names:
                # Normalize ALL prediction names
                pred_names_norm = [_normalize_product_name(n) for n in pred_names if _normalize_product_name(n)]
                
                name_candidates = []
                for candidate in (gt_obj or {}).get("Synthesis", []) or []:
                    if id(candidate) in matched_gt_ids_entity:
                        continue
                    cand_names = [str(x) for x in (candidate or {}).get("productNames", []) or []]
                    cand_names_norm = [_normalize_product_name(n) for n in cand_names if _normalize_product_name(n)]
                    
                    # Check if ANY pred name matches ANY candidate name
                    matched = False
                    for pred_name_norm in pred_names_norm:
                        if pred_name_norm in cand_names_norm:
                            matched = True
                            break
                        # Only allow substring matching if both names are reasonably long (>= 5 chars)
                        # to avoid false matches like "i" matching "nanocapsule ii"
                        for cn in cand_names_norm:
                            if (pred_name_norm and len(pred_name_norm) >= 5 and len(cn) >= 5 and 
                                (pred_name_norm in cn or cn in pred_name_norm)):
                                matched = True
                                break
                        if matched:
                            break
                    
                    if matched:
                        name_candidates.append(candidate)
                
                if len(name_candidates) == 1:
                    gt_synth = name_candidates[0]
                    matched_gt_ids_entity.add(id(gt_synth))
                elif len(name_candidates) > 1:
                    best_match = None
                    best_score = -1
                    for candidate in name_candidates:
                        # Use evaluation.py _gt_step_names() and _compare_steps()
                        cand_types = _gt_step_names(candidate)
                        matches, _, _ = _compare_steps(cand_types, pred_types)
                        if matches > best_score:
                            best_score = matches
                            best_match = candidate
                    if best_match is not None:
                        gt_synth = best_match
                        matched_gt_ids_entity.add(id(best_match))
            
            # Fall back to CCDC matching only if no name match was found
            if gt_synth is None and pred_ccdc:
                ccdc_candidates = []
                for candidate in (gt_obj or {}).get("Synthesis", []) or []:
                    if id(candidate) in matched_gt_ids_entity:
                        continue
                    gt_ccdc = _normalize_ccdc(str((candidate or {}).get("productCCDCNumber") or "").strip())
                    if gt_ccdc != "" and gt_ccdc == pred_ccdc:
                        ccdc_candidates.append(candidate)
                
                if len(ccdc_candidates) == 1:
                    gt_synth = ccdc_candidates[0]
                    matched_gt_ids_entity.add(id(gt_synth))
                elif len(ccdc_candidates) > 1:
                    best_match = None
                    best_score = -1
                    for candidate in ccdc_candidates:
                        # Use evaluation.py _gt_step_names() and _compare_steps()
                        cand_types = _gt_step_names(candidate)
                        matches, _, _ = _compare_steps(cand_types, pred_types)
                        if matches > best_score:
                            best_score = matches
                            best_match = candidate
                    if best_match is not None:
                        gt_synth = best_match
                        matched_gt_ids_entity.add(id(best_match))
            
            if gt_synth:
                # Use evaluation.py _gt_step_names() and _compare_steps()
                gt_types = _gt_step_names(gt_synth)
                e_tp, e_fp, e_fn = _compare_steps(gt_types, pred_types)
            else:
                gt_types = []
                e_tp = 0
                e_fp = len(pred_types)
                e_fn = 0
            
            # Prioritize product names over CCDC for display to show what was actually matched
            entity_label = pred_names[0] if pred_names else (pred_ccdc if pred_ccdc else "<unnamed>")
            entity_score = {
                "doi": doi,
                "entity": entity_label,
                "gt_len": len(gt_types),
                "pred_len": len(pred_types),
                "tp": e_tp,
                "fp": e_fp,
                "fn": e_fn,
            }
            entity_scores.append(entity_score)
            all_entity_scores.append(entity_score)
        
        # Handle unmatched GT syntheses: all their steps are FN
        for gt_synth in (gt_obj or {}).get("Synthesis", []) or []:
            if id(gt_synth) not in matched_gt_ids_entity:
                # Use evaluation.py _gt_step_names()
                gt_types_unmatched = _gt_step_names(gt_synth)
                
                # Get GT CCDC for display
                gt_ccdc_unmatched = _normalize_ccdc(str((gt_synth or {}).get("productCCDCNumber") or "").strip())
                if gt_ccdc_unmatched == "":
                    gt_ccdc_unmatched = ""
                gt_names_unmatched = [str(x) for x in (gt_synth.get("productNames", []) or [])]
                # Prioritize product names over CCDC for display
                entity_label_unmatched = gt_names_unmatched[0] if gt_names_unmatched else (gt_ccdc_unmatched if gt_ccdc_unmatched else "<unnamed>")
                
                entity_score_unmatched = {
                    "doi": doi,
                    "entity": entity_label_unmatched + " (GT only)",
                    "gt_len": len(gt_types_unmatched),
                    "pred_len": 0,
                    "tp": 0,
                    "fp": 0,
                    "fn": len(gt_types_unmatched),
                }
                entity_scores.append(entity_score_unmatched)
                all_entity_scores.append(entity_score_unmatched)
                
                # Record this as a missing GT CCDC/entity
                hv = doi_to_hash.get(doi, "<unknown>")
                missing_gt_ccdcs.append({
                    "doi": doi,
                    "hash": hv,
                    "gt_file": gt_path.as_posix(),
                    "pred_file": jf.as_posix(),
                    "ccdc": gt_ccdc_unmatched if gt_ccdc_unmatched else "N/A",
                    "entity_names": gt_names_unmatched,
                    "num_steps": len(gt_types_unmatched),
                })
        
        # Build GT-to-Pred matching map for detailed reporting
        # When multiple GT syntheses have the same CCDC, match each to best prediction
        gt_synths_flat = (gt_normalized or {}).get("Synthesis", []) or []
        pr_synths_flat = (pred_normalized or {}).get("Synthesis", []) or []
        
        # Create GT-to-Pred matching: for each GT synthesis, find best matching pred
        gt_to_pred_matches: List[Tuple[Dict, Optional[Dict], str]] = []  # (gt_synth, pred_synth, match_key)
        matched_pred_ids: Set[int] = set()
        
        for gt_synth in gt_synths_flat:
            gt_ccdc = _normalize_ccdc(str((gt_synth or {}).get("productCCDCNumber") or "").strip())
            gt_names = [str(x) for x in (gt_synth.get("productNames", []) or [])]
            
            # Normalize CCDC
            if not gt_ccdc:
                if gt_names:
                    match_key = f"NAME:{gt_names[0]}"
                else:
                    match_key = "NAME:<unnamed>"
            else:
                match_key = gt_ccdc
            
            # Get GT step types for matching
            gt_steps_raw = (gt_synth or {}).get("steps", []) or []
            gt_types = [t for (t, _d) in _expand_add_steps(gt_steps_raw)]
            
            # Find best matching pred synthesis
            best_pred = None
            best_score = -1
            
            for pred_synth in pr_synths_flat:
                if id(pred_synth) in matched_pred_ids:
                    continue
                
                pred_ccdc = _normalize_ccdc(str((pred_synth or {}).get("productCCDCNumber") or "").strip())
                pred_names = [str(x) for x in (pred_synth.get("productNames", []) or [])]
                
                # Check if CCDCs match
                ccdc_match = False
                if gt_ccdc and pred_ccdc:
                    ccdc_match = (gt_ccdc == pred_ccdc)
                
                # Check if ANY-to-ANY product names match (normalized)
                name_match = False
                gt_names_norm = [_normalize_product_name(n) for n in gt_names if _normalize_product_name(n)]
                pred_names_norm = [_normalize_product_name(n) for n in pred_names if _normalize_product_name(n)]
                if gt_names_norm and pred_names_norm:
                    for gn in gt_names_norm:
                        if gn in pred_names_norm:
                            name_match = True
                            break
                        for pn in pred_names_norm:
                            if gn and (gn in pn or pn in gn):
                                name_match = True
                                break
                        if name_match:
                            break
                
                # Only consider this pred if CCDC or name matches
                if not (ccdc_match or name_match):
                    continue
                
                # Score the match based on step types
                pred_steps_raw = (pred_synth or {}).get("steps", []) or []
                pred_types = [t for (t, _d) in _expand_add_steps(pred_steps_raw)]
                matches, _, _ = _compare_steps(gt_types, pred_types)
                
                if matches > best_score:
                    best_score = matches
                    best_pred = pred_synth
            
            if best_pred is not None:
                matched_pred_ids.add(id(best_pred))
            
            gt_to_pred_matches.append((gt_synth, best_pred, match_key))
        
        # Add unmatched predictions
        for pred_synth in pr_synths_flat:
            if id(pred_synth) not in matched_pred_ids:
                pred_ccdc = _normalize_ccdc(str((pred_synth or {}).get("productCCDCNumber") or "").strip())
                pred_names = [str(x) for x in (pred_synth.get("productNames", []) or [])]
                
                if not pred_ccdc:
                    if pred_names:
                        match_key = f"NAME:{pred_names[0]}"
                    else:
                        match_key = "NAME:<unnamed>"
                else:
                    match_key = pred_ccdc
                
                gt_to_pred_matches.append((None, pred_synth, match_key))
        
        # Per-DOI report with GT and Pred data
        lines: List[str] = []
        lines.append(f"# Steps Previous Scoring - {doi}\n")
        lines.append("\n")
        # Metadata block
        hv = doi_to_hash.get(doi, "<unknown>")
        lines.append(f"**DOI**: `{doi}`  \n")
        lines.append(f"**Hash**: `{hv}`  \n")
        lines.append(f"**Prediction file**: `{jf.as_posix()}`  \n")
        lines.append(f"**Ground truth file**: `{gt_path.as_posix()}`\n")
        if ignore_mode:
            lines.append(f"**Ignore mode**: Yes (vessel names ignored, H4PBPTA product filtered out)\n")
        elif ignore_vessel:
            lines.append(f"**Ignore vessel**: Yes (vessel names and types excluded from scoring)\n")
        lines.append("\n")
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        lines.append(f"**Fine-grained Scoring:** TP={tp} FP={fp} FN={fn} | P={prec:.3f} R={rec:.3f} F1={f1:.3f}\n")
        lines.append("\n")
        
        # Step-type-only scoring table (overall for this DOI)
        tprec, trec, tf1 = precision_recall_f1(ttp, tfp, tfn)
        lines.append("## Step type-only scoring\n\n")
        lines.append("| TP | FP | FN | Precision | Recall | F1 |\n")
        lines.append("| ---: | ---: | ---: | ---: | ---: | ---: |\n")
        lines.append(f"| {ttp} | {tfp} | {tfn} | {tprec:.3f} | {trec:.3f} | {tf1:.3f} |\n\n")
        
        # Per-entity step-type-only scores
        if entity_scores:
            lines.append("### Per-entity step type-only scores\n\n")
            lines.append("| Entity | GT | Pred | TP | FP | FN | Precision | Recall | F1 |\n")
            lines.append("|--------|---:|-----:|---:|---:|---:|----------:|-------:|----:|\n")
            for e in entity_scores:
                e_prec = (e["tp"] / e["pred_len"]) if e["pred_len"] > 0 else 0.0
                e_rec = (e["tp"] / e["gt_len"]) if e["gt_len"] > 0 else 0.0
                e_f1 = (2 * e_prec * e_rec / (e_prec + e_rec)) if (e_prec + e_rec) > 0 else 0.0
                lines.append(f"| {e['entity']} | {e['gt_len']} | {e['pred_len']} | {e['tp']} | {e['fp']} | {e['fn']} | {e_prec:.3f} | {e_rec:.3f} | {e_f1:.3f} |\n")
            lines.append("\n")
        
        # Show GT and Pred data (normalized)
        lines.append("## Ground Truth\n")
        lines.append("```json\n")
        lines.append(json.dumps(gt_obj, indent=2))
        lines.append("\n```\n\n")
        
        lines.append("## Prediction\n")
        lines.append("```json\n")
        lines.append(json.dumps(pred_obj, indent=2))
        lines.append("\n```\n\n")

        # Differences summary (normalized)
        gt_synths = _get_synths_by_ccdc(gt_normalized)
        pr_synths = _get_synths_by_ccdc(pred_normalized)
        
        # Error analysis by field (using normalized objects for proper matching)
        hv = doi_to_hash.get(doi, doi)
        field_errors, detailed_errors = _analyze_errors_by_field(gt_normalized, pred_normalized, effective_ignore_vessel, skip_order,
                                                                 collect_details=True, hash_value=hv, doi=doi)
        
        # Aggregate field errors for overall report
        for field_name, errors in field_errors.items():
            if field_name not in aggregated_field_errors:
                aggregated_field_errors[field_name] = {'fn': 0, 'fp': 0}
            aggregated_field_errors[field_name]['fn'] += errors['fn']
            aggregated_field_errors[field_name]['fp'] += errors['fp']
        
        # Aggregate detailed errors for separate markdown files
        for field_name, error_list in detailed_errors.items():
            if field_name not in aggregated_detailed_errors:
                aggregated_detailed_errors[field_name] = []
            aggregated_detailed_errors[field_name].extend(error_list)
        
        if field_errors:
            lines.append("## Error Analysis by Field\n\n")
            lines.append("This table shows which fields contribute most to errors **in successfully matched steps** (where step types match). Type mismatches and missing/extra steps are excluded from this analysis.\n\n")
            lines.append("| Field | FN | FP | Total Errors | % of Total FN | % of Total FP | Hypothetical P | Hypothetical R | Hypothetical F1 |\n")
            lines.append("|-------|---:|---:|-------------:|--------------:|--------------:|---------------:|---------------:|----------------:|\n")
            
            # Calculate totals for percentage
            total_fn_errors = sum(e['fn'] for e in field_errors.values())
            total_fp_errors = sum(e['fp'] for e in field_errors.values())
            
            # Sort by total errors (FN + FP) descending
            sorted_fields = sorted(field_errors.items(), key=lambda x: x[1]['fn'] + x[1]['fp'], reverse=True)
            
            for field_name, errors in sorted_fields:
                field_fn = errors['fn']
                field_fp = errors['fp']
                total_field_errors = field_fn + field_fp
                
                # Calculate percentages
                pct_fn = (field_fn / total_fn_errors * 100) if total_fn_errors > 0 else 0
                pct_fp = (field_fp / total_fp_errors * 100) if total_fp_errors > 0 else 0
                
                # Calculate hypothetical scores if this field were perfect
                hyp_tp = tp + field_fn  # Recovered FNs become TPs
                hyp_fp = max(0, fp - field_fp)  # Removed FPs
                hyp_fn = max(0, fn - field_fn)  # Removed FNs
                hyp_prec, hyp_rec, hyp_f1 = precision_recall_f1(hyp_tp, hyp_fp, hyp_fn)
                
                lines.append(f"| {field_name} | {field_fn} | {field_fp} | {total_field_errors} | {pct_fn:.1f}% | {pct_fp:.1f}% | {hyp_prec:.3f} | {hyp_rec:.3f} | {hyp_f1:.3f} |\n")
            
            lines.append("\n")
            lines.append(f"**Note**: Hypothetical scores assume fixing only that specific field while keeping all other errors.\n\n")
        
        gt_keys = set(gt_synths.keys())
        pr_keys = set(pr_synths.keys())
        fn_keys = sorted(gt_keys - pr_keys)
        fp_keys = sorted(pr_keys - gt_keys)
        gt_names = _collect_step_names_union([s for lst in gt_synths.values() for s in lst])
        pr_names = _collect_step_names_union([s for lst in pr_synths.values() for s in lst])
        fn_names = sorted(gt_names - pr_names)
        fp_names = sorted(pr_names - gt_names)
        lines.append("## Differences\n\n")
        lines.append(f"Keys (Prediction vs GT): {', '.join(sorted(pr_keys))} - {', '.join(sorted(gt_keys))}\n")
        lines.append(f"FN (missing keys): {', '.join(fn_keys) if fn_keys else 'None'}\n")
        lines.append(f"FP (extra keys): {', '.join(fp_keys) if fp_keys else 'None'}\n")
        lines.append(f"FN (missing chemical names): {', '.join(fn_names) if fn_names else 'None'}\n")
        lines.append(f"FP (extra chemical names): {', '.join(fp_names) if fp_names else 'None'}\n\n")

        # Field-level differences per CCDC and step
        field_diffs = _collect_step_field_differences(gt_synths, pr_synths, effective_ignore_vessel, short_mode, skip_order)
        if field_diffs:
            lines.append("### Field-level differences\n\n")
            for d in field_diffs:
                lines.append(d + "\n")
            lines.append("\n")

        # Side-by-side step types listing (GT vs Pred) per matched synthesis
        # When multiple GT syntheses have the same CCDC, show separate tables
        lines.append("## Step types (GT vs Pred)\n\n")
        lines.append("Each table shows a GT synthesis matched to its best prediction. When multiple GT syntheses have the same CCDC, they are shown separately.\n\n")
        
        # Group matches by CCDC to add indices when needed
        ccdc_to_matches: Dict[str, List[Tuple[Dict, Optional[Dict], str]]] = {}
        for gt_synth, pred_synth, match_key in gt_to_pred_matches:
            if match_key not in ccdc_to_matches:
                ccdc_to_matches[match_key] = []
            ccdc_to_matches[match_key].append((gt_synth, pred_synth, match_key))
        
        for match_key in sorted(ccdc_to_matches.keys()):
            matches_for_key = ccdc_to_matches[match_key]
            
            for idx, (gt_synth, pred_synth, _) in enumerate(matches_for_key, 1):
                # Add index if multiple entries for same CCDC
                if len(matches_for_key) > 1:
                    header = f"### [{match_key}] (Entry {idx}/{len(matches_for_key)})"
                else:
                    header = f"### [{match_key}]"
                
                lines.append(f"{header}\n")
                
                # Add GT synthesis name if available
                if gt_synth:
                    gt_names = [str(x) for x in (gt_synth.get("productNames", []) or [])]
                    if gt_names:
                        lines.append(f"**GT**: {', '.join(gt_names)}  \n")
                
                # Add Pred synthesis name if available
                if pred_synth:
                    pred_names = [str(x) for x in (pred_synth.get("productNames", []) or [])]
                    if pred_names:
                        lines.append(f"**Pred**: {', '.join(pred_names)}  \n")
                
                if not gt_synth and not pred_synth:
                    lines.append("(no data)\n\n")
                    continue
                
                # Get step types
                gt_types: List[str] = []
                if gt_synth:
                    raw_steps = (gt_synth or {}).get("steps", []) or []
                    gt_types = [t for (t, _d) in _expand_add_steps(raw_steps)]
                
                pr_types: List[str] = []
                if pred_synth:
                    raw_steps = (pred_synth or {}).get("steps", []) or []
                    pr_types = [t for (t, _d) in _expand_add_steps(raw_steps)]
                
                max_len = max(len(gt_types), len(pr_types))
                if max_len == 0:
                    lines.append("(no steps)\n\n")
                    continue
                
                for i in range(max_len):
                    gt_t = gt_types[i] if i < len(gt_types) else "-"
                    pr_t = pr_types[i] if i < len(pr_types) else "-"
                    lines.append(f"{i+1}. {gt_t} — {pr_t}\n")
                lines.append("\n")

        # Append entity text files if available (use hash from doi_to_hash)
        if hv != "<unknown>":
            entity_texts = _get_entity_text_files(hv, pred_obj)
            if entity_texts:
                lines.append("## Entity Text Files\n\n")
                lines.append("The following are the extracted entity text files used in the extraction process:\n\n")
                for entity_name in sorted(entity_texts.keys()):
                    lines.append(f"### {entity_name}\n\n")
                    lines.append("```\n")
                    lines.append(entity_texts[entity_name])
                    if not entity_texts[entity_name].endswith("\n"):
                        lines.append("\n")
                    lines.append("```\n\n")

        (OUT_ROOT / f"{doi}.md").write_text("".join(lines), encoding="utf-8")

    # Overall report
    lines_overall: List[str] = []
    lines_overall.append("# Steps Previous Scoring - Overall\n\n")
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
    
    # Step-type-only overall table - per-entity scores
    lines_overall.append("\n## Step type-only — Overall (Per-Entity)\n\n")
    lines_overall.append("| DOI | Entity | GT | Pred | TP | FP | FN | Precision | Recall | F1 |\n")
    lines_overall.append("|-----|--------|---:|-----:|---:|---:|---:|----------:|-------:|----:|\n")
    
    # Calculate overall aggregates from per-entity scores
    t_total_tp = 0
    t_total_gt = 0
    t_total_pred = 0
    for entity_score in all_entity_scores:
        e_prec = (entity_score["tp"] / entity_score["pred_len"]) if entity_score["pred_len"] > 0 else 0.0
        e_rec = (entity_score["tp"] / entity_score["gt_len"]) if entity_score["gt_len"] > 0 else 0.0
        e_f1 = (2 * e_prec * e_rec / (e_prec + e_rec)) if (e_prec + e_rec) > 0 else 0.0
        
        lines_overall.append(
            f"| {entity_score['doi']} | {entity_score['entity']} | "
            f"{entity_score['gt_len']} | {entity_score['pred_len']} | "
            f"{entity_score['tp']} | {entity_score['fp']} | {entity_score['fn']} | "
            f"{e_prec:.3f} | {e_rec:.3f} | {e_f1:.3f} |\n"
        )
        
        t_total_tp += entity_score["tp"]
        t_total_gt += entity_score["gt_len"]
        t_total_pred += entity_score["pred_len"]
    
    # Overall aggregate row - calculate P/R/F1 from totals
    t_overall_prec = (t_total_tp / t_total_pred) if t_total_pred > 0 else 0.0
    t_overall_rec = (t_total_tp / t_total_gt) if t_total_gt > 0 else 0.0
    t_overall_f1 = (2 * t_overall_prec * t_overall_rec / (t_overall_prec + t_overall_rec)) if (t_overall_prec + t_overall_rec) > 0 else 0.0
    lines_overall.append(
        f"| **Overall** | **All** | **{t_total_gt}** | **{t_total_pred}** | "
        f"**{t_total_tp}** | **{t_total_pred - t_total_tp}** | **{t_total_gt - t_total_tp}** | "
        f"**{t_overall_prec:.3f}** | **{t_overall_rec:.3f}** | **{t_overall_f1:.3f}** |\n"
    )

    # Report files with missing/N/A CCDC
    if files_with_missing_ccdc:
        lines_overall.append("\n## Files with Missing or N/A CCDC Numbers\n\n")
        for fpath in sorted(set(files_with_missing_ccdc)):
            lines_overall.append(f"- `{fpath}`\n")
    
    # Top 5 error sources across all files
    if aggregated_field_errors:
        lines_overall.append("\n## All Error Sources (Aggregated)\n\n")
        lines_overall.append("This table shows all fields contributing to errors **in successfully matched steps** (where step types match). Type mismatches and missing/extra steps are excluded from this analysis.\n\n")
        lines_overall.append("**Detailed error breakdown**: See separate markdown files in `error_details/` directory. Click field names below to navigate.\n\n")
        lines_overall.append("| Rank | Field | FN | FP | Total Errors | % of Total FN | % of Total FP | Hypothetical P | Hypothetical R | Hypothetical F1 |\n")
        lines_overall.append("|-----:|-------|---:|---:|-------------:|--------------:|--------------:|---------------:|---------------:|----------------:|\n")
        
        # Calculate totals for percentage
        total_fn_all = sum(e['fn'] for e in aggregated_field_errors.values())
        total_fp_all = sum(e['fp'] for e in aggregated_field_errors.values())
        
        # Sort by total errors (FN + FP) descending - show all fields
        sorted_fields_all_complete = sorted(aggregated_field_errors.items(), key=lambda x: x[1]['fn'] + x[1]['fp'], reverse=True)
        # Show all error types (no limit)
        sorted_fields_all = sorted_fields_all_complete
        
        for rank, (field_name, errors) in enumerate(sorted_fields_all, 1):
            field_fn = errors['fn']
            field_fp = errors['fp']
            total_field_errors = field_fn + field_fp
            
            # Calculate percentages
            pct_fn = (field_fn / total_fn_all * 100) if total_fn_all > 0 else 0
            pct_fp = (field_fp / total_fp_all * 100) if total_fp_all > 0 else 0
            
            # Calculate hypothetical scores if this field were perfect
            hyp_tp = total_tp + field_fn  # Recovered FNs become TPs
            hyp_fp = max(0, total_fp - field_fp)  # Removed FPs
            hyp_fn = max(0, total_fn - field_fn)  # Removed FNs
            hyp_prec, hyp_rec, hyp_f1 = precision_recall_f1(hyp_tp, hyp_fp, hyp_fn)
            
            # Create link to detailed error file
            safe_field_name = field_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            field_link = f"[{field_name}](error_details/{safe_field_name}.md)"
            
            lines_overall.append(f"| {rank} | {field_link} | {field_fn} | {field_fp} | {total_field_errors} | {pct_fn:.1f}% | {pct_fp:.1f}% | {hyp_prec:.3f} | {hyp_rec:.3f} | {hyp_f1:.3f} |\n")
        
        lines_overall.append("\n")
        lines_overall.append(f"**Current Overall**: P={overall_prec:.3f} R={overall_rec:.3f} F1={overall_f1:.3f}  \n")
        lines_overall.append(f"**Note**: Hypothetical scores show potential improvements if only that specific field were fixed across all files.\n\n")
        
        # Cumulative hypothetical improvements table
        lines_overall.append("### Cumulative Hypothetical Improvements\n\n")
        lines_overall.append("This table shows the potential improvements if we fix the top N error sources cumulatively.\n\n")
        lines_overall.append("| Top N Fixed | Cumulative FN Recovered | Cumulative FP Removed | Hypothetical P | Hypothetical R | Hypothetical F1 | F1 Gain |\n")
        lines_overall.append("|------------:|------------------------:|----------------------:|---------------:|---------------:|----------------:|--------:|\n")
        
        # Calculate cumulative improvements for all error sources
        for n in range(1, len(sorted_fields_all_complete) + 1):
            cumulative_fn = sum(sorted_fields_all_complete[i][1]['fn'] for i in range(n))
            cumulative_fp = sum(sorted_fields_all_complete[i][1]['fp'] for i in range(n))
            
            cum_hyp_tp = total_tp + cumulative_fn
            cum_hyp_fp = max(0, total_fp - cumulative_fp)
            cum_hyp_fn = max(0, total_fn - cumulative_fn)
            cum_hyp_prec, cum_hyp_rec, cum_hyp_f1 = precision_recall_f1(cum_hyp_tp, cum_hyp_fp, cum_hyp_fn)
            
            f1_gain = cum_hyp_f1 - overall_f1
            
            lines_overall.append(f"| Top {n} | {cumulative_fn} | {cumulative_fp} | {cum_hyp_prec:.3f} | {cum_hyp_rec:.3f} | {cum_hyp_f1:.3f} | +{f1_gain:.3f} |\n")
        
        lines_overall.append("\n")
    
    (OUT_ROOT / "_overall.md").write_text("".join(lines_overall), encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())
    
    # Generate separate report for missing GT CCDCs
    if missing_gt_ccdcs:
        lines_missing: List[str] = []
        lines_missing.append("# Missing GT Entities in Predictions (Previous Work)\n\n")
        lines_missing.append("This report lists synthesis entities that appear in ground truth but were not found in previous work predictions.\n\n")
        
        # Overall summary table
        lines_missing.append("## Summary\n\n")
        lines_missing.append(f"**Total missing entities**: {len(missing_gt_ccdcs)}\n")
        lines_missing.append(f"**Total missing steps**: {sum(e['num_steps'] for e in missing_gt_ccdcs)}\n\n")
        
        # Overall table
        lines_missing.append("## Overall Table\n\n")
        lines_missing.append("| Hash | DOI | CCDC | Entity Names | GT Steps | GT File | Pred File |\n")
        lines_missing.append("|------|-----|------|--------------|:--------:|---------|-----------|\n")
        
        for entry in missing_gt_ccdcs:
            entity_names_str = "; ".join(entry["entity_names"]) if entry["entity_names"] else "(none)"
            lines_missing.append(
                f"| {entry['hash']} | {entry['doi']} | {entry['ccdc']} | {entity_names_str} | "
                f"{entry['num_steps']} | `{entry['gt_file']}` | `{entry['pred_file']}` |\n"
            )
        
        lines_missing.append("\n")
        
        # Grouped by DOI
        lines_missing.append("## Details by DOI\n\n")
        
        # Group entries by DOI
        by_doi: Dict[str, List[Dict[str, Any]]] = {}
        for entry in missing_gt_ccdcs:
            if entry['doi'] not in by_doi:
                by_doi[entry['doi']] = []
            by_doi[entry['doi']].append(entry)
        
        for doi_val in sorted(by_doi.keys()):
            entries = by_doi[doi_val]
            hash_val = entries[0]['hash']
            lines_missing.append(f"### {doi_val} ({hash_val})\n\n")
            lines_missing.append(f"**GT File**: `{entries[0]['gt_file']}`  \n")
            lines_missing.append(f"**Pred File**: `{entries[0]['pred_file']}`  \n")
            lines_missing.append(f"**Missing entities**: {len(entries)}\n\n")
            
            for i, entry in enumerate(entries, 1):
                lines_missing.append(f"#### Entity {i}\n\n")
                lines_missing.append(f"- **CCDC**: {entry['ccdc']}\n")
                if entry['entity_names']:
                    lines_missing.append(f"- **Names**: {', '.join(entry['entity_names'])}\n")
                else:
                    lines_missing.append(f"- **Names**: (none)\n")
                lines_missing.append(f"- **GT Steps**: {entry['num_steps']}\n")
                lines_missing.append("\n")
        
        (OUT_ROOT / "_missing_gt_entities.md").write_text("".join(lines_missing), encoding="utf-8")
        print((OUT_ROOT / "_missing_gt_entities.md").resolve())
    
    # Generate separate markdown files for all error fields
    if aggregated_detailed_errors and aggregated_field_errors:
        # Sort fields by total errors
        sorted_fields_for_details = sorted(aggregated_field_errors.items(), 
                                          key=lambda x: x[1]['fn'] + x[1]['fp'], 
                                          reverse=True)
        
        error_details_dir = OUT_ROOT / "error_details"
        error_details_dir.mkdir(parents=True, exist_ok=True)
        
        for field_name, _field_counts in sorted_fields_for_details:
            if field_name not in aggregated_detailed_errors:
                continue
            
            details = aggregated_detailed_errors[field_name]
            
            # Create markdown file for this field
            field_lines: List[str] = []
            field_lines.append(f"# Error Details: {field_name}\n\n")
            field_lines.append(f"**Total errors**: {len(details)} occurrences\n")
            field_lines.append(f"**Total FN**: {_field_counts['fn']}\n")
            field_lines.append(f"**Total FP**: {_field_counts['fp']}\n\n")
            
            # Group errors by error type (FN/FP)
            fn_errors = [e for e in details if e['error_type'] == 'fn']
            fp_errors = [e for e in details if e['error_type'] == 'fp']
            
            if fn_errors:
                field_lines.append(f"## False Negatives (FN): {len(fn_errors)} occurrences\n\n")
                field_lines.append("These are values present in ground truth but missing or incorrect in predictions.\n\n")
                field_lines.append("| Hash | DOI | CCDC | Step | Step Type | GT Value | Pred Value |\n")
                field_lines.append("|------|-----|------|:----:|-----------|----------|------------|\n")
                
                for error in fn_errors:
                    gt_val_str = str(error['gt_value']) if error['gt_value'] is not None else "(none)"
                    pr_val_str = str(error['pr_value']) if error['pr_value'] is not None else "(none)"
                    field_lines.append(
                        f"| {error['hash']} | {error['doi']} | {error['ccdc']} | "
                        f"{error['step_idx']} | {error['step_type']} | "
                        f"`{gt_val_str}` | `{pr_val_str}` |\n"
                    )
                
                field_lines.append("\n")
            
            if fp_errors:
                field_lines.append(f"## False Positives (FP): {len(fp_errors)} occurrences\n\n")
                field_lines.append("These are values present in predictions but missing or incorrect compared to ground truth.\n\n")
                field_lines.append("| Hash | DOI | CCDC | Step | Step Type | GT Value | Pred Value |\n")
                field_lines.append("|------|-----|------|:----:|-----------|----------|------------|\n")
                
                for error in fp_errors:
                    gt_val_str = str(error['gt_value']) if error['gt_value'] is not None else "(none)"
                    pr_val_str = str(error['pr_value']) if error['pr_value'] is not None else "(none)"
                    field_lines.append(
                        f"| {error['hash']} | {error['doi']} | {error['ccdc']} | "
                        f"{error['step_idx']} | {error['step_type']} | "
                        f"`{gt_val_str}` | `{pr_val_str}` |\n"
                    )
                
                field_lines.append("\n")
            
            # Write the field-specific error details file
            safe_field_name = field_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            output_file = error_details_dir / f"{safe_field_name}.md"
            output_file.write_text("".join(field_lines), encoding="utf-8")
            print(f"  Generated error details: {output_file.resolve()}")


def _expand_add_steps_in_obj(obj: Any) -> Any:
    """Expand Add steps that contain multiple addedChemical entries into separate Add steps.

    Returns a new object with Add steps expanded.
    """
    if not obj or not isinstance(obj, dict):
        return obj

    result = {"Synthesis": []}

    for synth in (obj.get("Synthesis") or []):
        if not synth or not isinstance(synth, dict):
            continue

        new_synth = {k: v for k, v in synth.items() if k != "steps"}
        new_steps = []

        for step in (synth.get("steps") or []):
            if not isinstance(step, dict):
                continue

            # Check if this is an Add step with multiple chemicals
            if "Add" in step:
                add_data = step["Add"]
                added_chemicals = add_data.get("addedChemical", [])

                if isinstance(added_chemicals, list) and len(added_chemicals) > 1:
                    # Split into multiple Add steps, one per chemical
                    for i, chem in enumerate(added_chemicals):
                        new_add_data = dict(add_data)
                        new_add_data["addedChemical"] = [chem]
                        # Update comment to indicate this is part of a split
                        original_comment = new_add_data.get("comment", "")
                        new_add_data["comment"] = f"split {i+1}/{len(added_chemicals)}: {original_comment}"
                        new_steps.append({"Add": new_add_data})
                else:
                    # Single chemical or no chemicals - keep as is
                    new_steps.append(step)
            else:
                # Not an Add step - keep as is
                new_steps.append(step)

        new_synth["steps"] = new_steps
        result["Synthesis"].append(new_synth)

    return result


def _convert_dissolve_to_add(obj: Any) -> Any:
    """Convert all Dissolve steps to Add steps.

    Returns a new object with Dissolve steps converted to Add.
    Note: Multiple chemicals in the resulting Add steps will be handled by _expand_add_steps_in_obj.
    """
    if not obj or not isinstance(obj, dict):
        return obj

    result = {"Synthesis": []}

    for synth in (obj.get("Synthesis") or []):
        if not synth or not isinstance(synth, dict):
            continue

        new_synth = {k: v for k, v in synth.items() if k != "steps"}
        new_steps = []

        for step in (synth.get("steps") or []):
            if not isinstance(step, dict):
                continue

            # Check if this is a Dissolve step
            if "Dissolve" in step:
                dissolve_data = step["Dissolve"]

                # Get the solvent list (chemicals being dissolved)
                solvents = dissolve_data.get("solvent", [])
                if not isinstance(solvents, list):
                    solvents = [solvents] if solvents else []

                # Convert to Add step - the expansion will happen later
                add_step = {
                    "Add": {
                        "usedVesselName": dissolve_data.get("usedVesselName", "n/a"),
                        "usedVesselType": dissolve_data.get("usedVesselType", "n/a"),
                        "stepNumber": dissolve_data.get("stepNumber", -1),
                        "atmosphere": dissolve_data.get("atmosphere", "n/a"),
                        "targetPH": dissolve_data.get("targetPH", -1),
                        "comment": f"converted from dissolve: {dissolve_data.get('comment', 'n/a')}",
                        "duration": dissolve_data.get("duration", "n/a"),
                        "addedChemical": solvents,
                        "stir": dissolve_data.get("stir", False),
                        "isLayered": False
                    }
                }
                new_steps.append(add_step)
            else:
                # Not a Dissolve step, keep as is
                new_steps.append(step)

        new_synth["steps"] = new_steps
        result["Synthesis"].append(new_synth)

    return result


def _filter_out_product(obj: Any, product_name: str) -> Any:
    """Filter out syntheses containing the specified product name (case-insensitive).
    
    Args:
        obj: The object containing Synthesis array
        product_name: The product name to filter out (e.g., "H4PBPTA")
    
    Returns a new object with matching syntheses removed.
    """
    if not obj or not isinstance(obj, dict):
        return obj
    
    result = {"Synthesis": []}
    product_name_lower = product_name.lower()
    
    for synth in (obj.get("Synthesis") or []):
        if not synth or not isinstance(synth, dict):
            continue
        
        # Check if this synthesis contains the product name
        product_names = [str(x).lower() for x in (synth.get("productNames", []) or [])]
        
        # Skip this synthesis if product_name appears in any of its names
        if any(product_name_lower in name for name in product_names):
            continue
        
        # Keep this synthesis
        result["Synthesis"].append(synth)
    
    return result


def _convert_air_to_na(obj: Any, hash_value: str) -> Any:
    """Convert atmosphere 'air' to 'n/a' for specific hashes.
    
    Args:
        obj: The object containing Synthesis array
        hash_value: The hash value to check
    
    Returns a new object with atmosphere values converted.
    """
    if not obj or not isinstance(obj, dict):
        return obj
    
    # Only apply for specific hashes (currently none)
    if hash_value not in []:
        return obj
    
    result = {"Synthesis": []}
    
    for synth in (obj.get("Synthesis") or []):
        if not synth or not isinstance(synth, dict):
            continue
        
        new_synth = {k: v for k, v in synth.items() if k != "steps"}
        new_steps = []
        
        for step in (synth.get("steps") or []):
            if not isinstance(step, dict):
                continue
            
            # Create a new step dict with converted atmosphere
            new_step = {}
            for step_type, step_data in step.items():
                if isinstance(step_data, dict):
                    new_step_data = dict(step_data)
                    # Convert atmosphere "air" to "n/a"
                    if "atmosphere" in new_step_data:
                        atm = new_step_data["atmosphere"]
                        if isinstance(atm, str) and atm.lower() == "air":
                            new_step_data["atmosphere"] = "n/a"
                    new_step[step_type] = new_step_data
                else:
                    new_step[step_type] = step_data
            
            new_steps.append(new_step)
        
        new_synth["steps"] = new_steps
        result["Synthesis"].append(new_synth)
    
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Steps scoring evaluator")
    parser.add_argument("--previous", action="store_true", help="Evaluate previous_work_anchored/steps/*.json (default) against ground truth using fine-grained CCDC-anchored scoring")
    parser.add_argument("--no-anchor", action="store_true", help="Use previous_work/ instead of previous_work_anchored/ (only with --previous)")
    parser.add_argument("--no-vessel", action="store_true", help="Ignore vessel-related fields (usedVesselName, usedVesselType) in scoring")
    parser.add_argument("--short", action="store_true", help="Skip detailed field-level differences for steps with mismatched types")
    parser.add_argument("--skip-order", action="store_true", help="Use best-match by step type instead of strict positional matching (applies to both default and --previous modes)")
    parser.add_argument("--ignore", action="store_true", help="Ignore usedVesselName in scoring, filter out H4PBPTA product from comparison")
    parser.add_argument("--new", action="store_true", help="Use newer ground truth from newer_ground_truth_gao/prepared/steps, newer_ground_truth_lu/steps, and newer_ground_truth_sun/prepared/steps folders")
    parser.add_argument("--full", action="store_true", help="Use full_ground_truth/steps containing all ground truth files, output to evaluation/data/full_result/ (mutually exclusive with --new)")
    args = parser.parse_args()
    
    # Check for mutually exclusive arguments
    if args.new and args.full:
        parser.error("--new and --full are mutually exclusive")

    if args.previous:
        evaluate_previous(use_anchored=not args.no_anchor, ignore_vessel=args.no_vessel, short_mode=args.short, skip_order=args.skip_order, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full)
    else:
        evaluate_current(ignore_vessel=args.no_vessel, short_mode=args.short, skip_order=args.skip_order, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full)


if __name__ == "__main__":
    main()


