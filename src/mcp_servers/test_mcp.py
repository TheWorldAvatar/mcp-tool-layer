# math_server.py
from fastmcp import FastMCP

mcp = FastMCP("test")
# write to a file
 

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return int(a) + int(b)

@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers"""
    return int(a) * int(b)

@mcp.tool()
def should_be_skipped(a: int, b: int) -> str:
    """This tool should be skipped"""
    return "This tool should be skipped"

if __name__ == "__main__":
    mcp.run(transport="stdio")