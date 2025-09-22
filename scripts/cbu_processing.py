#!/usr/bin/env python3
"""
Normalize neutral linker SMILES to a deprotonated linker anion and emit identifiers + MOF/MOP core label.

- Acid sites handled for deprotonation: carboxylic, sulfonic, phosphonic.
- Labeling modes:
  * aggregated:     [(C{…}H{…}{hetero…})(CO2){m}]
  * ring-factored:  [ (C6H{k}{C^b?})_{r} (hetero…) (CO2){m} ]
  * auto: prefer ring-factored only when it carries extra carbon info (b>0); else aggregated.
- Hetero elements (e.g., N/S/P) are preserved in the core label.

Usage:
  python linker_normalize.py --smiles "C1=CC(=C(C=C1C(=O)O)N=NC2=CC(=CC(=C2)C(=O)O)C(=O)O)C(=O)O"
  # Options: --remove-k N  (remove exactly N acidic H; default: remove all)
  #          --label-mode auto|aggregated|ring
  #          --json
"""

import argparse, re, sys, json
from rdkit import Chem
from rdkit.Chem import inchi, rdMolDescriptors as Descr
from rdkit import RDLogger

# Silence RDKit chatter
RDLogger.DisableLog("rdApp.*")

# ---------------- acid-site detection ----------------
ACID_SMARTS = [
    ("carboxylic", Chem.MolFromSmarts("[CX3](=O)[OX2H1]")),       # –C(=O)OH
    ("sulfonic",   Chem.MolFromSmarts("[SX4](=O)(=O)[OX2H1]")),   # –SO2OH
    ("phosphonic", Chem.MolFromSmarts("[PX4](=O)[OX2H1]")),       # –P(=O)OH
]

def find_acid_OH_atoms(mol):
    """Return sorted O-atom indices of acidic OH groups."""
    O_idxs = set()
    for _, patt in ACID_SMARTS:
        for match in mol.GetSubstructMatches(patt):
            O_idxs.add(match[-1])  # OH oxygen is last in the patterns above
    return sorted(O_idxs)

def deprotonate(mol, k=None):
    """Convert up to k acidic OH → O−. If k=None, remove all. Returns (mol_out, removed)."""
    rmol = Chem.RWMol(mol)
    O_sites = find_acid_OH_atoms(rmol)
    if k is None:
        k = len(O_sites)
    removed = 0
    for oi in O_sites:
        if removed >= k:
            break
        O = rmol.GetAtomWithIdx(oi)
        O.SetFormalCharge(-1)
        O.SetNoImplicit(True)
        O.SetNumExplicitHs(0)  # drop the OH proton
        O.UpdatePropertyCache()
        removed += 1
    mol_out = rmol.GetMol()
    Chem.SanitizeMol(mol_out)
    return mol_out, removed

# ---------------- identifiers and properties ----------------
def std_inchi(mol):    return inchi.MolToInchi(mol)
def inchikey(s_inchi): return inchi.InchiToInchiKey(s_inchi)
def can_smiles(mol):   return Chem.MolToSmiles(mol, canonical=True)

def mol_formula(mol, with_charge=True):
    """
    RDKit CalcMolFormula may append charge (e.g., C16H6N2O8-4).
    Strip any trailing charge, then append Chem.GetFormalCharge(mol) if requested.
    """
    base = Descr.CalcMolFormula(mol)
    m = re.match(r"^(.*?)([+\-]\d+)?$", base)
    base_noq = m.group(1)
    if not with_charge:
        return base_noq
    q = Chem.GetFormalCharge(mol)
    return base_noq if q == 0 else f"{base_noq}{'+' if q > 0 else ''}{q}"

def exact_mw(mol):     return Descr.CalcExactMolWt(mol)

# ---------------- element helpers ----------------
ALLOWED = {"C","H","O","N","S","P"}  # tolerate typical heteroatoms in linkers

def parse_formula(formula_str):
    """Parse elemental counts; ignore trailing charge like -4 or +2."""
    m = re.match(r"^(.*?)([+\-]\d+)?$", formula_str)
    f = m.group(1)
    pairs = re.findall(r"([A-Z][a-z]?)(\d*)", f)
    d = {}
    for el, n in pairs:
        d[el] = d.get(el, 0) + (int(n) if n else 1)
    return d

def tok(el, n):
    if n <= 0: return ""
    return el if n == 1 else f"{el}{n}"

def _fmt_reduce_one(s):
    return s.replace("C1","C").replace("H1","H").replace("(CO2)1","(CO2)")

