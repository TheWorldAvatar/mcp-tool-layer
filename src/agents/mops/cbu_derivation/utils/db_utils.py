import csv
from typing import Dict, List, Set, Tuple


def load_cbu_database(csv_path: str) -> Tuple[List[Dict[str, str]], Set[str]]:
    """
    Load CBU database from CSV file and return (rows, formula_set).
    Expected headers: formula, category, smiles, canonical_smiles (optional).
    """
    cbu_data: List[Dict[str, str]] = []
    formula_set: Set[str] = set()
    with open(csv_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            formula = (row.get('formula') or '').strip()
            if not formula:
                continue
            smiles = row.get('canonical_smiles') or row.get('smiles') or 'N/A'
            cbu_data.append({
                'formula': formula,
                'category': (row.get('category') or '').strip(),
                'smiles': smiles,
            })
            formula_set.add(formula.lower())
    return cbu_data, formula_set


def check_exact_match(species_name: str, formula_set: Set[str]) -> bool:
    return (species_name or '').strip().lower() in (formula_set or set())

