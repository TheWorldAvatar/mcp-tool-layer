#!/usr/bin/env python3
"""
Chemical fuzzy search functionality for OntoMOP entities based on molecular formula and weight matching.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import Counter

# ---- atomic weights (avg) ----
ATOMIC_WEIGHTS = {
    "H": 1.00794, "C": 12.0107, "N": 14.0067, "O": 15.9994, "F": 18.9984032, "P": 30.973762,
    "S": 32.065, "Cl": 35.453, "Br": 79.904, "I": 126.90447, "Cu": 63.546, "Mo": 95.95,
    "Co": 58.933, "V": 50.9415, "Pd": 106.42, "B": 10.811, "Si": 28.0855, "Se": 78.96,
    "Sb": 121.760, "Te": 127.60, "Al": 26.9815386, "Ca": 40.078, "Fe": 55.845, "Ni": 58.6934,
    "Zn": 65.38, "Ag": 107.8682, "Cd": 112.411, "Sn": 118.710, "Pb": 207.2, "Mn": 54.938045,
    "Cr": 51.9961, "Ti": 47.867, "Zr": 91.224, "Hf": 178.49, "Nb": 92.90638, "Ta": 180.94788,
    "W": 183.84, "Re": 186.207, "Os": 190.23, "Ir": 192.217, "Pt": 195.084, "Au": 196.966569,
    "Hg": 200.59, "Tl": 204.3833, "Bi": 208.98040, "Po": 208.982, "At": 209.987, "Rn": 222.018,
    "Li": 6.941, "Be": 9.012182, "Na": 22.98976928, "Mg": 24.305, "K": 39.0983, "Rb": 85.4678,
    "Cs": 132.9054519, "Ba": 137.327, "Ra": 226.025
}

# Hill notation ordering
HILL_ORDER = [
    "C", "H", "N", "O", "F", "P", "S", "Cl", "Br", "I",
    "B", "Si", "Se", "Sb", "Te", "Mo", "Co", "V", "Cu", "Pd",
    "Al", "Ca", "Fe", "Ni", "Zn", "Ag", "Cd", "Sn", "Pb", "Mn",
    "Cr", "Ti", "Zr", "Hf", "Nb", "Ta", "W", "Re", "Os", "Ir",
    "Pt", "Au", "Hg", "Tl", "Bi", "Po", "At", "Rn", "Li", "Be",
    "Na", "Mg", "K", "Rb", "Cs", "Ba", "Ra"
]

# Regular expressions for parsing
ELEM_RE = re.compile(r"([A-Z][a-z]?)(\d*)")
GROUP_RE = re.compile(r"\(([^()]+)\)(\d*)")  # (content)mult


def sum_atoms(s: str) -> Counter:
    """Sum up all atoms in a formula string."""
    atoms = Counter()
    for el, num in ELEM_RE.findall(s):
        atoms[el] += int(num) if num else 1
    return atoms


def scale_counter(counter: Counter, k: int) -> Counter:
    """Scale all counts in a counter by factor k."""
    return Counter({el: n * k for el, n in counter.items()})


def parse_kg_label_atoms(label: str) -> Counter:
    """
    Parse a KG label to extract atomic composition.
    
    Args:
        label (str): Chemical formula/label (e.g., "[CuCl2(C5H3N)2(CO2)2]")
        
    Returns:
        Counter: Dictionary of element -> count
    """
    s = label.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    
    atoms = Counter()
    consumed = []
    
    # Handle grouped formulas like (C5H3N)2
    for content, mult in GROUP_RE.findall(s):
        mult = int(mult) if mult else 1
        atoms += scale_counter(sum_atoms(content), mult)
        consumed.append(f"({content}){mult if mult != 1 else ''}")
    
    # Remove consumed groups from the string
    leftover = s
    for c in consumed:
        leftover = leftover.replace(c, "")
    leftover = leftover.strip()
    
    # Parse remaining ungrouped elements
    if leftover:
        atoms += sum_atoms(leftover)
    
    return atoms


def hill_string(atoms: Counter) -> str:
    """
    Convert atomic composition to Hill notation formula.
    
    Args:
        atoms (Counter): Dictionary of element -> count
        
    Returns:
        str: Formula in Hill notation (C and H first, then alphabetical)
    """
    parts = []
    rest = atoms.copy()
    
    # C and H come first in Hill notation
    c = rest.pop("C", 0)
    h = rest.pop("H", 0)
    if c:
        parts.append("C" + (str(c) if c > 1 else ""))
    if h:
        parts.append("H" + (str(h) if h > 1 else ""))
    
    # Then elements in Hill order
    for e in HILL_ORDER:
        if e in ("C", "H"):
            continue
        n = rest.pop(e, 0)
        if n:
            parts.append(e + (str(n) if n > 1 else ""))
    
    # Finally, any remaining elements alphabetically
    for e in sorted(rest):
        n = rest[e]
        if n:
            parts.append(e + (str(n) if n > 1 else ""))
    
    return "".join(parts) if parts else ""


def calculate_molecular_weight(atoms: Counter) -> float:
    """
    Calculate molecular weight from atomic composition.
    
    Args:
        atoms (Counter): Dictionary of element -> count
        
    Returns:
        float: Molecular weight in g/mol
    """
    return round(sum(ATOMIC_WEIGHTS.get(e, 0.0) * n for e, n in atoms.items()), 4)


def score_candidate(kg_atoms: Counter, target_derived: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score a KG candidate against target derived properties.
    
    Args:
        kg_atoms (Counter): Atomic composition of KG entity
        target_derived (Dict): Target derived properties with formula, mol_weight, etc.
        
    Returns:
        Dict: Scoring results with formula match, weight error, and total score
    """
    kg_formula = hill_string(kg_atoms)
    kg_mw = calculate_molecular_weight(kg_atoms)
    target_mw = target_derived["mol_weight"]
    
    # Check exact formula match
    formula_match = (kg_formula == target_derived["formula"])
    
    # Calculate relative weight error
    rel_err = abs(kg_mw - target_mw) / target_mw if target_mw else 1.0
    
    # Scoring system
    formula_score = 6 if formula_match else 0
    
    if rel_err <= 0.003:
        mw_score = 4
    elif rel_err <= 0.007:
        mw_score = 3
    elif rel_err <= 0.015:
        mw_score = 2
    elif rel_err <= 0.03:
        mw_score = 1
    else:
        mw_score = 0
    
    return {
        "kg_formula": kg_formula,
        "kg_mw": kg_mw,
        "formula_match": formula_match,
        "mw_rel_err": round(rel_err, 6),
        "score": formula_score + mw_score,
        "formula_score": formula_score,
        "mw_score": mw_score
    }


