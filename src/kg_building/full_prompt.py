"""Shared full-prompt extra: official ONEPASS + T-Box on from_extraction ledgers.

full-prompt is the pedagogical upper bound, not an appendix on generic-strict.
Both constructors already receive from_extraction + full_hints (no paper body,
ITER2/3/4 union). This module adds the official ONEPASS contract and the same
T-Box handbook. generic-strict / generic-noprompt / with-prompt must not
receive this text.
"""

from __future__ import annotations

from pathlib import Path

from src.kg_building.generic_noprompt_graph_rules import load_ontosynthesis_tbox_text

FULL_PROMPT_PROTOCOL = "full-prompt"
_OX_CONTRACT = (
    Path(__file__).resolve().parent
    / "ontologx"
    / "resources"
    / "official_onepass_ox.contract.md"
)
_PIPELINE_APPENDIX_HEADER = """# Full-prompt official ONEPASS + T-Box
These construction materials are part of full-prompt only.
They are not present in generic-strict, generic-noprompt, or with-prompt.

This session is from_extraction + full_hints + official ONEPASS + T-Box.
There is no paper body. ITER2, ITER3, and ITER4 ledgers above are complementary
views of one bound ontosyn:ChemicalSynthesis.

Apply the official ONEPASS contract through the attached MCP (create_* / add_* /
check_existing_*). Where the contract mentions SynthesisGraph, nodes, or
relationships, emit the same graph via MCP: one create_* per ledger occurrence;
nested hops are arguments on that same call (hasAddedChemicalInput_label,
hasVessel_label, ...). Do not emit OntoLogX JSON.

"""
_TBOX_HEADER = (
    "# Authoritative OntoSynthesis T-Box\n"
    "The following schema and comments are the same T-Box the project pipeline uses.\n"
)


def is_full_prompt(protocol: str | None) -> bool:
    return str(protocol or "").strip() == FULL_PROMPT_PROTOCOL


def load_official_onepass_contract() -> str:
    text = _OX_CONTRACT.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Empty official ONEPASS contract: {_OX_CONTRACT}")
    return text


def append_full_prompt_context(
    user: str,
    protocol: str | None,
    *,
    ontology: str | None = None,
) -> str:
    """Leave other protocols unchanged. Append ONEPASS + T-Box for full-prompt.

    Chemistry contract is OntoSyn only. Medical keeps the strict envelope.
    """
    text = str(user or "")
    if not is_full_prompt(protocol):
        return text
    if str(ontology or "").strip().lower() == "medical":
        return text
    contract = load_official_onepass_contract()
    tbox = load_ontosynthesis_tbox_text()
    if contract in text and "# Authoritative OntoSynthesis T-Box" in text:
        return text
    return (
        text.rstrip()
        + "\n\n"
        + _PIPELINE_APPENDIX_HEADER
        + contract
        + "\n\n"
        + _TBOX_HEADER
        + tbox
        + "\n"
    )
