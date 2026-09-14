"""Shared A-Box health checks for Pipeline and OntoLogX Turtle.

The checker is ontology-neutral: it consumes a published instance graph plus
the T-Box (and optional SHACL) that compiled it. It does not read ledgers or
call an LLM.
"""

from .check import check_abox, check_ttl_file, check_ttl_files, tboxes_without_om2
from .report import Finding, HealthReport

__all__ = [
    "Finding",
    "HealthReport",
    "check_abox",
    "check_ttl_file",
    "check_ttl_files",
    "tboxes_without_om2",
]
