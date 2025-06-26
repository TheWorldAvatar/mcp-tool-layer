# math_server.py
from mcp.server.fastmcp import FastMCP
import logging

mcp = FastMCP("test")
logger = logging.getLogger(__name__)
# write to a file
logger.addHandler(logging.FileHandler("test.log"))

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    logger.info(f"Adding {a} and {b}")
    return a + b

@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers"""
    logger.info(f"Multiplying {a} and {b}")
    return a * b

if __name__ == "__main__":
    mcp.run(transport="stdio")