# Direct MCP Underlying Script Generation Meta-Prompt

You are an expert in creating domain-specific MCP underlying scripts from ontologies.

## Task

Generate a complete MCP underlying script that implements all necessary functions for knowledge graph construction based on the provided T-Box ontology.

## Inputs

**Ontology Name**: `{ontology_name}`
**Script Name**: `{script_name}`
**Namespace URI**: `{namespace_uri}`

**Reference Implementation Snippet** (for patterns and style only):
```python
{reference_snippet}
```

**T-Box Ontology**:
```turtle
{ontology_ttl}
```

**Extracted Entity Classes** (from T-Box):
{entity_classes}

**Extracted Object Properties** (from T-Box):
{object_properties}

**Extracted Datatype Properties** (from T-Box):
{datatype_properties}

## Design Philosophy

**Key Pattern: Auto-Create Auxiliary Entities Within Main Creation Functions**

Instead of forcing users to create every auxiliary entity separately and then link them, the main creation functions should accept parameters for auxiliary entities and handle finding/creating them internally. This provides a much better user experience.

**Example Pattern** (domain-agnostic):
```python
# BAD: User must create auxiliary entities separately
create_auxiliary_entity_A(name="aux1", type="typeX")  # Returns IRI
create_auxiliary_entity_B(value=100, unit="meter")    # Returns IRI  
create_main_entity(name="main", aux_A_iri="...", aux_B_iri="...")

# GOOD: User provides parameters, function handles everything
create_main_entity(
    name="main",
    aux_A_name="aux1",           # Function finds or creates internally
    aux_A_type="typeX",          # Using _find_or_create_aux_A helper
    aux_B_value=100,             # Function finds or creates internally
    aux_B_unit="meter"           # Using _find_or_create_aux_B helper
)
```

## Requirements

### 1. Import Structure

```python
#!/usr/bin/env python3
"""
{script_name}.py

Domain-specific MCP functions for {ontology_name} ontology.
Generated from T-Box ontology.
"""

import json
from typing import Dict, List, Optional, Any, Literal
from rdflib import Graph, Namespace, URIRef, Literal as RDFLiteral, RDF, RDFS
from datetime import datetime
import hashlib

# Import universal utilities (domain-agnostic helpers)
# IMPORTANT: Use relative import since this script is in ai_generated_contents_candidate/scripts/<ontology>/
# and universal_utils.py is in ai_generated_contents_candidate/scripts/
from ..universal_utils import (
    # Import only the functions listed below that are actually available:
    # {universal_utils_functions}
    locked_graph, init_memory, export_memory, _mint_hash_iri,
    _iri_exists, _find_by_type_and_label, _get_label, _set_single_label,
    _ensure_type_with_label, _require_existing, _sanitize_label,
    _format_success, _list_instances_with_label, _to_pos_int,
    _export_snapshot_silent, get_memory_paths, inspect_memory
)

# Namespace definitions (extract from T-Box)
NAMESPACE = Namespace("{namespace_uri}")
RDF = RDF
RDFS = RDFS
```

### 2. Guard System (prevents repeated check_existing calls)

Study the reference implementation and implement a guard system that:
- Tracks when `check_existing_*` functions are called
- Prevents redundant repeated calls
- Resets when non-check functions are called

Pattern from reference:
```python
import os
import json
from functools import wraps

def _guard_paths():
    """Return paths for guard state files."""
    # Study reference implementation

def _load_guard_state():
    """Load guard state from JSON."""
    # Study reference implementation

def _save_guard_state(state):
    """Save guard state to JSON."""
    # Study reference implementation

def _guard_check(func):
    """Decorator for check_existing_* functions."""
    # Study reference implementation

def _guard_noncheck(func):
    """Decorator for creation/modification functions."""
    # Study reference implementation
```

### 3. Check Existing Functions (Parent-Class Based)

**IMPORTANT**: Group `check_existing_*` functions by **parent class** to avoid redundancy.

**Analysis Required**:
1. Identify the class hierarchy from T-Box (look for `rdfs:subClassOf`)
2. For classes with a common parent, create ONE function for the parent
3. Only create separate functions for top-level classes or those without common parents

**Example Grouping**:
- If `ClassA`, `ClassB`, `ClassC` all inherit from `ParentClass`, create **only** `check_existing_ParentClass()`
- If `ClassX` has no parent, create `check_existing_ClassX()`
- If `ClassY` inherits from external ontology (e.g., `OntoLab:LabEquipment`), create `check_existing_ClassY()`

