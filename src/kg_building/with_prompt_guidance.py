"""Shared with-prompt extra: frozen OntoSyn KG-building guidance plus T-Box.

with-prompt is additive on each constructor's strict no-prompt surface.
It appends the frozen human guidance and the same OntoSynthesis T-Box
handbook generic-noprompt uses. It does not append the generic-noprompt
Graph rules. generic-strict and generic-noprompt must not receive the
guidance text.

with-prompt-qty and with-prompt-hops are OX overlays on with-prompt: same
frozen guidance and T-Box, plus a dual-coding appendix. Occurrence/ownership
is already hops on generic-strict / generic-noprompt / with-prompt. They do
not edit the frozen file.
"""

from __future__ import annotations

from pathlib import Path

from src.kg_building.generic_noprompt_graph_rules import (
    append_medical_tbox,
    append_ontosynthesis_tbox,
)

WITH_PROMPT_PROTOCOL = "with-prompt"
WITH_PROMPT_QTY_PROTOCOL = "with-prompt-qty"
WITH_PROMPT_HOPS_PROTOCOL = "with-prompt-hops"
_GUIDANCE_PATH = (
    Path(__file__).resolve().parent / "resources" / "ontosyn_with_prompt_guidance.md"
)
_MEDICAL_GUIDANCE_PATH = (
    Path(__file__).resolve().parent / "resources" / "medical_with_prompt_guidance.md"
)
_QTY_PATH = (
    Path(__file__).resolve().parent
    / "resources"
    / "ontosyn_with_prompt_quantity_dualcode.md"
)
_HOPS_PATH = (
    Path(__file__).resolve().parent
    / "resources"
    / "ontosyn_with_prompt_hops_dualcode.md"
)
_PIPELINE_APPENDIX_HEADER = (
    "# With-prompt OntoSyn KG-building guidance\n"
    "These construction rules are part of with-prompt only. "
    "They are not present in generic-strict.\n\n"
)
_MEDICAL_APPENDIX_HEADER = (
    "# With-prompt OntoMed KG-building guidance\n"
    "These construction rules are part of with-prompt only. "
    "They are not present in generic-strict.\n\n"
)
_QTY_APPENDIX_HEADER = (
    "# With-prompt-qty quantity dual-coding\n"
    "These rules are part of with-prompt-qty only. "
    "They are not present in with-prompt.\n\n"
)
_HOPS_APPENDIX_HEADER = (
    "# With-prompt-hops OX dual-coding\n"
    "These rules are part of with-prompt-hops only. "
    "They are not present in with-prompt.\n\n"
)


def load_ontosyn_with_prompt_guidance() -> str:
    text = _GUIDANCE_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Empty with-prompt guidance: {_GUIDANCE_PATH}")
    return text


def load_medical_with_prompt_guidance() -> str:
    text = _MEDICAL_GUIDANCE_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Empty medical with-prompt guidance: {_MEDICAL_GUIDANCE_PATH}")
    return text


def load_ontosyn_with_prompt_quantity_dualcode() -> str:
    text = _QTY_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Empty with-prompt-qty dual-coding: {_QTY_PATH}")
    return text


def load_ontosyn_with_prompt_hops_dualcode() -> str:
    text = _HOPS_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Empty with-prompt-hops dual-coding: {_HOPS_PATH}")
    return text


def is_with_prompt(protocol: str | None) -> bool:
    text = str(protocol or "").strip()
    return text in {
        WITH_PROMPT_PROTOCOL,
        WITH_PROMPT_QTY_PROTOCOL,
        WITH_PROMPT_HOPS_PROTOCOL,
    }


def is_with_prompt_qty(protocol: str | None) -> bool:
    return str(protocol or "").strip() == WITH_PROMPT_QTY_PROTOCOL


def is_with_prompt_hops(protocol: str | None) -> bool:
    return str(protocol or "").strip() == WITH_PROMPT_HOPS_PROTOCOL


def append_with_prompt_context(
    user: str,
    protocol: str | None,
    *,
    ontology: str | None = None,
) -> str:
    """Leave other protocols unchanged. Append frozen guidance and T-Box.

    with-prompt-qty also appends the quantity dual-coding appendix.
    with-prompt-hops also appends the hops dual-coding appendix.
    Occurrence hops itself is already on the strict surface.
    Chemistry extras are OntoSyn only. Medical gets the OntoMed guidance
    freeze plus the medical handbook, not the chemistry Add-edge text.
    """
    text = str(user or "")
    if not is_with_prompt(protocol):
        return text
    if str(ontology or "").strip().lower() == "medical":
        guidance = load_medical_with_prompt_guidance()
        if guidance not in text:
            text = text.rstrip() + "\n\n" + _MEDICAL_APPENDIX_HEADER + guidance + "\n"
        return append_medical_tbox(text)
    guidance = load_ontosyn_with_prompt_guidance()
    if guidance not in text:
        text = text.rstrip() + "\n\n" + _PIPELINE_APPENDIX_HEADER + guidance + "\n"
    if is_with_prompt_qty(protocol):
        dualcode = load_ontosyn_with_prompt_quantity_dualcode()
        if dualcode not in text:
            text = text.rstrip() + "\n\n" + _QTY_APPENDIX_HEADER + dualcode + "\n"
    if is_with_prompt_hops(protocol):
        dualcode = load_ontosyn_with_prompt_hops_dualcode()
        if dualcode not in text:
            text = text.rstrip() + "\n\n" + _HOPS_APPENDIX_HEADER + dualcode + "\n"
    return append_ontosynthesis_tbox(text)
