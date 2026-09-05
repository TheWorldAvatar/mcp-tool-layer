from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from rdflib import RDF, URIRef

from models.BaseAgent import (
    _summarize_react_tool_activity,
    project_react_history_to_receipts,
    stop_repeated_committed_output_calls,
)
from src.pipelines.utils.kg_full_hints_onepass import (
    build_mcp_semantic_surface_task_prompt,
)


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = (
    REPO
    / "ai_generated_contents_inferred_atomic_script_generation_20260831"
    / "scripts"
)


def _semantic_modules():
    sys.path.insert(0, str(SCRIPTS))
    try:
        for name in list(sys.modules):
            if name == "ontosynthesis" or name.startswith("ontosynthesis."):
                del sys.modules[name]
        main = importlib.import_module("ontosynthesis.semantic_main")
        operations = importlib.import_module("ontosynthesis.semantic_operations")
        runtime = importlib.import_module("ontosynthesis._fixed_rdf_runtime")
        return main, operations, runtime
    finally:
        sys.path.remove(str(SCRIPTS))


def test_no_contract_task_prompt_contains_only_bindings_and_ledger() -> None:
    prompt = build_mcp_semantic_surface_task_prompt()

    assert "{doi}" in prompt
    assert "{entity_uri}" in prompt
    assert "{iteration_hints}" in prompt
    assert "ONEPASS" not in prompt
    assert "official per-iteration construction rules" not in prompt.lower()
    assert "add_hasAddedChemicalInput" not in prompt


def test_semantic_surface_has_no_public_add_or_check_mutators() -> None:
    main, _, _ = _semantic_modules()
    tools = asyncio.run(main.mcp.get_tools())
    names = set(tools)

    assert "create_add_step" in names
    assert "update_step_duration" in names
    assert "link_supplier" in names
    assert not any(name.startswith("add_") for name in names)
    assert not any(name.startswith("check_existing_") for name in names)
    assert names - {
        "init_memory",
        "export_memory",
        "inspect_ordered_members",
    } == {
        name
        for name in names
        if name.startswith(("create_", "update_", "link_"))
    }


def test_basic_instruction_is_operational_not_onepass_contract() -> None:
    main, _, _ = _semantic_modules()
    prompt = asyncio.run(main.mcp.get_prompt("instruction"))
    text = prompt.fn()

    assert "create_*" in text
    assert "update_*" in text
    assert "link_*" in text
    assert "never reads that ledger" in text
    assert "ONEPASS" not in text
    assert "official per-iteration" not in text


def test_add_creator_and_quantity_updater_materialize_incrementally() -> None:
    _, operations, runtime = _semantic_modules()
    graph = runtime.retained_graph()
    runtime.reset_graph(graph)
    synthesis_iri = URIRef("https://example.com/synthesis/test")
    graph.add(
        (
            synthesis_iri,
            RDF.type,
            URIRef("https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis"),
        )
    )

    created = json.loads(
        operations.create_add_step(
            label="Add zinc nitrate",
            order=1,
            synthesis_iri=str(synthesis_iri),
            chemical_label="zinc nitrate",
            chemical_amount="1 mmol",
        )
    )
    assert created["status"] == "ok"
    step_iri = URIRef(created["iri"])
    assert (
        synthesis_iri,
        URIRef("https://www.theworldavatar.com/kg/OntoSyn/hasSynthesisStep"),
        step_iri,
    ) in graph
    assert len(
        list(
            graph.objects(
                step_iri,
                URIRef(
                    "https://www.theworldavatar.com/kg/OntoSyn/"
                    "hasAddedChemicalInput"
                ),
            )
        )
    ) == 1

    updated = json.loads(operations.update_step_duration(str(step_iri), "2 h"))
    assert updated["status"] == "ok"
    assert (
        step_iri,
        URIRef("https://www.theworldavatar.com/kg/OntoSyn/hasStepDuration"),
        URIRef(updated["quantity_iri"]),
    ) in graph


def test_v4_surface_is_smaller_and_has_no_raw_iri_linkers() -> None:
    _, _, _ = _semantic_modules()
    main = importlib.import_module("ontosynthesis.semantic_main_v4")
    tools = asyncio.run(main.mcp.get_tools())
    names = set(tools)

    assert len(names) == 16
    assert "create_heat_chill_step" in names
    assert "link_synthesis_equipment" in names
    assert "link_washing_solvent" not in names
    assert "create_chemical_input" not in names
    assert not any(name.startswith("add_") for name in names)


