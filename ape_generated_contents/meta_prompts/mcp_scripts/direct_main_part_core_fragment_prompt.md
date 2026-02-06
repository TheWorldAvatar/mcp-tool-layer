You are generating a **Python code fragment** that will be pasted into a final FastMCP `main.py`.

Ontology: `{ontology_name}`
Namespace: `{namespace_uri}`

## Your job
Generate ONLY the **core** tool wrappers:
- `init_memory` / `export_memory` tools (wrapping `init_memory_wrapper` / `export_memory_wrapper`) if present
- ALL `check_existing_*` tools
- ALL `create_*` tools

## Tool descriptions (required)
Each wrapper MUST include a concise docstring (triple-quoted) that describes what the tool does.
If a docstring hint is provided in the input below, copy it (or its first sentence) into the wrapper docstring.
If a `tbox:` hint is provided, include it verbatim in the docstring under a `T-Box:` line.

## Critical constraints
- This is a **fragment**, NOT a full server.
- **DO NOT** create a `FastMCP(...)` instance.
- **DO NOT** define `INSTRUCTION_PROMPT` or `@mcp.prompt(...)`.
- **DO NOT** include `if __name__ == "__main__":` or `mcp.run(...)`.
- You MAY assume the final file has `from fastmcp import FastMCP` and defines `mcp = FastMCP("...")`.
- You MAY use `@mcp.tool()` decorators in this fragment.

## Required imports (must appear at top of fragment)
You MUST include these imports in the fragment:

```python
from typing import Optional
```

You MUST import each underlying function using an underscored alias:
- `from .<real_module> import foo as _foo`
- wrappers must call `_foo(...)`

**Do NOT** use placeholder modules like `.module`. Import only from real repo modules (the prompt will list them).

## Functions to wrap (authoritative signatures)
{function_signatures}

## Reference snippet (style only)
{reference_main_snippet}

## Non-negotiable rules
1) **Underscore aliasing**: import every underlying function as `foo as _foo`, and every wrapper must call `_foo(...)`.
2) **No recursion**: wrapper functions must NEVER call themselves.
3) **Explicit parameters only**: do NOT use `*args` / `**kwargs`. Use explicit parameters from signatures.
4) Output ONLY Python code (no markdown, no explanations).


