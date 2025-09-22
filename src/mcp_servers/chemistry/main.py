from fastmcp import FastMCP
from src.utils.global_logger import get_logger, mcp_tool_logger

# Import chemistry operations
from src.mcp_servers.chemistry.operations.cbu_operations import (
    convert_smiles_to_cbu,
    batch_convert_smiles_to_cbu,
    get_cbu_help
)

mcp = FastMCP(name="chemistry")

logger = get_logger("mcp_server", "chemistry_main")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return """
# Chemistry MCP Server - CBU Conversion Tools

## Overview
This MCP server provides tools for converting SMILES strings to CBU (Chemical Building Unit) formula convention. It normalizes neutral linker SMILES to deprotonated linker states and provides MULTIPLE MOF/MOP core formula labeling formats for maximum flexibility.

## Available Tools

### 1. convert_smiles_to_cbu
Convert a single SMILES string to CBU formula convention with multiple label formats. This is the primary tool for individual conversions.

**When to use:** Converting single chemical linker SMILES to CBU format.
**Next steps:** Choose the appropriate core label format from the provided variants for your specific use case.

### 2. batch_convert_smiles_to_cbu  
Convert multiple SMILES strings to CBU formula convention in batch processing with all label variants.

**When to use:** Processing multiple chemical linkers efficiently.
**Next steps:** Analyze batch results and select consistent labeling formats across your dataset.

### 3. get_cbu_help
Get comprehensive help information about CBU conversion functionality, multiple output formats, and usage examples.

**When to use:** When you need detailed information about the conversion process, available label formats, or output structure.

## Workflow Recommendations

1. **Start with help**: Call `get_cbu_help` to understand all available label formats
2. **Single conversions**: Use `convert_smiles_to_cbu` for individual SMILES strings
3. **Choose format**: Select from core_labels object (merged, unmerged_mult, unmerged_underscore, unmerged_concat)
4. **Batch processing**: Use `batch_convert_smiles_to_cbu` for multiple SMILES strings
5. **Standardize**: Pick consistent labeling format across your workflow

## Supported Acid Sites
- Carboxylic acids (–C(=O)OH)
- Sulfonic acids (–SO2OH)
- Phosphonic acids (–P(=O)OH)

## Output Information
Each conversion provides:
- Deprotonated SMILES
- Standard InChI and InChI key
- Molecular formula with charge
- Exact molecular weight
- Primary CBU core label (chosen by label_mode)
- ALL available core label formats in core_labels object
"""

@mcp.tool(
    name="convert_smiles_to_cbu",
    description="""Convert a single SMILES string to CBU (Chemical Building Unit) formula convention with multiple label formats. 
    This tool normalizes neutral linker SMILES to deprotonated linker states and provides ALL available MOF/MOP core formula labeling formats.
    
    Parameters:
    - smiles: Neutral linker SMILES string (required)
    - remove_k: Number of acidic H to remove (optional, default: remove all acidic H)
    - label_mode: Labeling mode - 'auto', 'aggregated', or 'ring' (default: 'auto')
    
    Output includes both a primary 'core_label' and a 'core_labels' object with all format variants:
    merged, unmerged_mult, unmerged_underscore, unmerged_concat.
    Use this tool for individual SMILES conversions. For multiple SMILES strings, consider using batch_convert_smiles_to_cbu instead."""
)
@mcp_tool_logger
def convert_smiles_to_cbu_tool(smiles: str, remove_k: int = None, label_mode: str = "auto") -> str:
    """Convert single SMILES to CBU format."""
    return convert_smiles_to_cbu(smiles, remove_k, label_mode)

@mcp.tool(
    name="batch_convert_smiles_to_cbu",
    description="""Convert multiple SMILES strings to CBU formula convention in batch with all label format variants.
    This tool efficiently processes multiple neutral linker SMILES strings at once, providing all labeling formats for each.
    
    Parameters:
    - smiles_list: List of neutral linker SMILES strings (required)
    - remove_k: Number of acidic H to remove (optional, default: remove all acidic H) 
    - label_mode: Labeling mode - 'auto', 'aggregated', or 'ring' (default: 'auto')
    
    Each result includes both primary 'core_label' and 'core_labels' object with all format variants.
    Use this tool when you have multiple SMILES strings to convert. The output includes batch statistics and individual results.
    After batch conversion, select consistent labeling formats across your dataset."""
)
@mcp_tool_logger  
def batch_convert_smiles_to_cbu_tool(smiles_list: list, remove_k: int = None, label_mode: str = "auto") -> str:
    """Convert multiple SMILES to CBU format in batch."""
    return batch_convert_smiles_to_cbu(smiles_list, remove_k, label_mode)

@mcp.tool(
    name="get_cbu_help",
    description="""Get comprehensive help information about CBU (Chemical Building Unit) conversion functionality and multiple label formats.
    This tool provides detailed documentation about the conversion process, all available label format variants, parameters, and output structure.
    
    Use this tool when you need to understand:
    - How CBU conversion works
    - All available core label formats (merged, unmerged variants)
    - What parameters are available
    - Complete output format with examples
    - Which label format to choose for your use case
    
    Call this tool first if you're unfamiliar with CBU conversion processes or need to understand the new multiple format output."""
)
@mcp_tool_logger
def get_cbu_help_tool() -> str:
    """Get help information about CBU conversion."""
    return get_cbu_help()

if __name__ == "__main__":
    mcp.run(transport="stdio")