def test_v4_complete_step_attaches_facets_and_reuses_descriptors() -> None:
    _, _, runtime = _semantic_modules()
    operations = importlib.import_module("ontosynthesis.semantic_operations_v4")
    graph = runtime.retained_graph()
    runtime.reset_graph(graph)
    synthesis_iri = URIRef("https://example.com/synthesis/v4")
    graph.add(
        (
            synthesis_iri,
            RDF.type,
            URIRef("https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis"),
        )
    )

    first = json.loads(
        operations.create_heat_chill_step(
            "Heat",
            1,
            str(synthesis_iri),
            target_temperature="120 degC",
            duration="2 d",
            vessel_type="Teflon-lined autoclave",
            vessel_environment="nitrogen",
        )
    )
    second = json.loads(
        operations.create_heat_chill_step(
            "Cool",
            2,
            str(synthesis_iri),
            target_temperature="room temperature",
            vessel_type="Teflon-lined autoclave",
            vessel_environment="nitrogen",
        )
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    before_duplicate = len(graph)
    duplicate = json.loads(
        operations.create_heat_chill_step(
            "Heat repeated by agent",
            1,
            str(synthesis_iri),
            target_temperature="999 degC",
        )
    )
    assert duplicate["status"] == "ok"
    assert duplicate["already_committed"] is True
    assert len(graph) == before_duplicate
    vessel_type = URIRef("https://www.theworldavatar.com/kg/OntoSyn/VesselType")
    environment = URIRef(
        "https://www.theworldavatar.com/kg/OntoSyn/VesselEnvironment"
    )
    assert len(set(graph.subjects(RDF.type, vessel_type))) == 1
    assert len(set(graph.subjects(RDF.type, environment))) == 1


def test_v4_output_uses_representation_relation_not_input_material_relation() -> None:
    _, _, runtime = _semantic_modules()
    operations = importlib.import_module("ontosynthesis.semantic_operations_v4")
    signature = inspect.signature(operations.create_synthesis_output)
    assert "material_label" not in signature.parameters
    assert "representation_label" in signature.parameters

    graph = runtime.retained_graph()
    runtime.reset_graph(graph)
    synthesis_iri = URIRef("https://example.com/synthesis/output-v4")
    graph.add(
        (
            synthesis_iri,
            RDF.type,
            URIRef("https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis"),
        )
    )

    created = json.loads(
        operations.create_synthesis_output(
            str(synthesis_iri),
            "MOP-1",
            representation_label="MetalOrganicPolyhedron",
        )
    )
    assert created["status"] == "ok"
    output_iri = URIRef(created["iri"])
    representation_iri = URIRef(created["representation_iri"])
    assert (
        output_iri,
        URIRef("https://www.theworldavatar.com/kg/OntoSyn/isRepresentedBy"),
        representation_iri,
    ) in graph
    assert not list(
        graph.objects(
            output_iri,
            URIRef("https://www.theworldavatar.com/kg/OntoSyn/referencesMaterial"),
        )
    )
    before_duplicate = len(graph)
    duplicate = json.loads(
        operations.create_synthesis_output(
            str(synthesis_iri),
            "Repeated output",
            representation_label="Repeated representation",
        )
    )
    assert duplicate["status"] == "ok"
    assert duplicate["already_committed"] is True
    assert duplicate["iri"] == created["iri"]
    assert len(graph) == before_duplicate


def test_react_projection_keeps_full_state_and_emits_compact_receipts() -> None:
    long_aliases = "alias;" * 100
    messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "create_add_step",
                    "args": {
                        "label": "Add zinc nitrate",
                        "order": 1,
                        "chemical_alternative_names": long_aliases,
                    },
                }
            ],
        ),
        ToolMessage(
            name="create_add_step",
            tool_call_id="call-1",
            content=json.dumps(
                {
                    "status": "ok",
                    "iri": "https://example.com/add/1",
                    "dependent_iri": "https://example.com/input/1",
                    "message": "created",
                }
            ),
        ),
    ]

    projected = project_react_history_to_receipts({"messages": messages})[
        "llm_input_messages"
    ]

    assert len(messages) == 3
    assert messages[2].content
    assert len(projected) == 2
    assert projected[0] is messages[0]
    receipt = str(projected[1].content)
    assert "create_add_step" in receipt
    assert "https://example.com/add/1" in receipt
    assert long_aliases not in receipt
    assert "sha256" in receipt


