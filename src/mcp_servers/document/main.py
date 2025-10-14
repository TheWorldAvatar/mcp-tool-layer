from fastmcp import FastMCP
from src.utils.global_logger import get_logger, mcp_tool_logger 
from src.mcp_servers.document.operations.classify import classify_section
    
from threading import Lock

logger = get_logger("mcp_server", "document_main")
mcp = FastMCP(name="document")

@mcp.tool(name="keep_or_discard_section", description="Mark a section as keep or discard in the sections.json file")
@mcp_tool_logger
def keep_or_discard_section(section_index: int, option: str, doi: str) -> str:
    """
    Load sections.json, update a section with "keep" or "discard", and save back to sections.json.
    
    Args:
        
        section_index: The section number to update
        option: Either "keep" or "discard"
        doi: The task name (DOI identifier)
    
    Returns:
        Success message or error description
    """
    result = classify_section(section_index, option, doi)
    return result['message']
 
if __name__ == "__main__":
    mcp.run(transport="stdio")





