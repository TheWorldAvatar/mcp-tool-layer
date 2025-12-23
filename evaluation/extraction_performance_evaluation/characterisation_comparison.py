#!/usr/bin/env python3
# eval_characterisation.py
# CCDC-anchored evaluation; NA-consistent with calc_performance_metrics (both sides "N/A" => TP=1).

import argparse, json, re, unicodedata
from typing import Dict, List, Tuple, Set
from pathlib import Path

NA_TOKENS = {"n/a", "na", "not stated", "-", "—", ""}

def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")

def norm_text(s: str) -> str:
    return " ".join(nfkc(s).strip().split()).lower()

def is_na_like(s: str) -> bool:
    return norm_text(s) in NA_TOKENS

def norm_formula(s: str) -> str:
    return re.sub(r"\s+", "", nfkc(s or ""))

def norm_nospace(s: str) -> str:
    return re.sub(r"\s+", "", nfkc(s or ""))

def parse_bands(s: str) -> Set[int]:
    nums = set()
    for m in re.finditer(r"(\d{3,4})(?:\.\d+)?", s or ""):
        try:
            nums.add(int(round(float(m.group(0)))))
        except Exception:
            pass
    return nums

def parse_percent_series(s: str) -> Dict[str, float]:
    out = {}
    if not s: return out
    for token in re.split(r"[;,]\s*", s.strip()):
        m = re.match(r"([A-Za-z]+)\s+(-?\d+(?:\.\d+)?)", token)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out

def load_char_map(path: str) -> Dict[str, dict]:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    items = []
    for dev in (obj.get("Devices") or []):
        items.extend(dev.get("Characterisation") or [])
    out = {}
    for rec in items:
        ccdc = str(rec.get("productCCDCNumber") or "").strip()
        if not ccdc:
            continue
        ir = rec.get("InfraredSpectroscopy") or {}
        ea = rec.get("ElementalAnalysis") or {}
        out[ccdc] = {
            "names": set(map(norm_text, (rec.get("productNames") or []))),
            "ir_material_raw": str(ir.get("material") or ""),
            "ir_material": norm_text(ir.get("material") or ""),
            "ir_bands_raw": str(ir.get("bands") or ""),
            "ir_bands": norm_nospace(ir.get("bands") or ""),
            "ea_formula_raw": str(ea.get("chemicalFormula") or ""),
            "ea_formula": norm_formula(ea.get("chemicalFormula") or ""),
            "ea_calc_raw": str(ea.get("weightPercentageCalculated") or ""),
            "ea_calc": parse_percent_series(ea.get("weightPercentageCalculated") or ""),
            "ea_exp_raw": str(ea.get("weightPercentageExperimental") or ""),
            "ea_exp": parse_percent_series(ea.get("weightPercentageExperimental") or ""),
        }
    return out

def prf(tp:int, fp:int, fn:int) -> Tuple[float,float,float]:
    p = tp/(tp+fp) if (tp+fp) else 0.0
    r = tp/(tp+fn) if (tp+fn) else 0.0
    f1 = 2*p*r/(p+r) if (p+r) else 0.0
    return p,r,f1

def band_counts(pred:Set[int], gold:Set[int], tol:int=3) -> Tuple[int,int,int]:
    tp=fp=fn=0
    matched=set()
    for g in gold:
        cand = sorted([p for p in pred if abs(p-g)<=tol], key=lambda x:abs(x-g))
        if cand:
            pick=None
            for c in cand:
                if c not in matched:
                    pick=c; break
            if pick is not None:
                tp+=1; matched.add(pick)
            else:
                fn+=1
        else:
            fn+=1
    fp = len(pred - matched)
    return tp,fp,fn

def pct_counts(pd:Dict[str,float], gd:Dict[str,float]) -> Tuple[int,int,int]:
    tp=fp=fn=0
    for e,v in gd.items():
        if e in pd and round(pd[e],2)==round(v,2):
            tp+=1
        else:
            fn+=1
    for e in pd:
        if e not in gd:
            fp+=1
    return tp,fp,fn

