# Direct MCP Entities Script Generation Meta-Prompt

You are generating the **ENTITIES** script that implements ALL entity creation functions.

## Task

Generate a complete entity group script with `create_*` functions for a SPECIFIC GROUP of entities.

**Entity Group**: {group_name}  
**Description**: {group_description}  
**Entities in this group**: {entity_count}

**This is PART 2 of 2.** This script imports from the BASE script (`{script_name}_base.py`) to use helpers, decorators, and formatting functions.

## Inputs

**Ontology Name**: `{ontology_name}`
**Script Name**: `{script_name}_{group_name}.py`
**Base Script**: `{script_name}_base.py`

**Ontology Structure** (filtered to this group only):
```
{ontology_structure}
```

**CRITICAL**: You are generating {entity_count} `create_*` functions. Not more, not less. Count them before submitting.

## ⚠️ CRITICAL REQUIREMENTS ⚠️

### Requirement 1: Generate ALL Create Functions

You MUST generate a `create_*` function for **EVERY SINGLE class** listed in the ontology structure.

**Verification Checklist:**
- [ ] Count the total number of classes in the ontology structure
- [ ] Count the number of `create_*` functions you generated
- [ ] These numbers MUST be EQUAL
- [ ] If they don't match, you MISSED functions - generate them ALL

**NO SHORTCUTS. NO "..." PLACEHOLDERS. NO COMMENTS LIKE "# ... similar for other classes".**

**Generate EVERY SINGLE create_* function explicitly.**

### Requirement 2: Use Auto-Creation Pattern

Main creation functions should accept parameters for auxiliary entities (NOT IRIs) and use `_find_or_create_*` helpers from the base script to handle them internally.

**Example (blurred placeholders)**: For a step-like entity that needs an auxiliary entity:
```python
def create_{{StepType}}(
    label: str,
    order: int,
    # Direct parameters for auxiliary entities (NOT IRIs):
    aux_entity_label: Optional[str] = None,
    aux_entity_type_label: Optional[str] = None,
    # ... other parameters
) -> str:
    # Inside the function, use the helper:
    if aux_entity_label:
        aux_iri = _find_or_create_{{AuxEntityType}}(g, aux_entity_label, aux_entity_type_label)
        g.add((iri, {{NAMESPACE}}["{{hasAuxEntityPredicate}}"], aux_iri))
```

### Requirement 2.1: Class hierarchy typing (subclass + superclass)

If an entity class **inherits from** one or more parent classes in the ontology structure:

- You MUST add `rdf:type` triples for **both**:
  - the concrete class (subclass), and
  - each parent class (and any relevant ancestors when listed/known).

**Do not rely on RDFS reasoning at runtime.** The runtime validation often checks for a parent type explicitly.

Example:

```python
# If {{SubClass}} is a subclass of {{ParentClass}}:
g.add((iri, RDF.type, {{NAMESPACE}}["{{SubClass}}"]))
g.add((iri, RDF.type, {{NAMESPACE}}["{{ParentClass}}"]))
```

### Requirement 3: Unit Handling (OM-2) — do not ignore

If an entity creation function involves quantities (e.g., Temperature/Pressure/Duration/Volume/TemperatureRate/AmountOfSubstanceFraction):

- **Validate unit labels** against ontology-derived options (do not accept arbitrary units).
- **Map unit label → OM-2 unit IRI** when adding `om2:hasUnit`.
- **Prefer strict type hints** for unit labels (e.g., `Literal[...]` / Enum) derived from the OM-2 T-Box input when available.

Do not hardcode or list unit tables inside the script; rely on ontology inputs + shared utilities.

**STRICT REQUIREMENT (single source of truth for units)**:
- DO NOT define per-file unit dictionaries like `_TEMPERATURE_UNITS`, `_PRESSURE_UNIT_MAP`, etc.
- The base module MUST be the single source of truth (`OM2_UNIT_MAP`, `_resolve_om2_unit`, `_find_or_create_om2_quantity`).
- Entity scripts MUST import and use those base helpers for all quantity creation/attachment.

**OM-2 helper call style (IMPORTANT)**:
- Call `_resolve_om2_unit` as `_resolve_om2_unit(unit_label)` (single argument). Do NOT pass a Graph as the first argument.
- Call `_find_or_create_om2_quantity` with keyword args: `_find_or_create_om2_quantity(g, quantity_class=..., label=..., value=..., unit_label=...)`.

## Script Structure

### 1. Module Docstring and Imports

```python
#!/usr/bin/env python3
"""
{script_name}_entities.py

Entity creation functions for {ontology_name} ontology.
Implements create_* functions for all entity classes.

Part 2 of 2: Imports foundation from {script_name}_base.py
"""

from typing import Dict, List, Optional, Any
from rdflib import Graph, URIRef, Literal as RDFLiteral, RDF, RDFS

# Import from base script (all helpers, decorators, namespaces)
from .{script_name}_base import (
    # Namespaces (import the namespaces exported by base; at minimum `NAMESPACE`)
    # NOTE: do NOT hardcode ontology-specific namespace variable names in this meta-prompt.
    NAMESPACE, RDF, RDFS,
    
    # Guard decorators
    _guard_noncheck,
    
    # Formatting
    _json_line, _format_error, _format_success_json,
    
    # Graph operations (from universal_utils via base)
    locked_graph, _mint_hash_iri, _sanitize_label,
    _find_by_type_and_label, _set_single_label, _export_snapshot_silent,
    
    # ⚠️ CRITICAL: ONLY import functions that ACTUALLY EXIST in base script!
    # Available helpers extracted from base script:
{available_helpers}
    
    # Available check_existing functions from base script:
{available_check_functions}
    
    # Available add_* relationship functions from base script:
{available_add_functions}
)
```