**Pattern:**
```python
@_guard_check
def check_existing_{{ParentOrStandaloneClass}}() -> str:
    """
    List existing {{ParentOrStandaloneClass}} instances (IRI and label).
    This includes all subclasses: {{list_of_subclasses}}
    """
    with locked_graph() as g:
        return "\\n".join(_list_instances_with_label(g, NAMESPACE.{{ParentOrStandaloneClass}}))
```

**Generate ONLY the minimal set of check_existing functions based on class hierarchy.**

### 4. Relationship-Building Functions (add_* functions)

For **EACH** object property extracted from T-Box, generate `add_*` functions to establish relationships:

**Pattern:**
```python
@_guard_noncheck
def add_{{property_local_name}}_to_{{domain_class}}(
    {{domain_class}}_iri: str,
    {{range_class}}_iri: str
) -> str:
    """
    Attach {{range_class}} to {{domain_class}} via namespace:{{property_local_name}}.
    
    Establishes the relationship defined in the T-Box.
    """
    with locked_graph() as g:
        # Validate subject exists
        subject, msg = _require_existing(g, {{domain_class}}_iri, NAMESPACE.{{domain_class}}, "{{domain_class}}_iri")
        if subject is None:
            raise ValueError(msg or "Subject not found")
        
        # Validate object exists
        obj, msg2 = _require_existing(g, {{range_class}}_iri, NAMESPACE.{{range_class}}, "{{range_class}}_iri")
        if obj is None:
            raise ValueError(msg2 or "Object not found")
        
        # Idempotency check
        if (subject, NAMESPACE.{{property_local_name}}, obj) not in g:
            g.add((subject, NAMESPACE.{{property_local_name}}, obj))
        
        msg_out = f"Attached {{_get_label(g, obj)}} to {{_get_label(g, subject)}}."
    _export_snapshot_silent()
    return _format_success(subject, msg_out)
```

**For object properties with auto-creation support** (where range entity can be created from a label):
```python
@_guard_noncheck
def add_{{property_local_name}}_to_{{domain_class}}(
    {{domain_class}}_iri: str,
    {{range_class}}_iri: str = None,
    {{range_class}}_name: str = None,
    is_existing: bool = True
) -> str:
    """
    Attach {{range_class}} to {{domain_class}}, with auto-creation support.
    
    Can either attach existing entity (is_existing=True, provide IRI)
    or create new entity (is_existing=False, provide name).
    """
    # Study reference implementation for JSON envelope pattern
    # Support both existing IRI and new entity creation
```

**Generate add_* functions for ALL object properties in the T-Box.**

### 5. Entity Creation Functions

⚠️ **CRITICAL REQUIREMENT - DO NOT SKIP ANY CLASSES** ⚠️

You MUST generate a `create_*` function for **EVERY SINGLE class** listed in the extracted entity classes.

**Before you finish, verify:**
- Count the number of classes in the extracted entity classes list
- Count the number of `create_*` functions you generated
- These numbers MUST be EQUAL
- If they don't match, you have MISSED functions - go back and add them ALL

**Example**: If there are 25 classes (Add, ChemicalInput, ChemicalOutput, ChemicalSynthesis, Crystallize, DocumentContext, Dry, Equipment, Evaporate, ExecutionPoint, Filter, HeatChill, HeatChillDevice, Separate, SeparationType, Sonicate, Stir, Supplier, SynthesisStep, Transfer, Vessel, VesselEnvironment, VesselType, Yield, MetalOrganicPolyhedron), you MUST have 25 `create_*` functions.

**NO SHORTCUTS. NO "..." PLACEHOLDERS. GENERATE EVERY SINGLE FUNCTION.**

**CRITICAL DESIGN PATTERN: Three-Phase Creation with Auto-Create Helpers**

Study the reference implementation carefully. Main entity creation functions should:
1. Accept **direct parameters** for auxiliary entities (not IRIs!)
2. Use a **three-phase** pattern: pre-validate → auto-create auxiliaries → create main entity
3. Provide excellent UX by handling everything in one call

