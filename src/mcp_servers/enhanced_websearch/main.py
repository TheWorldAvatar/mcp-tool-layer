from fastmcp import FastMCP
from src.utils.global_logger import mcp_tool_logger
from src.mcp_servers.enhanced_websearch.operations.serper_search import google_search
from src.mcp_servers.enhanced_websearch.operations.docling_fetch import url_to_markdown

mcp = FastMCP(name="enhanced_websearch")

@mcp.tool(name="google_search", description="""
Search Google using Serper API.

Parameters:
- query: The search query string
- page: Cumulative page number (1 returns page 1, 2 returns pages 1+2, 3 returns pages 1+2+3, etc.)
""")
@mcp_tool_logger
def google_search_tool(query: str, page: int = 1) -> str:
    """Search Google and return JSON results."""
    return google_search(query, page)

@mcp.tool(name="url_to_markdown", description="""
Convert URL content to markdown format using Docling.

Parameters:
- url: The URL to fetch and convert to markdown
""")
@mcp_tool_logger
def url_to_markdown_tool(url: str) -> str:
    """Convert URL content to markdown."""
    return url_to_markdown(url)

if __name__ == "__main__":
    mcp.run(transport="stdio")
