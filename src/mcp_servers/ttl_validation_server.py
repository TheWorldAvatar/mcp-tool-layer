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
mcp = FastMCP(name="ontology_creation_and_validation", instructions="""This is a tool to create an ontology and validate it. Always validate the ontology after creating it. In most of the cases, you will need to use this set of tools to integrate data into semantic stack.

In addition, it is almost always true that you need to create a data schema file as the context for creating the ontology. 

Ontologies created must be compatible with the data schema. You should always design the ontology based on the data schema. 
""")
 
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



from models.Ontology import OntologyInput, OntologyBuilder, ClassInput, PropertyInput   
from typing import Union
from pathlib import Path
from fastmcp import FastMCP
import logging
log = logging.getLogger(__name__)   

# ---------------------------------------------------------------------------
# FastMCP public interface
# ---------------------------------------------------------------------------

mcp = FastMCP("ontology_creation")


ONTOLOGY_CREATION_DESCRIPTION = """
Generate an ontology (ttl file) from rich input. This is the only way to create ttl files according to the schema of given data. 

Usually, before creating the ontology, you should first produce schema of the data.   
"""


def _check_spaces_in_prefix(ontology: OntologyInput) -> bool:
    for prefix in ontology.prefixes:
        if " " in prefix:
            return True, f"Prefix {prefix} contains spaces, this is not allowed."
    return False, None




@mcp.tool( name="create_ontology", description="Generate an turtle ontology from rich input")
def create_ontology(ontology: OntologyInput) -> str:
    """Build, serialise, and persist an ontology based on *ontology* input."""
    return _create_ontology(ontology=ontology)


def _create_ontology(
    *,
    ontology: OntologyInput,
    output_dir: Union[Path, str] = Path("ontologies"),
) -> str:
    """Build, serialise, and persist an ontology based on *ontology* input."""

    # check for spaces in the prefixes
    has_spaces, error_message = _check_spaces_in_prefix(ontology)
    if has_spaces:
        log.error(error_message)
        return error_message


    builder = OntologyBuilder(ontology)
    g = builder.build()

    out_path = Path(output_dir) / f"{ontology.name.replace(' ', '_')}.ttl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(out_path), format="turtle")
    log.info("Ontology saved to %s", out_path)

    return g.serialize(format="turtle")


# ---------- bootstrap the server --------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")