def test_react_guard_stops_repeated_committed_output_call() -> None:
    synthesis_iri = "https://example.com/synthesis/output-guard"
    messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "output-1",
                    "name": "create_synthesis_output",
                    "args": {
                        "synthesis_iri": synthesis_iri,
                        "label": "MOP-1",
                    },
                }
            ],
        ),
        ToolMessage(
            name="create_synthesis_output",
            tool_call_id="output-1",
            content=json.dumps(
                {
                    "status": "ok",
                    "iri": "https://example.com/output/1",
                }
            ),
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "output-2",
                    "name": "create_synthesis_output",
                    "args": {
                        "synthesis_iri": synthesis_iri,
                        "label": "MOP-1",
                    },
                }
            ],
        ),
    ]

    guarded = stop_repeated_committed_output_calls({"messages": messages})

    assert len(guarded["messages"]) == 1
    assert not guarded["messages"][0].tool_calls
    assert "already committed" in guarded["messages"][0].content


def test_react_guard_uses_compiled_occurrence_contract() -> None:
    parent_iri = "https://example.com/synthesis/generated-guard"
    contract = {
        "schema_version": "occurrence-loop-guard.v1",
        "unique_parent_tools": [
            {
                "name": "create_ChemicalOutput",
                "identity_args": ["parent_iri"],
            }
        ],
        "ordered_member_tools": [
            {
                "name": "create_Add",
                "identity_args": ["parent_iri", "hasOrder"],
            }
        ],
    }
    output_messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "output-1",
                    "name": "create_ChemicalOutput",
                    "args": {"parent_iri": parent_iri, "label": "MOP-1"},
                }
            ],
        ),
        ToolMessage(
            name="create_ChemicalOutput",
            tool_call_id="output-1",
            content=json.dumps({"status": "ok", "iri": "https://example.com/output/1"}),
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "output-2",
                    "name": "create_ChemicalOutput",
                    "args": {"parent_iri": parent_iri, "label": "MOP-1"},
                }
            ],
        ),
    ]
    guarded = stop_repeated_committed_output_calls(
        {"messages": output_messages}, contract=contract
    )
    assert guarded["messages"][0].content
    assert "already committed" in guarded["messages"][0].content

    generated_contract = {
        **contract,
        "mutation_tools": [
            *contract["unique_parent_tools"],
            *contract["ordered_member_tools"],
        ],
    }
    assert stop_repeated_committed_output_calls(
        {"messages": output_messages}, contract=generated_contract
    ) == {}

    input_messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "input-1",
                    "name": "create_ChemicalInput",
                    "args": {"parent_iri": parent_iri, "label": "DMF"},
                }
            ],
        ),
        ToolMessage(
            name="create_ChemicalInput",
            tool_call_id="input-1",
            content=json.dumps({"status": "ok", "iri": "https://example.com/input/1"}),
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "input-2",
                    "name": "create_ChemicalInput",
                    "args": {"parent_iri": parent_iri, "label": "water"},
                }
            ],
        ),
    ]
    assert stop_repeated_committed_output_calls(
        {"messages": input_messages}, contract=contract
    ) == {}


def test_react_projection_preserves_distinct_compiled_has_order(
    monkeypatch,
) -> None:
    parent_iri = "https://example.com/synthesis/ordered-receipts"
    contract = {
        "schema_version": "occurrence-loop-guard.v1",
        "ordered_member_tools": [
            {
                "name": "create_Add",
                "identity_args": ["parent_iri", "hasOrder"],
            }
        ],
    }
    monkeypatch.setattr(
        "models.BaseAgent.load_occurrence_loop_guard_contract",
        lambda: contract,
    )
    messages = [HumanMessage(content="bound ledger")]
    for order in (1, 2):
        call_id = f"add-{order}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": call_id,
                            "name": "create_Add",
                            "args": {
                                "parent_iri": parent_iri,
                                "hasOrder": order,
                                "label": f"Add step {order}",
                            },
                        }
                    ],
                ),
                ToolMessage(
                    name="create_Add",
                    tool_call_id=call_id,
                    content=json.dumps(
                        {
                            "status": "ok",
                            "iri": f"https://example.com/add/{order}",
                        }
                    ),
                ),
            ]
        )

    projected = project_react_history_to_receipts({"messages": messages})
    receipt_lines = str(projected["llm_input_messages"][1].content).splitlines()[1:]
    receipts = [json.loads(line) for line in receipt_lines]

    assert len(receipts) == 2
    assert {receipt["args"]["hasOrder"] for receipt in receipts} == {1, 2}


