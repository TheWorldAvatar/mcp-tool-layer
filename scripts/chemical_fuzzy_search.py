#!/usr/bin/env python3
import re
import json
import argparse
from pathlib import Path
from collections import Counter
from typing import List, Dict, Any

DERIVED = { "name": "4,4'-(Ethyne-1,2-diyl)dibenzoate (linker form, edb²⁻)", "formula": "C16H8O4", 
 "mol_weight": 264.23, "smiles": "O=C([O-])c1ccc(cc1)C#Cc2ccc(cc2)C(=O)[O-]", 
 "inchi": "InChI=1S/C16H8O4/c17-15(18)11-3-7-13(8-4-11)1-2-14-9-5-12(6-10-14)16(19)20/h1-10H/p-2", 
 "inchikey": "XQZJZKJZQXJZKJ-UHFFFAOYSA-L", }

def load_kg_labels_from_json(json_file_path: str = "data/ontomop_extraction/all_formulas.json") -> List[Dict[str, Any]]:
    """
    Load KG labels from the extracted JSON files.
    
    Args:
        json_file_path (str): Path to the JSON file containing formula data
        
    Returns:
        List[Dict]: List of KG entities with their metadata
    """
    kg_entities = []
    
    try:
        formula_file = Path(json_file_path)
        if formula_file.exists():
            with open(formula_file, 'r', encoding='utf-8') as f:
                formula_data = json.load(f)
                for item in formula_data:
                    formula = item.get('formula', '').strip()
                    if formula:  # Only include non-empty formulas
                        kg_entities.append({
                            "label": formula,
                            "subject": item.get('subject', ''),
                            "subjectType": item.get('subjectType', ''),
                            "formulaType": item.get('formulaType', ''),
                        })
        else:
            raise FileNotFoundError(f"Warning: JSON file not found at {json_file_path}") 
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        return []
    
    return kg_entities

# ---- atomic weights (avg) ----
AW = {
    "H":1.00794,"C":12.0107,"N":14.0067,"O":15.9994,"F":18.9984032,"P":30.973762,
    "S":32.065,"Cl":35.453,"Br":79.904,"I":126.90447,"Cu":63.546,"Mo":95.95,
    "Co":58.933,"V":50.9415,"Pd":106.42
}

HILL_ORDER = [
    "C","H","N","O","F","P","S","Cl","Br","I",
    "B","Si","Se","Sb","Te","Mo","Co","V","Cu","Pd"
]

ELEM_RE = re.compile(r"([A-Z][a-z]?)(\d*)")
GROUP_RE = re.compile(r"\(([^()]+)\)(\d*)")  # (content)mult

def sum_atoms(s: str) -> Counter:
    atoms = Counter()
    for el, num in ELEM_RE.findall(s):
        atoms[el] += int(num) if num else 1
    return atoms

def scale_counter(counter: Counter, k: int) -> Counter:
    return Counter({el: n * k for el, n in counter.items()})

def parse_kg_label_atoms(label: str) -> Counter:
    s = label.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    atoms = Counter()
    consumed = []
    for content, mult in GROUP_RE.findall(s):
        mult = int(mult) if mult else 1
        atoms += scale_counter(sum_atoms(content), mult)
        consumed.append(f"({content}){mult if mult!=1 else ''}")
    leftover = s
    for c in consumed:
        leftover = leftover.replace(c, "")
    leftover = leftover.strip()
    if leftover:
        atoms += sum_atoms(leftover)
    return atoms

def hill_string(atoms: Counter) -> str:
    parts = []
    rest = atoms.copy()
    c = rest.pop("C", 0); h = rest.pop("H", 0)
    if c: parts.append("C" + (str(c) if c>1 else ""))
    if h: parts.append("H" + (str(h) if h>1 else ""))
    for e in HILL_ORDER:
        if e in ("C","H"): continue
        n = rest.pop(e, 0)
        if n: parts.append(e + (str(n) if n>1 else ""))
    for e in sorted(rest):
        n = rest[e]
        if n: parts.append(e + (str(n) if n>1 else ""))
    return "".join(parts) if parts else ""

def mol_weight(atoms: Counter) -> float:
    return round(sum(AW.get(e, 0.0)*n for e,n in atoms.items()), 4)

