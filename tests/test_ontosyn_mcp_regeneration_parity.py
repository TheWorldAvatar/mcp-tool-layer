"""Regenerate OntoSyn MCP and prove this change does not alter its public surface.

Direct edits to generated pack files do not count. This test runs the same
generation pipeline used by tmp/generate_occurrence_surface_ontosyn.py into a
fresh directory, then compares that output to frozen indep10 for the agent-facing
MCP contract. Implementation delta is checked by emitting the same compiled
surface with owner-default links forced off.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
    generate_runtime_support_slice,
)
from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    write_generation_contract_bundle,
)
from src.agents.scripts_and_prompts_generation.occurrence_surface_scripts import (
    emit_occurrence_operations,
)
from src.agents.scripts_and_prompts_generation.occurrence_surface_units import (
    compile_fallback_instruction,
)


REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "ai_generated_contents_occurrence_surface_20260902_indep10"
DOMAIN_CONFIG = REPO / "configs/domains/ontosynthesis.json"

UNCHANGED_SCRIPT_FILES = (
    "ontosynthesis_creation_base.py",
    "ontosynthesis_creation_entities.py",
    "ontosynthesis_creation_relationships.py",
    "_occurrence_loop_guard.json",
)


def _pin() -> dict:
    contract = json.loads(
        (
            GOLDEN / "ontology_structures" / "ontosynthesis" / "generation_contract.json"
        ).read_text(encoding="utf-8")
    )
    raw = contract.get("top_entity") or {}
    return {
        "status": "known",
        "class_local": str(raw["class_local"]),
        "class_iri": str(raw["class_iri"]),
        "rationale": "Pinned from the frozen indep10 T-Box semantic contract.",
        "evidence": [str(raw["class_local"])],
    }


def _public_surface(compiled: dict) -> dict[str, object]:
    tools = {}
    for item in compiled.get("public_tools") or []:
        names = ["label"]
        if item.get("parent_parameter"):
            names.append(str(item["parent_parameter"]))
        if item.get("ordering_property_local"):
            names.append(str(item["ordering_property_local"]))
        names.extend(
            str(value.get("property_local") or "")
            for value in item.get("datatype_inputs") or []
        )
        names.extend(str(value.get("parameter") or "") for value in item.get("quantities") or [])
        for group in ("fresh_dependents", "reusable_links"):
            for value in item.get(group) or []:
                names.append(str(value.get("label_parameter") or ""))
                names.extend(
                    str(nested.get("parameter_name") or "")
                    for nested in value.get("datatype_inputs") or []
                )
        names.extend(
            str(value.get("label_parameter") or "")
            for value in item.get("nested_reusable_links") or []
        )
        tools[str(item.get("name") or "")] = [name for name in names if name]
    return {
        "tools": tools,
        "linkers": sorted(
            str(item.get("name") or "")
            for item in compiled.get("public_linkers") or []
            if str(item.get("name") or "")
        ),
    }


def _identity_contracts(compiled: dict) -> dict[str, object]:
    return {
        str(item.get("name") or ""): item.get("identity_contract")
        for item in compiled.get("public_tools") or []
        if str(item.get("name") or "")
    }


def _top_level_functions(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            segment = ast.get_source_segment(source, node)
            if segment:
                out[node.name] = segment
    return out


def _signatures(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
            node.name.startswith("create_") or node.name.startswith("link_")
        ):
            out[node.name] = ast.unparse(node.args)
    return out


def _disable_owner_defaults(compiled: dict) -> dict:
    copy = json.loads(json.dumps(compiled))
    for tool in copy.get("public_tools") or []:
        for group in ("fresh_dependents", "reusable_links"):
            for item in tool.get(group) or []:
                item["default_label_from_owner"] = False
    return copy


def _generate_ontosyn_mcp(output_root: Path) -> dict:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=output_root,
        repository_root=REPO,
        write_files=True,
        operation_mode="occurrence_surface",
        selected_top_entity=_pin(),
    )
    generate_deterministic_script_slice(context)
    generate_deterministic_prompt_slice(context)
    generate_runtime_support_slice(context)
    write_generation_contract_bundle(context.contract, Path(context.contract_path))
    compiled = context.contract.get("occurrence_surface_units") or {}
    decisions = context.contract.get("occurrence_surface_decisions") or {}
    enabled = emit_occurrence_operations(context, compiled)
    disabled = emit_occurrence_operations(context, _disable_owner_defaults(compiled))
    return {
        "compiled": compiled,
        "decisions": decisions,
        "operations": enabled,
        "operations_without_defaults": disabled,
        "scripts": output_root / "scripts" / "ontosynthesis",
        "prompts": output_root / "prompts" / "ontosynthesis",
    }


@pytest.fixture(scope="module")
def regenerated_ontosyn(tmp_path_factory: pytest.TempPathFactory) -> dict:
    if not GOLDEN.is_dir():
        pytest.skip("frozen indep10 OntoSyn MCP is absent")
    output = tmp_path_factory.mktemp("ontosyn_mcp_regen")
    return _generate_ontosyn_mcp(output)


def test_regenerated_ontosyn_public_mcp_matches_indep10(regenerated_ontosyn: dict) -> None:
    golden_compiled = json.loads(
        (
            GOLDEN
            / "ontology_structures"
            / "ontosynthesis"
            / "generation_contract.json"
        ).read_text(encoding="utf-8")
    )["occurrence_surface_units"]
    compiled = regenerated_ontosyn["compiled"]
    decisions = regenerated_ontosyn["decisions"]
    assert compiled.get("errors") in (None, [])
    assert decisions.get("llm_judged_count") in {0, None}
    output = next(
        item
        for item in compiled.get("public_tools") or []
        if item.get("name") == "create_ChemicalOutput"
    )
    represented = next(
        item
        for item in output.get("reusable_links") or []
        if item.get("predicate_local") == "isRepresentedBy"
    )
    assert represented.get("required_bridge_link") is True
    assert _public_surface(compiled) == _public_surface(golden_compiled)
    assert _identity_contracts(compiled) == _identity_contracts(golden_compiled)
    assert compiled["instruction"] == compile_fallback_instruction(compiled)
    assert "never pass a created child handle" in compiled["instruction"]
    assert "do not substitute the bound root" not in compiled["instruction"]


def test_regenerated_ontosyn_tool_signatures_match_indep10_except_bridge_label(
    regenerated_ontosyn: dict,
) -> None:
    golden_ops = (
        GOLDEN / "scripts" / "ontosynthesis" / "ontosynthesis_occurrence_operations.py"
    ).read_text(encoding="utf-8")
    regenerated = _signatures(regenerated_ontosyn["operations"])
    frozen = _signatures(golden_ops)
    assert set(regenerated) == set(frozen)
    changed = sorted(name for name in regenerated if regenerated[name] != frozen[name])
    assert changed == ["create_ChemicalOutput"], changed
    assert "isRepresentedBy_label: str" in regenerated["create_ChemicalOutput"]
    assert "isRepresentedBy_label: str | None" not in regenerated["create_ChemicalOutput"]
    assert "isRepresentedBy_hasCCDCNumber: str | None" in regenerated[
        "create_ChemicalOutput"
    ]


def test_regenerated_ontosyn_core_scripts_match_indep10(
    regenerated_ontosyn: dict,
) -> None:
    scripts = regenerated_ontosyn["scripts"]
    golden_scripts = GOLDEN / "scripts" / "ontosynthesis"
    mismatched = [
        name
        for name in UNCHANGED_SCRIPT_FILES
        if (scripts / name).read_text(encoding="utf-8")
        != (golden_scripts / name).read_text(encoding="utf-8")
    ]
    assert mismatched == [], mismatched


def test_owner_default_changes_only_unique_parent_representation_tool(
    regenerated_ontosyn: dict,
) -> None:
    enabled = _top_level_functions(regenerated_ontosyn["operations"])
    disabled = _top_level_functions(regenerated_ontosyn["operations_without_defaults"])
    assert set(enabled) == set(disabled)
    changed = sorted(name for name in enabled if enabled[name] != disabled[name])
    assert changed == ["create_ChemicalOutput"], changed
    assert enabled["create_ChemicalInput"] == disabled["create_ChemicalInput"]
    output_fn = enabled["create_ChemicalOutput"]
    assert "isRepresentedBy_label = isRepresentedBy_label or _optional_label(label)" in output_fn
    assert "ensure=_ensure_default_links" in output_fn
    assert (
        "isRepresentedBy_label = isRepresentedBy_label or _optional_label(label)"
        not in disabled["create_ChemicalOutput"]
    )
    assert "ensure=_ensure_default_links" not in disabled["create_ChemicalOutput"]
    for name, body in enabled.items():
        if name == "create_ChemicalOutput":
            continue
        assert "or _optional_label(label)" not in body
        assert "ensure=_ensure_default_links" not in body
