# Auto-Create Pattern for Auxiliary Entities

## Overview

Updated the meta-prompt to emphasize the **auto-create pattern** where main entity creation functions accept direct parameters for auxiliary entities and internally handle finding or creating them, rather than requiring users to create auxiliary entities separately.

## Problem Statement

**BAD UX** (old pattern - user must create everything separately):
```python
# User must create vessel first
vessel_iri = create_vessel(name="round bottom flask", type="glass")

# User must create duration first  
duration_iri = create_duration(value=24, unit="hour")

# Then user can create the main entity
create_step(name="heating", order=1, vessel_iri=vessel_iri, duration_iri=duration_iri)
```

**GOOD UX** (new pattern - one call does everything):
```python
# User provides all parameters, function handles internal creation/reuse
create_step(
    name="heating",
    order=1,
    vessel_name="round bottom flask",  # Auto-find or create
    vessel_type="glass",                # Auto-find or create
    duration_value=24,                  # Auto-find or create
    duration_unit="hour"                # Auto-find or create
)
```

## Changes Made to Meta-Prompt

### 1. Added Design Philosophy Section

Added at the beginning to set the stage:
- Explains the auto-create pattern with clear before/after examples
- Uses **domain-agnostic** placeholders (aux_A, aux_B, main_entity)
- Shows the UX improvement clearly

### 2. New Section 6: Auto-Create Helpers

Added comprehensive guidance on implementing `_find_or_create_*` helper functions:

**Pattern shown (domain-agnostic):**
```python
def _find_or_create_{{AuxiliaryEntityType}}(
    g: Graph,
    identifying_param1: str,
    identifying_param2: str
) -> Optional[URIRef]:
    """
    Find existing entity by identifying parameters, or create new one.
    Prevents duplication while providing excellent UX.
    """
    # Search for existing entity
    for candidate in g.subjects(RDF.type, NAMESPACE.{{AuxiliaryEntityType}}):
        if <all_params_match>:
            return candidate  # Reuse existing
    
    # Not found - create new
    iri = _mint_hash_iri("{{AuxiliaryEntityType}}")
    # ... create and return
```

**Guidance includes:**
- Which entity types need helpers (measurements, equipment, conditions)
- How to search for existing entities to avoid duplication
- How to create new entities when not found
- Integration example showing three-phase pattern

### 3. Updated Section 5: Entity Creation Functions

Enhanced to emphasize the **three-phase pattern**:

**Phase 1: Pre-Validation (No Side Effects)**
- Validate ALL inputs before making ANY changes
- Check order, required parameters, etc.
- Fail fast if anything is wrong

**Phase 2: Auto-Create Auxiliary Entities**
- Use `_find_or_create_*` helpers to get IRIs
- Helpers handle finding existing or creating new
- Reuse entities where appropriate

**Phase 3: Create Main Entity and Attach**
- Now that all prerequisites are ready, create main entity
- Attach all auxiliary entities
- Link to parent entities

**Example signature shown:**
```python
def create_{{EntityClassName}}(
    label: str,
    required_param1: str,
    # NOT aux_entity_iri - instead:
    aux_entity_name: Optional[str] = None,
    aux_entity_type: Optional[str] = None,
    measurement_value: Optional[float] = None,
    measurement_unit: Optional[str] = None
) -> str:
```

### 4. Section Renumbering

Updated section numbers:
- Section 6: Auto-Create Helpers (NEW)
- Section 7: Helper Functions (was 6)
- Section 8: Memory Management (was 7)
- Section 9: Domain-Specific Validation (was 8)

## Key Principles Maintained

✅ **No Domain-Specific Information**: All examples use generic placeholders like:
- `{{AuxiliaryEntityType}}`, `{{MainEntity}}`, `{{Measurement}}`
- `aux_entity_name`, `measurement_value`, `identifying_param1`
- Never mentions specific domain terms (no concrete class/property/entity names)

✅ **Blurred but Explicit Examples**: Examples are detailed and show:
- Exact function structure
- Clear three-phase pattern
- Specific helper patterns
- But all with generic placeholder names

✅ **Reference-Based**: Emphasizes "Study the reference implementation" multiple times to guide the LLM to extract patterns from the provided reference code snippet

## Benefits

1. **Better UX**: Users make one call instead of many
2. **Automatic Deduplication**: Helpers find existing entities to avoid duplicates
3. **Cleaner Code**: Less boilerplate in user code
4. **Consistent with Reference**: Matches the pattern in `mcp_creation.py`
5. **Domain-Agnostic**: Meta-prompt works for any ontology

## Testing

When regenerating scripts with this updated meta-prompt:

**Expected Result:**
- Main creation functions accept auxiliary entity parameters directly (e.g., `vessel_name`, `vessel_type_name`, `duration_value`, `duration_unit`)
- Internal `_find_or_create_*` helper functions handle finding/creating auxiliary entities
- Three-phase pattern: validate → auto-create → create main entity
- User experience matches the reference `mcp_creation.py` implementation

**To Verify:**
```bash
python -m src.agents.scripts_and_prompts_generation.generation_main --direct --ontosynthesis
```

Then check:
1. `create_*` functions have auxiliary entity parameters (not just IRIs)
2. `_find_or_create_*` helper functions exist
3. Functions follow three-phase pattern
4. Duplicate entities are avoided through helpers

## Files Modified

- ✅ `ape_generated_contents/meta_prompts/mcp_scripts/direct_underlying_script_prompt.md`
  - Added "Design Philosophy" section
  - Added Section 6: Auto-Create Helpers
  - Updated Section 5: Entity Creation Functions
  - Renumbered subsequent sections

