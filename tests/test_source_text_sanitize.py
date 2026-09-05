from __future__ import annotations

from src.utils.source_text_sanitize import sanitize_source_markdown


def test_sanitize_greek_super_sub_and_middle_dot() -> None:
    raw = "VMOP-α / VMOP-β · VMOC-3·2pyrene H₂O Cu²⁺ γ-cyclodextrin"
    out = sanitize_source_markdown(raw)
    assert "α" not in out
    assert "β" not in out
    assert "γ" not in out
    assert "·" not in out
    assert "₂" not in out
    assert "²" not in out
    assert "VMOP-alpha" in out
    assert "VMOP-beta" in out
    assert "VMOC-3-2pyrene" in out
    assert "H2O" in out
    assert "Cu2+" in out
    assert "gamma-cyclodextrin" in out


def test_sanitize_strips_zero_width_and_replacement_chars() -> None:
    raw = "IRMOP-\u200b50\ufffd and soft\u00adhyphen"
    out = sanitize_source_markdown(raw)
    assert "\u200b" not in out
    assert "\ufffd" not in out
    assert "\u00ad" not in out
    assert "IRMOP-50" in out
    assert "softhyphen" in out


def test_sanitize_preserves_markdown_newlines() -> None:
    raw = "## Synthesis of VMOP-α\n\nStep 1\n"
    out = sanitize_source_markdown(raw)
    assert out.startswith("## Synthesis of VMOP-alpha")
    assert "\n\nStep 1\n" in out
