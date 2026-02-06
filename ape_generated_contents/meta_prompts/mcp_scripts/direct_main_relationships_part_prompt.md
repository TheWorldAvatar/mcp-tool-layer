# Direct MCP Main Relationships (add_*) Server Generation Meta-Prompt

You are an expert in creating FastMCP server *modules*.

## Task

Generate a complete `main_relationships.py` FastMCP server for ontology `{ontology_name}` that exposes:
- ALL relationship/connect tools (`add_*` functions; or `add_relation` if present)

**Do NOT include memory wrappers, check_existing_*, or create_* tools in this module.**

## Critical architecture requirement (split into two FULL servers)

This file MUST be a complete FastMCP server:

- It MUST contain `mcp = FastMCP("<name>")`
- It MUST use `@mcp.prompt(name="instruction")` for instructions (relationship-focused)
- It MUST use `@mcp.tool()` decorators for tools
- It MUST end with:

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

## Inputs

**Ontology Name**: `{ontology_name}`
**Namespace URI**: `{namespace_uri}`

**Functions to wrap** (bodies omitted on purpose; follow signatures exactly):
{function_signatures}

{architecture_note}

## Non‑negotiable rules

1) **Underscore aliasing**: import every underlying function as `foo as _foo`, and every wrapper must call `_foo(...)`.
2) **No recursion**: wrapper functions must NEVER call themselves.
3) **Explicit parameters only**: do NOT use `*args` / `**kwargs`. Use explicit parameters from signatures.
4) **Do not start server on import**: only call `mcp.run(...)` under `if __name__ == "__main__":`.
5) Output ONLY Python code (no markdown fences, no explanations).