```python
def create_{{EntityClassName}}(
    label: str,
    # Required properties
    required_param1: str,
    required_param2: int,
    # Optional datatype properties (extract from T-Box)
    optional_datatype_prop: Optional[str] = None,
    # Auxiliary entity parameters (NOT IRIs - use helper to find/create internally)
    aux_entity_name: Optional[str] = None,
    aux_entity_type: Optional[str] = None,
    measurement_value: Optional[float] = None,
    measurement_unit: Optional[str] = None,
    # ... other auxiliary entity parameters from T-Box
) -> str:
    """
    Create {{EntityClassName}} with validation and deduplication.
    
    Uses three-phase pattern:
    - Phase 1: Validate all inputs (no side effects)
    - Phase 2: Auto-create/find auxiliary entities using _find_or_create_* helpers
    - Phase 3: Create main entity and attach everything
    
    Args:
        label: Human-readable label
        required_param1: Description from T-Box
        aux_entity_name: Name of auxiliary entity (auto-created if needed)
        aux_entity_type: Type of auxiliary entity (auto-created if needed)
        measurement_value: Value for measurement (auto-created if needed with unit)
        measurement_unit: Unit for measurement (auto-created if needed with value)
        # Document each parameter based on T-Box datatype/object properties
    
    Returns:
        JSON envelope with status, iri, created, etc.
    """
    def _json_line(payload: Dict[str, object]) -> str:
        return json.dumps(payload, separators=(",", ":")).replace("\\n", " ")
    
    try:
        with locked_graph() as g:
            # Validation
            sanitized_label = _sanitize_label(label)
            
            # Deduplication check
            existing = _find_by_type_and_label(g, NAMESPACE.{{EntityClassName}}, sanitized_label)
            if existing is not None:
                return _json_line({{
                    "status": "error",
                    "iri": None,
                    "created": False,
                    "code": "ALREADY_EXISTS",
                    "message": f"{{EntityClassName}} with label '{{sanitized_label}}' already exists"
                }})
            
            # IRI minting
            iri = _mint_hash_iri("{{EntityClassName}}")
            
            # Triple creation
            g.add((iri, RDF.type, NAMESPACE.{{EntityClassName}}))
            _set_single_label(g, iri, sanitized_label)
            
            # Add datatype properties from T-Box
            # Add object property links from T-Box
            
        _export_snapshot_silent()
        return _json_line({{
            "status": "ok",
            "iri": str(iri),
            "created": True,
            "code": None,
            "message": f"Created {{EntityClassName}}"
        }})
    except Exception as e:
        return _json_line({{
            "status": "error",
            "iri": None,
            "created": False,
            "code": "INTERNAL_ERROR",
            "message": str(e)
        }})
```

### 6. Auto-Create Helpers (CRITICAL - enables one-call creation pattern)

These internal helpers enable the pattern where main creation functions accept auxiliary entity parameters and handle everything in one call. **Study the reference implementation carefully for examples of this pattern.**

```python
def _find_or_create_{{AuxiliaryEntityType}}(
    g: Graph,
    identifying_param1: str,
    identifying_param2: str
) -> Optional[URIRef]:
    """
    Find existing {{AuxiliaryEntityType}} by identifying parameters, or create new one.
    
    This enables main creation functions to accept parameters for auxiliary entities
    and internally handle finding/creating them without requiring the user to create
    them separately first.
    
    Example: Instead of requiring user to:
      1. create_auxiliary_entity(name="aux1", type="typeX") -> get IRI
      2. create_main_entity(name="main", auxiliary_iri="...")
    
    User can simply:
      create_main_entity(name="main", aux_name="aux1", aux_type="typeX")
    
    Returns:
        URIRef of existing or newly created entity
    """
    # Search for existing entity matching the identifying parameters
    for candidate in g.subjects(RDF.type, NAMESPACE.{{AuxiliaryEntityType}}):
        # Check if all identifying parameters match
        candidate_label = _get_label(g, candidate)
        # Check other identifying properties...
        # If complete match found, return existing entity (avoid duplication)
        if <all_params_match>:
            return candidate
    
    # Not found - create new entity
    iri = _mint_hash_iri("{{AuxiliaryEntityType}}")
    g.add((iri, RDF.type, NAMESPACE.{{AuxiliaryEntityType}}))
    _set_single_label(g, iri, identifying_param1)
    # Add all identifying properties
    return iri
```

**Apply This Pattern To:**
- Measurement/quantity entities (e.g., temperature, duration, volume with value+unit)
- Equipment entities (e.g., containers/vessels with name+type)
- Environmental/condition entities (e.g., atmosphere, pressure conditions)
- Any entity type that is commonly referenced as an auxiliary/supporting entity

**Integration Example in Main Creation Function:**