def score_candidate(kg_atoms: Counter, derived) -> dict:
    kg_formula = hill_string(kg_atoms)
    kg_mw = mol_weight(kg_atoms)
    target_mw = derived["mol_weight"]
    formula_match = (kg_formula == derived["formula"])
    rel_err = abs(kg_mw - target_mw) / target_mw if target_mw else 1.0
    formula_score = 6 if formula_match else 0
    if   rel_err <= 0.003: mw_score = 4
    elif rel_err <= 0.007: mw_score = 3
    elif rel_err <= 0.015: mw_score = 2
    elif rel_err <= 0.03:  mw_score = 1
    else:                  mw_score = 0
    return {
        "kg_formula": kg_formula,
        "kg_mw": kg_mw,
        "formula_match": formula_match,
        "mw_rel_err": round(rel_err, 6),
        "score": formula_score + mw_score,
    }

def main():
    parser = argparse.ArgumentParser(description='Chemical fuzzy search against KG database')
    parser.add_argument('--json-file', '-j', 
                       default='data/ontomop_extraction/all_formulas.json',
                       help='Path to JSON file containing chemical formulas')
    parser.add_argument('--limit', '-l', type=int, default=20,
                       help='Number of top results to display (default: 20)')
    parser.add_argument('--target-formula', '-t',
                       help='Target chemical formula to match against (overrides default)')
    parser.add_argument('--target-mw', '-m', type=float,
                       help='Target molecular weight (required if using custom target formula)')
    parser.add_argument('--strict', '-s', action='store_true',
                       help='Strict mode: only show results where molecular formula matches exactly')
    
    args = parser.parse_args()
    
    # Use custom target if provided
    target_data = DERIVED
    if args.target_formula:
        if not args.target_mw:
            print("Error: --target-mw is required when using --target-formula")
            return
        target_data = {
            "name": "Custom Target",
            "formula": args.target_formula,
            "mol_weight": args.target_mw
        }
    
    print("Multi-aspect match against KG sample\n")
    print(f"Target: {target_data['formula']}  MW={target_data['mol_weight']}")
    if args.strict:
        print("Mode: STRICT (only exact molecular formula matches)")
    else:
        print("Mode: FUZZY (all results ranked by similarity)")
    print()

    # Load KG labels from JSON file
    kg_entities = load_kg_labels_from_json(args.json_file)
    print(f"Loaded {len(kg_entities)} chemical entities from JSON\n")

    if not kg_entities:
        print("No entities loaded. Exiting.")
        return

    results = []
    filtered_count = 0
    for entity in kg_entities:
        label = entity["label"]
        try:
            atoms = parse_kg_label_atoms(label)
            res = score_candidate(atoms, target_data)
            
            # Apply strict filtering if requested
            if args.strict and not res["formula_match"]:
                filtered_count += 1
                continue  # Skip results that don't have exact formula match
                
            results.append((entity, atoms, res))
        except Exception as e:
            print(f"Warning: Failed to parse {label}: {e}")
            continue

    results.sort(key=lambda r: (-r[2]["score"], r[2]["mw_rel_err"]))

    # Show top N results
    top_results = results[:args.limit]
    
    for i, (entity, atoms, res) in enumerate(top_results, 1):
        print(f"#{i} KG label: {entity['label']}")
        print(f"   Subject URI : {entity['subject']}")
        print(f"   Subject Type: {entity['subjectType'].split('/')[-1] if entity['subjectType'] else 'N/A'}")
        print(f"   Formula Type: {entity['formulaType'].split('/')[-1] if entity['formulaType'] else 'N/A'}")
        print(f"   atoms       : {dict(atoms)}")
        print(f"   kg_formula  : {res['kg_formula']} | match={res['formula_match']}")
        print(f"   kg_MW       : {res['kg_mw']} | rel_err={res['mw_rel_err']}")
        print(f"   total_score : {res['score']}")
        print("-"*80)
        
    # Summary message
    if args.strict:
        total_analyzed = len(results) + filtered_count
        print(f"\nShowing top {args.limit} results out of {len(results)} exact matches")
        print(f"(Filtered out {filtered_count} non-matching entities from {total_analyzed} total)")
    else:
        print(f"\nShowing top {args.limit} results out of {len(results)} total entities analyzed")

if __name__ == "__main__":
    main()