def test_react_projection_normalizes_pre_execution_validation(
    monkeypatch,
) -> None:
    contract = {
        "schema_version": "occurrence-loop-guard.v1",
        "ordered_member_tools": [
            {
                "name": "create_Add",
                "identity_args": ["parent_iri", "hasOrder"],
            }
        ],
    }
    monkeypatch.setattr(
        "models.BaseAgent.load_occurrence_loop_guard_contract",
        lambda: contract,
    )
    messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "invalid-add",
                    "name": "create_Add",
                    "args": {
                        "parent_iri": "urn:synthesis:1",
                        "hasOrder": 1,
                        "label": "Add",
                        "isSealed": True,
                    },
                }
            ],
        ),
        ToolMessage(
            name="create_Add",
            tool_call_id="invalid-add",
            status="error",
            content=(
                "1 validation error for create_Add\n"
                "isSealed\nUnexpected keyword argument"
            ),
        ),
    ]

    projected = project_react_history_to_receipts({"messages": messages})
    receipt = str(projected["llm_input_messages"][1].content)

    assert '"code": "TOOL_ARGUMENT_VALIDATION"' in receipt
    assert '"pre_execution_error": true' in receipt
    assert '"graph_changed": false' in receipt
    assert '"semantic_fingerprint":' in receipt


def test_react_guard_safely_retries_extra_arguments_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = {
        "mutation_tools": [
            {
                "name": "create_Add",
                "identity_args": ["parent_iri", "hasOrder"],
            }
        ],
    }
    monkeypatch.setattr(
        "models.BaseAgent.load_occurrence_loop_guard_contract",
        lambda: contract,
    )
    artifact_root = tmp_path / "generated"
    ownership_path = (
        artifact_root
        / "scripts"
        / "ontosynthesis"
        / "_occurrence_argument_ownership.json"
    )
    ownership_path.parent.mkdir(parents=True)
    ownership_path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "create_Add",
                        "allowed_arguments": [
                            "parent_iri",
                            "hasOrder",
                            "label",
                        ],
                    },
                    {
                        "name": "create_HeatChill",
                        "allowed_arguments": [
                            "parent_iri",
                            "hasOrder",
                            "label",
                            "hasTargetTemperature",
                            "isSealed",
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TWA_GENERATED_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("TWA_MAIN_ONTOLOGY_NAME", "ontosynthesis")
    messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "invalid-add",
                    "name": "create_Add",
                    "args": {
                        "parent_iri": "urn:synthesis:1",
                        "hasOrder": 1,
                        "label": "Add",
                        "hasTargetTemperature": "90 degC",
                        "isSealed": True,
                    },
                }
            ],
        ),
        ToolMessage(
            name="create_Add",
            tool_call_id="invalid-add",
            status="error",
            content=(
                "2 validation errors for create_Add\n"
                "hasTargetTemperature\nUnexpected keyword argument\n"
                "isSealed\nUnexpected keyword argument"
            ),
        ),
        AIMessage(content="Done."),
    ]

    guarded = stop_repeated_committed_output_calls(
        {"messages": messages}, contract=contract
    )
    repair = guarded["messages"][0]
    assert repair.tool_calls[0]["name"] == "create_Add"
    assert repair.tool_calls[0]["args"] == {
        "parent_iri": "urn:synthesis:1",
        "hasOrder": 1,
        "label": "Add",
    }
    notice = json.loads(str(repair.content))
    assert notice["removed_arguments"] == [
        "hasTargetTemperature",
        "isSealed",
    ]
    assert notice["argument_owners"]["hasTargetTemperature"] == [
        "create_HeatChill"
    ]

    successful_messages = messages + [
        repair,
        ToolMessage(
            name="create_Add",
            tool_call_id=repair.tool_calls[0]["id"],
            content=json.dumps({"status": "ok", "graph_changed": True}),
        ),
        AIMessage(content="Done after correction."),
    ]
    assert (
        stop_repeated_committed_output_calls(
            {"messages": successful_messages}, contract=contract
        )
        == {}
    )