```python
def create_{{MainEntity}}(
    name: str,
    order: int,
    comment: str,
    # Direct auxiliary entity parameters (no IRI needed!)
    aux_entity_name: Optional[str] = None,
    aux_entity_type: Optional[str] = None,
    measurement_value: Optional[float] = None,
    measurement_unit: Optional[str] = None,
    # ... other params
) -> str:
    """
    Create {{MainEntity}} with automatic auxiliary entity handling.
    
    User provides simple parameters; function internally finds or creates auxiliary entities.
    """
    with locked_graph() as g:
        # PHASE 1: Pre-validation (NO side effects until all checks pass)
        top_iri = _current_top_entity_iri()
        if top_iri is None:
            raise ValueError("No active top entity")
        
        # Validate all inputs (order, required params, etc.)
        # ...
        
        # PHASE 2: Auto-create/find auxiliary entities using helpers
        aux_iri = None
        if aux_entity_name is not None and aux_entity_type is not None:
            # Helper handles finding existing or creating new
            aux_iri = _find_or_create_{{AuxiliaryEntity}}(g, aux_entity_name, aux_entity_type)
        
        measurement_iri = None
        if measurement_value is not None and measurement_unit is not None:
            # Helper handles finding existing or creating new
            measurement_iri = _find_or_create_{{Measurement}}(g, measurement_value, measurement_unit)
        
        # PHASE 3: Create main entity and attach everything
        sanitized_name = _sanitize_label(name)
        iri = _mint_hash_iri("{{MainEntity}}")
        g.add((iri, RDF.type, NAMESPACE.{{MainEntity}}))
        _set_single_label(g, iri, sanitized_name)
        g.add((iri, RDFS.comment, Literal(str(comment))))
        
        # Attach auxiliary entities
        if aux_iri:
            g.add((iri, NAMESPACE.{{hasAuxiliaryEntity}}, aux_iri))
        if measurement_iri:
            g.add((iri, NAMESPACE.{{hasMeasurement}}, measurement_iri))
        
        # Link to parent if applicable
        g.add((top_iri, NAMESPACE.{{hasMainEntity}}, iri))
    
    _export_snapshot_silent()
    return _format_success(iri, f"Created {{MainEntity}} '{{sanitized_name}}'")
```

### 7. Helper Functions

Implement these helper functions (study reference for patterns):

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

def _format_success(iri: URIRef, message: str) -> str:
    """Format success message with IRI."""
    return f"{{str(iri)}} | {{message}}"

def _format_success_json(iri: URIRef|str|None, message: str, *, created: bool, **extra) -> str:
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

### 8. Memory Management Functions

```python
@_guard_noncheck
def init_memory(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None) -> str:
    """Initialize or resume memory graph."""
    # Study reference implementation

@_guard_noncheck
def export_memory() -> str:
    """Export entire graph to TTL file."""
    # Study reference implementation

def _export_snapshot_silent() -> str:
    """Export snapshot silently (internal use after modifications)."""
    _guard_note_noncheck()
    try:
        return export_memory()
    except Exception:
        return ""
```

### 9. Domain-Specific Validation (extract from T-Box comments)

If the T-Box ontology includes `rdfs:comment` annotations with validation rules:
- Extract and implement these rules
- Add runtime validation in appropriate functions
- Use Literal types for enumerated values

Example (if T-Box defines allowed values):
```python
# Extract from T-Box comments/restrictions
ALLOWED_{{PROPERTY_NAME}}_VALUES = {{
    "value1", "value2", "value3"  # From T-Box
}}

# Validate in functions:
if value not in ALLOWED_{{PROPERTY_NAME}}_VALUES:
    raise ValueError(f"Invalid value: {{value}}")
```

## Output Format

Generate ONLY the complete Python code. Do NOT include:
- Markdown code fences (```)
- Explanations or commentary outside the code
- File paths or directory structures

Start directly with `#!/usr/bin/env python3` and the module docstring.

## Critical Guidelines

1. **Domain-Agnostic Core**: Use `universal_utils` for all graph operations
2. **Domain-Specific Logic**: Derive ALL function names from T-Box classes and properties
3. **Complete Coverage**: Generate functions for ALL classes and properties in the extracted lists
4. **Reference Patterns**: Follow the code patterns from the reference snippet
5. **JSON Responses**: Use standardized JSON envelopes for creation functions
6. **Guard System**: Implement guard decorators for all check_existing functions
7. **Validation**: Extract validation rules from T-Box `rdfs:comment` if present
8. **No Assumptions**: Do not assume entity types, properties, or relationships not in the T-Box

Generate the complete underlying script now.
