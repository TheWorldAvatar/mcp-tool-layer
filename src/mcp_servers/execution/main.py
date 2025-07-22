from fastmcp import FastMCP
from src.mcp_servers.execution.operations.file_access import full_file_access
from src.utils.global_logger import get_logger, mcp_tool_logger

mcp = FastMCP(name ="execution_utils", instructions="""This provides some utilities for task execution, including full file access""")
logger = get_logger("mcp_server", "execution_utils")

@mcp.prompt(name="instruction", description="This prompt provides detailed instructions for executing the task")
def instruction_prompt():
    return """
    full_file_access tool allows reading the file content, including json, ttl, and obda files. Other file types are not allowed during the execution process.
    """

@mcp_tool_logger
@mcp.tool(name="full_file_access", description="This tool provides full file access to the file system. Currently, it only supports json, ttl, and obda files. Any other file type should not be read in the execution process. use relative path to access the file.")
def full_file_access_tool(file_path: str) -> str:
    logger.info(f"Full file access tool called with file path: {file_path}")
    content = full_file_access(file_path)
    logger.info(f"Full file access tool returned content (first 50 chars): {content[:min(50, len(content))]}")
    return content

if __name__ == "__main__":
    mcp.run(transport="stdio")






