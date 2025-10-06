from fastmcp import FastMCP

mcp = FastMCP("placeholder")

@mcp.tool()
def placeholder_tool(input: str) -> str:
    """
    This is a placeholder tool with no functionality.
    """
    return "This is a placeholder tool with no functionality."


if __name__ == "__main__":
    mcp.run(transport="stdio")


