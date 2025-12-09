# Direct MCP Base Script Generation Meta-Prompt

You are generating the **BASE/FOUNDATION/INFRASTRUCTURE** script that provides ONLY core utilities for knowledge graph operations.

## Task

Generate a complete base script that implements **INFRASTRUCTURE ONLY**:
- Guard system (`_guard_*`, `_guard_paths`, `_load_guard_state`, `_save_guard_state`)
- Namespace definitions (ONTOSYN, ONTOLAB, ONTOMOPS, etc.)
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
# IMPORTANT: Define ALL ontology namespaces explicitly
# For ontosynthesis, this includes:
ONTOSYN = Namespace("{namespace_uri}OntoSyn/")
ONTOLAB = Namespace("{namespace_uri}OntoLab/")
ONTOMOPS = Namespace("{namespace_uri}ontomops/")
MATERIAL = Namespace("http://www.theworldavatar.com/ontology/ontocape/material/material.owl#")
# Add other namespaces as needed for your ontology
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

**Generate helpers for commonly referenced auxiliary entities:**
- Vessel, VesselType, VesselEnvironment
- ChemicalInput, Supplier
- Equipment, HeatChillDevice
- Any other entities frequently used as auxiliary/supporting entities

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
    
    # Handle additional parameters (e.g., vessel_type for Vessel)
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

## Output

Generate ONLY the Python code for `{script_name}_base.py`. No explanations, no markdown outside code blocks.

