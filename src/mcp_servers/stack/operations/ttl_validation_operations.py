#!/usr/bin/env python
"""
TTL validation operations
Functions for validating Turtle (.ttl) files with rdflib.
"""

from pathlib import Path
from rdflib import Graph
from models.Ontology import OntologyInput, OntologyBuilder, ClassInput, PropertyInput   
from typing import Union
import logging

log = logging.getLogger(__name__)   

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
 

def create_ontology(ontology: OntologyInput) -> str:
    """Build, serialise, and persist an ontology based on *ontology* input."""
    return _create_ontology(ontology=ontology)

def _create_ontology(
    *,
    ontology: OntologyInput,
    output_dir: Union[Path, str] = Path("ontologies"),
) -> str:
    """Build, serialise, and persist an ontology based on *ontology* input."""

    builder = OntologyBuilder(ontology)
    g = builder.build()

    out_path = Path(output_dir) / f"{ontology.name.replace(' ', '_')}.ttl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(out_path), format="turtle")
    log.info("Ontology saved to %s", out_path)

    return g.serialize(format="turtle") 