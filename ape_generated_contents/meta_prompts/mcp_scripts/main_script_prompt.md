# MCP Main Script Generation Meta-Prompt

You are an expert in creating FastMCP server interface scripts.

## Task

Generate a complete `main.py` file that exposes the functions from the underlying MCP script as FastMCP tools.

## Inputs

**Ontology Name**: `{ontology_name}`
**Script Name**: `{script_name}`
**Output Directory**: `{output_dir}`

**Design Principles**:
```
{design_principles}
```

**T-Box Ontology**:
```turtle
{ontology_ttl}
```

**Underlying MCP Script**:
```python
{underlying_script}
```

## Requirements

1. **Import Structure**:
   - Import FastMCP: `from fastmcp import FastMCP`
   - Import all functions from the underlying script
   - Import necessary types and utilities

2. **MCP Server Setup**:
   - Create FastMCP instance: `mcp = FastMCP("{ontology_name}_mcp")`
   - Use proper naming conventions

3. **Tool Exposure**:
   - For each function in the underlying script, create an `@mcp.tool()` decorator
   - Provide clear, helpful tool descriptions
   - Document all parameters with types and descriptions
   - Include examples in docstrings where helpful

4. **Tool Descriptions**:
   - Be specific about what each tool does
   - Reference the ontology classes/properties it operates on
   - Include constraints and validation rules
   - Mention return types and formats

5. **Error Handling**:
   - Tools should handle errors gracefully
   - Return informative error messages
   - Validate inputs before processing

6. **Main Block**:
   - Include `if __name__ == "__main__":` block
   - Call `mcp.run()` to start the server

## Output Format

Generate ONLY the complete Python code for `main.py`. Do NOT include:
- Markdown code fences
- Explanations or commentary
- File paths or directory structures

Start directly with the Python imports.

## Example Structure

```python
from fastmcp import FastMCP
from .{ontology_name}_creation import *

mcp = FastMCP("{ontology_name}_mcp")

@mcp.tool()
def create_entity(name: str, properties: dict) -> str:
    """
    Create a new entity in the knowledge graph.
    
    Args:
        name: The name/identifier for the entity
        properties: Dictionary of properties to set
        
    Returns:
        The IRI of the created entity
    """
    # Implementation using underlying functions
    pass

if __name__ == "__main__":
    mcp.run()
```

Generate the complete main.py script now.
