#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single-format SMILES → canonical pipeline for CBUs.

Guarantee:
- Outputs exactly ONE chemical format: RDKit canonical SMILES of the deprotonated form
  with all carboxylic acids forced to COO- (no neutral COOH, no KG placeholders).
- No alternate renderings. No UI-only strings.

Assumptions covered (based on typical CBU CSVs):
- Inputs may contain KG placeholders "[O]" for carboxylate sites. Treated as "[O-]".
- Inputs may be neutral COOH or already deprotonated COO-.
- Inputs may include salts/solvents or multiple fragments. We keep the parent (FragmentParent).
- Inputs may include tautomers or aromatic variants. We canonicalize tautomers.
- Inputs may be messy but parseable by RDKit; we sanitize with safe fallbacks.
- Stereochemistry is ignored for canonicalization (isomericSmiles=False) for consistency.

Output columns (CSV/TSV):
- input_smiles, source_kind (kg-placeholder|neutral|anion|unknown),
  canonical_smiles (the ONLY format we emit), inchikey (if RDKit InChI present),
  rdkit_can_hash16 (SHA-256 of canonical for extra anchoring), error (empty if ok).

Usage:
  python cbu_canonical.py --input CBUs.csv --smiles-col smiles > out.csv
  python cbu_canonical.py --input CBUs.csv --smiles-col smiles --tsv > out.tsv
"""

import sys
import csv
import re
import argparse
import hashlib
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.MolStandardize import rdMolStandardize as ST

# Optional InChI
try:
    from rdkit.Chem import inchi as RDInchi
    _HAS_INCHI = True
except Exception:
    RDInchi = None
    _HAS_INCHI = False

# Reactions: COOH <-> COO- (carboxyl only; this is the intended CBU acid group)
RXN_COOH_TO_COO = AllChem.ReactionFromSmarts('[CX3:1](=O)[OX2H:2]>>[CX3:1](=O)[O-:2]')

def _get_tautomer_tool():
    for name in ("TautomerCanonicalizer", "TautomerEnumerator"):
        tool = getattr(ST, name, None)
        if tool is not None:
            try:
                obj = tool()
                if hasattr(obj, "Canonicalize"):
                    return obj
            except Exception:
                pass
    return None

_TAUT_TOOL = _get_tautomer_tool()

def _apply_reaction_until_stable(m: Chem.Mol, rxn) -> Chem.Mol:
    while True:
        prods = rxn.RunReactants((m,))
        if not prods:
            break
        m = Chem.Mol(prods[0][0])
        Chem.SanitizeMol(m)
    return m

def _sanitize(m: Chem.Mol) -> Chem.Mol:
    try:
        Chem.SanitizeMol(m)
        return m
    except Exception:
        try:
            Chem.Kekulize(m, clearAromaticFlags=True)
        except Exception:
            pass
        Chem.SanitizeMol(m, catchErrors=True)
        return m

def _normalize_core(m: Chem.Mol) -> Chem.Mol:
    m = ST.Cleanup(m)
    m = ST.FragmentParent(m)   # parent fragment only
    m = ST.Normalize(m)
    m = ST.Reionize(m)
    m = _sanitize(m)
    return m

def _canonicalize_tautomer(m: Chem.Mol) -> Chem.Mol:
    if _TAUT_TOOL is None:
        return m
    try:
        return _TAUT_TOOL.Canonicalize(m)
    except Exception:
        return m

def _mol_from_smiles(s: str) -> Chem.Mol:
    if s is None:
        raise ValueError("Empty SMILES")
    s = s.strip()
    if not s:
        raise ValueError("Empty SMILES")
    m = Chem.MolFromSmiles(s)
    if m is None:
        raise ValueError(f"Invalid SMILES: {s}")
    return _sanitize(m)

def _to_storage_canonical(smiles_any: str):
    """
    Returns:
      canonical_smiles (COO- enforced; the ONLY output format),
      inchikey_or_none,
      source_kind: 'kg-placeholder' | 'neutral' | 'anion' | 'unknown'
    """
    # 1) Normalize KG placeholders: treat "[O]" as carboxylate site -> "[O-]"
    src = "unknown"
    if "[O]" in (smiles_any or ""):
        src = "kg-placeholder"
        s = smiles_any.replace("[O]", "[O-]")
    else:
        s = smiles_any or ""

    # 2) Parse
    m = _mol_from_smiles(s)

    # 3) RDKit standardization
    m = _normalize_core(m)

    # 4) Heuristic source tag if unknown
    txt = Chem.MolToSmiles(m, canonical=False)
    if src == "unknown":
        if "C(=O)O" in txt and "[O-]" not in txt:
            src = "neutral"
        elif "[O-]" in txt:
            src = "anion"

    # 5) Force COO- (single canonical chemistry format)
    m = _apply_reaction_until_stable(m, RXN_COOH_TO_COO)

    # 6) Tautomer canonicalization
    m = _canonicalize_tautomer(m)

    # 7) Final RDKit canonical (single format we expose)
    can = Chem.MolToSmiles(m, canonical=True, isomericSmiles=False, kekuleSmiles=False)

    # 8) InChIKey if available
    ik = None
    if _HAS_INCHI:
        try:
            ik = RDInchi.MolToInchiKey(m)
        except Exception:
            ik = None

    return can, ik, src

def _process_row(smiles: str):
    try:
        canonical, inchikey, src = _to_storage_canonical(smiles)
        rdkit_hash16 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return {
            "input_smiles": smiles,
            "source_kind": src,
            "canonical_smiles": canonical,       # THE ONLY FORMAT WE OUTPUT
            "inchikey": inchikey or "",
            "rdkit_can_hash16": rdkit_hash16,
            "error": "",
        }
    except Exception as e:
        return {
            "input_smiles": smiles,
            "source_kind": "",
            "canonical_smiles": "",
            "inchikey": "",
            "rdkit_can_hash16": "",
            "error": str(e),
        }

def main():
    ap = argparse.ArgumentParser(description="CBU SMILES → single canonical (COO- enforced).")
    ap.add_argument("--input", required=True, help="Input CSV file path.")
    ap.add_argument("--smiles-col", required=True, help="Column name containing SMILES.")
    ap.add_argument("--output", default=None, help="Output file path (default: stdout).")
    ap.add_argument("--tsv", action="store_true", help="Write TSV instead of CSV.")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"Input file not found: {in_path}")

    # Open input
    with in_path.open("r", encoding="utf-8", newline="") as fin:
        reader = csv.DictReader(fin)
        if args.smiles_col not in reader.fieldnames:
            raise SystemExit(f"--smiles-col '{args.smiles_col}' not in CSV header: {reader.fieldnames}")

        out_fields = list(reader.fieldnames)
        # Ensure output fields present; we always write the canonical_smiles only (plus anchors)
        for col in ["source_kind","canonical_smiles","inchikey","rdkit_can_hash16","error"]:
            if col not in out_fields:
                out_fields.append(col)

        if args.output:
            fout = open(args.output, "w", encoding="utf-8", newline="")
            close_out = True
        else:
            fout = sys.stdout
            close_out = False

        try:
            writer = csv.DictWriter(fout, fieldnames=out_fields, delimiter=("\t" if args.tsv else ","), lineterminator="\n")
            writer.writeheader()

            for row in reader:
                smiles = (row.get(args.smiles_col) or "").strip()
                rec = _process_row(smiles)
                row.update(rec)
                writer.writerow(row)
        finally:
            if close_out:
                fout.close()

if __name__ == "__main__":
    main()
