from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from prompt_builder import (
    ENTITY_REUSE_CONTRACT,
    FULL_HINTS_CONTRACT,
    OFFICIAL_ONEPASS_OX_CONTRACT,
    OUTPUT_CONTRACT,
    OX_GENERIC_GRAPH_RULES,
    build_system_prompt,
)
from strict_noprompt import build_strict_noprompt_system_prompt


FORBIDDEN = (
    "One Add represents one source-grounded",
    "Core atomicity",
    "wash of retained solid",
    "Do not default atmosphere",
    "om-2:degreeCelsius",
    "HeatChill, not Stir",
    "exactly one ontosyn:hasAddedChemicalInput",
    "exactly one ontosyn:hasChemicalOutput",
    "init_memory",
    "create_Add(",
    "check_existing_",
    "# Authoritative OntoSynthesis T-Box",
    "Ontology Schema - Structured Property Mapping",
)


def test_strict_prompt_keeps_pipeline_ownership_only():
    prompt = build_strict_noprompt_system_prompt()

    assert "hasAddedChemicalInput" in prompt
    assert "Nested ownership" in prompt
    assert "bound ChemicalSynthesis root via hasSynthesisStep" in prompt
    assert "Facets on this occurrence: hasOrder" in prompt
    assert "hasDocumentContext, hasEquipment, and retrievedFrom" in prompt or (
        "hasDocumentContext, hasEquipment, retrievedFrom" in prompt
    )
    assert "VesselEnvironment" in prompt
    assert "SynthesisGraph" in prompt
    assert "Read each occurrence heading" in prompt
    assert OX_GENERIC_GRAPH_RULES in prompt
    assert "most specific type available" in prompt
    assert "every emitted node must be reachable from the bound ChemicalSynthesis" in prompt

    for phrase in FORBIDDEN:
        assert phrase not in prompt, phrase

    assert OUTPUT_CONTRACT not in prompt
    assert FULL_HINTS_CONTRACT not in prompt
    assert ENTITY_REUSE_CONTRACT not in prompt
    assert OFFICIAL_ONEPASS_OX_CONTRACT.read_text(encoding="utf-8").strip() not in prompt


def test_official_no_prompt_still_includes_tbox_and_graph_rules():
    prompt = build_system_prompt(
        per_entity=True,
        from_extraction=True,
        full_hints=True,
        entity_reuse=True,
        official_onepass_guidance=False,
    )
    assert "Authoritative OntoSynthesis T-Box" in prompt
    assert "Do not default atmosphere" in prompt
    assert "One Add owns exactly one ontosyn:hasAddedChemicalInput" in prompt
    assert OX_GENERIC_GRAPH_RULES in prompt
    assert "most specific type available" in prompt
    assert "every emitted node must be reachable from the bound ChemicalSynthesis" in prompt
