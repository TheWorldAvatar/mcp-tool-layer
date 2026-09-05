from __future__ import annotations

import asyncio
from pathlib import Path

from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    derive_main_surface_contract,
)
from src.experiments.onepass_mcp_manual_projection import (
    build_projection_mcp,
    expose_to_mock_agent,
    recover_distributed_contract,
    render_canonical_onepass_contract,
    render_invariant_onepass_contract,
    render_post_binding_onepass_contract,
)
from models.BaseAgent import compose_user_message_with_mcp_instruction
from src.pipelines.utils.kg_full_hints_onepass import (
    build_mcp_native_onepass_user_aligned_task_prompt,
)


REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "ai_generated_contents_legacy_split_20260901"


def _run_visibility_experiment(mode: str):
    canonical = render_invariant_onepass_contract(
        artifact_root=ARTIFACT,
        repository_root=REPO,
    )
    mcp = build_projection_mcp(canonical_contract=canonical, mode=mode)
    user_task = (
        "DOI: mock-doi\n"
        "Root label: MOCK-MOP\n"
        "Root IRI: urn:mock:synthesis\n"
        "ITER2 ledger: one output and two synthesis inputs.\n"
        "ITER3 ledger: Add 1, Add 2, HeatChill 3.\n"
        "ITER4 ledger: explicit yield 42%."
    )
    view = asyncio.run(expose_to_mock_agent(mcp, user_task=user_task))
    return canonical, view


def test_scheme_a_recovers_exact_contiguous_contract_without_added_descriptions() -> None:
    canonical, view = _run_visibility_experiment("full_instruction")

    assert view.recover_canonical_contract() == canonical
    assert view.system_instruction.count(canonical) == 1
    # Scheme A does not add the experimental "exact excerpts" descriptions.
    assert all(
        "Exact tool-associated excerpts" not in tool["description"]
        for tool in view.tool_catalog.values()
    )


def test_scheme_a_user_aligned_reconstructs_exact_legacy_user_template() -> None:
    canonical = render_canonical_onepass_contract(
        artifact_root=ARTIFACT,
        repository_root=REPO,
    )
    contract = render_post_binding_onepass_contract(
        artifact_root=ARTIFACT,
        repository_root=REPO,
    )
    user_template = build_mcp_native_onepass_user_aligned_task_prompt()

    assert "<<<MCP_ONEPASS_USER_CONTRACT>>>" not in user_template
    assert compose_user_message_with_mcp_instruction(
        task_instruction=user_template,
        mcp_instruction=contract,
    ) == canonical
    assert compose_user_message_with_mcp_instruction(
        task_instruction=user_template,
        mcp_instruction=contract,
        task_continuation="\n\nGraph lifecycle instructions:\n- open_or_resume.\n",
    ) == (
        canonical
        + "\n\nGraph lifecycle instructions:\n- open_or_resume.\n"
    )

    mcp = build_projection_mcp(
        canonical_contract=contract,
        mode="full_instruction_user_aligned",
    )
    prompt = asyncio.run(mcp.get_prompt("instruction"))
    messages = asyncio.run(prompt.render())
    assert [str(message.content.text) for message in messages] == [contract]


def test_scheme_b_recovers_exact_contract_from_ordered_mcp_union() -> None:
    canonical, view = _run_visibility_experiment(
        "distributed_tool_descriptions"
    )

    assert recover_distributed_contract(view) == canonical
    assert canonical not in view.system_instruction
    assert "[OP-" in view.system_instruction
    assert all(
        "Exact tool-associated excerpts" in tool["description"]
        for tool in view.tool_catalog.values()
    )


def test_mock_agent_sees_global_contract_and_case_ledger_in_separate_channels() -> None:
    _canonical, view = _run_visibility_experiment("full_instruction")

    # Only stable whole-graph rules remain in MCP instruction/system context.
    assert "complementary" in view.system_instruction
    assert "ITER2 owns the synthesis/output/input/document skeleton" in view.system_instruction
    assert "Clause-to-sequence patterns" in view.system_instruction
    assert "create_om2_quantity" in view.system_instruction
    assert "Call init_memory" in view.system_instruction
    assert "Call export_memory" in view.system_instruction
    assert "Bound runtime inputs" not in view.system_instruction
    assert "{doi}" not in view.system_instruction
    assert "{entity_label}" not in view.system_instruction
    assert "{entity_uri}" not in view.system_instruction
    assert "{iteration_hints}" not in view.system_instruction

    # Per-case evidence is visible only in the user task, not baked into MCP.
    assert "mock-doi" in view.user_task
    assert "explicit yield 42%" in view.user_task
    assert "mock-doi" not in view.system_instruction
    assert "explicit yield 42%" not in view.system_instruction


def test_exposed_catalog_is_the_real_split_surface() -> None:
    _canonical, view = _run_visibility_experiment("full_instruction")
    expected = set(
        derive_main_surface_contract(ARTIFACT / "scripts/ontosynthesis")[
            "expected_mcp_tools"
        ]
    )

    assert set(view.tool_catalog) == expected
    assert "create_Add" in view.tool_catalog
    assert "add_hasSynthesisStep" in view.tool_catalog
    assert "add_hasAddedChemicalInput" in view.tool_catalog

    add_schema = view.tool_catalog["create_Add"]["input_schema"]
    properties = add_schema["properties"]
    assert "hasOrder" in properties
    assert "parent_iri" not in properties
    assert not any(name.startswith("owned_") for name in properties)


def test_scheme_b_preserves_cross_tool_order_with_global_sequence_ids() -> None:
    _canonical, view = _run_visibility_experiment(
        "distributed_tool_descriptions"
    )

    add_description = view.tool_catalog["create_Add"]["description"]
    parent_description = view.tool_catalog["add_hasSynthesisStep"]["description"]
    yield_description = view.tool_catalog["add_hasYield"]["description"]

    def sequences(text: str) -> list[int]:
        return [
            int(line[4:9])
            for line in text.splitlines()
            if line.startswith("[OP-")
        ]

    assert sequences(add_description) == sorted(sequences(add_description))
    assert sequences(parent_description) == sorted(sequences(parent_description))
    assert sequences(yield_description) == sorted(sequences(yield_description))
    assert "create_Add" in add_description
    assert "add_hasSynthesisStep" in parent_description
    assert "add_hasYield" in yield_description
