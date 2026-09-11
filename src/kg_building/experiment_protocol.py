"""Locked 1:1 KG experiment profiles.

``--protocol generic-noprompt``, ``generic-strict``, ``with-prompt``, and
``full-prompt`` select the information surface for both constructors.

Pipeline KG always starts from the official no-contract envelope (bindings +
ledger, no paper body). generic-strict stops there. generic-noprompt appends
the shared Graph rules and the same T-Box handbook OX receives. with-prompt
appends the frozen OntoSyn KG-building guidance and the same T-Box handbook.
full-prompt is
the pedagogical upper bound: from_extraction + full_hints + official ONEPASS
+ T-Box. Occurrence MCP instruction is unchanged in all modes. Extra MCP
servers listed on the stage tool list inject their user-envelope snippets in
every protocol.

OX generic-strict is occurrence / ownership only. That occurrence is hardcoded
as whole-graph hops (Pipeline MCP expander dual-encoding) and does not change
across generic-strict, generic-noprompt, and with-prompt. OX generic-noprompt
is that same hops occurrence plus Graph rules and T-Box. OX with-prompt is
the same hops occurrence plus the same frozen guidance and T-Box Pipeline
receives.
OX full-prompt is the historical official system (not an appendix on the
occurrence map): from_extraction + full_hints + official ONEPASS + T-Box.

``with-prompt-qty`` and ``with-prompt-hops`` are OX-only overlays on
with-prompt. They are not locked Pipeline 1:1 protocols. They only add
dual-coding appendices; occurrence/ownership is already hops on the three
locked chemistry protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.extraction_runtime.locked_mechanisms import (
    apply_extraction_revision_lock,
    extraction_revision_enabled,
)
from src.kg_building.revision_lock import apply_kg_revision_lock
from src.kg_building.scorer_repo import ensure_scorer_repo

PROTOCOL_NAMES = ("generic-noprompt", "generic-strict", "with-prompt", "full-prompt")
OX_OVERLAY_PROTOCOLS = ("with-prompt-qty", "with-prompt-hops")
OX_PROTOCOL_NAMES = PROTOCOL_NAMES + OX_OVERLAY_PROTOCOLS

KG_MODEL = "openai/gpt-4o-2024-11-20"
KG_SEED = 42
KG_TEMPERATURE = 0.0

PIPELINE_STEPS = [
    "pdf_conversion",
    "tbox_slim",
    "top_entity_extraction",
    "top_entity_kg_building",
    "main_ontology_extractions",
    "main_kg_building",
]
MEDICAL_PIPELINE_STEPS = [
    "pdf_conversion",
    "top_entity_extraction",
    "top_entity_kg_building",
    "main_ontology_extractions",
    "main_kg_building",
]
KIMI_KG_MODEL = "moonshotai/kimi-k3"

PROTOCOL_ENV = {
    "TWA_LLM_SEED": str(KG_SEED),
    "TWA_MCP_TOOL_DESCRIPTIONS_ENABLED": "0",
    "TWA_SEMANTIC_OPERATION_SURFACE": "1",
}

DEFAULT_SCORER_REPO = Path.home() / "Documents" / "GitHub" / "MCP-enhanced-MOPs-Extraction_Reproduction"


@dataclass(frozen=True)
class ExperimentProtocol:
    name: str
    ox_prompt_profile: str
    pipeline_kg: str = "no-contract"
    kg_model: str = KG_MODEL
    seed: int = KG_SEED
    temperature: float = KG_TEMPERATURE
    entity_reuse: bool = True
    official_onepass_guidance: bool = False
    from_main_run: bool = False
    mcp_instruction_in_user: bool = False
    react_history_projection: bool = True
    react_argument_firewall: bool = True
    extraction_revision: bool = True
    kg_revision: bool = False
    pipeline_steps: tuple[str, ...] = tuple(PIPELINE_STEPS)


def resolve_protocol(
    name: str,
    *,
    allow_ox_overlays: bool = False,
) -> ExperimentProtocol:
    text = str(name or "").strip()
    allowed = OX_PROTOCOL_NAMES if allow_ox_overlays else PROTOCOL_NAMES
    if text not in allowed:
        listed = ", ".join(allowed)
        raise ValueError(f"Unknown --protocol {text!r}; expected {listed}")
    return ExperimentProtocol(
        name=text,
        ox_prompt_profile=text,
        official_onepass_guidance=text == "full-prompt",
    )


def lock_revision_policy(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extraction revision follows domain execution_profile; KG revision stays off."""
    updated = apply_kg_revision_lock(apply_extraction_revision_lock(config))
    if config is None:
        return updated
    config.update(updated)
    for key in list(config):
        if key.startswith("skip_iter") and key.endswith("_extraction"):
            config.pop(key, None)
    return config


def apply_to_pipeline_config(config: dict[str, Any], protocol: str) -> dict[str, Any]:
    """Replace run topology with the locked 1:1 Pipeline no-contract path."""
    spec = resolve_protocol(protocol)
    ontology = str(config.get("ontology") or config.get("domain") or "").strip().lower()
    is_medical = ontology == "medical"
    if is_medical:
        config.setdefault("execution_profile", "simple_main")
    config["experiment_protocol"] = spec.name
    config["pipeline_kg"] = spec.pipeline_kg
    config["kg_model"] = spec.kg_model
    config["kg_seed"] = spec.seed
    config["steps"] = list(MEDICAL_PIPELINE_STEPS if is_medical else spec.pipeline_steps)
    config["vision_pdf_conversion"] = True if is_medical else False
    config["official_onepass_guidance"] = spec.official_onepass_guidance
    lock_revision_policy(config)
    config["extraction_revision"] = (
        spec.extraction_revision if extraction_revision_enabled(config) else False
    )
    config["kg_revision"] = spec.kg_revision
    return config


def apply_to_ox_args(args: Any, protocol: str) -> ExperimentProtocol:
    """Lock OX model/seed/reuse and forbid inherited-main Turtle."""
    spec = resolve_protocol(protocol, allow_ox_overlays=True)
    args.prompt_profile = spec.ox_prompt_profile
    args.model = spec.kg_model
    args.seed = spec.seed
    if getattr(args, "from_main_run", None) is not None:
        raise ValueError("--protocol forbids --from-main-run (that is a different experiment)")
    args.kg_revision = False
    return spec


def ox_summary_fields(spec: ExperimentProtocol) -> dict[str, Any]:
    return {
        "experiment_protocol": spec.name,
        "prompt_profile": spec.ox_prompt_profile,
        "entity_reuse": spec.entity_reuse,
        "official_onepass_guidance": spec.official_onepass_guidance,
        "from_main_run": None,
        "pipeline_kg": spec.pipeline_kg,
        "extraction_revision": spec.extraction_revision,
        "kg_revision": spec.kg_revision,
        "model": spec.kg_model,
        "seed": spec.seed,
        "pipeline_token_budget": "equivalent",
    }


def resolve_scorer_repo(explicit: str | Path | None = None) -> Path | None:
    return ensure_scorer_repo(explicit)
