from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from prompt_builder import (
    FULL_HINTS_CONTRACT,
    OFFICIAL_ONEPASS_OX_CONTRACT,
    OX_GENERIC_GRAPH_RULES,
    build_system_prompt,
)


def test_official_onepass_ox_prompt_uses_static_native_contract():
    prompt = build_system_prompt(
        per_entity=True,
        from_extraction=True,
        full_hints=True,
        entity_reuse=True,
        official_onepass_guidance=True,
    )

    assert OX_GENERIC_GRAPH_RULES in prompt
    assert OFFICIAL_ONEPASS_OX_CONTRACT.read_text(encoding="utf-8").strip() in prompt
    assert "unique, contiguous, globally increasing" in prompt
    assert "integer sequence beginning at 1" in prompt
    assert "One `Add` owns exactly one fresh step-local" in prompt
    assert "every representable headed operation occurrence" in prompt
    assert "`ontosyn:usesEquipment`" in prompt
    assert "`ontosyn:isSealed`" in prompt
    assert "`ontosyn:isSeparationType`" in prompt
    assert "`ontosyn:removesSpecies`" in prompt
    assert FULL_HINTS_CONTRACT not in prompt

    for mcp_choreography in (
        "init_memory",
        "export_memory",
        "check_existing_",
        "reuse_authorization_token",
        "create_Add(",
        "add_hasSynthesisStep(",
    ):
        assert mcp_choreography not in prompt


def test_official_onepass_ox_guidance_requires_full_hints():
    with pytest.raises(ValueError, match="requires full_hints=True"):
        build_system_prompt(official_onepass_guidance=True)