def eval_one(pred:dict, gold:dict) -> dict:
    # names
    tpN = len(pred["names"] & gold["names"])
    fpN = len(pred["names"] - gold["names"])
    fnN = len(gold["names"] - pred["names"])

    # ir_material: strict match; if both NA-like -> TP=1
    if is_na_like(pred["ir_material_raw"]) and is_na_like(gold["ir_material_raw"]):
        tpM, fpM, fnM = 1, 0, 0
    else:
        tpM = int(bool(pred["ir_material"]) and pred["ir_material"] == gold["ir_material"])
        fpM = int(bool(pred["ir_material"]) and pred["ir_material"] != gold["ir_material"])
        fnM = int(bool(gold["ir_material"]) and pred["ir_material"] != gold["ir_material"])

    # ir_bands: direct string comparison after removing spaces; if both NA-like -> TP=1
    if is_na_like(pred.get("ir_bands_raw", "")) and is_na_like(gold.get("ir_bands_raw", "")):
        tpB, fpB, fnB = 1, 0, 0
    else:
        bands_match = bool(pred["ir_bands"]) and (pred["ir_bands"] == gold["ir_bands"])
        tpB = int(bands_match)
        fpB = int(bool(pred["ir_bands"]) and not bands_match)
        fnB = int(bool(gold["ir_bands"]) and not bands_match)

    # ea_formula: strict; if both NA-like -> TP=1
    if is_na_like(pred["ea_formula_raw"]) and is_na_like(gold["ea_formula_raw"]):
        tpF, fpF, fnF = 1, 0, 0
    else:
        tpF = int(bool(pred["ea_formula"]) and pred["ea_formula"] == gold["ea_formula"])
        fpF = int(bool(pred["ea_formula"]) and pred["ea_formula"] != gold["ea_formula"])
        fnF = int(bool(gold["ea_formula"]) and pred["ea_formula"] != gold["ea_formula"])

    # ea percentages: if both NA-like strings -> TP=1; else per-element exact at 2dp
    if is_na_like(pred["ea_calc_raw"]) and is_na_like(gold["ea_calc_raw"]):
        tpC, fpC, fnC = 1, 0, 0
    else:
        tpC, fpC, fnC = pct_counts(pred["ea_calc"], gold["ea_calc"])
    if is_na_like(pred["ea_exp_raw"]) and is_na_like(gold["ea_exp_raw"]):
        tpE, fpE, fnE = 1, 0, 0
    else:
        tpE, fpE, fnE = pct_counts(pred["ea_exp"],  gold["ea_exp"])

    return {
        "names": (tpN,fpN,fnN),
        "ir_material": (tpM,fpM,fnM),
        "ir_bands": (tpB,fpB,fnB),
        "ea_formula": (tpF,fpF,fnF),
        "ea_calc": (tpC,fpC,fnC),
        "ea_exp": (tpE,fpE,fnE),
    }

