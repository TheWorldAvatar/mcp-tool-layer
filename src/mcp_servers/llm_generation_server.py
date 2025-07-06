from fastmcp import FastMCP
from src.mcp_descriptions.llm_generation import LLM_GENERATION_DESCRIPTION

mcp = FastMCP("llm_generation")


@mcp.tool(name="llm_generation", description=LLM_GENERATION_DESCRIPTION)
def llm_generation(file_name: str, file_content: str) -> str:
    file_path = f"sandbox/tasks/{file_name}"
    return file_path



if __name__ == "__main__":
    mcp.run(transport="stdio")