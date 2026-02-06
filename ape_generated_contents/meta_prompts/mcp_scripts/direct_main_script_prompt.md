# Direct MCP Main Script Generation Meta-Prompt

You are an expert in creating FastMCP server interface scripts.

## Task

Generate a complete `main.py` file that exposes ALL functions from the underlying MCP script as FastMCP tools with comprehensive descriptions.

## Inputs

**Ontology Name**: `{ontology_name}`
**Script Name**: `{script_name}`
**Namespace URI**: `{namespace_uri}`

**Reference Main Script** (for patterns and style only):
```python
{reference_main_snippet}
```

**Functions Extracted from Underlying Script** (brief inventory; bodies omitted on purpose):
{function_signatures}

{architecture_note}

## Requirements

### 1. Import Structure

Import FastMCP and **ALL** functions from the underlying script(s).

**CRITICAL ALIAS RULE (to avoid NameError / infinite recursion):**
- If you import a function as an underscored alias (recommended), e.g. `foo as _foo`, then **ALL call sites must call `_foo(...)`**.
- Wrapper functions must **never** call themselves (e.g. inside `def create_SomeClass(...):` do **NOT** do `return create_SomeClass(...)`).

**Example (BAD vs GOOD):**

```python
# BAD (will recurse forever)
def create_SomeClass(...):
    return create_SomeClass(...)

# BAD (NameError if only _export_memory_wrapper is imported)
def export_memory():
    return export_memory_wrapper()

# GOOD
from .some_module import create_SomeClass as _create_SomeClass, export_memory_wrapper as _export_memory_wrapper
def create_SomeClass(...):
    return _create_SomeClass(...)
def export_memory():
    return _export_memory_wrapper()
```

```python
from fastmcp import FastMCP

# IMPORTANT: Check the architecture note above to determine import structure
# - Single script: import from .{script_name}
# - Split architecture: import from BOTH .{script_name}_base AND .{script_name}_entities

# Memory management (if present)
# Check functions - import EVERY check_existing_* function
# (extract from function list above)
    
    # Creation functions - import EVERY create_* function
    # (extract from function list above)
    
    # Relationship functions - import EVERY add_* function
    # (extract from function list above)
    
    # Any other public functions
    # (extract from function list above)
)
```

**CRITICAL**: Import EVERY function listed in "Functions Extracted from Underlying Script" above.

### 2. FastMCP Server Setup

```python
mcp = FastMCP("{ontology_name}")
```

### 3. Instruction Prompt

Create a comprehensive instruction prompt that explains:
- The domain and ontology being used
- ALL available tools (extracted from underlying script)
- Domain-specific rules (extracted from T-Box comments if present)
- Typical workflow for using the tools

**DO NOT use generic placeholder text.** Study the T-Box to understand the domain.

**Pattern (FastMCP 2.x safe):**
```python
INSTRUCTION_PROMPT = f"""
You are an expert assistant for knowledge graph construction using the {ontology_name} ontology.

## Available Tools

**Memory Management:**
- `init_memory`: Initialize a new knowledge graph session
- `export_memory`: Export the current graph to TTL format

**Entity Checking** (check before creating to avoid duplicates):
[List ALL check_existing_* functions here with brief descriptions]

**Entity Creation:**
[List ALL create_* functions here with brief descriptions]

**Relationship Building** (link entities together):
[List ALL add_* functions here with brief descriptions]

## Domain Rules

[Extract from T-Box rdfs:comment annotations if present]
[Otherwise, provide general guidance based on ontology structure]

## Typical Workflow

1. Initialize: `init_memory`
2. Check existing entities to avoid duplicates
3. Create entities with required properties
4. Link entities using relationship builders (add_* functions)
5. Export: `export_memory`

[Provide domain-specific workflow examples based on T-Box structure]
"""

# IMPORTANT: Do NOT use `mcp.set_initial_instructions(...)`.
# Always expose the instruction prompt via a FastMCP prompt:
@mcp.prompt(name="instruction")
def instruction_prompt():
    return INSTRUCTION_PROMPT
```

### 4. Tool Wrappers

Create ONE `@mcp.tool()` wrapper FOR EACH function imported from the underlying script.

**For check_existing_* functions:**
```python
@mcp.tool()
def check_existing_{{EntityClassName}}() -> str:
    """
    List existing {{EntityClassName}} instances in the knowledge graph.
    
    Returns a list of IRIs and labels for all {{EntityClassName}} instances.
    Use this before creating new instances to avoid duplicates.
    
    Returns:
        String with one instance per line (format: "IRI | label")
    """
    return _check_existing_{{EntityClassName}}()
```

**For create_* functions:**

⚠️ **CRITICAL**: Extract the FULL function signature from the underlying script!

