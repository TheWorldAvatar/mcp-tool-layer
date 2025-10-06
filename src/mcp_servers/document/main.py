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


# @mcp.tool(name="get_classification_status", description="Get the classification status for a task")
# @mcp_tool_logger
# def get_status(doi: str) -> str:
#     """
#     Get the classification status for a task.
    
#     Args:
#         doi: The task name (DOI identifier)
    
#     Returns:
#         Status information as a formatted string
#     """
#     status = get_classification_status(doi)
    
#     if not status['success']:
#         return status.get('message', 'Error getting classification status')
    
#     return f"Classification Status for {doi}:\n" \
#            f"Total sections: {status['total_sections']}\n" \
#            f"Classified: {status['classified']}\n" \
#            f"Keep: {status['keep']}\n" \
#            f"Discard: {status['discard']}\n" \
#            f"Progress: {status['percentage_complete']:.1f}%"

 
if __name__ == "__main__":
    mcp.run(transport="stdio")