def main():
    ap = argparse.ArgumentParser(description="CCDC-anchored characterisation evaluator with NA-consistent scoring")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--file", help="Hash for evaluation/data/merged_tll/<hash>/characterisation.json")
    grp.add_argument("--pred")
    ap.add_argument("--gold")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.file:
        pred_path, gold_path = _resolve_paths_from_hash(args.file.strip())
    else:
        if not args.gold:
            raise SystemExit("--gold is required when --pred is used")
        pred_path, gold_path = args.pred, args.gold

    P = load_char_map(pred_path)
    G = load_char_map(gold_path)

    cc_pred, cc_gold = set(P), set(G)
    matched = sorted(cc_pred & cc_gold)
    missing = sorted(cc_gold - cc_pred)
    extra   = sorted(cc_pred - cc_gold)

    print(f"Ground truth: {len(cc_gold)} | Predicted: {len(cc_pred)}")
    print(f"CCDC match → TP={len(matched)} FP={len(extra)} FN={len(missing)}")

    totals = {k:(0,0,0) for k in ["names","ir_material","ir_bands","ea_formula","ea_calc","ea_exp"]}
    overall_tp=overall_fp=overall_fn=0

    # matched CCDC
    for c in matched:
        res = eval_one(P[c], G[c])
        for k,(tp,fp,fn) in res.items():
            a,b,cnt = totals[k]
            totals[k]=(a+tp, b+fp, cnt+fn)
            overall_tp += tp; overall_fp += fp; overall_fn += fn

    # missing in pred → all FN
    for c in missing:
        g=G[c]
        overall_fn += len(g["names"]);  t=totals["names"]; totals["names"]=(t[0],t[1],t[2]+len(g["names"]))
        # ir_material
        if not is_na_like(g["ir_material_raw"]):
            overall_fn += 1; t=totals["ir_material"]; totals["ir_material"]=(t[0],t[1],t[2]+1)
        else:
            overall_tp += 1; t=totals["ir_material"]; totals["ir_material"]=(t[0]+1,t[1],t[2])
        # ir_bands
        if not is_na_like(g.get("ir_bands_raw", "")):
            overall_fn += 1; t=totals["ir_bands"]; totals["ir_bands"]=(t[0],t[1],t[2]+1)
        else:
            overall_tp += 1; t=totals["ir_bands"]; totals["ir_bands"]=(t[0]+1,t[1],t[2])
        # ea_formula
        if not is_na_like(g["ea_formula_raw"]):
            overall_fn += 1; t=totals["ea_formula"]; totals["ea_formula"]=(t[0],t[1],t[2]+1)
        else:
            overall_tp += 1; t=totals["ea_formula"]; totals["ea_formula"]=(t[0]+1,t[1],t[2])
        # ea_calc
        if is_na_like(g["ea_calc_raw"]):
            overall_tp += 1; t=totals["ea_calc"]; totals["ea_calc"]=(t[0]+1,t[1],t[2])
        else:
            overall_fn += len(g["ea_calc"]); totals["ea_calc"]=(totals["ea_calc"][0],totals["ea_calc"][1],totals["ea_calc"][2]+len(g["ea_calc"]))
        # ea_exp
        if is_na_like(g["ea_exp_raw"]):
            overall_tp += 1; t=totals["ea_exp"]; totals["ea_exp"]=(t[0]+1,t[1],t[2])
        else:
            overall_fn += len(g["ea_exp"]); totals["ea_exp"]=(totals["ea_exp"][0],totals["ea_exp"][1],totals["ea_exp"][2]+len(g["ea_exp"]))

    # extra in pred → all FP
    for c in extra:
        p=P[c]
        overall_fp += len(p["names"]);  t=totals["names"]; totals["names"]=(t[0],t[1]+len(p["names"]),t[2])
        if not is_na_like(p["ir_material_raw"]):
            overall_fp += 1; t=totals["ir_material"]; totals["ir_material"]=(t[0],t[1]+1,t[2])
        else:
            overall_tp += 1; t=totals["ir_material"]; totals["ir_material"]=(t[0]+1,t[1],t[2])
        if not is_na_like(p.get("ir_bands_raw", "")):
            overall_fp += 1; t=totals["ir_bands"]; totals["ir_bands"]=(t[0],t[1]+1,t[2])
        else:
            overall_tp += 1; t=totals["ir_bands"]; totals["ir_bands"]=(t[0]+1,t[1],t[2])
        if not is_na_like(p["ea_formula_raw"]):
            overall_fp += 1; t=totals["ea_formula"]; totals["ea_formula"]=(t[0],t[1]+1,t[2])
        else:
            overall_tp += 1; t=totals["ea_formula"]; totals["ea_formula"]=(t[0]+1,t[1],t[2])
        if is_na_like(p["ea_calc_raw"]):
            overall_tp += 1; t=totals["ea_calc"]; totals["ea_calc"]=(t[0]+1,t[1],t[2])
        else:
            overall_fp += len(p["ea_calc"]); t=totals["ea_calc"]=(totals["ea_calc"][0],totals["ea_calc"][1]+len(p["ea_calc"]),totals["ea_calc"][2])
        if is_na_like(p["ea_exp_raw"]):
            overall_tp += 1; t=totals["ea_exp"]; totals["ea_exp"]=(t[0]+1,t[1],t[2])
        else:
            overall_fp += len(p["ea_exp"]); t=totals["ea_exp"]=(totals["ea_exp"][0],totals["ea_exp"][1]+len(p["ea_exp"]),totals["ea_exp"][2])

    # per category
    for k,(tp,fp,fn) in totals.items():
        Pk,Rk,Fk = prf(tp,fp,fn)
        print(f"{k}: TP={tp} FP={fp} FN={fn} | P={Pk:.3f} R={Rk:.3f} F1={Fk:.3f}")

    # overall
    P_all,R_all,F1_all = prf(overall_tp,overall_fp,overall_fn)
    print(f"Overall: TP={overall_tp} FP={overall_fp} FN={overall_fn} | P={P_all:.3f} R={R_all:.3f} F1={F1_all:.3f}")

    if args.debug:
        for c in matched:
            g, p = G[c], P[c]
            diffs=[]
            if p["names"] != g["names"]:
                diffs.append(f"names pred={sorted(p['names'])} gold={sorted(g['names'])}")
            if (not (is_na_like(p['ir_material_raw']) and is_na_like(g['ir_material_raw']))) and (p["ir_material"] != g["ir_material"]):
                diffs.append(f"ir_material pred='{p['ir_material_raw']}' gold='{g['ir_material_raw']}'")
            if not ((is_na_like(p.get('ir_bands_raw','')) and is_na_like(g.get('ir_bands_raw',''))) or (p["ir_bands"] == g["ir_bands"])):
                diffs.append(f"ir_bands pred='{p['ir_bands_raw']}' gold='{g['ir_bands_raw']}'")
            if (not (is_na_like(p['ea_formula_raw']) and is_na_like(g['ea_formula_raw']))) and (p["ea_formula"] != g["ea_formula"]):
                diffs.append(f"ea_formula pred='{p['ea_formula_raw']}' gold='{g['ea_formula_raw']}'")
            if not ((is_na_like(p['ea_calc_raw']) and is_na_like(g['ea_calc_raw'])) or pct_counts(p["ea_calc"], g["ea_calc"])==(len(g["ea_calc"]),0,0)):
                diffs.append(f"ea_calc pred='{p['ea_calc_raw']}' gold='{g['ea_calc_raw']}'")
            if not ((is_na_like(p['ea_exp_raw']) and is_na_like(g['ea_exp_raw'])) or pct_counts(p["ea_exp"], g["ea_exp"])==(len(g["ea_exp"]),0,0)):
                pred_raw = p['ea_exp_raw']
                gold_raw = g['ea_exp_raw']
                diffs.append(f"ea_exp pred='{pred_raw}' gold='{gold_raw}'")
            if diffs:
                print(f"- CCDC {c}: " + " | ".join(diffs))

