import csv
from typing import Dict, List, Set, Tuple
from models.locations import DATA_CCDC_DIR
import os

def load_res_file(ccdc_number: str) -> str:
    """
    Load the RES file from the ccdc/res directory.
    """
    res_file_path = os.path.join(DATA_CCDC_DIR, "res", f"{ccdc_number}.res")
    if not os.path.exists(res_file_path):
        return "RES file is not provided. "
    with open(res_file_path, "r", encoding="utf-8") as f:
        return f.read()

def load_mol2_file(ccdc_number: str):
    """
    Load the MOL2 file from the ccdc/mol2 directory.
    """
    mol2_file_path = os.path.join(DATA_CCDC_DIR, "mol2", f"{ccdc_number}.mol2")
    if not os.path.exists(mol2_file_path):
        # raise FileNotFoundError(f"MOL2 file not found at {mol2_file_path}")
        return "MOL2 file is not provided. "
    with open(mol2_file_path, "r", encoding="utf-8") as f:
        return f.read()


def load_cif_file(ccdc_number: str) -> str:
    """
    Load the CIF file from the ccdc/cif directory.
    """
    cif_file_path = os.path.join(DATA_CCDC_DIR, "cif", f"{ccdc_number}.cif")
    if not os.path.exists(cif_file_path):
        return "CIF file is not provided. "
    with open(cif_file_path, "r", encoding="utf-8") as f:
        return f.read()


def load_cbu_database(csv_path: str) -> Tuple[List[Dict[str, str]], Set[str]]:
    """
    Load CBU database from CSV file and return as list of dictionaries.
    Only includes formula, category, and smiles columns.
    Also returns a set of all formulas for quick exact matching.
    """
    cbu_data = []
    formula_set = set()
    try:
        with open(csv_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row['formula']:  # Skip empty rows
                    cbu_data.append({
                        'formula': row['formula'],
                        'category': row['category'],
                        'smiles': row['canonical_smiles'] if row['canonical_smiles'] != 'N/A' else row['smiles']
                    })
                    # Add formula to set for quick lookups (case-insensitive)
                    formula_set.add(row['formula'].strip().lower())
    except Exception as e:
        print(f"Error loading CBU database: {e}")
    return cbu_data, formula_set


def format_cbu_database_for_prompt(cbu_data: List[Dict[str, str]]) -> str:
    """
    Format CBU database for inclusion in the prompt.
    **Only includes INORGANIC/METAL CBUs** - organic CBUs are explicitly excluded.
    """
    prompt_lines = ["**Available Existing Metal/Inorganic CBU Database (Organic CBUs excluded):**\n"]
    prompt_lines.append("| Formula | Category | SMILES |")
    prompt_lines.append("|---------|----------|--------|")
    
    # Filter to ONLY inorganic/metal CBUs - explicitly exclude organic
    inorganic_cbus = [cbu for cbu in cbu_data if cbu['category'] == 'Inorganic']
    
    for cbu in inorganic_cbus:
        formula = cbu['formula']
        category = cbu['category']
        smiles = cbu['smiles'] if cbu['smiles'] != 'N/A' else 'Not available'
        prompt_lines.append(f"| {formula} | {category} | {smiles} |")
    
    return "\n".join(prompt_lines)


# Prompt templates (kept here for reuse by agents)
INSTRUCTION_PROMPT_ENHANCED_3 = (
    "Derive metal CBUs from RES/CIF and the paper content. Use only provided text. "
    "Output the ASU/primitive half-cluster metal node description and exact formulas as printed."
)
INSTRUCTION_PROMPT_ENHANCED_3_WITH_CBU = (
    INSTRUCTION_PROMPT_ENHANCED_3 + " Use the provided CBU database table to ground formulas when possible."
)


if __name__ == "__main__":
    ccdc_number = "2359351"
    res_content = load_res_file(ccdc_number)
    cif_content = load_cif_file(ccdc_number)
    print(res_content)
 