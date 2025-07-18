#!/usr/bin/env python
"""
OBDA creation operations
Functions for creating Ontop-compatible OBDA 2.x mapping files.
"""

from __future__ import annotations

import os
import re
from models.locations import ROOT_DIR
from src.utils.resource_db_operations import ResourceDBOperator
from models.Resource import Resource
 
###############################################################################
# Helper utilities
###############################################################################

_CAMEL_RE = re.compile(r"[^0-9a-zA-Z]")

resource_db_operator = ResourceDBOperator()

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

def _ensure_prefixes(prefixes: dict[str, str]) -> dict[str, str]:
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

def create_obda_file(
    *,
    table_name: str,
    columns: list[str],
    prefixes: dict[str, str],
    id_column: str = "uuid",
    ontology_class: str | None = None,
    iri_template: str = "entity_{uuid}",
    use_xsd_typing: bool = False,
    meta_task_name: str,
    iteration_index: int
) -> str:

    # ------------------------------------------------------------------ validations
    if id_column not in columns:
        raise ValueError(f"id_column '{id_column}' not found in provided columns list")

    prefixes = _ensure_prefixes(prefixes)

    # Destination path ---------------------------------------------------------
    # Only accept relative paths, output to sandbox/data/{meta_task_name}/{iteration_index}/{meta_task_name}_{iteration_index}.obda
    dst = os.path.join(ROOT_DIR, f"sandbox/data/{meta_task_name}/{iteration_index}/{meta_task_name}_{iteration_index}.obda")
    os.makedirs(os.path.dirname(dst), exist_ok=True)


    # ---------------------------------------------------------------- template build
    lines: list[str] = ["[PrefixDeclaration]"]
    for pfx, iri in prefixes.items():
        label = ":" if pfx == "" else f"{pfx}:"
        lines.append(f"{label}\t{iri}")

    lines.append("")
    lines.append("[MappingDeclaration] @collection [[[")

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


    lines.append("]]")

    # ---------------------------------------------------------------- write file
    with open(dst, "w") as f:
        f.write("\n".join(lines))

    relative_output_path = f"sandbox/data/{meta_task_name}/{iteration_index}/{meta_task_name}_{iteration_index}.obda"

    obda_resource = Resource(
        type="obda",
        relative_path=relative_output_path,
        absolute_path=os.path.join(ROOT_DIR, relative_output_path),
        uri=f"file://{os.path.join(ROOT_DIR, relative_output_path)}",
        meta_task_name=meta_task_name,
        iteration=iteration_index,
        description=f"OBDA mapping file for {table_name} with {len(columns)} columns",
    )
    resource_db_operator.register_resource(obda_resource)
    return f"The OBDA mapping file has been created and registered in the resource database. The relative path is {relative_output_path} and the absolute path is {os.path.join(ROOT_DIR, relative_output_path)}"

if __name__ == "__main__":
    result = create_obda_file(
        table_name="ontocompchem",
        columns=["uuid", "name", "description"],
        prefixes={
            "": "http://www.ontocompchem.com/",
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
            "owl": "http://www.w3.org/2002/07/owl#",
        },
        id_column="uuid",
        ontology_class="OntologyClass",
        iri_template="entity_{uuid}",
        meta_task_name="ontocompchem",
        iteration_index=1
    )

    # db_operator = ResourceDBOperator()
    resources = resource_db_operator.get_resources_by_meta_task_name_and_iteration(meta_task_name="ontocompchem4", iteration=2)
    for resource in resources:
        print(resource)

 