# -------- Hash → file resolution (align with cbu_comparison) --------
REPO_ROOT = Path(__file__).resolve().parents[2]
MERGED_DIR = REPO_ROOT / "evaluation" / "data" / "merged_tll"
GT_DIR = REPO_ROOT / "earlier_ground_truth" / "characterisation"
DOI_HASH_MAP = REPO_ROOT / "data" / "doi_to_hash.json"


def _resolve_paths_from_hash(hash_value: str) -> Tuple[str, str]:
    pred = MERGED_DIR / hash_value / "characterisation.json"
    if not pred.exists():
        raise FileNotFoundError(f"Predicted not found: {pred}")
    # try doi map first
    if DOI_HASH_MAP.exists():
        try:
            m = json.loads(DOI_HASH_MAP.read_text(encoding="utf-8"))
            for doi, hv in m.items():
                if str(hv).strip() == hash_value:
                    gt = GT_DIR / (doi.replace("/", "_") + ".json")
                    if gt.exists():
                        return str(pred), str(gt)
        except Exception:
            pass
    # fallback: CCDC intersection
    try:
        p_map = load_char_map(str(pred))
        p_cc = set(p_map.keys())
    except Exception:
        p_cc = set()
    best = None
    for gt in GT_DIR.glob("*.json"):
        try:
            g_map = load_char_map(str(gt))
            g_cc = set(g_map.keys())
            if p_cc & g_cc:
                best = gt
                break
        except Exception:
            continue
    if not best:
        raise FileNotFoundError(f"Ground truth not found for hash {hash_value} in {GT_DIR}")
    return str(pred), str(best)

if __name__ == "__main__":
    main()
