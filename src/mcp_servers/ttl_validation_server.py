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
from fastmcp import FastMCP
from src.mcp_descriptions.ttl import TTL_VALIDATION_DESCRIPTION
# --------------------------------------------------------------------------- #
mcp = FastMCP("ttl_validator")
 
# ---------- public MCP tools -------------------------------------------------
@mcp.tool(name="validate_ttl_file", description=TTL_VALIDATION_DESCRIPTION, tags=["ontology"])
def validate_ttl_file(ttl_path: str) -> str:
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