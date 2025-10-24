import json
import argparse
from pathlib import Path
from collections import Counter
from typing import Any, Iterable, List, Tuple, Dict
import re


def _to_list(data: Any) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # If dict, compare over its values as items
        return list(data.values())
    # Fallback: treat scalar as single-item list
    return [data]


def _canonicalize(obj: Any) -> Any:
    """
    Convert Python object into a structure with sorted keys and items for deterministic comparison.
    Lists are converted to lists of canonicalized items, then sorted by their JSON dumps.
    Dicts have keys sorted and values canonicalized recursively.
    """
    if isinstance(obj, dict):
        return {k: _canonicalize(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        items = [_canonicalize(x) for x in obj]
        # Sort items deterministically by their JSON string
        return sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    return obj


def _to_fingerprint(obj: Any) -> str:
    canon = _canonicalize(obj)
    return json.dumps(canon, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_fingerprints(path: Path) -> List[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to load JSON: {path}: {e}")
    items = _to_list(raw)
    fps = [_to_fingerprint(x) for x in items]
    return fps


def score_sets(gt_fps: Iterable[str], res_fps: Iterable[str]) -> Tuple[int, int, int, int, int]:
    """
    Return (gt_total, res_total, matched, gt_only, res_only) where matching is multiset-based.
    """
    gt = Counter(gt_fps)
    rs = Counter(res_fps)
    all_keys = set(gt.keys()) | set(rs.keys())
    matched = sum(min(gt[k], rs[k]) for k in all_keys)
    gt_total = sum(gt.values())
    res_total = sum(rs.values())
    gt_only = gt_total - matched
    res_only = res_total - matched
    return gt_total, res_total, matched, gt_only, res_only


# ---------------- Category-specific extractors ----------------

BRACKET_RX = re.compile(r"\[[^\]]+\]")


def extract_cbu_gt(data: Any) -> List[str]:
    out: List[str] = []
    for proc in (data or {}).get("synthesisProcedures", []) or []:
        f1 = str((proc or {}).get("cbuFormula1") or "").strip()
        f2 = str((proc or {}).get("cbuFormula2") or "").strip()
        if f1:
            out.append(f1)
        if f2:
            out.append(f2)
    return out


def extract_cbu_pred(data: Any) -> List[str]:
    out: List[str] = []
    # common shape: {"cbus":[{"cbu_formula":"[...]"}, ...]}
    cbus = (data or {}).get("cbus")
    if isinstance(cbus, list):
        for item in cbus:
            v = str((item or {}).get("cbu_formula") or (item or {}).get("cbuFormula") or "").strip()
            if v:
                out.append(v)
    # fallback: scan strings in doc for bracketed formulas
    if not out:
        s = json.dumps(data, ensure_ascii=False)
        out.extend(BRACKET_RX.findall(s))
    return out


def extract_chem_gt(data: Any) -> List[str]:
    out: List[str] = []
    for sp in (data or {}).get("synthesisProcedures", []) or []:
        for step in (sp or {}).get("steps", []) or []:
            for key, val in (step or {}).items():
                if isinstance(val, dict):
                    for ic in val.get("inputChemicals", []) or []:
                        for chem in (ic or {}).get("chemical", []) or []:
                            f = str((chem or {}).get("chemicalFormula") or "").strip()
                            if f:
                                out.append(f)
    return out


def extract_chem_pred(data: Any) -> List[str]:
    out: List[str] = []
    # Heuristic: collect all fields named formula/chemicalFormula under any dict named chemical/chemicals
    def walk(x: Any):
        if isinstance(x, dict):
            if "chemicalFormula" in x:
                v = str(x.get("chemicalFormula") or "").strip()
                if v:
                    out.append(v)
            if "formula" in x:
                v = str(x.get("formula") or "").strip()
                if v:
                    out.append(v)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return out


def extract_char_gt(data: Any) -> List[str]:
    out: List[str] = []
    for dev in (data or {}).get("Devices", []) or []:
        for ch in (dev or {}).get("Characterisation", []) or []:
            ccdc = str((ch or {}).get("productCCDCNumber") or "").strip()
            if ccdc:
                out.append(f"CCDC:{ccdc}")
    return out


def extract_char_pred(data: Any) -> List[str]:
    out: List[str] = []
    def walk(x: Any):
        if isinstance(x, dict):
            if "productCCDCNumber" in x:
                v = str(x.get("productCCDCNumber") or "").strip()
                if v:
                    out.append(f"CCDC:{v}")
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return out


def extract_steps_gt(data: Any) -> List[str]:
    out: List[str] = []
    for syn in (data or {}).get("Synthesis", []) or []:
        for st in (syn or {}).get("steps", []) or []:
            if isinstance(st, dict):
                op = next(iter(st.keys()), None)
                if op:
                    out.append(op)
    return out


def extract_steps_pred(data: Any) -> List[str]:
    out: List[str] = []
    def walk_steps(x: Any):
        if isinstance(x, dict):
            # a step dict with a single op key
            if len(x) == 1 and next(iter(x.keys()), None) in {"Add", "Filter", "HeatChill", "Sonicate", "Stir", "Crystallize"}:
                out.append(next(iter(x.keys())))
            for v in x.values():
                walk_steps(v)
        elif isinstance(x, list):
            for v in x:
                walk_steps(v)
    walk_steps(data)
    return out


def load_category_tokens(gt_path: Path, res_path: Path, category: str) -> Tuple[List[str], List[str]]:
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    pr = json.loads(res_path.read_text(encoding="utf-8"))
    if category == "cbu":
        return extract_cbu_gt(gt), extract_cbu_pred(pr)
    if category == "chemicals1":
        return extract_chem_gt(gt), extract_chem_pred(pr)
    if category == "characterisation":
        return extract_char_gt(gt), extract_char_pred(pr)
    if category == "steps":
        return extract_steps_gt(gt), extract_steps_pred(pr)
    # fallback to fingerprinting
    return load_fingerprints(gt_path), load_fingerprints(res_path)


# ---------------- Anchor-based mappers and scorers ----------------

def map_cbu_by_ccdc_gt(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    for proc in (data or {}).get("synthesisProcedures", []) or []:
        ccdc = str((proc or {}).get("mopCCDCNumber") or "").strip()
        if not ccdc:
            continue
        vals: List[str] = []
        for k in ("cbuFormula1", "cbuFormula2"):
            v = str((proc or {}).get(k) or "").strip()
            if v:
                vals.append(v)
        if vals:
            m[ccdc] = sorted(vals)
    return m


def map_cbu_by_ccdc_pred(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    # Try common structure
    items = (data or {}).get("cbus")
    if isinstance(items, list):
        # If entries carry ccdc_number alongside cbus, try grouping
        for it in items:
            ccdc = str((it or {}).get("ccdc_number") or (it or {}).get("mopCCDCNumber") or "").strip()
            v = str((it or {}).get("cbu_formula") or (it or {}).get("cbuFormula") or "").strip()
            if ccdc and v:
                m.setdefault(ccdc, []).append(v)
    # Also scan any top-level mapping of CCDC to cbus array
    if not m:
        # fallback heuristic
        s = json.dumps(data, ensure_ascii=False)
        # not robust; leave empty
        return {}
    # sort lists
    for k in list(m.keys()):
        m[k] = sorted([x for x in m[k] if x])
        if not m[k]:
            del m[k]
    return m


def score_anchor_maps(gt_map: Dict[str, Any], pred_map: Dict[str, Any], eq_fn=None) -> Tuple[int, int, int, int, int]:
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


def map_char_by_ccdc_gt(data: Any) -> Dict[str, Any]:
    m: Dict[str, Any] = {}
    for dev in (data or {}).get("Devices", []) or []:
        for ch in (dev or {}).get("Characterisation", []) or []:
            ccdc = str((ch or {}).get("productCCDCNumber") or "").strip()
            if not ccdc:
                continue
            # record minimal normalized info for comparison
            m[ccdc] = _canonicalize(ch)
    return m


def map_char_by_ccdc_pred(data: Any) -> Dict[str, Any]:
    m: Dict[str, Any] = {}
    def walk(x: Any):
        if isinstance(x, dict):
            if "productCCDCNumber" in x:
                ccdc = str(x.get("productCCDCNumber") or "").strip()
                if ccdc and ccdc not in m:
                    m[ccdc] = _canonicalize(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return m


def map_chems_by_name_gt(data: Any) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for sp in (data or {}).get("synthesisProcedures", []) or []:
        for step in (sp or {}).get("steps", []) or []:
            for key, val in (step or {}).items():
                if isinstance(val, dict):
                    for ic in val.get("inputChemicals", []) or []:
                        for chem in (ic or {}).get("chemical", []) or []:
                            name_list = (chem or {}).get("chemicalName") or []
                            formula = str((chem or {}).get("chemicalFormula") or "").strip()
                            for nm in name_list or []:
                                nms = str(nm).strip()
                                if nms:
                                    m[nms] = formula
    return m


def map_chems_by_name_pred(data: Any) -> Dict[str, str]:
    m: Dict[str, str] = {}
    def walk(x: Any):
        if isinstance(x, dict):
            names = x.get("chemicalName") or x.get("names")
            formula = x.get("chemicalFormula") or x.get("formula")
            if names and formula:
                if isinstance(names, list):
                    for nm in names:
                        nms = str(nm).strip()
                        if nms:
                            m.setdefault(nms, str(formula).strip())
                else:
                    nms = str(names).strip()
                    if nms:
                        m.setdefault(nms, str(formula).strip())
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return m


def map_steps_by_ccdc_gt(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    for syn in (data or {}).get("Synthesis", []) or []:
        ccdc = str((syn or {}).get("productCCDCNumber") or "").strip()
        if not ccdc:
            continue
        ops: List[str] = []
        for st in (syn or {}).get("steps", []) or []:
            if isinstance(st, dict) and len(st) == 1:
                ops.append(next(iter(st.keys())))
        m[ccdc] = ops
    return m


def map_steps_by_ccdc_pred(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    def walk(x: Any):
        if isinstance(x, dict):
            if "productCCDCNumber" in x and "steps" in x:
                ccdc = str(x.get("productCCDCNumber") or "").strip()
                ops: List[str] = []
                for st in x.get("steps") or []:
                    if isinstance(st, dict) and len(st) == 1:
                        ops.append(next(iter(st.keys())))
                if ccdc:
                    m[ccdc] = ops
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return m


def render_report(title: str, file_scores: List[Tuple[str, Tuple[int, int, int, int, int]]]) -> str:
    lines: List[str] = []
    lines.append(f"### {title}")
    lines.append("")
    lines.append("| File | GT | Pred | Matched | GT-only | Pred-only | Precision | Recall | F1 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, (gt_total, res_total, matched, gt_only, res_only) in file_scores:
        precision = (matched / res_total * 100.0) if res_total else 0.0
        recall = (matched / gt_total * 100.0) if gt_total else 0.0
        denom = precision + recall
        f1 = (2 * precision * recall / denom) if denom > 0 else 0.0
        lines.append(
            f"| {name} | {gt_total} | {res_total} | {matched} | {gt_only} | {res_only} | {precision:.1f}% | {recall:.1f}% | {f1:.1f}% |"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Merged result scoring")
    parser.add_argument("--file", type=str, default=None, help="Limit scoring to a single DOI (with '/' or '_') or 8-char hash")
    args = parser.parse_args()
    # Hardcoded roots: GT by DOI under earlier_ground_truth; predictions under merged_tll by hash (or DOI dir)
    GT_BASE = Path("earlier_ground_truth")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/result")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    # Category -> predicted filename mapping
    cat_to_fname = {
        "cbu": "cbu.json",
        "chemicals1": "chemicals.json",
        "characterisation": "characterisation.json",
        "steps": "steps.json",
    }

    # Load doi<->hash mapping
    doi_to_hash_path = Path("data/doi_to_hash.json")
    hash_to_doi: Dict[str, str] = {}
    if doi_to_hash_path.exists():
        try:
            d2h = json.loads(doi_to_hash_path.read_text(encoding="utf-8"))
            if isinstance(d2h, dict):
                for doi, hv in d2h.items():
                    sdoi = str(doi).strip()
                    shv = str(hv).strip()
                    if len(shv) == 8:
                        hash_to_doi[shv] = sdoi
        except Exception:
            pass

    def doi_to_filename(doi: str) -> str:
        return doi.replace("/", "_").replace(":", "_") + ".json"

    def _resolve_hashes(arg_val: str) -> List[str]:
        if not arg_val:
            return []
        ident = arg_val.strip()
        # 8-char hex hash
        if len(ident) == 8 and all(c in "0123456789abcdefABCDEF" for c in ident):
            return [ident.lower()]
        # treat as DOI; normalize underscores -> slashes
        norm_doi = ident.replace("_", "/")
        hits: List[str] = []
        for hv, doi in hash_to_doi.items():
            if doi == norm_doi:
                hits.append(hv)
        return hits

    if args.file:
        res_hashes = _resolve_hashes(args.file)
    else:
        res_hashes = [p.name for p in RES_ROOT.iterdir() if p.is_dir()] if RES_ROOT.exists() else []
        res_hashes.sort()

    overall_rows: List[Tuple[str, Tuple[int, int, int, int, int]]] = []

    for hv in res_hashes:
        doi = hash_to_doi.get(hv)
        if not doi:
            continue

        res_dir = RES_ROOT / hv
        if not res_dir.exists():
            alt = RES_ROOT / doi.replace("/", "_").replace(":", "_")
            if alt.exists():
                res_dir = alt
        if not res_dir.exists():
            continue

        scores: List[Tuple[str, Tuple[int, int, int, int, int]]] = []
        for cat, fname in cat_to_fname.items():
            gt_path = GT_BASE / cat / doi_to_filename(doi)
            res_path = res_dir / fname
            if not gt_path.exists() or not res_path.exists():
                # Skip categories without matching files
                continue
            # Anchor-based comparisons per category
            if cat == "cbu":
                gt_map = map_cbu_by_ccdc_gt(json.loads(gt_path.read_text(encoding="utf-8")))
                pred_map = map_cbu_by_ccdc_pred(json.loads(res_path.read_text(encoding="utf-8")))
                if pred_map:
                    scores.append((fname, score_anchor_maps(gt_map, pred_map)))
            elif cat == "characterisation":
                gt_map = map_char_by_ccdc_gt(json.loads(gt_path.read_text(encoding="utf-8")))
                pred_map = map_char_by_ccdc_pred(json.loads(res_path.read_text(encoding="utf-8")))
                if pred_map:
                    scores.append((fname, score_anchor_maps(gt_map, pred_map)))
            elif cat == "chemicals1":
                gt_map = map_chems_by_name_gt(json.loads(gt_path.read_text(encoding="utf-8")))
                pred_map = map_chems_by_name_pred(json.loads(res_path.read_text(encoding="utf-8")))
                if pred_map:
                    # Compare formula equality for matching names
                    def eq(a, b): return str(a).strip() == str(b).strip()
                    scores.append((fname, score_anchor_maps(gt_map, pred_map, eq_fn=eq)))
            elif cat == "steps":
                gt_map = map_steps_by_ccdc_gt(json.loads(gt_path.read_text(encoding="utf-8")))
                pred_map = map_steps_by_ccdc_pred(json.loads(res_path.read_text(encoding="utf-8")))
                if pred_map:
                    # Compare sequence equality of operation names per CCDC
                    scores.append((fname, score_anchor_maps(gt_map, pred_map)))

        report = render_report(f"Merged Result Scoring - {hv}", scores)
        (OUT_ROOT / f"{hv}.md").write_text(report, encoding="utf-8")
        print(report)

        summed = tuple(sum(t[i] for _, t in scores) for i in range(5))  # type: ignore
        overall_rows.append((hv, summed))

    overall_report = render_report("Merged Result Scoring - Overall", overall_rows)
    (OUT_ROOT / "_overall.md").write_text(overall_report, encoding="utf-8")
    print(overall_report)


if __name__ == "__main__":
    main()


