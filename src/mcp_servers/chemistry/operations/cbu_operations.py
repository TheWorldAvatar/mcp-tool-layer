"""
CBU (Chemical Building Unit) operations for canonical SMILES lookup and grounding.
"""

import sys
import os
import json
from typing import Dict, Any, Optional, Tuple

# Import canonical SMILES processing
scripts_dir = os.path.join(os.path.dirname(__file__), '../../../../scripts/cbu_alignment')
sys.path.append(scripts_dir)

try:
    from smiles_based_cbu_processing import to_kg_canonical_from_neutral, to_kg_canonical_from_kg
except ImportError as e:
    print(f"Warning: Could not import smiles_based_cbu_processing from {scripts_dir}: {e}")
    to_kg_canonical_from_neutral = None
    to_kg_canonical_from_kg = None

# Load all_smiles.json for CBU lookup
def load_smiles_database():
    """Load the all_smiles.json database for CBU lookup."""
    try:
        smiles_db_path = os.path.join(scripts_dir, "all_smiles.json")
        if os.path.exists(smiles_db_path):
            with open(smiles_db_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            print(f"Warning: all_smiles.json not found at {smiles_db_path}")
            return {}
    except Exception as e:
        print(f"Error loading all_smiles.json: {e}")
        return {}

# Global database - loaded once
_SMILES_DATABASE = load_smiles_database()

def convert_smiles_to_canonical(smiles: str) -> Tuple[str, str, str]:
    """
    Convert SMILES to canonical forms using the CBU processing functions.
    
    Args:
        smiles: Input SMILES string
        
    Returns:
        Tuple of (rdkit_canonical, kg_canonical, inchikey)
    """
    if not to_kg_canonical_from_neutral or not to_kg_canonical_from_kg:
        raise ImportError("SMILES canonicalization functions not available")
    
    try:
        # Try treating as neutral first
        rdkit_can, kg_can, ik = to_kg_canonical_from_neutral(smiles)
        return rdkit_can, kg_can, ik
    except Exception as e1:
        try:
            # Try treating as KG-style if neutral fails
            rdkit_can, kg_can, ik = to_kg_canonical_from_kg(smiles)
            return rdkit_can, kg_can, ik
        except Exception as e2:
            raise ValueError(f"Failed to canonicalize SMILES '{smiles}': {e1}, {e2}")

def lookup_smiles_in_database(smiles: str) -> str:
    """
    Look up SMILES in the all_smiles.json database for CBU information.
    
    Args:
        smiles: Input SMILES string
    
    Returns:
        JSON string containing the CBU lookup results
    """
    try:
        # Validate input
        if not smiles or not isinstance(smiles, str):
            raise ValueError("SMILES string is required and must be a valid string")
        
        # Convert to canonical forms
        rdkit_canonical, kg_canonical, inchikey = convert_smiles_to_canonical(smiles)
        
        # Look up KG canonical SMILES in database
        if kg_canonical in _SMILES_DATABASE:
            cbu_data = _SMILES_DATABASE[kg_canonical]
            result = {
                "found": True,
                "input_smiles": smiles,
                "rdkit_canonical": rdkit_canonical,
                "kg_canonical": kg_canonical,
                "inchikey": inchikey,
                "cbu_data": cbu_data
            }
        else:
            result = {
                "found": False,
                "input_smiles": smiles,
                "rdkit_canonical": rdkit_canonical,
                "kg_canonical": kg_canonical,
                "inchikey": inchikey,
                "message": "No CBU data found for this canonical SMILES",
                "total_database_entries": len(_SMILES_DATABASE)
            }
        
        return json.dumps(result, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_result = {
            "found": False,
            "error": str(e),
            "input_smiles": smiles,
            "total_database_entries": len(_SMILES_DATABASE)
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)

def batch_lookup_smiles(smiles_list: list) -> str:
    """
    Look up multiple SMILES strings in the CBU database in batch.
    
    Args:
        smiles_list: List of SMILES strings
    
    Returns:
        JSON string containing batch lookup results
    """
    if not isinstance(smiles_list, list):
        return json.dumps({
            "error": "smiles_list must be a list",
            "message": "Input must be a list of SMILES strings"
        }, indent=2)
    
    results = []
    found_count = 0
    
    for i, smiles in enumerate(smiles_list):
        try:
            # Lookup individual SMILES
            result_json = lookup_smiles_in_database(smiles)
            result = json.loads(result_json)
            result["batch_index"] = i
            
            if result.get("found", False):
                found_count += 1
            
            results.append(result)
        except Exception as e:
            error_result = {
                "batch_index": i,
                "found": False,
                "error": str(e),
                "input_smiles": smiles
            }
            results.append(error_result)
    
    batch_result = {
        "batch_size": len(smiles_list),
        "found_count": found_count,
        "not_found_count": len(smiles_list) - found_count,
        "success_rate": f"{(found_count / len(smiles_list) * 100):.1f}%" if smiles_list else "0%",
        "total_database_entries": len(_SMILES_DATABASE),
        "results": results
    }
    
    return json.dumps(batch_result, ensure_ascii=False, indent=2)

def search_database_by_formula(formula: str) -> str:
    """
    Search the CBU database by original formula.
    
    Args:
        formula: CBU formula to search for (e.g., "[(C6H4)(CO2)2]")
    
    Returns:
        JSON string containing search results
    """
    try:
        if not formula or not isinstance(formula, str):
            raise ValueError("Formula string is required and must be a valid string")
        
        matches = []
        for kg_canonical, cbu_data in _SMILES_DATABASE.items():
            if cbu_data.get("original_formula") == formula:
                match = {
                    "kg_canonical_smiles": kg_canonical,
                    "cbu_data": cbu_data
                }
                matches.append(match)
        
        result = {
            "search_formula": formula,
            "matches_found": len(matches),
            "matches": matches,
            "total_database_entries": len(_SMILES_DATABASE)
        }
        
        return json.dumps(result, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_result = {
            "error": str(e),
            "search_formula": formula,
            "total_database_entries": len(_SMILES_DATABASE)
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)

def get_database_stats() -> str:
    """
    Get statistics about the CBU database.
    
    Returns:
        JSON string containing database statistics
    """
    try:
        if not _SMILES_DATABASE:
            return json.dumps({
                "total_entries": 0,
                "message": "CBU database not loaded or empty"
            }, indent=2)
        
        # Count unique formulas
        formulas = set()
        iri_entries = 0
        
        for cbu_data in _SMILES_DATABASE.values():
            formula = cbu_data.get("original_formula")
            if formula:
                formulas.add(formula)
            
            iri_data = cbu_data.get("iri_data")
            if isinstance(iri_data, list):
                iri_entries += len(iri_data)
            elif iri_data:
                iri_entries += 1
        
        # Sample entries
        sample_entries = []
        for i, (kg_canonical, cbu_data) in enumerate(_SMILES_DATABASE.items()):
            if i < 3:  # First 3 entries as samples
                sample_entries.append({
                    "kg_canonical_smiles": kg_canonical,
                    "original_formula": cbu_data.get("original_formula"),
                    "inchikey": cbu_data.get("inchikey")
                })
        
        stats = {
            "total_entries": len(_SMILES_DATABASE),
            "unique_formulas": len(formulas),
            "total_iri_entries": iri_entries,
            "database_loaded": True,
            "sample_entries": sample_entries
        }
        
        return json.dumps(stats, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_result = {
            "error": str(e),
            "database_loaded": bool(_SMILES_DATABASE)
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)

def get_cbu_help() -> str:
    """
    Get help information about CBU (Chemical Building Unit) lookup functionality.
    
    Returns:
        Help text explaining CBU lookup functionality
    """
    help_text = f"""
# CBU (Chemical Building Unit) Lookup Help

## Overview
This tool provides canonical SMILES-based lookup of Chemical Building Unit (CBU) information from a curated database. 
It converts input SMILES to canonical forms and performs exact matches against the CBU database.

## Database Status
- Total entries: {len(_SMILES_DATABASE)}
- Database loaded: {'Yes' if _SMILES_DATABASE else 'No'}

## Functions Available

### 1. lookup_smiles_in_database
Look up a single SMILES string in the CBU database using canonical SMILES matching.

**Parameters:**
- `smiles`: Input SMILES string (any format - neutral, charged, etc.)

**Process:**
1. Converts input SMILES to canonical forms (RDKit canonical and KG canonical)
2. Searches database using KG canonical SMILES as the key
3. Returns exact match results only

**Example:**
- Input: "c1(ccc(cc1)C(=O)O)C(=O)O"
- Canonicalized: "[O]C(=O)c1cccc(C([O])=O)c1"
- Result: CBU data if found, including formula, IRI data, etc.

### 2. batch_lookup_smiles
Look up multiple SMILES strings in batch processing.

**Parameters:**
- `smiles_list`: List of SMILES strings

**Returns:**
- Batch statistics (success rate, found/not found counts)
- Individual results for each SMILES

### 3. search_database_by_formula
Search the database by CBU formula string.

**Parameters:**
- `formula`: CBU formula (e.g., "[(C6H4)(CO2)2]")

**Returns:**
- All canonical SMILES entries matching the formula
- Associated CBU data for each match

### 4. get_database_stats
Get comprehensive statistics about the loaded CBU database.

**Returns:**
- Total entries, unique formulas, IRI counts
- Sample entries for verification

## Canonical SMILES Conversion
The tool uses two canonicalization approaches:
1. **Neutral processing**: Treats input as neutral molecule, converts to deprotonated canonical form
2. **KG-style processing**: Treats input as already in KG format, canonicalizes directly

**Key formats:**
- **RDKit canonical**: Standard RDKit canonicalization with charges
- **KG canonical**: Modified format using [O] instead of [O-] for consistency

## Database Structure
Each entry contains:
- `original_formula`: CBU formula (e.g., "[(C6H4)(CO2)2]")
- `original_smiles`: Original SMILES from source
- `rdkit_canonical`: RDKit canonical form
- `kg_canonical`: KG canonical form (used as database key)
- `inchikey`: InChI key for chemical identification
- `iri_data`: Ontology IRI information (subject, subjectType, formulaType)

## Output Format
**Successful lookup:**
```json
{{
  "found": true,
  "input_smiles": "input_string",
  "rdkit_canonical": "canonical_form_with_charges",
  "kg_canonical": "kg_form_database_key",
  "inchikey": "INCHIKEY-STRING",
  "cbu_data": {{
    "original_formula": "[(C6H4)(CO2)2]",
    "original_smiles": "source_smiles",
    "rdkit_canonical": "rdkit_form",
    "kg_canonical": "kg_form",
    "inchikey": "INCHIKEY",
    "iri_data": [{{ ... }}]
  }}
}}
```

**Not found:**
```json
{{
  "found": false,
  "input_smiles": "input_string",
  "rdkit_canonical": "canonical_form",
  "kg_canonical": "kg_form",
  "inchikey": "INCHIKEY",
  "message": "No CBU data found for this canonical SMILES"
}}
```

## Usage Strategy
1. **Single lookup**: Use `lookup_smiles_in_database` for individual SMILES
2. **Batch processing**: Use `batch_lookup_smiles` for multiple SMILES
3. **Formula search**: Use `search_database_by_formula` to find all SMILES for a formula
4. **Database info**: Use `get_database_stats` to verify database status

## Important Notes
- **Exact match only**: No fuzzy matching or similarity search
- **Canonical forms**: Input SMILES are canonicalized before lookup
- **Database scope**: Only includes ChemicalBuildingUnit entries from ontology
- **Performance**: Database loaded once at startup for fast lookups
"""
    return help_text