"""Generic-vs-domain policy checks for the overlay generator templates."""

from __future__ import annotations

from pathlib import Path

# Tokens that must never appear in handwritten templates. Derived fills may
# contain them after T-Box compilation.
FORBIDDEN_TEMPLATE_TOKENS = (
    "HeatChill",
    "ChemicalSynthesis",
    "ChemicalOutput",
    "OntoSyn",
    "hasStepDuration",
    "hasTargetTemperature",
    "hasYield",
    "8 h",
    "60 degC",
    "never Time",
    "create_HeatChill",
    "room temperature",
    "overnight",
)


def template_source_paths() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parent
    return (
        root / "instruction.py",
        root / "quantity_runtime_emit.py",
    )


def handwritten_template_blobs() -> dict[str, str]:
    """Return generator-owned prompt templates, not compiled fills."""
    from src.kg_building_mcp_generation.overlay.instruction import (
        COMPACT_QUANTITY_POLICY,
        CREATE_OM2_DOC,
        CREATE_OM2_FRESH_MESSAGE,
        CREATE_OM2_RECOVERY_HINT,
        CREATE_OM2_REPLAY_MESSAGE,
        LINK_OM2_DOC,
        QUANTITY_SKIP_POLICY,
        TOOL_QUANTITY_SUFFIX,
    )

    return {
        "COMPACT_QUANTITY_POLICY": COMPACT_QUANTITY_POLICY,
        "QUANTITY_SKIP_POLICY": QUANTITY_SKIP_POLICY,
        "TOOL_QUANTITY_SUFFIX": TOOL_QUANTITY_SUFFIX,
        "CREATE_OM2_DOC": CREATE_OM2_DOC,
        "CREATE_OM2_FRESH_MESSAGE": CREATE_OM2_FRESH_MESSAGE,
        "CREATE_OM2_REPLAY_MESSAGE": CREATE_OM2_REPLAY_MESSAGE,
        "CREATE_OM2_RECOVERY_HINT": CREATE_OM2_RECOVERY_HINT,
        "LINK_OM2_DOC": LINK_OM2_DOC,
    }


def forbidden_hits(text: str) -> list[str]:
    return [token for token in FORBIDDEN_TEMPLATE_TOKENS if token in text]