**IMPORTANT**: Import ONLY the functions listed above. Do NOT import functions that don't exist!

### 2. Entity Creation Functions

⚠️ **CRITICAL**: The ontology structure includes a "Detailed Create Function Signatures" section. This shows EXACTLY what parameters each `create_*` function needs. **Use these signatures - do NOT skip parameters!**

For **EACH** entity class, generate a complete `create_*` function with ALL parameters listed in its detailed signature:

```python
@_guard_noncheck
def create_{{EntityClassName}}(
    label: str,
    # Required datatype properties (based on T-Box)
    required_prop1: {{type}},
    # Optional datatype properties
    optional_prop1: Optional[{{type}}] = None,
    optional_prop2: Optional[{{type}}] = None,
    # Auxiliary entity parameters (NOT IRIs - labels for auto-creation)
    aux_entity_label: Optional[str] = None,
    aux_entity_type_label: Optional[str] = None,
    # ... all relevant parameters from T-Box
) -> str:
    """
    Create {{EntityClassName}} with validation and deduplication.
    
    Uses auto-creation pattern: accepts labels for auxiliary entities,
    internally finds or creates them using _find_or_create_* helpers.
    
    Args:
        label: Human-readable label (required)
        required_prop1: Description from T-Box
        optional_prop1: Description from T-Box
        aux_entity_label: Label of auxiliary entity (auto-created if needed)
        # ... document all parameters
    
    Returns:
        JSON envelope: {{status, iri, created, code, message}}
    """
    try:
        with locked_graph() as g:
            # 1. Validation
            sanitized_label = _sanitize_label(label)
            
            # 2. Deduplication check
            existing = _find_by_type_and_label(g, NAMESPACE.{{EntityClassName}}, sanitized_label)
            if existing is not None:
                return _json_line({{
                    "status": "error",
                    "iri": None,
                    "created": False,
                    "code": "ALREADY_EXISTS",
                    "message": f"{{EntityClassName}} with label '{{sanitized_label}}' already exists"
                }})
            
            # 3. IRI minting
            iri = _mint_hash_iri("{{EntityClassName}}")
            
            # 4. Type and label
            g.add((iri, RDF.type, NAMESPACE.{{EntityClassName}}))
            _set_single_label(g, iri, sanitized_label)
            
            # 5. Add datatype properties
            if required_prop1 is not None:
                g.add((iri, NAMESPACE.hasProp1, RDFLiteral({{cast}}(required_prop1))))
            if optional_prop1 is not None:
                g.add((iri, NAMESPACE.hasOptionalProp1, RDFLiteral({{cast}}(optional_prop1))))
            
            # 6. Auto-create and link auxiliary entities
            if aux_entity_label:
                aux_iri = _find_or_create_{{AuxType}}(g, aux_entity_label, aux_entity_type_label)
                g.add((iri, NAMESPACE.hasAuxEntity, aux_iri))
            
            # Add all datatype and object property connections based on T-Box
            
        # 7. Export snapshot
        _export_snapshot_silent()
        
        # 8. Return success
        return _json_line({{
            "status": "ok",
            "iri": str(iri),
            "created": True,
            "code": None,
            "message": f"Created {{EntityClassName}} '{{sanitized_label}}'"
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

### 3. Generate for ALL Classes

⚠️ **MANDATORY**: You MUST generate the complete `create_*` function for EACH of these {entity_count} entities:

{entity_classes_list}

**VERIFICATION CHECKLIST BEFORE SUBMITTING:**
- [ ] I generated {entity_count} create_* functions
- [ ] I did NOT use "..." or "# similar for other classes"
- [ ] I did NOT skip any entity from the list above
- [ ] Each function is COMPLETE with all parameters from the ontology structure

**IF YOU SKIP EVEN ONE FUNCTION, THE CODE WILL FAIL.**

## Final Verification

Before finishing, verify:

1. ✓ Generated `create_*` functions count == Total classes count
2. ✓ Each function has proper docstring
3. ✓ Each function uses `@_guard_noncheck` decorator
4. ✓ Each function returns JSON envelope (status, iri, created, code, message)
5. ✓ Each function uses auto-creation pattern for auxiliary entities
6. ✓ Each function includes all datatype properties from T-Box
7. ✓ Each function includes all object property connections from T-Box
8. ✓ **OM-2 unit enforcement (if OM-2 quantities are referenced in ontology input)**:
   - I did NOT define per-file unit maps like `_TEMPERATURE_UNIT_MAP` / `_PRESSURE_UNIT_MAP` / etc.
   - I imported and used the shared `OM2_UNIT_MAP` and `_find_or_create_om2_quantity` (or equivalent) from `{script_name}_base.py`
   - Quantity creation reuses existing quantities when value+unit match, and does not create duplicates
9. ✓ **Ergonomic incremental updates (reference parity)**:
   - If the reference behavior implies common “add/update” flows (e.g., appending additional measurement/annotation values to an existing entity),
     I provided the relevant helper(s) in the appropriate module (typically relationships) rather than forcing users to recreate entities.

## Output

Generate ONLY the Python code for `{script_name}_entities.py`. No explanations, no markdown outside code blocks.

