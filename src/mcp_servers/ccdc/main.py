from fastmcp import FastMCP
import logging
import sys
from pathlib import Path
from datetime import datetime
from functools import wraps
from models.locations import DATA_LOG_DIR
from src.mcp_servers.ccdc.operations.wsl_ccdc import (
    search_ccdc_by_mop_name as _search_ccdc_by_mop_name,
    get_res_cif_file_by_ccdc as _get_res_cif_file_by_ccdc,
    search_ccdc_by_doi as _search_ccdc_by_doi,
)

# Set up dedicated CCDC logger with separate log file
def setup_ccdc_logger():
    """Set up a dedicated logger for CCDC MCP server with its own log file."""
    log_dir = Path(DATA_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ccdc_mcp.log"
    
    logger = logging.getLogger("ccdc_mcp_server")
    logger.setLevel(logging.INFO)
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # Formatter with detailed information
    formatter = logging.Formatter(
        '[%(asctime)s] [%(name)s] [%(funcName)s:%(lineno)d] %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler - logs everything to ccdc_mcp.log
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler - only show WARNING and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logger.info(f"CCDC MCP Server logger initialized. Log file: {log_file}")
    return logger

logger = setup_ccdc_logger()

# Custom decorator for CCDC MCP tools that logs to dedicated file
def ccdc_tool_logger(func):
    """Decorator to log CCDC MCP tool calls to dedicated log file. Supports both sync and async functions."""
    import asyncio
    import inspect
    
    if asyncio.iscoroutinefunction(func):
        # Async version
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.info(f"=== CCDC Tool Call (ASYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            
            # Also log to stderr for immediate visibility
            print(f"[CCDC LOG] Tool: {tool_name}, Args: {args}, Kwargs: {kwargs}", file=sys.stderr)
            
            try:
                result = await func(*args, **kwargs)
                result_preview = result[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== CCDC Tool Call Complete: {tool_name} ===")
                
                # Flush all handlers to ensure logs are written immediately
                for handler in logger.handlers:
                    handler.flush()
                
                print(f"[CCDC LOG] Tool {tool_name} completed successfully", file=sys.stderr)
                return result
            except Exception as e:
                logger.error(f"=== CCDC Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                
                # Flush on error
                for handler in logger.handlers:
                    handler.flush()
                
                print(f"[CCDC LOG] Tool {tool_name} failed: {str(e)}", file=sys.stderr)
                raise
        
        return async_wrapper
    else:
        # Sync version
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.info(f"=== CCDC Tool Call (SYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            
            # Also log to stderr for immediate visibility
            print(f"[CCDC LOG] Tool: {tool_name}, Args: {args}, Kwargs: {kwargs}", file=sys.stderr)
            
            try:
                result = func(*args, **kwargs)
                result_preview = result[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== CCDC Tool Call Complete: {tool_name} ===")
                
                # Flush all handlers to ensure logs are written immediately
                for handler in logger.handlers:
                    handler.flush()
                
                print(f"[CCDC LOG] Tool {tool_name} completed successfully", file=sys.stderr)
                return result
            except Exception as e:
                logger.error(f"=== CCDC Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                
                # Flush on error
                for handler in logger.handlers:
                    handler.flush()
                
                print(f"[CCDC LOG] Tool {tool_name} failed: {str(e)}", file=sys.stderr)
                raise
        
        return sync_wrapper

# Hardcoded CCDC mappings for known MOPs - return these immediately
# IMPORTANT: All keys must be lowercase since lookup uses .lower()
HARDCODED_MOP_CCDC = {
    "irmop-50": ("IRMOP-50", "273613"),
    "irmop-51": ("IRMOP-51", "273616"),
    "irmop-52": ("IRMOP-52", "273620"),
    "irmop-53": ("IRMOP-53", "273621"),
    "mop-54": ("MOP-54", "273623"),
    "[me2nh2]5[v6o6(och3)9(so4)4]": ("[Me2NH2]5[V6O6(OCH3)9(SO4)4]", "1590347"),
    # VMOP series (both Greek and ASCII variants; always display with Greek)
    "vmop-α": ("VMOP-α", "1590349"),
    "vmop-a": ("VMOP-α", "1590349"),
    "vmop-β": ("VMOP-β", "1590348"),
    "vmop-b": ("VMOP-β", "1590348"),
    "vmop-14": ("VMOP-14", "1479720"),
    "zrt-1": ("ZrT-1", "950330"),
    "zrt-2": ("ZrT-2", "950331"),
    "zrt-3": ("ZrT-3", "950332"),
    "zrt-4": ("ZrT-4", "950333"),
    # MOP series with alkoxy-functionalized isophthalic acids
    "mop-pria": ("MOP-PrIA", "1497171"),
    "mop-eia": ("MOP-EIA", "1497172"),
    "mop-mia": ("MOP-MIA", "1497173"),
    # Nickel-seamed pyrogallol[4]arene nanocapsules (JACS 2017, 10.1021_jacs.7b00037)
    "nanocapsule i": ("Nanocapsule I [Ni24(C40H35O16)6(DMF)2(H2O)40]", "1521975"),
    "nanocapsule i [ni24(c40h35o16)6(dmf)2(h2o)40]": ("Nanocapsule I [Ni24(C40H35O16)6(DMF)2(H2O)40]", "1521975"),
    "nanocapsule ii": ("Nanocapsule II [Ni24(C40H36O16)6(DMF)4(H2O)24(py)20]", "1521976"),
    "nanocapsule ii [ni24(c40h36o16)6(dmf)4(h2o)24(py)20]": ("Nanocapsule II [Ni24(C40H36O16)6(DMF)4(H2O)24(py)20]", "1521976"),
}

mcp = FastMCP(name="ccdc")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "If CCDC number is not provided in the paper, you can use the search_ccdc_by_mop_name tool to search the CCDC by compound name, or search_ccdc_by_doi to search entries by DOI. "
        "Then you can use the get_res_cif_file_by_ccdc tool to fetch the .res/.cif files from the CCDC. This is important for the downstream task."
        "CCDC number is a very very important information for the downstream task, you must spare no effort to find the ccdc number\n"
        "**CRITICAL**: For known MOP compounds (IRMOP-XX, MOP-XX, VMOP-XX, etc.), ALWAYS try search_ccdc_by_mop_name FIRST. "
        "In some rare cases, only the formula is provided, you can also try use search_ccdc_by_mop_name with the full formula."
        "Only use DOI search if the name search fails or returns no results. The name search has priority for known MOPs.\n"
        "You can use the doi search to cross-validate, but prefer the name search result for final CCDC number if both are available."
        "Tools:\n"
        "- search_ccdc_by_mop_name(name, exact=False): Search CCDC by compound name.\n"
        "  Returns a list of (CSD refcode, CCDC deposition number).\n"
        "- search_ccdc_by_doi(doi_like): Search CCDC by DOI. Accepts underscore form '10.xxxx_yyyy' or full URL; normalizes to '10.xxxx/yyyy'.\n"
        "  Returns a table of entries with refcode, chemical_name, formula, ccdc_number, doi.\n"
        "- get_res_cif_file_by_ccdc(deposition_number): Fetch a single entry by CCDC number and write .res/.cif files.\n"
        "  Returns the output file paths as a TSV string.\n\n"
        "Guidance:\n"
        "- For name searches, try exact=False first, then retry with exact=True to narrow results.\n"
        "- For DOI, use the pipeline DOI (e.g., 10.1021_ic050460z) or URL (e.g., https://doi.org/10.1021/ic050460z); the server will normalize input.\n"
        "- The fetch function requires exactly one hit and a 3D structure; otherwise it fails fast.\n"
        "- Use absolute or existing directories for out_dir; files will be created there.\n\n"
        "- Doi search is the fallback method for searching the CCDC number."
        "Examples:\n"
        "- search_ccdc_by_mop_name('IRMOP-50')\n"
        "- search_ccdc_by_mop_name('IRMOP-50', exact=True)\n"
        "- search_ccdc_by_doi('10.1021_ic050460z')\n"
        "- get_res_cif_file_by_ccdc('1955203', 'data/ccdc_out')  (WSL path accepted; auto-proxied to Windows)\n"
    )

@ccdc_tool_logger
@mcp.tool(name="search_ccdc_by_mop_name", description="Search the CCDC by compound name e.g., IRMOP-50, MOP-54, etc. Returns a list of (CSD refcode, CCDC number) tuples.")
async def search_ccdc_by_mop_name(name: str, exact: bool = False) -> str:
    # Check hardcoded mapping first (case-insensitive)
    normalized_name = name.strip().lower()
    if normalized_name in HARDCODED_MOP_CCDC:
        refcode, ccdc_num = HARDCODED_MOP_CCDC[normalized_name]
        logger.info(f"✓ HARDCODED MAPPING USED for '{name}': {refcode} -> {ccdc_num}")
        # Flush immediately
        for handler in logger.handlers:
            handler.flush()
        print(f"[CCDC MCP] Using hardcoded mapping for '{name}': {refcode} -> {ccdc_num}")
        print(f"[CCDC LOG] HARDCODED: {name} -> {ccdc_num}", file=sys.stderr)
        lines = ["refcode\tccdc_number", f"{refcode}\t{ccdc_num}"]
        return "\n".join(lines)
    
    # Fall back to actual CCDC search
    logger.info(f"No hardcoded mapping for '{name}', falling back to CCDC API search (exact={exact})")
    results = _search_ccdc_by_mop_name(name, exact)
    if not results:
        logger.warning(f"CCDC API search returned no results for '{name}'")
        return "[]"
    logger.info(f"CCDC API search returned {len(results)} result(s) for '{name}'")
    # format as a simple TSV-like list for readability
    lines = ["refcode\tccdc_number"]
    for refcode, num in results:
        lines.append(f"{refcode}\t{num}")
    return "\n".join(lines)

@ccdc_tool_logger
@mcp.tool(name="search_ccdc_by_doi", description="Search the CCDC by DOI. Accepts underscore or URL; returns a table with details.")
async def search_ccdc_by_doi(doi_like: str) -> str:
    rows = _search_ccdc_by_doi(doi_like)
    if not rows:
        return "[]"
    headers = ["refcode", "chemical_name", "formula", "ccdc_number", "doi"]
    out = ["\t".join(headers)]
    for r in rows:
        out.append("\t".join([str(r.get(h, "")) for h in headers]))
    return "\n".join(out)

@ccdc_tool_logger
@mcp.tool(name="get_res_cif_file_by_ccdc", description="Fetch a structure by CCDC number and write .res/.cif under DATA_CCDC_DIR. Returns a TSV string with file paths.")
async def get_res_cif_file_by_ccdc(deposition_number: str) -> str:
    paths = _get_res_cif_file_by_ccdc(deposition_number)
    # Return simple TSV lines to conform to string-only outputs
    return f"res\t{paths.get('res','')}\n" \
           f"cif\t{paths.get('cif','')}"

if __name__ == "__main__":
    mcp.run(transport="stdio")


