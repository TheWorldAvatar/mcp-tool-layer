#!/usr/bin/env python
"""
ttl_validator_mcp.py
A tiny MCP server that validates a Turtle (.ttl) file with rdflib.

• In:  file path (either /projects/data/… or a local Windows path)
• Out: "OK — parsed successfully, N triples."  or  "ERROR — <details>"

Requires:
    pip install rdflib mcp  # (or whatever package gives you FastMCP)
"""

from pathlib import Path

from rdflib import Graph
from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------- #
mcp = FastMCP("ttl_validator")
 
# ---------- public MCP tools -------------------------------------------------
@mcp.tool()
def validate_ttl_file(ttl_path: str) -> str:
    """
    Validate a Turtle file for syntax errors and return a status string.

    The ttl file is the ontology file, which is used to define the ontology, which is necessary for the entire semantic data pipeline.

    After a ttl file is created, it needs to be validated to ensure the syntax is correct.

    Example
    -------
    >>> validate_ttl_file("/projects/data/test/benzene.ttl")
    'OK — parsed successfully, 142 triples.'

    Args:
        ttl_path (str): The path to the ttl file to be validated
        
    """

    # replace /projects/data with data
    ttl_path = ttl_path.replace("/projects/data", "data")

    candidate = Path(ttl_path)
    if not candidate.exists():
        return f"ERROR — file not found: {ttl_path}"

    try:
        g = Graph()
        g.parse(candidate, format="turtle")
        return f"OK — parsed successfully, {len(g)} triples."
    except Exception as exc:  # SyntaxError, BadSyntax, etc. all inherit from Exception
        return f"ERROR — {type(exc).__name__}: {exc}"

# ---------- bootstrap the server --------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")