def _hetero_tokens(els):
    parts = []
    for el in sorted(els.keys()):
        if el not in ("C","H","O"):
            t = tok(el, els[el])
            if t: parts.append(t)
    return "".join(parts)

# ---------------- MOF/MOP core labelers ----------------
def label_aggregated_aryl_diCO2(formula_noq):
    """
    Build [(C{C’}H{H’}{hetero…})(CO2){m}] where:
      m  = O/2
      C’ = C - m
      H’ = H
      hetero from all non-CHO elements.
    """
    els = parse_formula(formula_noq)
    if any(k not in ALLOWED for k in els):
        return None
    C, H, O = els.get("C",0), els.get("H",0), els.get("O",0)
    if O % 2:
        return None
    m = O // 2
    Cprime, Hprime = C - m, H
    if Cprime <= 0 or Hprime < 0:
        return None
    hetero = _hetero_tokens(els)  # add e.g., N2, S, P
    core = _fmt_reduce_one(f"(C{Cprime}H{Hprime}{hetero})")
    co2  = _fmt_reduce_one(f"(CO2){m}")
    return f"[{core}{co2}]"

def label_ring_factored_aryl_diCO2(formula_noq):
    """
    Try ring factoring: head = (C6H{k}{C^b?})_{r}, extras = (hetero…), tail = (CO2){m}
    Returns (label, b) or (None, None). b>0 means extra carbon per ring beyond 6.
    """
    els = parse_formula(formula_noq)
    if any(k not in ALLOWED for k in els):
        return (None, None)
    C, H, O = els.get("C",0), els.get("H",0), els.get("O",0)
    if O % 2:
        return (None, None)
    m = O // 2
    Cprime, Hprime = C - m, H
    best = (None, None)
    hetero = _hetero_tokens(els)
    extras = "" if hetero == "" else f"({hetero})"
    for r in (2, 3, 1, 4):
        if r == 0 or Cprime - 6*r < 0 or Hprime % r != 0:
            continue
        k = Hprime // r
        if not (0 <= k <= 6):
            continue
        b_num = Cprime - 6*r
        if b_num % r != 0:
            continue
        b = b_num // r
        # Build ring head
        ring_core = f"C6H{k}"
        if b == 1:
            ring_core += "C"
        elif b > 1:
            ring_core += f"C{b}"
        ring = f"({ring_core})"
        head = ring if r == 1 else f"{ring}_{r}"
        co2  = _fmt_reduce_one(f"(CO2){m}")
        label = f"[{head}{extras}{co2}]"
        if best[0] is None or (b > (best[1] or 0)):
            best = (label, b)
    return best

def choose_core_label(formula_noq, mode="auto"):
    agg = label_aggregated_aryl_diCO2(formula_noq)
    ring, b = label_ring_factored_aryl_diCO2(formula_noq)
    mode = mode.lower()
    if mode == "aggregated": return agg
    if mode == "ring":       return ring
    # auto
    if ring and b and b > 0:
        return ring
    return agg

# ---------------- pipeline ----------------
def normalize_linker_from_smiles(smiles_neutral, remove_k=None, label_mode="auto"):
    m0 = Chem.MolFromSmiles(smiles_neutral)
    if m0 is None:
        raise ValueError("Invalid SMILES.")
    Chem.SanitizeMol(m0)

    m1, removed = deprotonate(m0, k=remove_k)  # if remove_k=None: remove all acidic H
    s_inchi = std_inchi(m1)
    formula_noq = mol_formula(m1, with_charge=False)
    out = {
        "removed_H": removed,
        "smiles": can_smiles(m1),
        "std_inchi": s_inchi,
        "inchikey": inchikey(s_inchi),
        "formula": mol_formula(m1, with_charge=True),
        "exact_mw": exact_mw(m1),
        "core_label": choose_core_label(formula_noq, mode=label_mode),
    }
    return out

# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smiles", required=True, help="Neutral linker SMILES")
    ap.add_argument("--remove-k", type=int, default=None, help="Number of acidic H to remove; default=all acidic")
    ap.add_argument("--label-mode", choices=["auto","aggregated","ring"], default="auto")
    ap.add_argument("--json", action="store_true", help="Print JSON only")
    args = ap.parse_args()

    try:
        res = normalize_linker_from_smiles(args.smiles, remove_k=args.remove_k, label_mode=args.label_mode)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for k,v in res.items():
            print(f"{k}: {v}")

if __name__ == "__main__":
    main()
