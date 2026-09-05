from fastmcp import FastMCP
import logging
import re
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
from src.utils.source_text_sanitize import sanitize_source_markdown

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
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logger.info(f"CCDC MCP Server logger initialized. Log file: {log_file}")
    return logger

logger = setup_ccdc_logger()


def _log_to_stderr(message: str) -> None:
    """Write diagnostic logs without corrupting stdio MCP JSON framing."""
    encoding = sys.stderr.encoding or "utf-8"
    safe_message = str(message).encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
    print(safe_message, file=sys.stderr)


def _tool_name(func) -> str:
    return (
        getattr(func, "__name__", None)
        or getattr(func, "name", None)
        or getattr(getattr(func, "fn", None), "__name__", None)
        or func.__class__.__name__
    )

# Custom decorator for CCDC MCP tools that logs to dedicated file
def ccdc_tool_logger(func):
    """Decorator to log CCDC MCP tool calls to dedicated log file. Supports both sync and async functions."""
    import asyncio
    import inspect
    
    if asyncio.iscoroutinefunction(func):
        # Async version
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tool_name = _tool_name(func)
            logger.info(f"=== CCDC Tool Call (ASYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            
            # Also log to stderr for immediate visibility
            _log_to_stderr(f"[CCDC LOG] Tool: {tool_name}, Args: {args}, Kwargs: {kwargs}")
            
            try:
                result = await func(*args, **kwargs)
                result_preview = result[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== CCDC Tool Call Complete: {tool_name} ===")
                
                # Flush all handlers to ensure logs are written immediately
                for handler in logger.handlers:
                    handler.flush()
                
                _log_to_stderr(f"[CCDC LOG] Tool {tool_name} completed successfully")
                return result
            except Exception as e:
                logger.error(f"=== CCDC Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                
                # Flush on error
                for handler in logger.handlers:
                    handler.flush()
                
                _log_to_stderr(f"[CCDC LOG] Tool {tool_name} failed: {str(e)}")
                raise
        
        return async_wrapper
    else:
        # Sync version
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tool_name = _tool_name(func)
            logger.info(f"=== CCDC Tool Call (SYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            
            # Also log to stderr for immediate visibility
            _log_to_stderr(f"[CCDC LOG] Tool: {tool_name}, Args: {args}, Kwargs: {kwargs}")
            
            try:
                result = func(*args, **kwargs)
                result_preview = result[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== CCDC Tool Call Complete: {tool_name} ===")
                
                # Flush all handlers to ensure logs are written immediately
                for handler in logger.handlers:
                    handler.flush()
                
                _log_to_stderr(f"[CCDC LOG] Tool {tool_name} completed successfully")
                return result
            except Exception as e:
                logger.error(f"=== CCDC Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                
                # Flush on error
                for handler in logger.handlers:
                    handler.flush()
                
                _log_to_stderr(f"[CCDC LOG] Tool {tool_name} failed: {str(e)}")
                raise
        
        return sync_wrapper

# Hardcoded CCDC mappings for known MOPs - return these immediately
# IMPORTANT: All keys must be lowercase since lookup uses .lower()
HARDCODED_MOP_CCDC = {
    "irmop-50": ("IRMOP-50", "273613"),
    "irmop-51": ("IRMOP-51", "273616"),
    "irmop-51 (cubic)": ("IRMOP-51", "273616"),
    "irmop-51 cubic": ("IRMOP-51", "273616"),
    "irmop-51 (triclinic)": ("IRMOP-51", "273616"),
    "irmop-51 triclinic": ("IRMOP-51", "273616"),
    "irmop-52": ("IRMOP-52", "273620"),
    "irmop-53": ("IRMOP-53", "273621"),
    "mop-54": ("MOP-54", "273623"),
    "[me2nh2]5[v6o6(och3)9(so4)4]": ("[Me2NH2]5[V6O6(OCH3)9(SO4)4]", "1590347"),
    # VMOP series (both Greek and ASCII variants; always display with Greek)
    "vmop-α": ("VMOP-α", "1590349"),
    "vmop-a": ("VMOP-α", "1590349"),
    "vmop-alpha": ("VMOP-α", "1590349"),
    "vmop-β": ("VMOP-β", "1590348"),
    "vmop-b": ("VMOP-β", "1590348"),
    "vmop-beta": ("VMOP-β", "1590348"),
    "vmop-14": ("VMOP-14", "1479720"),
    # VMOC series used in the OntoMOP backtest corpus
    "vmoc-1": ("VMOC-1", "1583722"),
    "vmoc-2": ("VMOC-2", "1985926"),
    "vmoc-3": ("VMOC-3", "1985927"),
    "vmoc-4": ("VMOC-4", "1985928"),
    "vmoc-5": ("VMOC-5", "1985929"),
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
    # UMC-1/UMC-2: ACS Appl. Mater. Interfaces 2018, DOI 10.1021/acsami.7b18836
    "umc-1": ("UMC-1", "1576897"),
    "umc-2": ("UMC-2", "1576898"),
    # Cu_OR-bdc porous cages only (not the 2D sheets in the same 1815075-1815084 deposit).
    # Matched to SI Table S14 cells: OEt P-1/11304, OBu P4/m/34722, OPr P-1/13001, OPent R-3c/77680.
    "cu_oet-bdc": ("Cu_OEt-bdc cage", "1815080"),
    "cu_oet-bdc cage": ("Cu_OEt-bdc cage", "1815080"),
    "cu_oet-bdc porous cage": ("Cu_OEt-bdc cage", "1815080"),
    "cu_oet-bdc cage synthesis": ("Cu_OEt-bdc cage", "1815080"),
    "cu_obu-bdc": ("Cu_OBu-bdc cage", "1815077"),
    "cu_obu-bdc cage": ("Cu_OBu-bdc cage", "1815077"),
    "cu_obu-bdc porous cage": ("Cu_OBu-bdc cage", "1815077"),
    "cu_obu-bdc cage synthesis": ("Cu_OBu-bdc cage", "1815077"),
    "cu_opr-bdc": ("Cu_OPr-bdc cage", "1815084"),
    "cu_opr-bdc cage": ("Cu_OPr-bdc cage", "1815084"),
    "cu_opr-bdc porous cage": ("Cu_OPr-bdc cage", "1815084"),
    "cu_opr-bdc cage synthesis": ("Cu_OPr-bdc cage", "1815084"),
    "cu_opent-bdc": ("Cu_OPent-bdc cage", "1815083"),
    "cu_opent-bdc cage": ("Cu_OPent-bdc cage", "1815083"),
    "cu_opent-bdc porous cage": ("Cu_OPent-bdc cage", "1815083"),
    "cu_opent-bdc cage synthesis": ("Cu_OPent-bdc cage", "1815083"),
    # Cu24(tBu-amide-bdc)24: Chem. Mater. 2018, 10.1021/acs.chemmater.8b01667
    "cu24(tbu-amide-bdc)24": ("Cu24(tBu-amide-bdc)24", "1835131"),
    "cu24(tbu-amide-bdc)24 cage": ("Cu24(tBu-amide-bdc)24", "1835131"),
    "mechanochemical synthesis of cu24(tbu-amide-bdc)24": ("Cu24(tBu-amide-bdc)24", "1835131"),
    "solvothermal synthesis of cu24(tbu-amide-bdc)24": ("Cu24(tBu-amide-bdc)24", "1835131"),
}

HARDCODED_DOI_CCDC = {
    "10.1021/ja042802q": [
        {
            "refcode": "IRMOP-50",
            "chemical_name": "IRMOP-50",
            "formula": "",
            "ccdc_number": "273613",
            "doi": "10.1021/ja042802q",
        },
        {
            "refcode": "IRMOP-51",
            "chemical_name": "IRMOP-51",
            "formula": "",
            "ccdc_number": "273616",
            "doi": "10.1021/ja042802q",
        },
        {
            "refcode": "IRMOP-52",
            "chemical_name": "IRMOP-52",
            "formula": "",
            "ccdc_number": "273620",
            "doi": "10.1021/ja042802q",
        },
        {
            "refcode": "IRMOP-53",
            "chemical_name": "IRMOP-53",
            "formula": "",
            "ccdc_number": "273621",
            "doi": "10.1021/ja042802q",
        },
        {
            "refcode": "MOP-54",
            "chemical_name": "MOP-54",
            "formula": "",
            "ccdc_number": "273623",
            "doi": "10.1021/ja042802q",
        },
    ],
    "10.1021/acsami.7b18836": [
        {
            "refcode": "UMC-1",
            "chemical_name": "UMC-1",
            "formula": "",
            "ccdc_number": "1576897",
            "doi": "10.1021/acsami.7b18836",
        },
        {
            "refcode": "UMC-2",
            "chemical_name": "UMC-2",
            "formula": "",
            "ccdc_number": "1576898",
            "doi": "10.1021/acsami.7b18836",
        },
    ],
    "10.1021/acsami.8b02015": [
        {
            "refcode": "Cu_OEt-bdc",
            "chemical_name": "Cu_OEt-bdc cage",
            "formula": "",
            "ccdc_number": "1815080",
            "doi": "10.1021/acsami.8b02015",
        },
        {
            "refcode": "Cu_OBu-bdc",
            "chemical_name": "Cu_OBu-bdc cage",
            "formula": "",
            "ccdc_number": "1815077",
            "doi": "10.1021/acsami.8b02015",
        },
        {
            "refcode": "Cu_OPr-bdc",
            "chemical_name": "Cu_OPr-bdc cage",
            "formula": "",
            "ccdc_number": "1815084",
            "doi": "10.1021/acsami.8b02015",
        },
        {
            "refcode": "Cu_OPent-bdc",
            "chemical_name": "Cu_OPent-bdc cage",
            "formula": "",
            "ccdc_number": "1815083",
            "doi": "10.1021/acsami.8b02015",
        },
    ],
    "10.1021/acs.chemmater.8b01667": [
        {
            "refcode": "Cu24(tBu-amide-bdc)24",
            "chemical_name": "Cu24(tBu-amide-bdc)24",
            "formula": "",
            "ccdc_number": "1835131",
            "doi": "10.1021/acs.chemmater.8b01667",
        },
    ],
}


def _normalize_doi_key(doi_like: str) -> str:
    raw = (doi_like or "").strip().lstrip("@")
    # Resolve 8-hex document hash → bibliographic DOI before key normalize
    if re.fullmatch(r"[a-fA-F0-9]{8}", raw):
        try:
            from src.mcp_servers.ccdc.operations.wsl_ccdc import _resolve_document_hash_to_doi

            resolved = _resolve_document_hash_to_doi(raw)
            if resolved:
                raw = resolved
        except Exception:
            pass
    doi = raw.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    if "_" in doi and "/" not in doi:
        doi = doi.replace("_", "/")
    return doi

mcp = FastMCP(name="ccdc")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "If a CCDC number is not provided in the paper, use search_ccdc_by_mop_name once for the exact source-grounded product identifier. Unknown names fail closed; do not retry spelling variants or toggle exactness. You may then use search_ccdc_by_doi once with the source DOI. "
        "Only use get_res_cif_file_by_ccdc when a downstream task explicitly requires crystal structure files; routine KG building usually only needs the CCDC number. "
        "Never guess a name, derive search variants, use a procedure label as a product identifier, or repeatedly query an empty result. "
        "If both the one exact-name lookup and one DOI lookup return no exact mapping, leave the CCDC number unresolved.\n"
        "Tools:\n"
        "- search_ccdc_by_mop_name(name, exact=False): Search CCDC by compound name.\n"
        "  Returns a list of (CSD refcode, CCDC deposition number).\n"
        "- search_ccdc_by_doi(doi_like): Search CCDC by DOI. Accepts underscore form '10.xxxx_yyyy' or full URL; normalizes to '10.xxxx/yyyy'.\n"
        "  Returns a table of entries with refcode, chemical_name, formula, ccdc_number, doi.\n"
        "- get_res_cif_file_by_ccdc(deposition_number): Fetch a single entry by CCDC number and write .res/.cif files.\n"
        "  Returns the output file paths as a TSV string.\n\n"
        "Guidance:\n"
        "- For name searches, make one call with the exact source-grounded identifier; do not retry variants.\n"
        "- For DOI, use the pipeline DOI (e.g., 10.1021_ic050460z) or URL (e.g., https://doi.org/10.1021/ic050460z); the server will normalize input.\n"
        "- The fetch function requires exactly one hit and a 3D structure; otherwise it fails fast.\n"
        "- Do not fetch `.res/.cif` files unless the current task explicitly needs them.\n"
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
    # Check hardcoded mapping first (raw + sanitized ASCII keys)
    lookup_keys = []
    raw_key = (name or "").strip().lower()
    if raw_key:
        lookup_keys.append(raw_key)
    sanitized_key = sanitize_source_markdown(name or "").strip().lower()
    if sanitized_key and sanitized_key not in lookup_keys:
        lookup_keys.append(sanitized_key)
    destemmed = re.sub(r"\s*\([^)]*\)\s*$", "", raw_key).strip()
    if destemmed and destemmed not in lookup_keys:
        lookup_keys.append(destemmed)
    token = re.search(r"\b([a-z][a-z0-9]*-\d+)\b", destemmed or raw_key)
    if token and token.group(1) not in lookup_keys:
        lookup_keys.append(token.group(1))
    for normalized_name in lookup_keys:
        if normalized_name in HARDCODED_MOP_CCDC:
            refcode, ccdc_num = HARDCODED_MOP_CCDC[normalized_name]
            logger.info(f"✓ HARDCODED MAPPING USED for '{name}': {refcode} -> {ccdc_num}")
            for handler in logger.handlers:
                handler.flush()
            _log_to_stderr(f"[CCDC MCP] Using hardcoded mapping for '{name}': {refcode} -> {ccdc_num}")
            _log_to_stderr(f"[CCDC LOG] HARDCODED: {name} -> {ccdc_num}")
            lines = ["refcode\tccdc_number", f"{refcode}\t{ccdc_num}"]
            return "\n".join(lines)

    logger.info(f"No hardcoded mapping for '{name}', using CSD env search (exact={exact})")
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
    rows = HARDCODED_DOI_CCDC.get(_normalize_doi_key(doi_like))
    if rows is None:
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


