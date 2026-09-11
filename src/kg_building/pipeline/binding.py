"""Official no-contract KG user envelope. This package does not generate thick KG prompts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.kg_building.full_prompt import append_full_prompt_context
from src.kg_building.generic_noprompt_graph_rules import (
    append_generic_noprompt_context,
)
from src.kg_building.with_prompt_guidance import append_with_prompt_context
from src.kg_building.pipeline.external_mcp_prompt import attached_external_mcp_contract
from src.kg_building.pipeline.main_kg.runtime_layers import (
    attach_no_contract_runtime_layers,
    extracted_hints_block,
)

NO_CONTRACT_USER = """Use the attached MCP for the bound graph-building task.

Runtime bindings:
- DOI: {doi}
- Bound root label: {entity_label}
- Bound root IRI: {entity_uri}

Semantic ledger supplied to the agent:
{iteration_hints}

Before export_memory, call every public create_* that matches a ledger heading. Do not export while any of those tools is still unused.
"""


def pin_entity_context(*, name: str, iri: str = "") -> None:
    """Pin the generated MCP scope before init_memory / export_memory."""
    scope = str(name or "").strip()
    root = str(iri or "").strip()
    if scope:
        os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME"] = scope
    else:
        os.environ.pop("TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME", None)
    if root:
        os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI"] = root
    else:
        os.environ.pop("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", None)


def bind_kg_runtime_context(
    *,
    hints: str,
    doi: str,
    entity_label: str = "",
    entity_uri: str = "",
    extra_bindings: str = "",
    known_top_entities: list[dict[str, Any]] | None = None,
    doi_folder: str | Path | None = None,
    entity_safe: str = "",
    protocol: str | None = None,
    ontology: str | None = None,
    mcp_tools: list[str] | None = None,
) -> str:
    """Build the official no-contract user message: bindings + ledger, no paper body.

    generic-strict and unset protocol keep this envelope only.
    generic-noprompt appends the shared Graph rules and T-Box handbook.
    with-prompt appends the frozen OntoSyn KG-building guidance and T-Box.
    full-prompt appends official ONEPASS plus the same T-Box handbook.
    Loaded extra MCP snippets are part of this envelope in every protocol.
    """
    parts = [
        NO_CONTRACT_USER.format(
            doi=str(doi or "").strip(),
            entity_label=str(entity_label or "").strip(),
            entity_uri=str(entity_uri or "").strip(),
            iteration_hints=extracted_hints_block(hints),
        ).rstrip()
    ]
    extra = str(extra_bindings or "").strip()
    if extra:
        parts.append(extra)
    mcp_contract = attached_external_mcp_contract(mcp_tools)
    if mcp_contract:
        parts.append(mcp_contract)
    user = attach_no_contract_runtime_layers(
        "\n\n".join(parts) + "\n",
        doi_folder=doi_folder,
        known_top_entities=known_top_entities,
        entity_label=entity_label,
        entity_safe=entity_safe,
    )
    user = append_generic_noprompt_context(user, protocol, ontology=ontology)
    user = append_with_prompt_context(user, protocol, ontology=ontology)
    return append_full_prompt_context(user, protocol, ontology=ontology)
