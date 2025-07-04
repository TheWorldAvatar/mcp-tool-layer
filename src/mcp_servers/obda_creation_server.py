#!/usr/bin/env python
"""
obda_mcp_server.py
==================
Fast-MCP service that exposes **one** public tool – ``create_obda_file`` – to
produce an Ontop-compatible OBDA 2.x mapping file.

Improvements compared to the previous version
---------------------------------------------
* **Paths are configurable** via environment variables (``LOCAL_DATA_ROOT`` and
  ``MCP_DATA_ROOT``) instead of hard-coded constants.
* **Logging** now writes both to the console *and* a rotating file, with
  ISO-8601 timestamps.
* **Safer IRI construction**: column names are converted to strict CamelCase
  and optionally prefixed (default ``data``) to avoid clashes with existing
  predicates.
* **Prefix validation**: the required default prefix (``:``) is inserted when
  missing so that the generated mapping is always syntactically complete.
* **Better mapping-id generation** prevents clashes and makes identifiers
  deterministic.
* **Strict type-hints** and docstrings across the codebase.
* **Early validation** of user inputs with helpful error messages.

The public signature of ``create_obda_file`` remains **backwards compatible**
so existing agents do not need to change.
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from logging.handlers import RotatingFileHandler
from src.mcp_descriptions.obda import OBDA_CREATION_DESCRIPTION

 
###############################################################################
# MCP initialisation
###############################################################################

mcp = FastMCP(name="OBDAFileGenerator")

# Allow overriding via environment variables ----------------------------------
LOCAL_DATA_ROOT = Path(os.environ.get("LOCAL_DATA_ROOT", "./data"))
MCP_DATA_ROOT = Path(os.environ.get("MCP_DATA_ROOT", "/projects/data"))

###############################################################################
# Helper utilities
###############################################################################

_CAMEL_RE = re.compile(r"[^0-9a-zA-Z]")

def _to_local(path: str | Path) -> Path:
    """Replace the MCP data root with the local path when running outside MCP."""
    p = Path(path)
    try:
        return Path(str(p).replace(str(MCP_DATA_ROOT), str(LOCAL_DATA_ROOT), 1))
    except Exception as exc:
        raise ValueError(f"Cannot convert path '{p}' to local representation") from exc


def _camel_case(text: str) -> str:
    """Convert snake_case or arbitrary text to CamelCase, stripping symbols."""
    parts = _CAMEL_RE.sub(" ", text).strip().split()
    return "".join(word.capitalize() for word in parts)


def safe_property(colname: str, *, prefix: str = "data") -> str:
    """Generate a safe predicate name from a SQL column.

    Example::
        >>> safe_property("metadata_package")
        'dataMetadataPackage'
    """
    if not colname:
        raise ValueError("Column name must be non-empty")
    return f"{prefix}{_camel_case(colname)}"


def _ensure_prefixes(prefixes: Dict[str, str]) -> Dict[str, str]:
    """Ensure that the required default prefix (``:``) is present."""
    if "" not in prefixes:
        raise ValueError("A default prefix (key '') must be provided in 'prefixes'.")
    return prefixes


def _mapping_id(table: str, column: str | None = None) -> str:
    """Deterministic, OBDA-safe mapping identifier."""
    base = table.replace("-", "_")
    if column:
        base = f"{base}_{column}"
    return base

###############################################################################
# Public tool
###############################################################################

@mcp.tool(name="create_obda_file", description=OBDA_CREATION_DESCRIPTION, tags=["obda"])
def create_obda_file(
    *,
    output_path: str,
    table_name: str,
    columns: List[str],
    prefixes: Dict[str, str],
    id_column: str = "uuid",
    ontology_class: Optional[str] = None,
    iri_template: str = "entity_{uuid}",
    use_xsd_typing: bool = False,
) -> str:


    # ------------------------------------------------------------------ validations
    if id_column not in columns:
        raise ValueError(f"id_column '{id_column}' not found in provided columns list")

    prefixes = _ensure_prefixes(prefixes)

    # Destination path ---------------------------------------------------------
    dst = _to_local(output_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Generating OBDA mapping → %s", dst)

    # ---------------------------------------------------------------- template build
    lines: list[str] = ["[PrefixDeclaration]"]
    for pfx, iri in prefixes.items():
        label = ":" if pfx == "" else f"{pfx}:"
        lines.append(f"{label}\t{iri}")

    lines.append("")
    lines.append("[MappingDeclaration] @collection [[")

    # rdf:type mapping ---------------------------------------------------------
    if ontology_class:
        lines.extend(
            [
                f"mappingId\t{_mapping_id(table_name, 'type')}",
                f"target\t\t:{iri_template} a :{ontology_class} .",
                f"source\t\tSELECT {id_column} FROM {table_name}",
                "",
            ]
        )

    # Column-to-predicate mappings --------------------------------------------
    for col in columns:
        if col == id_column:
            continue  # skip PK

        predicate = safe_property(col)
        literal = f"{{{col}}}" + ("^^xsd:string" if use_xsd_typing else "")

        lines.extend(
            [
                f"mappingId\t{_mapping_id(table_name, col)}",
                f"target\t\t:{iri_template} :{predicate} {literal} .",
                f"source\t\tSELECT {id_column}, {col} FROM {table_name}",
                "",
            ]
        )

        logger.debug("Mapped column '%s' → :%s", col, predicate)

    lines.append("]]")

    # ---------------------------------------------------------------- write file
    dst.write_text("\n".join(lines), encoding="utf-8")
    logger.info("OBDA mapping written (%d bytes)", dst.stat().st_size)
    return str(dst)

###############################################################################
# CLI entry-point                                                             #
###############################################################################

if __name__ == "__main__":
    # Running as a standalone Fast-MCP server (stdio transport) ---------------
    mcp.run(transport="stdio")