def load_kg_labels_from_json() -> List[Dict[str, Any]]:
    """
    Load KG labels from the extracted JSON files.
    
    This function loads and filters chemical entities from three JSON files:
    1. all_formulas.json - Chemical formulas for building units
    2. all_labels.json - Entity labels that may contain chemical formulas  
    3. all_identifiers.json - Entity identifiers that may contain chemical information
    
    Returns:
        List[Dict]: List of KG entities with their metadata, filtered to include
                   only entries that appear to contain chemical formulas
    """
    kg_entities = []
    
    try:
        # Load formulas - these are always chemical formulas so include all
        formula_file = Path("data/ontomop_extraction/all_formulas.json")
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
                            "data_type": "formula"
                        })
        
        # Load labels - filter to those that look like chemical formulas
        labels_file = Path("data/ontomop_extraction/all_labels.json")
        if labels_file.exists():
            with open(labels_file, 'r', encoding='utf-8') as f:
                labels_data = json.load(f)
                for item in labels_data:
                    label = item.get('label', '').strip()
                    # Include labels that contain chemical formula indicators
                    # Look for brackets, parentheses, and common chemical elements
                    if label and any(char in label for char in ['[', ']', '(', ')', 'C', 'H', 'N', 'O', 'S', 'P']):
                        # Additional filtering to avoid non-chemical labels
                        if not any(word in label.lower() for word in ['unit', 'per', 'gram', 'mole', 'charge', 'angstrom']):
                            kg_entities.append({
                                "label": label,
                                "subject": item.get('subject', ''),
                                "subjectType": item.get('subjectType', ''),
                                "data_type": "label"
                            })
        
        # Load identifiers - filter to those that might contain chemical formulas
        identifiers_file = Path("data/ontomop_extraction/all_identifiers.json")
        if identifiers_file.exists():
            with open(identifiers_file, 'r', encoding='utf-8') as f:
                identifiers_data = json.load(f)
                for item in identifiers_data:
                    identifier = item.get('identifier', '').strip()
                    # Include identifiers that look like they contain chemical formulas
                    # Be more selective here since most identifiers are likely not chemical formulas
                    if identifier and any(char in identifier for char in ['[', ']', '(', ')']):
                        # Also require at least one common element
                        if any(elem in identifier for elem in ['C', 'H', 'N', 'O', 'Cu', 'Fe', 'Ni']):
                            kg_entities.append({
                                "label": identifier,
                                "subject": item.get('subject', ''),
                                "subjectType": item.get('subjectType', ''),
                                "data_type": "identifier"
                            })
                        
    except FileNotFoundError as e:
        print(f"Data file not found: {e}")
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON file: {e}")
    except Exception as e:
        print(f"Error loading KG labels from JSON: {e}")
    
    return kg_entities


