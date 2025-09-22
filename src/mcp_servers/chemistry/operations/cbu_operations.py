"""
CBU (Chemical Building Unit) operations for converting SMILES to CBU formula convention.
"""

import sys
import os
import json
from typing import Dict, Any, Optional

# Add the scripts directory to Python path to import the updated cbu_processing
scripts_dir = os.path.join(os.path.dirname(__file__), '../../../../scripts')
sys.path.append(scripts_dir)

try:
    from cbu_processing import normalize_linker_from_smiles
except ImportError as e:
    print(f"Warning: Could not import cbu_processing from {scripts_dir}: {e}")
    normalize_linker_from_smiles = None

def convert_smiles_to_cbu(smiles: str, remove_k: Optional[int] = None, label_mode: str = "auto") -> str:
    """
    Convert SMILES string to CBU (Chemical Building Unit) formula convention.
    
    Args:
        smiles: Neutral linker SMILES string
        remove_k: Number of acidic H to remove; if None, removes all acidic H
        label_mode: Labeling mode - 'auto', 'aggregated', or 'ring'
    
    Returns:
        JSON string containing the CBU conversion results
    """
    if normalize_linker_from_smiles is None:
        return json.dumps({
            "error": "CBU processing module not available",
            "message": "Could not import cbu_processing module"
        }, indent=2)
    
    try:
        # Validate inputs
        if not smiles or not isinstance(smiles, str):
            raise ValueError("SMILES string is required and must be a valid string")
        
        if label_mode not in ["auto", "aggregated", "ring"]:
            raise ValueError("label_mode must be 'auto', 'aggregated', or 'ring'")
        
        if remove_k is not None and (not isinstance(remove_k, int) or remove_k < 0):
            raise ValueError("remove_k must be a non-negative integer or None")
        
        # Perform the conversion
        result = normalize_linker_from_smiles(smiles, remove_k=remove_k, label_mode=label_mode)
        
        # Add metadata
        result["input_smiles"] = smiles
        result["input_remove_k"] = remove_k
        result["input_label_mode"] = label_mode
        
        return json.dumps(result, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_result = {
            "error": str(e),
            "input_smiles": smiles,
            "input_remove_k": remove_k,
            "input_label_mode": label_mode
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)

def batch_convert_smiles_to_cbu(smiles_list: list, remove_k: Optional[int] = None, label_mode: str = "auto") -> str:
    """
    Convert multiple SMILES strings to CBU formula convention in batch.
    
    Args:
        smiles_list: List of neutral linker SMILES strings
        remove_k: Number of acidic H to remove; if None, removes all acidic H
        label_mode: Labeling mode - 'auto', 'aggregated', or 'ring'
    
    Returns:
        JSON string containing batch conversion results
    """
    if not isinstance(smiles_list, list):
        return json.dumps({
            "error": "smiles_list must be a list",
            "message": "Input must be a list of SMILES strings"
        }, indent=2)
    
    results = []
    for i, smiles in enumerate(smiles_list):
        try:
            # Convert individual SMILES
            result_json = convert_smiles_to_cbu(smiles, remove_k, label_mode)
            result = json.loads(result_json)
            result["batch_index"] = i
            results.append(result)
        except Exception as e:
            error_result = {
                "batch_index": i,
                "error": str(e),
                "input_smiles": smiles,
                "input_remove_k": remove_k,
                "input_label_mode": label_mode
            }
            results.append(error_result)
    
    batch_result = {
        "batch_size": len(smiles_list),
        "successful_conversions": len([r for r in results if "error" not in r]),
        "failed_conversions": len([r for r in results if "error" in r]),
        "results": results
    }
    
    return json.dumps(batch_result, ensure_ascii=False, indent=2)

def get_cbu_help() -> str:
    """
    Get help information about CBU (Chemical Building Unit) conversion.
    
    Returns:
        Help text explaining CBU conversion functionality
    """
    help_text = """
# CBU (Chemical Building Unit) Conversion Help

## Overview
This tool converts neutral linker SMILES strings to deprotonated linker anions and emits identifiers + MOF/MOP core labels. 
The updated cbu_processing.py script now provides MULTIPLE core label formats for maximum flexibility.

## Functions Available

### 1. convert_smiles_to_cbu
Convert a single SMILES string to CBU formula convention using normalize_linker_from_smiles().

**Parameters:**
- `smiles`: Neutral linker SMILES string (required)
- `remove_k`: Number of acidic H to remove (optional, default: remove all acidic H)
- `label_mode`: Labeling mode - 'auto', 'aggregated', or 'ring' (default: 'auto')

**Example:**
- Input: "C1=CC(=C(C=C1C(=O)O)N=NC2=CC(=CC(=C2)C(=O)O)C(=O)O)C(=O)O"
- Output: Deprotonated form with multiple CBU core label formats

### 2. batch_convert_smiles_to_cbu
Convert multiple SMILES strings in batch processing.

**Parameters:**
- `smiles_list`: List of neutral linker SMILES strings (required)
- `remove_k`: Number of acidic H to remove (optional, default: remove all acidic H)
- `label_mode`: Labeling mode - 'auto', 'aggregated', or 'ring' (default: 'auto')

### 3. get_cbu_help
Display this help information.

## Label Modes
- **auto**: Prefer ring-factored only when it carries extra carbon info (b>0); else aggregated
- **aggregated**: [(C{…}H{…}{hetero…})(CO2){m}] format
- **ring**: Ring-factored format (defaults to underscore style)

## Multiple Core Label Formats
The output now includes ALL available formats:
- **merged**: [(C{C'}H{H'}{hetero…})(CO2){m}] - aggregated format
- **unmerged_mult**: [({unit}){r}(hetero)(CO2){m}] - e.g., [(C6H4C)2(CO2)2]
- **unmerged_underscore**: [({unit})_{r}(hetero)(CO2){m}] - e.g., [(C6H4C)_2(CO2)2]
- **unmerged_concat**: [({unit}{unit}...)(hetero)(CO2){m}] - e.g., [(C6H6C6)(CO2)4]

## Supported Acid Sites
- Carboxylic: –C(=O)OH
- Sulfonic: –SO2OH  
- Phosphonic: –P(=O)OH

## Output Format
JSON object containing:
- `removed_H`: Number of hydrogen atoms removed during deprotonation
- `smiles`: Canonical SMILES of deprotonated form
- `std_inchi`: Standard InChI identifier
- `inchikey`: InChI key for database lookup
- `formula`: Molecular formula with formal charge
- `exact_mw`: Exact molecular weight
- `core_label`: Primary CBU core label (chosen by label_mode)
- `core_labels`: Object with ALL available label formats

## Example Usage & Output
```json
{
  "input_smiles": "C1=CC(=C(C=C1C(=O)O)N=NC2=CC(=CC(=C2)C(=O)O)C(=O)O)C(=O)O",
  "input_remove_k": null,
  "input_label_mode": "auto",
  "removed_H": 4,
  "smiles": "...",
  "std_inchi": "...", 
  "inchikey": "...",
  "formula": "C16H6N2O8-4",
  "exact_mw": 366.0062,
  "core_label": "[(C6H2N)_2(N2)(CO2)4]",
  "core_labels": {
    "merged": "[(C10H6N2)(CO2)4]",
    "unmerged_underscore": "[(C6H2N)_2(N2)(CO2)4]", 
    "unmerged_mult": "[(C6H2N)2(N2)(CO2)4]",
    "unmerged_concat": "[(C6H2NC6H2N)(N2)(CO2)4]"
  }
}
```

## Technical Notes
- Uses RDKit for molecular processing
- Ring factoring attempts multiple ring counts (2,3,1,4) preferring larger b values
- Handles hetero elements (N/S/P) in core labels
- All ring-factored variants are generated when applicable
- Choose the format that best suits your database or analysis needs
"""
    return help_text
