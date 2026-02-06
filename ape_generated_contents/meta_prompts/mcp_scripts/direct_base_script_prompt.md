# Direct MCP Base Script Generation Meta-Prompt

You are generating the **BASE/FOUNDATION/INFRASTRUCTURE** script that provides ONLY core utilities for knowledge graph operations.

## Task

Generate a complete base script that implements **INFRASTRUCTURE ONLY**:
- Guard system (`_guard_*`, `_guard_paths`, `_load_guard_state`, `_save_guard_state`)
- Namespace definitions (primary `NAMESPACE` + any additional namespaces provided by configuration / ontology input)
- JSON/Error formatting helpers (`_json_line`, `_format_error`, `_format_success_json`)
- Auto-create helper functions (`_find_or_create_*`)
- Memory management wrappers (`init_memory_wrapper`, `export_memory_wrapper`)

**⚠️ CRITICAL: DO NOT INCLUDE:**
- `check_existing_*` functions (these are in `{script_name}_checks.py`)
- `add_*_to_*` functions (these are in `{script_name}_relationships.py`)
- `create_*` functions (these are in `{script_name}_entities_1.py` and `{script_name}_entities_2.py`)

**This is the FOUNDATION script that other scripts import from.**

## Inputs

**Ontology Name**: `{ontology_name}`
**Script Name**: `{script_name}_base.py`
**Namespace URI**: `{namespace_uri}`

**Ontology Structure** (concise, focused extraction):
```
{ontology_structure}
```

**Available Universal Utils Functions**:
{universal_utils_functions}

## Script Structure

### 1. Module Docstring and Imports

```python
#!/usr/bin/env python3
"""
{script_name}_base.py

Infrastructure/foundation module for {ontology_name} ontology operations.
Provides: guard system, namespaces, formatting helpers, auto-create helpers, memory wrappers.

DOES NOT include:
- check_existing_* functions (in {script_name}_checks.py)
- add_*_to_* functions (in {script_name}_relationships.py)
- create_* functions (in {script_name}_entities_1.py and {script_name}_entities_2.py)
"""

import os
import json
import hashlib
from functools import wraps
from typing import Dict, List, Optional, Any, Literal
from rdflib import Graph, Namespace, URIRef, Literal as RDFLiteral, RDF, RDFS

# Import universal utilities
from ..universal_utils import (
    locked_graph, init_memory, export_memory, _mint_hash_iri,
    _iri_exists, _find_by_type_and_label, _get_label, _set_single_label,
    _ensure_type_with_label, _require_existing, _sanitize_label,
    _format_success, _list_instances_with_label, _to_pos_int,
    _export_snapshot_silent, get_memory_paths, inspect_memory
)

# Namespace definitions (CRITICAL - export ALL for entities script to import)
NAMESPACE = Namespace("{namespace_uri}")
# IMPORTANT: Define any additional namespaces that appear in the ontology structure,
# including external ontologies (e.g., OM-2) or secondary namespaces under the same base URI.
# Do NOT hardcode domain/ontology-specific namespace lists here; derive what you define from the provided ontology input.
#
# Example (external ontology, blurred):
# EXTERNAL_NS = Namespace("<external_ontology_uri>")
RDF = RDF
RDFS = RDFS
```

### 2. Guard System

Implement the complete guard system:
- `_guard_paths()` - paths for guard state files
- `_load_guard_state()` - load guard state
- `_save_guard_state(state)` - save guard state
- `_guard_note_check(kind)` - note check call
- `_guard_note_noncheck()` - note non-check call
- `_guard_check(func)` - decorator for check functions
- `_guard_noncheck(func)` - decorator for create/modify functions

**Reliability requirement (IMPORTANT):**
- The guard system must be **non-fatal by default**. It must never crash MCP tool execution.
- If you want to enforce guard rules, make it **opt-in** via an environment variable (e.g., `TWA_MCP_GUARD_ENFORCE=1`).
- Even in enforcement mode, require **at least one check** before write-like operations, not a check before *every* mutation.
- If enforcement fails, return a JSON error envelope via `_format_error(...)` instead of raising.

### 3. JSON/Error Formatting Helpers

```python
def _json_line(d: dict) -> str:
    """Convert dict to single-line JSON string."""
    return json.dumps(d, ensure_ascii=False, separators=(',', ':'))

def _format_error(message: str, *, code: str="VALIDATION_FAILED", retryable: bool=False, **extra) -> str:
    """Format error response as JSON envelope."""
    payload = {{
        "status": "error",
        "iri": None,
        "created": False,
        "retryable": bool(retryable),
        "code": code,
        "message": message,
    }}
    payload.update(extra)
    return _json_line(payload)

def _format_success_json(iri: URIRef or str or None, message: str, *, created: bool, **extra) -> str:
    """Format success response as JSON envelope."""
    payload = {{
        "status": "ok",
        "iri": (str(iri) if iri is not None else None),
        "created": bool(created),
        "code": None,
        "message": message,
    }}
    payload.update(extra)
    return _json_line(payload)
```

