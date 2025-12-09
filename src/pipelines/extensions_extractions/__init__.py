"""
Extensions Extractions Pipeline Step

This module handles extraction and KG building for extension ontologies:
- OntoMOPs: Metal-organic polyhedra descriptions
- OntoSpecies: Chemical species characterizations

Each extension:
1. Extracts relevant information from the paper for each top-level entity
2. Runs an agent with MCP tools to build the extension A-Box
3. Links the extension A-Box to the main OntoSynthesis A-Box
"""

from .extract import run_step

__all__ = ["run_step"]