def test_argument_owner_mismatch_rejects_add_and_separate_without_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root = tmp_path / "firewall-artifact"
    ownership_path = (
        artifact_root
        / "scripts"
        / "ontosynthesis"
        / "_occurrence_argument_ownership.json"
    )
    ownership_path.parent.mkdir(parents=True)
    ownership_path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "create_Add",
                        "allowed_arguments": [
                            "parent_iri",
                            "hasOrder",
                            "label",
                        ],
                    },
                    {
                        "name": "create_HeatChill",
                        "allowed_arguments": [
                            "parent_iri",
                            "hasOrder",
                            "label",
                            "hasTargetTemperature",
                            "isSealed",
                        ],
                    },
                    {
                        "name": "create_Separate",
                        "allowed_arguments": [
                            "parent_iri",
                            "hasOrder",
                            "label",
                        ],
                    },
                    {
                        "name": "create_Evaporate",
                        "allowed_arguments": [
                            "parent_iri",
                            "hasOrder",
                            "label",
                            "removesSpecies_label",
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TWA_GENERATED_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("TWA_MAIN_ONTOLOGY_NAME", "ontosynthesis")
    cases = [
        (
            "create_Add",
            {
                "parent_iri": "urn:root",
                "hasOrder": 1,
                "label": "Add",
                "hasTargetTemperature": "90 degC",
                "isSealed": True,
            },
            ["hasTargetTemperature", "isSealed"],
            "create_HeatChill",
        ),
        (
            "create_Separate",
            {
                "parent_iri": "urn:root",
                "hasOrder": 2,
                "label": "Separate",
                "removesSpecies_label": "mother liquor",
            },
            ["removesSpecies_label"],
            "create_Evaporate",
        ),
    ]
    for index, (tool_name, args, removed, owner) in enumerate(cases):
        call_id = f"tool-call-{index}"
        messages = [
            HumanMessage(content="ledger"),
            AIMessage(
                id=f"provider-message-{index}",
                content="",
                tool_calls=[
                    {
                        "id": call_id,
                        "name": tool_name,
                        "args": args,
                    }
                ],
            ),
            ToolMessage(
                name=tool_name,
                tool_call_id=call_id,
                status="error",
                content="ValidationError: unexpected keyword argument",
            ),
        ]
        sanitized = stop_repeated_committed_output_calls(
            {"messages": messages[:2]},
            argument_firewall=True,
        )
        rewritten = sanitized["messages"][0]
        assert rewritten.tool_calls[0]["name"] == tool_name
        assert all(
            argument not in rewritten.tool_calls[0]["args"]
            for argument in removed
        )
        warning = rewritten.additional_kwargs["argument_firewall"]["warnings"][0]
        assert warning["removed_arguments"] == removed
        assert warning["skip_receipt"]["controlled"] is True
        assert warning["removed_facets"][0]["owner_candidates"] == [owner]

        ordinary = project_react_history_to_receipts({"messages": messages})
        assert "TOOL_ARGUMENT_VALIDATION" in str(
            ordinary["llm_input_messages"][1].content
        )
        projected = project_react_history_to_receipts(
            {"messages": messages},
            classify_argument_owner_mismatch=True,
        )
        receipt = str(projected["llm_input_messages"][1].content)
        assert "ARGUMENT_OWNER_MISMATCH" in receipt
        assert all(argument in receipt for argument in removed)
        assert owner in receipt
        assert "The call was not executed" in receipt
        activity = _summarize_react_tool_activity(
            messages,
            classify_argument_owner_mismatch=True,
        )
        payload = activity["tool_outputs"][0]["structured_content"]
        assert payload["code"] == "ARGUMENT_OWNER_MISMATCH"
        assert payload["graph_changed"] is False
        assert payload["argument_owners"][removed[0]] == [owner]


def test_react_projection_preserves_already_committed_receipt() -> None:
    messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "output-committed",
                    "name": "create_synthesis_output",
                    "args": {"synthesis_iri": "https://example.com/synthesis/1"},
                }
            ],
        ),
        ToolMessage(
            name="create_synthesis_output",
            tool_call_id="output-committed",
            content=json.dumps(
                {
                    "status": "ok",
                    "already_committed": True,
                    "graph_changed": False,
                    "graph_revision": 4,
                    "semantic_fingerprint": "output:1",
                }
            ),
        ),
    ]

    projected = project_react_history_to_receipts({"messages": messages})
    receipt = str(projected["llm_input_messages"][1].content)

    assert '"already_committed": true' in receipt
    assert '"graph_changed": false' in receipt
    assert '"graph_revision": 4' in receipt
    assert '"semantic_fingerprint": "output:1"' in receipt