### 4. Auto-Create Helper Functions (_find_or_create_*)

These enable the "auto-creation pattern" where main creation functions can accept auxiliary entity parameters and handle finding/creating them internally.

**Generate helpers for auxiliary/supporting entities:**
- Derive which auxiliaries need `_find_or_create_*` from the provided ontology structure (do NOT rely on hardcoded example entity names).
- Prioritize entities that are frequently referenced as object-property targets but have relatively few outgoing connections.

**Pattern:**
```python
def _find_or_create_{{EntityType}}(g: Graph, label: str, **additional_params) -> URIRef:
    """
    Find existing {{EntityType}} by label, or create new one.
    Enables auto-creation pattern in main entity creation functions.
    
    Returns:
        URIRef of existing or newly created entity
    """
    sanitized_label = _sanitize_label(label)
    
    # Search for existing
    for candidate in g.subjects(RDF.type, NAMESPACE.{{EntityType}}):
        if _get_label(g, candidate) == sanitized_label:
            return candidate
    
    # Not found - create new
    iri = _mint_hash_iri("{{EntityType}}")
    g.add((iri, RDF.type, NAMESPACE.{{EntityType}}))
    _set_single_label(g, iri, sanitized_label)
    
    # Handle additional parameters (when the ontology indicates auxiliary metadata fields)
    # Add triples for additional params
    
    return iri
```

### 5. Memory Management Wrappers

```python
@_guard_noncheck
def init_memory_wrapper(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None) -> str:
    """Initialize or resume memory graph."""
    try:
        return init_memory(doi, top_level_entity_name)
    except Exception as e:
        return _format_error(f"Failed to initialize memory: {{e}}")

@_guard_noncheck
def export_memory_wrapper() -> str:
    """Export entire graph to TTL file."""
    try:
        return export_memory()
    except Exception as e:
        return _format_error(f"Failed to export memory: {{e}}")
```

## Requirements

1. **Infrastructure ONLY**: Generate ONLY guard system, namespaces, helpers, and `_find_or_create_*` functions
2. **NO check_existing_* functions**: These are in `{script_name}_checks.py`
3. **NO add_* functions**: These are in `{script_name}_relationships.py`
4. **NO create_* functions**: These are in `{script_name}_entities_1.py` and `{script_name}_entities_2.py`
5. **Exportable**: All classes/functions must be importable by other scripts (guard decorators, namespaces, helpers)
6. **Guard System**: Complete implementation (decorators, state management)
7. **Error Handling**: Robust error handling with JSON envelopes

8. **Unit enforcement awareness (OM-2)**: If the ontology uses OM-2 quantities, the overall system expects **strict unit handling** (label→IRI mapping and validation) driven by ontology inputs (e.g., OM-2 mock T-Box). Do not hardcode unit tables; derive any unit label options from the provided ontology input.

9. **OM-2 quantity helpers (if OM-2 is mentioned in ontology input)**:
   If the ontology structure mentions OM-2 quantities/units (e.g., Temperature/Pressure/Duration/Volume and an OM-2 unit inventory section),
   this base script MUST include reusable helpers/constants to support unit-safe quantity creation in other modules:
   - `OM2 = Namespace("<om2_namespace_uri>")` (use the configured namespace contract / inputs; do not hardcode URIs)
   - `OM2_UNIT_MAP: Dict[str, URIRef]` constructed ONLY from the provided OM-2 unit inventory (label → OM2.<term>)
   - Optional `Literal[...]` / Enum types for unit labels (do NOT list anything not present in the inventory)
   - A small helper like `_resolve_om2_unit(unit_label: str) -> URIRef` (validates via OM2_UNIT_MAP)
   - A small helper like `_find_or_create_om2_quantity(g, quantity_class: URIRef, label: str, value: float, unit_label: str) -> URIRef` that:
     - validates unit_label via OM2_UNIT_MAP
     - reuses an existing quantity instance if the graph already contains the SAME `quantity_class` with SAME numerical value and SAME unit
     - otherwise creates a new instance and sets exactly one `om-2:hasNumericalValue` and one `om-2:hasUnit`
     - uses a numeric datatype (e.g., XSD.double) for numerical values
   - IMPORTANT: This module must be the single source of truth for unit mappings (do NOT duplicate per-file unit maps in entity scripts).
   
   These are infrastructure helpers (not ontology entity create_* functions), but they are required so entity/relationship scripts can handle
   related concepts mentioned by the ontology (e.g., Temperature nodes).

## Output

Generate ONLY the Python code for `{script_name}_base.py`. No explanations, no markdown outside code blocks.

