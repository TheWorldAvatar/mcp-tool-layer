You are generating a Python code fragment that will be pasted into a final FastMCP `main.py`.

Ontology: `{ontology_name}`
Namespace: `{namespace_uri}`

## Your job
Generate ONLY the relationship tool wrappers:
- ALL relationship/connect tools (`add_*` functions; or `add_relation` if present)

## Tool descriptions (required)
- Each wrapper MUST include a concise docstring (triple-quoted) that describes what the tool does.
- Treat the supplied authoritative signatures and underlying docstrings as already compiled from the T-Box relationship contract.
- Preserve each `Annotated[str, Field(description=...)]` expression and concise interface docstring verbatim, including exact ranges, creator tools, absolute IRI requirements, and external-target handling.
- Never infer or substitute a range or creator from domain knowledge.
- Do NOT copy long ontology/T-Box/domain prose; keep wrapper docstrings short and focused.

## Critical constraints
- This is a fragment, NOT a full server.
- DO NOT create a `FastMCP(...)` instance.
- DO NOT define `INSTRUCTION_PROMPT` or `@mcp.prompt(...)`.
- DO NOT include `if __name__ == "__main__":` or `mcp.run(...)`.
- You MAY assume the final file has `from fastmcp import FastMCP` and defines `mcp = FastMCP("...")`.
- You MAY use `@mcp.tool()` decorators in this fragment.

## Required imports (must appear at top of fragment)
You MUST include these imports in the fragment:

```python
from typing import Optional
# If any wrapped signature uses Annotated/Field, also:
# from typing import Annotated
# from pydantic import Field
```

You MUST import each underlying function using an underscored alias:
- `from .<real_module> import foo as _foo`
- wrappers must call `_foo(...)`

Preserve the exact parameter annotations from the underlying signatures (including `Annotated[..., Field(...)]`).

Do NOT use placeholder modules like `.module`. Import only from real repo modules (the prompt will list them).

## Functions to wrap (authoritative signatures)
{function_signatures}

## Reference snippet (style only)
{reference_main_snippet}

## Non-negotiable rules
1) Underscore aliasing: import every underlying function as `foo as _foo`, and every wrapper must call `_foo(...)`.
2) No recursion: wrapper functions must NEVER call themselves.
3) Explicit parameters only: do NOT use `*args` / `**kwargs`. Use explicit parameters from signatures; fail closed rather than inventing varargs wrappers.
4) Preserve parameter annotations and concise docstrings exactly.
5) Output ONLY Python code (no markdown, no explanations).