```python
@mcp.tool()
def create_{{EntityClassName}}(
    # ⚠️ COPY THE EXACT SIGNATURE from underlying script!
    # Include EVERY parameter: label, all datatype properties, all aux_entity_label params
    # Example (blurred):
    label: str,
    order_index: Optional[int] = None,  # datatype property (order-like)
    flag_bool: Optional[bool] = None,  # datatype property (boolean)
    aux_entity_label: Optional[str] = None,  # auxiliary entity label (auto-created)
    aux_entity_type_label: Optional[str] = None,  # auxiliary entity type label (auto-created)
    # ... EVERY parameter from the underlying create_* function
) -> str:
    """
    Create a new {{EntityClassName}} in the knowledge graph.
    
    [Describe what this entity represents based on T-Box]
    
    Args:
        label: [Description]
        order_index: [Description - order/index in workflow]
        flag_bool: [Description - optional boolean]
        aux_entity_label: [Aux entity label - auto-created if needed]
        aux_entity_type_label: [Aux entity type label - auto-created with aux entity]
        # ... document EVERY parameter
        
    Returns:
        JSON envelope with status, IRI, and creation details
    """
    # CRITICAL: delegate to the imported underscored alias, NOT the wrapper itself.
    return _create_{{EntityClassName}}(
        label=label,
        order_index=order_index,
        flag_bool=flag_bool,
        aux_entity_label=aux_entity_label,
        aux_entity_type_label=aux_entity_type_label,
        # ... pass EVERY parameter by name
    )
```

**For add_* relationship functions:**
```python
@mcp.tool()
def add_{{relationship}}_to_{{EntityClassName}}(
    {{EntityClassName}}_iri: str,
    {{RelatedEntity}}_iri: str,
    # ... any other parameters from underlying
) -> str:
    """
    Link a {{RelatedEntity}} to a {{EntityClassName}}.
    
    Establishes the '{{relationship}}' relationship in the knowledge graph.
    [Explain semantic meaning from T-Box]
    [Reference T-Box property: namespace:{{property_name}}]
    
    Args:
        {{EntityClassName}}_iri: IRI of the {{EntityClassName}}
        {{RelatedEntity}}_iri: IRI of the {{RelatedEntity}} to attach
        
    Returns:
        Success message with details of the relationship created
    """
    return _add_{{relationship}}_to_{{EntityClassName}}(
        {{EntityClassName}}_iri={{EntityClassName}}_iri,
        {{RelatedEntity}}_iri={{RelatedEntity}}_iri,
        # ... pass ALL parameters
    )
```

**For init_memory and export_memory:**
```python
@mcp.tool()
def init_memory(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None) -> str:
    """
    Initialize a new knowledge graph session or resume an existing one.
    
    Args:
        doi: Optional DOI identifier for the knowledge graph
        top_level_entity_name: Optional name for the top-level entity
        
    Returns:
        Status message
    """
    # If underlying provides init_memory_wrapper, import it as _init_memory_wrapper and call it here.
    return _init_memory_wrapper(doi=doi, top_level_entity_name=top_level_entity_name)

@mcp.tool()
def export_memory() -> str:
    """
    Export the entire knowledge graph to TTL format.
    
    Saves the graph to a file and returns the export status.
    
    Returns:
        Export status message with file path
    """
    # If underlying provides export_memory_wrapper, import it as _export_memory_wrapper and call it here.
    return _export_memory_wrapper()
```

### 5. Signature Preservation

**CRITICAL**: For each wrapper function:
- Match **every parameter name** exactly from underlying script
- Match **every type hint** exactly (including Literal, Optional, Union)
- Match **every default value** exactly
- Do NOT simplify or change anything

### 6. Main Entry Point

```python
if __name__ == "__main__":
    mcp.run()
```

## VALIDATION CHECKLIST

Before outputting, verify:

☐ **Imports**: Did I import ALL {total_functions} functions from the underlying script?
☐ **Wrappers**: Did I create a @mcp.tool wrapper for EACH of the {total_functions} functions?
☐ **Memory**: Did I expose init_memory and export_memory if they exist?
☐ **Check functions**: Did I expose ALL check_existing_* functions?
☐ **Create functions**: Did I expose ALL create_* functions?
☐ **Add functions**: Did I expose ALL add_* relationship functions?
☐ **Signatures**: Did I preserve EXACT function signatures with all parameters and types?
☐ **Descriptions**: Did I write comprehensive docstrings (not just "Args: ... Returns: ...")?
☐ **Instruction**: Did I create a detailed instruction prompt based on the T-Box structure?

**If any item is unchecked, GO BACK and fix it.**

## Output Format

Return ONLY the complete Python code for `main.py`.

Do NOT include:
- Markdown code fences (```)
- Explanations or commentary outside the code
- File paths or directory structures

Start directly with the imports.

## Critical Guidelines

1. **Complete Coverage**: Wrap ALL {total_functions} functions from the underlying script
2. **No Omissions**: Every function must have a corresponding wrapper
3. **Signatures**: Prefer matching parameter names and defaults when provided; do NOT add type hints unless explicitly listed.
   Do NOT use `*args, **kwargs`. Always use the explicit parameters from the extracted signatures.
4. **Comprehensive Docs**: Write detailed docstrings based on T-Box semantics
5. **Domain-Specific**: Use T-Box information to make descriptions meaningful
6. **No Assumptions**: Only wrap functions that actually exist in the underlying script

**FINAL REMINDER**: You extracted {total_functions} functions. Your main.py MUST have {total_functions} corresponding @mcp.tool wrappers. Count them before outputting.

Generate the complete main.py script now.
