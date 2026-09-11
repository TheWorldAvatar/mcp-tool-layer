"""Run the generated KG MCP agent for one entity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.extraction_runtime.domain_binding import RuntimeDomain
from src.kg_building.pipeline.binding import bind_kg_runtime_context, pin_entity_context
from src.extraction_runtime.names import entity_artifact_name
from src.kg_building.experiment_protocol import KG_MODEL, KG_SEED, KG_TEMPERATURE
from src.kg_building.revision_lock import KG_MAX_ATTEMPTS, assert_kg_revision_locked_off


def ontology_from_config(config: dict) -> str:
    domain = config.get("domain")
    name = getattr(domain, "ontology_name", None)
    if name:
        return str(name)
    return str(config.get("ontology") or "").strip()


def kg_mcp_binding(config: dict) -> tuple[str, list[str]]:
    domain = config.get("domain")
    if not isinstance(domain, RuntimeDomain):
        raise ValueError("domain config is required for KG MCP binding")
    mcp_set, tools = domain.mcp_capability("kg_building")
    if not mcp_set or not tools:
        raise ValueError(
            f"{domain.ontology_name} mcp_capabilities.kg_building is missing set_name/tools"
        )
    if config.get("test_mcp_config"):
        mcp_set = str(config["test_mcp_config"])
    return mcp_set, tools


def build_kg_prompt(
    *,
    hints: str,
    doi: str,
    entity_label: str,
    entity_uri: str,
    extra_bindings: str = "",
    known_top_entities: list[dict[str, Any]] | None = None,
    doi_folder: str | Path | None = None,
    entity_safe: str = "",
    protocol: str | None = None,
    ontology: str | None = None,
    mcp_tools: list[str] | None = None,
) -> str:
    return bind_kg_runtime_context(
        hints=hints,
        doi=doi,
        entity_label=entity_label,
        entity_uri=entity_uri,
        extra_bindings=extra_bindings,
        known_top_entities=known_top_entities,
        doi_folder=doi_folder,
        entity_safe=entity_safe,
        protocol=protocol,
        ontology=ontology,
        mcp_tools=mcp_tools,
    )


def _dump_kg_prompts(
    dump_dir: Path,
    *,
    entity_safe: str,
    user_text: str,
    instruction: str,
) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    (dump_dir / f"{entity_safe}.user.md").write_text(user_text, encoding="utf-8")
    (dump_dir / f"{entity_safe}.instruction.md").write_text(
        instruction or "", encoding="utf-8"
    )


async def run_kg_agent(
    prompt: str,
    config: dict,
    *,
    doi_hash: str,
    entity_label: str,
    entity_uri: str = "",
    mcp_set: str | None = None,
    mcp_tools: list[str] | None = None,
    recursion_limit: int = 120,
    dump_dir: str | Path | None = None,
) -> tuple[str, dict]:
    from models.ModelConfig import ModelConfig
    from src.extraction_runtime.agent.client import BaseAgent

    assert_kg_revision_locked_off(config)
    if KG_MAX_ATTEMPTS != 1:
        raise RuntimeError("KG revision is locked to a single agent pass")
    safe = entity_artifact_name(entity_label)
    pin_entity_context(name=safe, iri=entity_uri)
    if mcp_set and mcp_tools:
        tools = [str(item) for item in mcp_tools if str(item).strip()]
        bound_set = str(mcp_set).strip()
        if config.get("test_mcp_config"):
            bound_set = str(config["test_mcp_config"])
    else:
        bound_set, tools = kg_mcp_binding(config)
    model_name = str(config.get("kg_model") or "").strip() or KG_MODEL
    agent = BaseAgent(
        model_name=model_name,
        model_config=ModelConfig(
            temperature=KG_TEMPERATURE, top_p=0.01, seed=KG_SEED
        ),
        mcp_set_name=bound_set,
        mcp_tools=tools,
    )
    initial_args: dict[str, str] = {
        "doi": doi_hash,
        "top_level_entity_name": safe,
    }
    if entity_uri:
        initial_args["root_iri"] = entity_uri
    if dump_dir is not None:
        _dump_kg_prompts(Path(dump_dir), entity_safe=safe, user_text=prompt, instruction="")
    reply, metadata = await agent.run(
        prompt,
        recursion_limit=recursion_limit,
        mcp_instruction_in_user=False,
        required_initial_tool="init_memory",
        required_initial_tool_args=initial_args,
        required_final_tool="export_memory",
        required_final_tool_args={
            "doi": doi_hash,
            "top_level_entity_name": safe,
        },
        react_history_projection=True,
        react_argument_firewall=True,
    )
    if dump_dir is not None:
        _dump_kg_prompts(
            Path(dump_dir),
            entity_safe=safe,
            user_text=prompt,
            instruction=str(metadata.get("mcp_instruction") or ""),
        )
    return reply, metadata
