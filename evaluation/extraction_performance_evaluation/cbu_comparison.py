#!/usr/bin/env python3
# eval_cbu_v2.py
# CCDC-anchored, order-agnostic CBU evaluation with strict/soft formula match
# and union-of-slots species-name match.

import argparse, json, re, unicodedata
from typing import Dict, Set, Tuple, List
from pathlib import Path

def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")

def _collapse_ws(s: str) -> str:
    return " ".join((s or "").split())

def _strict_formula(s: str) -> str:
    s = _nfkc(s).strip()
    s = s.replace("’", "'").replace("′", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", "", s)

def _soft_formula(s: str) -> str:
    # Soft == strict for stricter evaluation.
    return _strict_formula(s)

def _norm_name(s: str) -> str:
    s = _nfkc(s)
    # Normalize Unicode primes/apostrophes to ASCII
    s = s.replace("’", "'").replace("′", "'")
    s = s.lower()
    s = _collapse_ws(s)
    return s

def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _index(obj: dict) -> Dict[str, dict]:
    """
    Returns map:
      CCDC -> {
        "formulas": set({f1,f2} minus empties),
        "names1": set([...]),
        "names2": set([...]),
        "names_all": union(names1, names2)
      }
    """
    out = {}
    for rec in (obj.get("synthesisProcedures") or []):
        ccdc = str(rec.get("mopCCDCNumber") or "").strip()
        if not ccdc:
            continue
        f1 = str(rec.get("cbuFormula1") or "")
        f2 = str(rec.get("cbuFormula2") or "")
        n1 = [str(x) for x in (rec.get("cbuSpeciesNames1") or [])]
        n2 = [str(x) for x in (rec.get("cbuSpeciesNames2") or [])]
        out[ccdc] = {
            "formulas": {f for f in (f1, f2) if f and f.upper() != "N/A"},
            "names1": {x for x in n1 if x},
            "names2": {x for x in n2 if x},
            "names_all": {x for x in (n1 + n2) if x},
        }
    return out

def _prf(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2*p*r/(p+r) if (p+r) else 0.0
    return p, r, f1

def _set_counts(pred: Set[str], gold: Set[str], norm) -> Tuple[int,int,int,Set[str],Set[str],Set[str]]:
    p = {norm(x) for x in pred if x}
    g = {norm(x) for x in gold if x}
    inter = p & g
    return len(inter), len(p - g), len(g - p), p, g, inter

def evaluate(pred_path: str, gold_path: str, debug: bool = False) -> None:
    P = _index(_load(pred_path))
    G = _index(_load(gold_path))

    cc_pred, cc_gold = set(P), set(G)
    matched = sorted(cc_pred & cc_gold)
    missing_in_pred = sorted(cc_gold - cc_pred)
    extra_in_pred = sorted(cc_pred - cc_gold)

    print(f"Ground truth: {len(cc_gold)} entries | Predicted: {len(cc_pred)} entries")
    print(f"Entry match by CCDC → TP={len(matched)} FP={len(extra_in_pred)} FN={len(missing_in_pred)}")

    # ---- Formulas, order-insensitive per-CCDC ----
    tpF=fpF=fnF=0
    tpFs=fpFs=fnFs=0
    for c in matched:
        tp, fp, fn, _, _, _ = _set_counts(P[c]["formulas"], G[c]["formulas"], _strict_formula)
        tpF += tp; fpF += fp; fnF += fn
        tp, fp, fn, _, _, _ = _set_counts(P[c]["formulas"], G[c]["formulas"], _soft_formula)
        tpFs += tp; fpFs += fp; fnFs += fn
    for c in missing_in_pred:
        fnF  += len({_strict_formula(x) for x in G[c]["formulas"] if x})
        fnFs += len({_soft_formula(x)   for x in G[c]["formulas"] if x})
    for c in extra_in_pred:
        fpF  += len({_strict_formula(x) for x in P[c]["formulas"] if x})
        fpFs += len({_soft_formula(x)   for x in P[c]["formulas"] if x})

    p,r,f1 = _prf(tpF,fpF,fnF)
    ps,rs,f1s = _prf(tpFs,fpFs,fnFs)
    print(f"Formulas (strict): TP={tpF} FP={fpF} FN={fnF} | P={p:.3f} R={r:.3f} F1={f1:.3f}")
    print(f"Formulas (soft):   TP={tpFs} FP={fpFs} FN={fnFs} | P={ps:.3f} R={rs:.3f} F1={f1s:.3f}")

    # ---- Species names — union of slots, strict text ----
    tpN=fpN=fnN=0
    for c in matched:
        tp, fp, fn, _, _, _ = _set_counts(P[c]["names_all"], G[c]["names_all"], _norm_name)
        tpN += tp; fpN += fp; fnN += fn
    for c in missing_in_pred:
        fnN += len({_norm_name(x) for x in G[c]["names_all"] if x})
    for c in extra_in_pred:
        fpN += len({_norm_name(x) for x in P[c]["names_all"] if x})

    pn,rn,f1n = _prf(tpN,fpN,fnN)
    print(f"Species names (union, strict text): TP={tpN} FP={fpN} FN={fnN} | P={pn:.3f} R={rn:.3f} F1={f1n:.3f}")

    # ---- Overall across all datapoints (formulas + names) ----
    tp_all = tpF + tpN
    fp_all = fpF + fpN
    fn_all = fnF + fnN
    P_overall, R_overall, F1_overall = _prf(tp_all, fp_all, fn_all)
    print("\nOverall (all datapoints = formulas + names):")
    print(f"  TP={tp_all} FP={fp_all} FN={fn_all} | P={P_overall:.3f} R={R_overall:.3f} F1={F1_overall:.3f}")

    if debug:
        print("\n# Differences:")
        for c in matched:
            sf_p = {_strict_formula(x) for x in P[c]['formulas']}
            sf_g = {_strict_formula(x) for x in G[c]['formulas']}
            if sf_p != sf_g:
                print(f"- CCDC {c} formulas differ: pred={sorted(P[c]['formulas'])} | gold={sorted(G[c]['formulas'])}")
            n_p = {_norm_name(x) for x in P[c]['names_all']}
            n_g = {_norm_name(x) for x in G[c]['names_all']}
            if n_p != n_g:
                print(f"- CCDC {c} names differ: pred={sorted(P[c]['names_all'])} | gold={sorted(G[c]['names_all'])}")
        for c in missing_in_pred:
            print(f"- CCDC {c} missing in prediction: all GT formulas and names counted as FN")
        for c in extra_in_pred:
            print(f"- CCDC {c} extra in prediction: all predicted formulas and names counted as FP")

REPO_ROOT = Path(__file__).resolve().parents[2]
MERGED_DIR = REPO_ROOT / "evaluation" / "data" / "merged_tll"
GT_DIR = REPO_ROOT / "earlier_ground_truth" / "cbu"
DOI_HASH_MAP = REPO_ROOT / "data" / "doi_to_hash.json"

def _resolve_paths_from_hash(hash_value: str) -> Tuple[str, str]:
    pred = MERGED_DIR / hash_value / "cbu.json"
    if not pred.exists():
        raise FileNotFoundError(f"Predicted not found: {pred}")
    if DOI_HASH_MAP.exists():
        try:
            m = json.loads((DOI_HASH_MAP).read_text(encoding="utf-8"))
            for doi, hv in m.items():
                if str(hv).strip() == hash_value:
                    gt = GT_DIR / (doi.replace("/", "_") + ".json")
                    if gt.exists():
                        return str(pred), str(gt)
        except Exception:
            pass
    try:
        p_obj = _load(str(pred))
        p_cc = {str(r.get("mopCCDCNumber") or "").strip() for r in (p_obj.get("synthesisProcedures") or [])}
    except Exception:
        p_cc = set()
    best = None
    for gt in GT_DIR.glob("*.json"):
        try:
            g_obj = _load(str(gt))
            g_cc = {str(r.get("mopCCDCNumber") or "").strip() for r in (g_obj.get("synthesisProcedures") or [])}
            if p_cc & g_cc:
                best = gt
                break
        except Exception:
            continue
    if not best:
        raise FileNotFoundError(f"Ground truth not found for hash {hash_value} in {GT_DIR}")
    return str(pred), str(best)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CBU evaluation anchored by CCDC with order-insensitive formulas and union-of-slots names.")
    ap.add_argument("--file", required=True, help="Hash value identifying evaluation/data/merged_tll/<hash>/cbu.json")
    ap.add_argument("--debug", action="store_true", help="Print per-CCDC differences")
    args = ap.parse_args()
    pred_path, gold_path = _resolve_paths_from_hash(args.file.strip())
    evaluate(pred_path, gold_path, debug=bool(args.debug))
