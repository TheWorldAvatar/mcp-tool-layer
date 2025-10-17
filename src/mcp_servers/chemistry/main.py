from fastmcp import FastMCP
from src.utils.global_logger import get_logger

# Import chemistry operations
from src.mcp_servers.chemistry.operations.canonical_search import (
    fuzzy_search_canonical_smiles,
)
from src.mcp_servers.chemistry.operations.cas_to_smiles import (
    cas_to_smiles,
)
from src.mcp_servers.chemistry.operations.enhanced_smiles_based_cbu_processing import (
    _to_storage_canonical,
)
import json
import os

# Enhanced SMILES processing now imported from package path above
_HAS_ENHANCED_SMILES = True

mcp = FastMCP(name="chemistry")

logger = get_logger("mcp_server", "chemistry_main")

def convert_smiles_to_canonical_enhanced(smiles: str):
    """
    Convert SMILES to canonical forms using enhanced processing functions.
    
    Uses the single-format enhanced processing that guarantees one canonical output:
    RDKit canonical SMILES of the deprotonated form with all carboxylic acids as COO-.
    
    Args:
        smiles: Input SMILES string
        
    Returns:
        Tuple of (canonical_smiles, inchikey, source_kind)
    """
    if not _HAS_ENHANCED_SMILES:
        raise ImportError("Enhanced SMILES processing functions not available")
    
    try:
        # Use single-format enhanced processing
        canonical_smiles, inchikey, source_kind = _to_storage_canonical(smiles)
        return canonical_smiles, inchikey, source_kind
    except Exception as e:
        raise ValueError(f"Failed to canonicalize SMILES '{smiles}': {e}")

@mcp.prompt(name="chemistry_instructions")
def chemistry_instructions_prompt():
    return """
# Chemistry MCP Server Instructions

This MCP server provides enhanced SMILES canonicalization and fuzzy CBU search capabilities for chemical structure processing.

## Available Tools:

1. **canonicalize_smiles**: Convert SMILES to single canonical format using enhanced processing
2. **fuzzy_smiles_search**: Find similar SMILES in CBU database (top 20 matches)
3. **cas_to_smiles**: Convert CAS registry numbers to SMILES strings via PubChem

## Key Features:

- **Single-Format Enhanced Processing**: Guarantees exactly ONE chemical format output
- **Enforced Carboxylate Deprotonation**: All carboxylic acids forced to COO- (no neutral COOH)
- **No Alternate Renderings**: Eliminates dual format confusion, only canonical SMILES output
- **Deterministic Results**: Same input always produces same canonical output
- **Source Kind Detection**: Identifies input as kg-placeholder, neutral, anion, or unknown
- **InChI Key Generation**: Generates molecular identifiers for verification
- **Fuzzy SMILES Search**: Find similar canonical SMILES in CBU database using string similarity
- **CAS to SMILES Conversion**: Query PubChem databases to convert CAS numbers to SMILES
- **Robust Error Handling**: Enhanced processing with clear error messages

## Processing Guarantees:

- **Input Handling**: Supports KG placeholders [O], neutral COOH, and deprotonated COO- forms
- **Output Format**: RDKit canonical SMILES with enforced COO- carboxylate sites
- **Consistency**: Stereochemistry ignored for canonicalization (isomericSmiles=False)
- **Standardization**: Full RDKit cleanup, fragmentation, normalization, and reionization

## Usage:

1. Use `canonicalize_smiles(smiles)` to get the single canonical format
2. Use `fuzzy_smiles_search(smiles)` to find similar CBU entries using canonical form
3. Use `cas_to_smiles(cas_number)` to convert CAS registry numbers to SMILES via PubChem
4. All processing automatically enforces COO- deprotonation for consistency
"""


@mcp.tool()
def canonicalize_smiles(smiles: str) -> str:
    """
    Convert SMILES to canonical form using enhanced CBU processing functions.
    
    Uses single-format enhanced processing that guarantees one canonical output:
    RDKit canonical SMILES of the deprotonated form with all carboxylic acids as COO-.
    No alternate renderings or UI-only strings.
    
    Args:
        smiles: Input SMILES string
    
    Returns:
        JSON string containing canonical SMILES, InChI key, and source kind
    """
    logger.info(f"Canonicalizing SMILES: {smiles}")
    try:
        canonical_smiles, inchikey, source_kind = convert_smiles_to_canonical_enhanced(smiles)
        result = {
            "input_smiles": smiles,
            "canonical_smiles": canonical_smiles,
            "inchikey": inchikey or "",
            "source_kind": source_kind,
            "processing_method": "enhanced_single_format",
            "description": "Single canonical format: COO- enforced, no alternate renderings",
            "success": True
        }
        logger.info(f"Enhanced SMILES canonicalization completed")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error in enhanced SMILES canonicalization: {e}")
        error_result = {
            "input_smiles": smiles,
            "processing_method": "enhanced_single_format",
            "success": False,
            "error": str(e)
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)

@mcp.tool()
def fuzzy_smiles_search(canonical_smiles: str) -> str:
    """
    Find similar canonical SMILES in the CBU database using fuzzy string matching.
     
    Args:
        canonical_smiles: Input SMILES string, which is already canonicalized
    
    Returns:
        JSON string containing top 20 similar CBU entries with similarity scores
    """
    logger.info(f"Fuzzy search for SMILES: {canonical_smiles}")
    
    try:
        # Use the canonical_search module for fuzzy matching
        search_result = fuzzy_search_canonical_smiles(canonical_smiles, top_n=20)
        
        # Parse the result to add additional information
        result_data = json.loads(search_result)
        if result_data.get("search_successful", False):
            result_data["canonical_smiles_used"] = canonical_smiles
            result_data["processing_method"] = "enhanced_single_format"
        
        logger.info(f"Enhanced fuzzy search completed via canonical_search module")
        return json.dumps(result_data, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.error(f"Error in enhanced fuzzy SMILES search: {e}")
        error_result = {
            "input_original_smiles": canonical_smiles,
            "processing_method": "enhanced_single_format",
            "search_successful": False,
            "error": str(e)
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)

@mcp.tool(name="cas_to_smiles")
def cas_to_smiles_convert(cas_number: str) -> str:
    """
    Convert CAS registry number to SMILES strings using PubChem API.
    
    This function queries PubChem's compound and substance databases to find
    chemical structures matching the given CAS number and returns their SMILES
    representations along with other chemical properties.
    
    Args:
        cas_number: CAS registry number (e.g., "50446-44-1")
    
    Returns:
        JSON string containing SMILES strings, compound IDs, and chemical properties
    """
    logger.info(f"Converting CAS number to SMILES: {cas_number}")
    
    try:
        # Use the cas_to_smiles operation
        result = cas_to_smiles(cas_number)
        
        # Add metadata for MCP response
        result["processing_method"] = "pubchem_api"
        result["description"] = "CAS to SMILES conversion via PubChem compound and substance databases"
        
        if result["success"]:
            logger.info(f"CAS conversion successful: found {len(result.get('smiles_list', []))} SMILES")
        else:
            logger.warning(f"CAS conversion failed: no matches found for {cas_number}")
            
        return json.dumps(result, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.error(f"Error in CAS to SMILES conversion: {e}")
        error_result = {
            "cas": cas_number,
            "processing_method": "pubchem_api",
            "success": False,
            "error": str(e),
            "description": "CAS to SMILES conversion failed"
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
