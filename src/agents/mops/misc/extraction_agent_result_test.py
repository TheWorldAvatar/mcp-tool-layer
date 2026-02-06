#!/usr/bin/env python3
import os
import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from collections import Counter

DATA_DIR = Path("data")
CHEM_GT_DIR = Path("earlier_ground_truth/chemicals1")
DOI_HASH_MAP_PATH = DATA_DIR / "doi_to_hash.json"

# Optional fallback/override mapping from product code to CCDC
NAME_TO_CCDC: Dict[str, str] = {
    "IRMOP-50": "273613",
    "IRMOP-51": "273616",
    "IRMOP-52": "273620",
    "IRMOP-53": "273621",
    "MOP-54": "273623",
}


def _load_doi_to_hash() -> Dict[str, str]:
    if not DOI_HASH_MAP_PATH.exists():
        return {}
    with open(DOI_HASH_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _invert_hash_to_doi(doi_to_hash: Dict[str, str]) -> Dict[str, str]:
    return {h: d for d, h in doi_to_hash.items()}


def _resolve_hash_and_doi(arg_file: Optional[str], doi_to_hash: Dict[str, str]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    hash_to_doi = _invert_hash_to_doi(doi_to_hash)

    if not arg_file:
        for entry in DATA_DIR.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith('.') or entry.name in ["log", "ontologies", "third_party_repos", "__pycache__"]:
                continue
            h = entry.name
            doi = hash_to_doi.get(h, None)
            if doi:
                pairs.append((h, doi))
        return pairs

    maybe_hash_dir = DATA_DIR / arg_file
    if maybe_hash_dir.exists() and maybe_hash_dir.is_dir():
        doi = hash_to_doi.get(arg_file, None)
        if doi:
            pairs.append((arg_file, doi))
        else:
            print(f"Warning: DOI not found for hash {arg_file}; skipping")
        return pairs

    h = doi_to_hash.get(arg_file, None)
    if h:
        pairs.append((h, arg_file))
        return pairs

    print(f"Target '{arg_file}' not found as hash or DOI; nothing to do.")
    return pairs

# ---------------------- text parsing helpers ----------------------

def _first_json_block(txt: str) -> str:
    lines = txt.splitlines()
    in_block = False
    buf: List[str] = []
    fence_started = False
    for ln in lines:
        if not in_block:
            if ln.strip().startswith("```"):
                tag = ln.strip().lstrip("`").lower()
                if "json" in tag:
                    in_block = True
                    fence_started = True
                    continue
        else:
            if ln.strip().startswith("```"):
                break
            buf.append(ln)
    if fence_started:
        return "\n".join(buf)
    return txt


def _extract_json_objects_from_text(txt: str) -> List[dict]:
    s = _first_json_block(txt)

    objs: List[dict] = []
    brace = 0
    start_idx = None
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        else:
            if ch == '"':
                in_str = True
                continue
            if ch == '{':
                if brace == 0:
                    start_idx = i
                brace += 1
                continue
            if ch == '}':
                if brace > 0:
                    brace -= 1
                    if brace == 0 and start_idx is not None:
                        chunk = s[start_idx:i + 1]
                        try:
                            obj = json.loads(chunk)
                            if isinstance(obj, dict):
                                objs.append(obj)
                        except Exception:
                            pass
                        start_idx = None
                continue
    return objs


def _is_output_object(o: dict) -> bool:
    sec = str(o.get("section") or o.get("Section") or "").lower()
    if "output" in sec:
        return True
    role = str(o.get("role") or "").lower()
    if role in ("product", "output", "chemical_output"):
        return True
    if bool(o.get("is_output")) or bool(o.get("output")):
        return True
    return False


def _chemical_output_block(txt: str) -> Optional[str]:
    m_out = re.search(r"(?im)^\s*-?\s*ChemicalOutput\s*:\s*$", txt)
    if not m_out:
        return None
    start = m_out.end()
    m_end = re.search(r"(?im)^\s*-?\s*Chemical(Inputs|Output)\s*:\s*$|^\s*```", txt[start:])
    end = start + m_end.start() if m_end else len(txt)
    return txt[start:end]

# ---------------------- counting helpers ----------------------

def _count_inputs_markdown_all_inputs(txt: str) -> int:
    lines = txt.splitlines()
    in_inputs = False
    count = 0
    for ln in lines:
        s = ln.strip()
        if re.match(r"^-?\s*ChemicalInputs\s*:\s*$", s, flags=re.IGNORECASE):
            in_inputs = True
            continue
        if re.match(r"^-?\s*ChemicalOutput\s*:\s*$", s, flags=re.IGNORECASE):
            in_inputs = False
            continue
        if in_inputs and re.match(r"^-\s*Name\s*:\s*", s, flags=re.IGNORECASE):
            count += 1
    return count


def _count_inputs_in_hint_file(path: Path) -> int:
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading {path}: {e}")
        return 0

    objs = _extract_json_objects_from_text(txt)
    if objs:
        count = 0
        for o in objs:
            if "name" in o and not _is_output_object(o):
                count += 1
        if count > 0:
            return count

    md_inputs_count = _count_inputs_markdown_all_inputs(txt)
    if md_inputs_count > 0:
        return md_inputs_count

    count_dash = len(re.findall(r"(?m)^\s*-\s*Name\s*:\s*", txt))
    if count_dash > 0:
        return count_dash

    count_jsonl = len(re.findall(r'(?m)^\s*"name"\s*:\s*', txt))
    return count_jsonl

# ---------------------- CCDC extraction ----------------------

def _extract_ccdc_from_text(txt: str) -> Optional[str]:
    for obj in _extract_json_objects_from_text(txt):
        if _is_output_object(obj):
            for key in ("ccdc", "CCDC", "ccdcNumber", "CCDCNumber", "representation", "Representation"):
                val = obj.get(key)
                if not val:
                    continue
                m = re.search(r"([0-9]{6,})", str(val))
                if m:
                    return m.group(1)
    out_block = _chemical_output_block(txt)
    if out_block:
        m = re.search(r"(?im)^\s*-\s*Representation\s*:\s*(?:CCDC\s*)?([0-9]{6,})\b", out_block)
        if m:
            return m.group(1)
        m = re.search(r"CCDC\s*([0-9]{6,})", out_block, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    m = re.search(r"CCDC\s*([0-9]{6,})", txt, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"(?im)^\s*-\s*Representation\s*:\s*(?:CCDC\s*)?([0-9]{6,})\b", txt)
    return m.group(1) if m else None


def _infer_ccdc_from_name_sources(path: Path, txt: str) -> Optional[str]:
    name = path.stem.replace("iter2_hints_", "").strip()
    code = name.replace("_", "-")
    if code in NAME_TO_CCDC:
        return NAME_TO_CCDC[code]
    out_block = _chemical_output_block(txt) or ""
    m = re.search(r"(?im)^\s*-\s*Name\s*:\s*(.+?)\s*$", out_block)
    if m:
        nm = m.group(1).strip()
        if nm in NAME_TO_CCDC:
            return NAME_TO_CCDC[nm]
        nm2 = nm.replace(" ", "").upper()
        for k in NAME_TO_CCDC:
            if k.replace(" ", "").upper() == nm2:
                return NAME_TO_CCDC[k]
    return None

# ---------------------- identifier extraction ----------------------

def _norm_ident(s: str) -> str:
    if s is None:
        return ""
    x = str(s).strip()
    # Strip common parenthetical qualifiers first
    x = re.sub(r"\(\s*as\s+stated\s*\)", "", x, flags=re.IGNORECASE)
    x = re.sub(r"\(\s*looked\s+up:.*?\)", "", x, flags=re.IGNORECASE)
    # Normalize unicode dashes/quotes
    x = x.replace("–", "-").replace("—", "-")
    x = x.replace("’", "'").replace("“", '"').replace("”", '"')
    # Remove surrounding quotes
    if (x.startswith('"') and x.endswith('"')) or (x.startswith("'") and x.endswith("'")):
        x = x[1:-1]
    # Collapse spaces and lowercase
    x = re.sub(r"\s+", " ", x).strip()
    return x.lower()


def _extract_pred_identifiers_from_text(txt: str) -> Set[str]:
    idents: Set[str] = set()
    for obj in _extract_json_objects_from_text(txt):
        if _is_output_object(obj):
            for key in ("name", "names", "chemicalName", "chemicalNames"):
                vals = obj.get(key)
                if isinstance(vals, list):
                    for v in vals:
                        n = _norm_ident(v)
                        if n:
                            idents.add(n)
                else:
                    n = _norm_ident(vals)
                    if n:
                        idents.add(n)
            for key in ("formula", "chemicalFormula"):
                n = _norm_ident(obj.get(key))
                if n:
                    idents.add(n)
    block = _chemical_output_block(txt) or ""
    m = re.search(r"(?im)^\s*-\s*Name\s*:\s*(.+?)\s*$", block)
    if m:
        n = _norm_ident(m.group(1))
        if n:
            idents.add(n)
    m = re.search(r"(?im)^\s*-\s*Formula\s*:\s*(.+?)\s*$", block)
    if m:
        n = _norm_ident(m.group(1))
        if n:
            idents.add(n)
    return idents


def _extract_gt_identifiers(data: Dict) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for proc in data.get("synthesisProcedures", []) or []:
        for step in proc.get("steps", []) or []:
            for oc in step.get("outputChemical", []) or []:
                cnum = str(oc.get("CCDCNumber") or oc.get("ccdcNumber") or "").strip()
                if not cnum:
                    continue
                s = out.setdefault(cnum, set())
                names = oc.get("names") or oc.get("chemicalName") or oc.get("name")
                if isinstance(names, list):
                    for v in names:
                        n = _norm_ident(v)
                        if n:
                            s.add(n)
                else:
                    n = _norm_ident(names)
                    if n:
                        s.add(n)
                n = _norm_ident(oc.get("chemicalFormula") or oc.get("formula"))
                if n:
                    s.add(n)
    return out

# ---------------------- identifier extraction (INPUTS only) ----------------------

def _extract_pred_input_identifiers_list(txt: str) -> List[str]:
    idents: List[str] = []
    # JSON objects that are not outputs are considered inputs
    for obj in _extract_json_objects_from_text(txt):
        if _is_output_object(obj):
            continue
        for key in ("name", "names", "alternativeNames", "alternative_names", "chemicalName", "chemicalNames"):
            vals = obj.get(key)
            if isinstance(vals, list):
                for v in vals:
                    n = _norm_ident(v)
                    if n:
                        idents.append(n)
            else:
                n = _norm_ident(vals)
                if n:
                    idents.append(n)
        for key in ("formula", "chemicalFormula"):
            n = _norm_ident(obj.get(key))
            if n:
                idents.append(n)
    # Markdown ChemicalInputs sections
    for m in re.finditer(r"(?im)^\s*-?\s*ChemicalInputs\s*:\s*$", txt):
        start = m.end()
        endm = re.search(r"(?im)^\s*-?\s*Chemical(Inputs|Output)\s*:\s*$|^\s*```", txt[start:])
        end = start + endm.start() if endm else len(txt)
        sub = txt[start:end]
        # Name
        for nm in re.findall(r"(?im)^\s*-\s*Name\s*:\s*(.+?)\s*$", sub):
            n = _norm_ident(nm)
            if n:
                idents.append(n)
        # Alternative Names
        for an in re.findall(r"(?im)^\s*-\s*Alternative\s*Names\s*:\s*(.+?)\s*$", sub):
            parts = re.split(r"\s*;\s*", an)
            for p in parts:
                n = _norm_ident(p)
                if n:
                    idents.append(n)
        # Formula
        for fm in re.findall(r"(?im)^\s*-\s*Formula\s*:\s*(.+?)\s*$", sub):
            n = _norm_ident(fm)
            if n:
                idents.append(n)
    return idents


def _extract_pred_input_identifiers_from_text(txt: str) -> Set[str]:
    return set(_extract_pred_input_identifiers_list(txt))


def _predicted_ccdc_input_idents_map(hash_dir: Path) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    mcp_dir = hash_dir / "mcp_run"
    if not mcp_dir.exists():
        return out
    for path in sorted(mcp_dir.glob("iter2_hints_*.txt")):
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            continue
        ccdc = _infer_ccdc_from_name_sources(path, txt)
        if not ccdc:
            ccdc = _extract_ccdc_from_text(txt)
        if not ccdc:
            continue
        idents = set(_extract_pred_input_identifiers_list(txt))
        if not idents:
            continue
        s = out.setdefault(ccdc, set())
        s |= idents
    return out


def _gt_ccdc_input_idents_map(doi: str) -> Optional[Dict[str, Set[str]]]:
    gt_path = CHEM_GT_DIR / f"{doi}.json"
    if not gt_path.exists():
        return None
    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    out: Dict[str, Set[str]] = {}
    for proc in data.get("synthesisProcedures", []) or []:
        ccdc_vals: Set[str] = set()
        inputs: Set[str] = set()
        for step in proc.get("steps", []) or []:
            # inputs
            for ic in step.get("inputChemicals", []) or []:
                for chem in ic.get("chemical", []) or []:
                    for key in ("chemicalName", "names", "name"):
                        vals = chem.get(key)
                        if isinstance(vals, list):
                            for v in vals:
                                n = _norm_ident(v)
                                if n:
                                    inputs.add(n)
                        else:
                            n = _norm_ident(vals)
                            if n:
                                inputs.add(n)
                    n = _norm_ident(chem.get("chemicalFormula") or chem.get("formula"))
                    if n:
                        inputs.add(n)
            # outputs -> CCDC
            for oc in step.get("outputChemical", []) or []:
                cnum = str(oc.get("CCDCNumber") or oc.get("ccdcNumber") or "").strip()
                if cnum:
                    ccdc_vals.add(cnum)
        for c in ccdc_vals:
            s = out.setdefault(c, set())
            s |= inputs
    return out

# New: Counters for explicit species-level matching

def _predicted_ccdc_input_counters_map(hash_dir: Path) -> Dict[str, Counter]:
    out: Dict[str, Counter] = {}
    mcp_dir = hash_dir / "mcp_run"
    if not mcp_dir.exists():
        return out
    for path in sorted(mcp_dir.glob("iter2_hints_*.txt")):
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            continue
        ccdc = _infer_ccdc_from_name_sources(path, txt)
        if not ccdc:
            ccdc = _extract_ccdc_from_text(txt)
        if not ccdc:
            continue
        idents = _extract_pred_input_identifiers_list(txt)
        if not idents:
            continue
        cnt = out.setdefault(ccdc, Counter())
        cnt.update(idents)
    return out


def _gt_ccdc_input_counters_map(doi: str) -> Optional[Dict[str, Counter]]:
    gt_path = CHEM_GT_DIR / f"{doi}.json"
    if not gt_path.exists():
        return None
    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    out: Dict[str, Counter] = {}
    for proc in data.get("synthesisProcedures", []) or []:
        ccdc_vals: Set[str] = set()
        inputs: List[str] = []
        for step in proc.get("steps", []) or []:
            for ic in step.get("inputChemicals", []) or []:
                for chem in ic.get("chemical", []) or []:
                    for key in ("chemicalName", "names", "name"):
                        vals = chem.get(key)
                        if isinstance(vals, list):
                            for v in vals:
                                n = _norm_ident(v)
                                if n:
                                    inputs.append(n)
                        else:
                            n = _norm_ident(vals)
                            if n:
                                inputs.append(n)
                    n = _norm_ident(chem.get("chemicalFormula") or chem.get("formula"))
                    if n:
                        inputs.append(n)
            for oc in step.get("outputChemical", []) or []:
                cnum = str(oc.get("CCDCNumber") or oc.get("ccdcNumber") or "").strip()
                if cnum:
                    ccdc_vals.add(cnum)
        for c in ccdc_vals:
            cnt = out.setdefault(c, Counter())
            cnt.update(inputs)
    return out

# ---------------------- species-level alias sets (INPUTS) ----------------------

def _extract_pred_input_species_aliases_from_text(txt: str) -> List[Set[str]]:
    species: List[Set[str]] = []
    # JSON: each non-output object = one species alias set
    for obj in _extract_json_objects_from_text(txt):
        if _is_output_object(obj):
            continue
        alias: Set[str] = set()
        for key in ("name", "names", "alternativeNames", "alternative_names", "chemicalName", "chemicalNames"):
            vals = obj.get(key)
            if isinstance(vals, list):
                for v in vals:
                    n = _norm_ident(v)
                    if n:
                        alias.add(n)
            else:
                n = _norm_ident(vals)
                if n:
                    alias.add(n)
        for key in ("formula", "chemicalFormula"):
            n = _norm_ident(obj.get(key))
            if n:
                alias.add(n)
        if alias:
            species.append(alias)
    # Markdown: each ChemicalInputs block = one species alias set
    for m in re.finditer(r"(?im)^\s*-?\s*ChemicalInputs\s*:\s*$", txt):
        start = m.end()
        endm = re.search(r"(?im)^\s*-?\s*Chemical(Inputs|Output)\s*:\s*$|^\s*```", txt[start:])
        end = start + endm.start() if endm else len(txt)
        sub = txt[start:end]
        alias: Set[str] = set()
        for nm in re.findall(r"(?im)^\s*-\s*Name\s*:\s*(.+?)\s*$", sub):
            n = _norm_ident(nm)
            if n:
                alias.add(n)
        for an in re.findall(r"(?im)^\s*-\s*Alternative\s*Names\s*:\s*(.+?)\s*$", sub):
            parts = re.split(r"\s*;\s*", an)
            for p in parts:
                n = _norm_ident(p)
                if n:
                    alias.add(n)
        for fm in re.findall(r"(?im)^\s*-\s*Formula\s*:\s*(.+?)\s*$", sub):
            n = _norm_ident(fm)
            if n:
                alias.add(n)
        if alias:
            species.append(alias)
    return species


def _predicted_ccdc_input_species_map(hash_dir: Path) -> Dict[str, List[Set[str]]]:
    out: Dict[str, List[Set[str]]] = {}
    mcp_dir = hash_dir / "mcp_run"
    if not mcp_dir.exists():
        return out
    for path in sorted(mcp_dir.glob("iter2_hints_*.txt")):
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            continue
        ccdc = _infer_ccdc_from_name_sources(path, txt) or _extract_ccdc_from_text(txt)
        if not ccdc:
            continue
        plist = out.setdefault(ccdc, [])
        plist.extend(_extract_pred_input_species_aliases_from_text(txt))
    return out


def _gt_ccdc_input_species_map(doi: str) -> Optional[Dict[str, List[Set[str]]]]:
    gt_path = CHEM_GT_DIR / f"{doi}.json"
    if not gt_path.exists():
        return None
    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    out: Dict[str, List[Set[str]]] = {}
    for proc in data.get("synthesisProcedures", []) or []:
        ccdc_vals: Set[str] = set()
        species_aliases: List[Set[str]] = []
        for step in proc.get("steps", []) or []:
            for ic in step.get("inputChemicals", []) or []:
                for chem in ic.get("chemical", []) or []:
                    alias: Set[str] = set()
                    for key in ("chemicalName", "names", "name"):
                        vals = chem.get(key)
                        if isinstance(vals, list):
                            for v in vals:
                                n = _norm_ident(v)
                                if n:
                                    alias.add(n)
                        else:
                            n = _norm_ident(vals)
                            if n:
                                alias.add(n)
                    n = _norm_ident(chem.get("chemicalFormula") or chem.get("formula"))
                    if n:
                        alias.add(n)
                    if alias:
                        species_aliases.append(alias)
            for oc in step.get("outputChemical", []) or []:
                cnum = str(oc.get("CCDCNumber") or oc.get("ccdcNumber") or "").strip()
                if cnum:
                    ccdc_vals.add(cnum)
        for c in ccdc_vals:
            lst = out.setdefault(c, [])
            lst.extend(species_aliases)
    return out


def _species_alias_match_count(pred_list: List[Set[str]], gold_list: List[Set[str]]) -> Tuple[int, int, int]:
    matched_g = [False] * len(gold_list)
    tp = 0
    for p in pred_list:
        found = False
        for i, g in enumerate(gold_list):
            if not matched_g[i] and (p & g):
                matched_g[i] = True
                tp += 1
                found = True
                break
        # unmatched preds counted later as FP; unmatched golds as FN
    fp = len(pred_list) - tp
    fn = len(gold_list) - tp
    return tp, fp, fn

# ---------------------- main comparisons ----------------------

def _extract_counts_from_iter2(hash_dir: Path) -> List[int]:
    mcp_dir = hash_dir / "mcp_run"
    if not mcp_dir.exists():
        return []
    counts: List[int] = []
    for path in sorted(mcp_dir.glob("iter2_hints_*.txt")):
        counts.append(_count_inputs_in_hint_file(path))
    return counts


def _ground_truth_counts_for_doi(doi: str) -> Optional[List[int]]:
    gt_path = CHEM_GT_DIR / f"{doi}.json"
    if not gt_path.exists():
        return None
    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading GT {gt_path}: {e}")
        return None

    counts: List[int] = []
    for proc in data.get("synthesisProcedures", []) or []:
        for step in proc.get("steps", []) or []:
            counts.append(len(step.get("inputChemicals", []) or []))
    return counts


def compare_counts(extracted: List[int], gt: List[int]) -> Tuple[bool, List[int], List[int]]:
    a = sorted(extracted)
    b = sorted(gt)
    return (a == b, a, b)


def _accumulate_prf(tp_fp_fn: Tuple[int, int, int], extracted: List[int], gt: List[int]) -> Tuple[int, int, int]:
    tp, fp, fn = tp_fp_fn
    # Align by sorted order to avoid penalizing permutation differences
    a = sorted(extracted)
    b = sorted(gt)
    L = max(len(a), len(b))
    for i in range(L):
        p = a[i] if i < len(a) else 0
        g = b[i] if i < len(b) else 0
        tp += min(p, g)
        fp += max(p - g, 0)
        fn += max(g - p, 0)
    return tp, fp, fn


def _predicted_ccdc_counts(hash_dir: Path) -> Dict[str, int]:
    mcp_dir = hash_dir / "mcp_run"
    out: Dict[str, int] = {}
    if not mcp_dir.exists():
        return out
    for path in sorted(mcp_dir.glob("iter2_hints_*.txt")):
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            continue
        ccdc = _infer_ccdc_from_name_sources(path, txt)
        if not ccdc:
            ccdc = _extract_ccdc_from_text(txt)
        if not ccdc:
            continue
        cnt = _count_inputs_in_hint_file(path)
        out[ccdc] = out.get(ccdc, 0) + cnt
    return out


def _gt_ccdc_counts_for_doi(doi: str) -> Optional[Dict[str, int]]:
    gt_path = CHEM_GT_DIR / f"{doi}.json"
    if not gt_path.exists():
        return None
    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    out: Dict[str, int] = {}
    for proc in data.get("synthesisProcedures", []) or []:
        ccdc_vals: List[str] = []
        step_count = 0
        for step in proc.get("steps", []) or []:
            step_count += len(step.get("inputChemicals", []) or [])
            for oc in step.get("outputChemical", []) or []:
                cnum = str(oc.get("CCDCNumber") or oc.get("ccdcNumber") or "").strip()
                if cnum:
                    ccdc_vals.append(cnum)
        ccdc_vals = list({c for c in ccdc_vals if c})
        if not ccdc_vals:
            continue
        for c in ccdc_vals:
            out[c] = out.get(c, 0) + step_count
    return out

# ---------------------- run ----------------------

def run(arg_file: Optional[str]) -> int:
    doi_to_hash = _load_doi_to_hash()
    pairs = _resolve_hash_and_doi(arg_file, doi_to_hash)
    if not pairs:
        return 1

    total = 0
    mismatches = 0
    matches = 0
    tp = fp = fn = 0
    ccdc_tp = ccdc_fp = ccdc_fn = 0
    id_tp = id_fp = id_fn = 0
    pair_error: List[Tuple[str, str, int]] = []

    for h, doi in pairs:
        print("\n" + "=" * 80)
        total += 1
        hash_dir = DATA_DIR / h
        if not hash_dir.exists():
            print(f"Error {h}: directory not found")
            continue

        extracted = _extract_counts_from_iter2(hash_dir)
        if not extracted:
            print(f"Error {h}: No iter2 hint files found")
            continue

        gt_counts = _ground_truth_counts_for_doi(doi)
        if gt_counts is None:
            print(f"Error {h}: Ground truth JSON not found for DOI {doi}")
            continue

        tp, fp, fn = _accumulate_prf((tp, fp, fn), extracted, gt_counts)

        ok, a, b = compare_counts(extracted, gt_counts)
        if ok:
            matches += 1
            print(f"{h} (DOI: {doi})")
            print(f"  Extracted (sorted): {a}")
            print(f"  Ground truth (sorted): {b}")
            print(f"  Result: OK MATCH")
        else:
            mismatches += 1
            print(f"{h} (DOI: {doi})")
            print(f"  Extracted (sorted): {a}")
            print(f"  Ground truth (sorted): {b}")
            print(f"  Result: X MISMATCH")

        pred_map = _predicted_ccdc_counts(hash_dir)
        gt_map = _gt_ccdc_counts_for_doi(doi) or {}
        pair_fp = pair_fn = 0
        if pred_map or gt_map:
            print(f"  CCDC-wise counts:")
            all_ccdc = sorted(set(pred_map.keys()) | set(gt_map.keys()))
            for c in all_ccdc:
                p = pred_map.get(c, 0)
                g = gt_map.get(c, 0)
                tag = "OK" if p == g else "DIFF"
                print(f"    CCDC {c}: pred={p} gt={g} [{tag}]")
                ccdc_tp += min(p, g)
                diff_p = max(p - g, 0)
                diff_g = max(g - p, 0)
                ccdc_fp += diff_p
                ccdc_fn += diff_g
                pair_fp += diff_p
                pair_fn += diff_g
        pair_error.append((h, doi, pair_fp + pair_fn))

        # Identifier-level comparison per CCDC (INPUT identifiers only)
        pred_inputs_map = _predicted_ccdc_input_idents_map(hash_dir)
        gt_inputs_map = _gt_ccdc_input_idents_map(doi) or {}
        if pred_inputs_map or gt_inputs_map:
            print(f"  CCDC-wise input identifier match (name/alt/formula):")
            all_ccdc_ids = sorted(set(pred_inputs_map.keys()) | set(gt_inputs_map.keys()))
            for c in all_ccdc_ids:
                preds = pred_inputs_map.get(c, set())
                golds = gt_inputs_map.get(c, set())
                inter = preds & golds
                hit = bool(inter)
                print(f"    CCDC {c}: match={hit} | pred_ids={len(preds)} gold_ids={len(golds)}")
                if hit:
                    id_tp += 1
                    # print(f"      matches: {sorted(inter)}")
                else:
                    if preds and not golds:
                        id_fp += 1
                    elif golds and not preds:
                        id_fn += 1
                    else:
                        id_fp += 1
                        id_fn += 1
                    # show details when mismatch
                    print(f"      pred: {sorted(preds) if preds else []}")
                    print(f"      gold: {sorted(golds) if golds else []}")


    precision_ord = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall_ord = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1_ord = (2 * precision_ord * recall_ord / (precision_ord + recall_ord)) if (precision_ord + recall_ord) > 0 else 0.0

    precision = ccdc_tp / (ccdc_tp + ccdc_fp) if (ccdc_tp + ccdc_fp) > 0 else 0.0
    recall = ccdc_tp / (ccdc_tp + ccdc_fn) if (ccdc_tp + ccdc_fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    id_p = id_tp / (id_tp + id_fp) if (id_tp + id_fp) > 0 else 0.0
    id_r = id_tp / (id_tp + id_fn) if (id_tp + id_fn) > 0 else 0.0
    id_f1 = (2 * id_p * id_r / (id_p + id_r)) if (id_p + id_r) > 0 else 0.0

    print(f"\nSummary: matches={matches}, mismatches={mismatches}, total={total}")
    print(f"Order-aligned (aux): TP={tp} FP={fp} FN={fn} | P={precision_ord:.3f} R={recall_ord:.3f} F1={f1_ord:.3f}")
    print(f"CCDC-wise (final counts): TP={ccdc_tp} FP={ccdc_fp} FN={ccdc_fn}")
    print(f"Precision={precision:.3f} Recall={recall:.3f} F1={f1:.3f}")
    print(f"Identifier-wise (per-CCDC presence, input identifiers): TP={id_tp} FP={id_fp} FN={id_fn}")
    print(f"Identifier Precision={id_p:.3f} Recall={id_r:.3f} F1={id_f1:.3f}")

    offenders = sorted(pair_error, key=lambda x: x[2], reverse=True)
    if offenders:
        print("\nTop error-contributing hash+DOI pairs (by CCDC-wise FP+FN):")
        for h, doi, err in offenders:
            if err > 0:
                print(f"  {h} | {doi} | errors={err}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Compare iter2 input counts with ground truth")
    parser.add_argument("--file", dest="file", type=str, help="Target by hash or DOI; default all", required=False)
    args = parser.parse_args()
    raise SystemExit(run(args.file))


if __name__ == "__main__":
    main()
