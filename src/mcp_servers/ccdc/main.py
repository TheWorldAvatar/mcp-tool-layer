from fastmcp import FastMCP
from src.utils.global_logger import mcp_tool_logger
from src.mcp_servers.ccdc.operations.wsl_ccdc import (
    search_ccdc_by_mop_name as _search_ccdc_by_mop_name,
    get_res_cif_file_by_ccdc as _get_res_cif_file_by_ccdc,
)

mcp = FastMCP(name="ccdc")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "If ccdc number is not provided in the paper, you can use the search_ccdc_by_mop_name tool to search the CCDC by compound name. "
        "Then you can use the get_res_cif_file_by_ccdc tool to fetch the .res/.cif files from the CCDC. This is important for the downstream task."
        "CCDC MCP server (local CSD required)\n"
        "Tools:\n"
        "- search_ccdc_by_mop_name(name, exact=False): Search CCDC by compound name.\n"
        "  Returns a list of (CSD refcode, CCDC deposition number).\n"
        "- get_res_cif_file_by_ccdc(deposition_number): Fetch a single entry by CCDC number and write .res/.cif files.\n"
        "  Returns the output file paths as a TSV string.\n\n"
        "Guidance:\n"
        "- You must have a licensed local CSD installed and accessible to the 'ccdc' Python package.\n"
        "- For name searches, try exact=False first, then retry with exact=True to narrow results.\n"
        "- The fetch function requires exactly one hit and a 3D structure; otherwise it fails fast.\n"
        "- Use absolute or existing directories for out_dir; files will be created there.\n\n"
        "Examples:\n"
        "- search_ccdc_by_mop_name('aspirin')\n"
        "- search_ccdc_by_mop_name('Synthesis of IRMOP-50', exact=True)\n"
        "- get_res_cif_file_by_ccdc('1955203', 'data/ccdc_out')  (WSL path accepted; auto-proxied to Windows)\n"
    )

@mcp.tool(name="search_ccdc_by_mop_name", description="Search the CCDC by compound name. Returns a list of (CSD refcode, CCDC number) tuples.")
@mcp_tool_logger
def search_ccdc_by_mop_name(name: str, exact: bool = False) -> str:
    results = _search_ccdc_by_mop_name(name, exact)
    if not results:
        return "[]"
    # format as a simple TSV-like list for readability
    lines = ["refcode\tccdc_number"]
    for refcode, num in results:
        lines.append(f"{refcode}\t{num}")
    return "\n".join(lines)

@mcp.tool(name="get_res_cif_file_by_ccdc", description="Fetch a structure by CCDC number and write .res/.cif under DATA_CCDC_DIR. Returns a TSV string with file paths.")
@mcp_tool_logger
def get_res_cif_file_by_ccdc(deposition_number: str) -> str:
    paths = _get_res_cif_file_by_ccdc(deposition_number)
    # Return simple TSV lines to conform to string-only outputs
    return f"res\t{paths.get('res','')}\n" \
           f"cif\t{paths.get('cif','')}"

if __name__ == "__main__":
    mcp.run(transport="stdio")