def chemical_fuzzy_search(
    target_name: str,
    target_formula: str,
    target_mol_weight: float,
    target_smiles: Optional[str] = None,
    target_inchi: Optional[str] = None,
    target_inchikey: Optional[str] = None,
    limit: int = 10,
    strict: bool = False
) -> List[Dict[str, Any]]:
    """
    Perform chemical fuzzy search against KG entities using formula and molecular weight matching.
    
    Args:
        target_name (str): Name of the target compound
        target_formula (str): Target molecular formula (e.g., "C16H10O4")
        target_mol_weight (float): Target molecular weight in g/mol
        target_smiles (str, optional): Target SMILES string
        target_inchi (str, optional): Target InChI string
        target_inchikey (str, optional): Target InChI key
        limit (int): Maximum number of results to return
        strict (bool): If True, only return entities with exact molecular formula match
        
    Returns:
        List[Dict]: Sorted list of matching entities with scores and metadata
    """
    try:
        # Define target derived properties
        target_derived = {
            "name": target_name,
            "formula": target_formula,
            "mol_weight": target_mol_weight,
            "smiles": target_smiles or "",
            "inchi": target_inchi or "",
            "inchikey": target_inchikey or ""
        }
        
        # Load KG labels from JSON files
        kg_entities = load_kg_labels_from_json()
        
        if not kg_entities:
            return []
        
        # Score each candidate
        results = []
        for entity in kg_entities:
            label = entity.get('label', '')
            if not label:
                continue
                
            try:
                # Parse atomic composition from label
                atoms = parse_kg_label_atoms(label)
                if not atoms:  # Skip if no atoms found
                    continue
                    
                # Score the candidate
                score_result = score_candidate(atoms, target_derived)
                
                # Apply strict filtering if requested
                if strict and not score_result.get("formula_match", False):
                    continue  # Skip results that don't have exact formula match
                
                # Create result entry
                result = {
                    "kg_label": label,
                    "subject": entity.get('subject', ''),
                    "subjectType": entity.get('subjectType', ''),
                    "data_type": entity.get('data_type', ''),
                    "atoms": dict(atoms),
                    **score_result,
                    "target_name": target_name,
                    "target_formula": target_formula,
                    "target_mol_weight": target_mol_weight
                }
                results.append(result)
                
            except Exception as e:
                # Skip entities that can't be parsed
                continue
        
        # Sort by score (descending) and molecular weight error (ascending)
        results.sort(key=lambda r: (-r["score"], r["mw_rel_err"]))
        
        return results[:limit]
        
    except Exception as e:
        print(f"Error in chemical fuzzy search: {e}")
        return []


if __name__ == "__main__":
    # Test data availability
    print("Chemical Fuzzy Search Module")
    print("=" * 50)
    
    # Check if data files exist
    data_files = [
        "data/ontomop_extraction/all_formulas.json",
        "data/ontomop_extraction/all_labels.json", 
        "data/ontomop_extraction/all_identifiers.json"
    ]
    
    print("Checking data file availability:")
    for file_path in data_files:
        file_obj = Path(file_path)
        status = "✓ Found" if file_obj.exists() else "✗ Missing"
        print(f"  {status}: {file_path}")
    
    print()
    
    # Load and show basic statistics from JSON files
    try:
        kg_entities = load_kg_labels_from_json()
        print(f"Loaded {len(kg_entities)} KG entities from JSON files")
        
        # Show breakdown by data type
        data_type_counts = {}
        for entity in kg_entities:
            dt = entity.get('data_type', 'unknown')
            data_type_counts[dt] = data_type_counts.get(dt, 0) + 1
        
        print("Entity breakdown by data type:")
        for data_type, count in data_type_counts.items():
            print(f"  {data_type}: {count}")
            
        # Show some example entities
        print(f"\nExample entities (first 5):")
        for i, entity in enumerate(kg_entities[:5], 1):
            print(f"  {i}. {entity['label']} ({entity['data_type']})")
            
    except Exception as e:
        print(f"Error loading KG entities: {e}")
    
    print("\nModule loaded successfully. Use chemical_fuzzy_search() function to perform searches.")
