# src/agents/mops/dynamic_mcp/modules/prompt_utils.py
"""Prompt utilities for dynamic MCP agent, aligned with EXTRACTION_SCOPE, new placeholders, and DOI injection."""
import re
from typing import Any

# Allowed variables in prompt templates
# Supports both {{var}} and {var} forms for these names
ALLOWED_VARS = {"doi", "iteration", "paper_content", "entity_label", "entity_uri"}


def collect_scopes(scopes_ns: Any) -> list[tuple[int, str]]:
    """
    Collect EXTRACTION_SCOPE_* definitions from a module/namespace.

    Returns:
        List of (iteration_index, scope_text) sorted by iteration_index.
    """
    scopes: list[tuple[int, str]] = []
    for name in dir(scopes_ns):
        if name.startswith("EXTRACTION_SCOPE_"):
            try:
                i = int(name.split("_")[-1])
                scopes.append((i, getattr(scopes_ns, name)))
            except Exception:
                pass
    scopes.sort(key=lambda x: x[0])
    return scopes


def collect_prompts(prompts_ns: Any) -> dict[int, str]:
    """
    Collect MCP_PROMPT_ITER_* templates except cleanup.
    """
    pm: dict[int, str] = {}
    for name in dir(prompts_ns):
        if name.startswith("MCP_PROMPT_ITER_") and name != "MCP_PROMPT_ITER_CLEANUP":
            try:
                i = int(name.split("_")[-1])
                pm[i] = getattr(prompts_ns, name)
            except Exception:
                pass
    return pm

 

def format_prompt(tmpl: str, **vals) -> str:
    """
    Substitute placeholders in either {{var}} or {var} form, but only for allowed variables.
    Unknown placeholders are left intact. No KeyError on missing values.
    """
    payload = {k: str(v) for k, v in vals.items() if k in ALLOWED_VARS}

    out = tmpl

    # Replace double-brace tokens {{var}}
    for k, v in payload.items():
        out = out.replace(f"{{{{{k}}}}}", v)

    # Replace single-brace tokens {var} only if not part of {{ }}
    def _sub_single(m):
        name = m.group(1)
        return payload.get(name, m.group(0))

    out = re.sub(r"(?<!\{)\{([a-zA-Z0-9_]+)\}(?!\})", _sub_single, out)

    return out


 