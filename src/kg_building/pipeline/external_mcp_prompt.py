"""Per-server KG user snippets. Injected when that MCP is on this stage's tool list.

Occurrence / graph-building servers are not listed here. A snippet is copied
into the no-contract envelope in every protocol, including generic-strict.
"""

from __future__ import annotations

from typing import Iterable

_OCCURRENCE_SERVERS = frozenset({"llm_created_mcp"})


def _is_occurrence_server(name: str) -> bool:
    return name in _OCCURRENCE_SERVERS or name.endswith("_extension")


MCP_PROMPT_SNIPPETS: dict[str, str] = {
    "ccdc": """Loaded external MCP: `ccdc`

This is the crystallographic identifier resolver for the bound identity. The semantic ledger above is the source document for this pass.

- If that ledger already states an explicit CCDC / CSD deposition number for the bound identity, copy that exact number into the compiled occurrence optional arguments that carry the CCDC facet (`hasCCDCNumber_label` and `hasCCDCNumber_hasCCDCNumberValue` on the owner `create_*` call). Do not call CCDC search tools to replace or second-guess a ledger-attested number.
- If the bound identity has no CCDC / deposition number in the ledger, you must call this MCP before `export_memory`: `search_ccdc_by_mop_name` once with the exact source-grounded product identifier; if that returns no exact mapping, `search_ccdc_by_doi` once with the runtime DOI. Copy a returned CCDC number into those same occurrence optional arguments.
- A tool result that reports no match, ok=false, matched=false, or empty content is unresolved. Leave the CCDC optional arguments unset; do not invent a number.
- Do not fetch `.res` / `.cif` files unless a compiled argument explicitly requires a structure file.""",
    "pubchem": """Loaded external MCP: `pubchem`

This is the molecular-identity resolver for the bound identity. The semantic ledger above is the source document for this pass.

- If the ledger already states an explicit PubChem identifier, formula, or structure string for the bound identity, copy those exact values into the compiled occurrence optional arguments. Do not call PubChem to replace ledger-attested values.
- If the bound identity needs a PubChem identifier and the ledger does not have one, call this MCP on the exact source-grounded name or structure, then copy returned values into those optional arguments.
- Unresolved lookup stays unset; do not invent a CID, SMILES, InChI, or formula.""",
    "enhanced_websearch": """Loaded external MCP: `enhanced_websearch`

Use this only for a source-supported bound identity when a specialist lookup MCP is absent or returned no match. External pages may enrich identity fields already licensed by the ledger; they must not invent a new occurrence, relation, or participation fact. Unresolved search stays unset; do not invent values.""",
}


def loaded_mcp_names(mcp_tools: Iterable[str] | None) -> list[str]:
    names: list[str] = []
    for raw in mcp_tools or []:
        name = str(raw or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def attached_external_mcp_contract(mcp_tools: Iterable[str] | None) -> str:
    """Return the user-envelope block for every loaded non-occurrence MCP with a snippet."""
    blocks: list[str] = []
    for name in loaded_mcp_names(mcp_tools):
        if _is_occurrence_server(name):
            continue
        text = str(MCP_PROMPT_SNIPPETS.get(name) or "").strip()
        if text:
            blocks.append(text)
    if not blocks:
        return ""
    return "Attached external MCP contract:\n\n" + "\n\n".join(blocks)
