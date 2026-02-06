You are generating a complete, runnable **FastMCP** `main.py` for ontology `{ontology_name}`.

## Inputs
You are given two Python fragments:
- **CORE FRAGMENT**: wrappers for init/check/create tools
- **RELATIONSHIPS FRAGMENT**: wrappers for relationship tools

These fragments are the ONLY source of wrapper logic. Do NOT invent new wrappers or change signatures.

## CORE FRAGMENT
```python
{part_core_code}
```

## RELATIONSHIPS FRAGMENT
```python
{part_relationships_code}
```

## Your job
Produce ONE final `main.py` that:
- Imports `FastMCP` and creates `mcp = FastMCP("{ontology_name}")`
- Defines an instruction prompt via:
  - `@mcp.prompt(name="instruction")`
  - returning a constant `INSTRUCTION_PROMPT: str`
- Includes BOTH fragments’ imports and wrapper functions
  - De-duplicate imports safely
  - Keep wrappers exactly as provided (same names and signatures AND docstrings)
- Ensures every tool wrapper is decorated with `@mcp.tool()` (if already decorated, keep it)
- Ends with:

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

## Non-negotiable rules
1) Output ONLY Python code (no markdown, no explanations).
2) Do NOT add `from __future__ import annotations`.
3) Ensure `from typing import Optional` is present near the top.
4) Do NOT call any wrapper from itself (no recursion).
5) If any underlying import uses `foo as _foo`, wrappers MUST call `_foo(...)`.
6) Ensure every `@mcp.tool()` wrapper has a non-empty docstring; preserve docstrings from fragments.