def test_react_projection_keeps_repairable_facet_warning() -> None:
    messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "heat-quantity-warning",
                    "name": "create_HeatChill",
                    "args": {
                        "parent_iri": "urn:root",
                        "hasOrder": 1,
                        "label": "Heat",
                        "hasTargetTemperature": "60 oC",
                    },
                }
            ],
        ),
        ToolMessage(
            name="create_HeatChill",
            tool_call_id="heat-quantity-warning",
            content=json.dumps(
                {
                    "status": "ok",
                    "already_committed": False,
                    "graph_changed": True,
                    "semantic_fingerprint": "facet-warning",
                    "omitted_facet": True,
                    "facet_warnings": [
                        {
                            "facet": "hasTargetTemperature",
                            "code": "QUANTITY_FACET_OMITTED",
                        }
                    ],
                }
            ),
        ),
    ]

    projected = project_react_history_to_receipts({"messages": messages})
    receipt = str(projected["llm_input_messages"][1].content)

    assert '"omitted_facet": true' in receipt
    assert "hasTargetTemperature" in receipt
    assert "retry that same occurrence identity" in receipt


def test_react_guard_does_not_stop_mixed_duplicate_and_new_call() -> None:
    parent_iri = "https://example.com/synthesis/mixed-guard"
    contract = {
        "schema_version": "occurrence-loop-guard.v1",
        "ordered_member_tools": [
            {
                "name": "create_Add",
                "identity_args": ["parent_iri", "hasOrder"],
            }
        ],
    }
    messages = [
        HumanMessage(content="bound ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "add-1",
                    "name": "create_Add",
                    "args": {"parent_iri": parent_iri, "hasOrder": 1},
                }
            ],
        ),
        ToolMessage(
            name="create_Add",
            tool_call_id="add-1",
            content=json.dumps({"status": "ok", "iri": "https://example.com/add/1"}),
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "add-1-again",
                    "name": "create_Add",
                    "args": {"parent_iri": parent_iri, "hasOrder": 1},
                },
                {
                    "id": "add-2",
                    "name": "create_Add",
                    "args": {"parent_iri": parent_iri, "hasOrder": 2},
                },
            ],
        ),
    ]

    assert stop_repeated_committed_output_calls(
        {"messages": messages}, contract=contract
    ) == {}


def test_react_guard_stops_after_three_explicit_no_progress_turns(
    monkeypatch,
) -> None:
    monkeypatch.delenv("TWA_REACT_NO_PROGRESS_THRESHOLD", raising=False)
    messages = [HumanMessage(content="bound ledger")]
    for index in range(3):
        call_id = f"link-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": call_id,
                            "name": "link_hasDevice",
                            "args": {
                                "subject_iri": "https://example.com/step/1",
                                "object_iri": "https://example.com/device/1",
                            },
                        }
                    ],
                ),
                ToolMessage(
                    name="link_hasDevice",
                    tool_call_id=call_id,
                    content=json.dumps(
                        {
                            "status": "ok",
                            "already_committed": True,
                            "graph_changed": False,
                            "graph_revision": 9,
                            "semantic_fingerprint": "edge:device:1",
                        }
                    ),
                ),
            ]
        )
    messages.append(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "link-next",
                    "name": "link_hasDevice",
                    "args": {
                        "subject_iri": "https://example.com/step/1",
                        "object_iri": "https://example.com/device/1",
                    },
                }
            ],
        )
    )

    guarded = stop_repeated_committed_output_calls(
        {"messages": messages}, contract={}
    )
    payload = json.loads(guarded["messages"][0].content)

    assert payload["code"] == "react_no_progress"
    assert payload["no_progress_turns"] == 3
    assert payload["last_graph_revision"] == 9


def test_react_guard_counts_pre_execution_validation_errors_as_no_progress(
    monkeypatch,
) -> None:
    monkeypatch.delenv("TWA_REACT_NO_PROGRESS_THRESHOLD", raising=False)
    messages = [HumanMessage(content="bound ledger")]
    for index in range(3):
        call_id = f"invalid-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": call_id,
                            "name": "create_Step",
                            "args": {"unsupported_argument": "value"},
                        }
                    ],
                ),
                ToolMessage(
                    name="create_Step",
                    tool_call_id=call_id,
                    content="ValidationError: unexpected keyword argument",
                    status="error",
                ),
            ]
        )
    messages.append(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "invalid-next",
                    "name": "create_Step",
                    "args": {"unsupported_argument": "value"},
                }
            ],
        )
    )

    guarded = stop_repeated_committed_output_calls(
        {"messages": messages}, contract={}
    )
    payload = json.loads(guarded["messages"][0].content)

    assert payload["code"] == "react_no_progress"
    assert payload["no_progress_turns"] == 3
