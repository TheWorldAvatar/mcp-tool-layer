from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.extraction_runtime.agent.client import BaseAgent
from src.extraction_runtime.agent.loop import (
    project_react_history_to_receipts,
    stop_repeated_committed_output_calls,
    summarize_react_tool_activity,
)
from src.extraction_runtime.agent.react_hooks import reset_react_hook_caches
from src.kg_building.experiment_protocol import ExperimentProtocol


HOOKS = "src.extraction_runtime.agent.react_hooks"


@pytest.fixture(autouse=True)
def _isolate_react_hook_contracts(monkeypatch):
    monkeypatch.delenv("TWA_GENERATED_ARTIFACT_ROOT", raising=False)
    monkeypatch.delenv("TWA_MAIN_ONTOLOGY_NAME", raising=False)
    monkeypatch.delenv("TWA_REACT_NO_PROGRESS_THRESHOLD", raising=False)
    reset_react_hook_caches()
    yield
    reset_react_hook_caches()


def _write_ownership(tmp_path: Path, payload: dict) -> None:
    path = (
        tmp_path
        / "scripts"
        / "ontosynthesis"
        / "_occurrence_argument_ownership.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_pipeline_and_agent_defaults_enable_both_hooks() -> None:
    spec = inspect.signature(BaseAgent.run)
    assert spec.parameters["react_history_projection"].default is True
    assert spec.parameters["react_argument_firewall"].default is True
    protocol = ExperimentProtocol(name="generic-strict", ox_prompt_profile="generic-strict")
    assert protocol.react_history_projection is True
    assert protocol.react_argument_firewall is True


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
    assert "AUTHORITATIVE MCP STATE" in receipt
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


def test_react_projection_preserves_distinct_compiled_has_order(monkeypatch) -> None:
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
    monkeypatch.setattr(f"{HOOKS}.load_occurrence_loop_guard_contract", lambda: contract)
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


def test_argument_firewall_preserves_legal_subset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_ownership(
        tmp_path,
        {
            "tools": [
                {
                    "name": "create_Add",
                    "allowed_arguments": ["parent_iri", "hasOrder", "label"],
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
        },
    )
    monkeypatch.setenv("TWA_GENERATED_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("TWA_MAIN_ONTOLOGY_NAME", "ontosynthesis")
    messages = [
        HumanMessage(content="ledger"),
        AIMessage(
            id="provider-message-0",
            content="",
            tool_calls=[
                {
                    "id": "tool-call-0",
                    "name": "create_Add",
                    "args": {
                        "parent_iri": "urn:root",
                        "hasOrder": 1,
                        "label": "Add",
                        "hasTargetTemperature": "90 degC",
                        "isSealed": True,
                    },
                }
            ],
        ),
    ]

    sanitized = stop_repeated_committed_output_calls(
        {"messages": messages},
        argument_firewall=True,
    )
    rewritten = sanitized["messages"][0]
    assert rewritten.tool_calls[0]["name"] == "create_Add"
    assert rewritten.tool_calls[0]["args"] == {
        "parent_iri": "urn:root",
        "hasOrder": 1,
        "label": "Add",
    }
    firewall = rewritten.additional_kwargs["argument_firewall"]
    assert firewall["code"] == "ARGUMENT_FIREWALL_SANITIZED"
    warning = firewall["warnings"][0]
    assert warning["removed_arguments"] == ["hasTargetTemperature", "isSealed"]
    assert warning["skip_receipt"]["controlled"] is True
    assert warning["removed_facets"][0]["owner_candidates"] == ["create_HeatChill"]


def test_argument_owner_mismatch_in_projected_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_ownership(
        tmp_path,
        {
            "tools": [
                {
                    "name": "create_Add",
                    "allowed_arguments": ["parent_iri", "hasOrder", "label"],
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
        },
    )
    monkeypatch.setenv("TWA_GENERATED_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("TWA_MAIN_ONTOLOGY_NAME", "ontosynthesis")
    messages = [
        HumanMessage(content="ledger"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "invalid-add",
                    "name": "create_Add",
                    "args": {
                        "parent_iri": "urn:root",
                        "hasOrder": 1,
                        "label": "Add",
                        "hasTargetTemperature": "90 degC",
                    },
                }
            ],
        ),
        ToolMessage(
            name="create_Add",
            tool_call_id="invalid-add",
            status="error",
            content="ValidationError: unexpected keyword argument",
        ),
    ]

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
    assert "hasTargetTemperature" in receipt
    assert "create_HeatChill" in receipt

    activity = summarize_react_tool_activity(
        messages,
        classify_argument_owner_mismatch=True,
    )
    payload = activity["tool_outputs"][0]["structured_content"]
    assert payload["code"] == "ARGUMENT_OWNER_MISMATCH"
    assert payload["graph_changed"] is False
    assert payload["argument_owners"]["hasTargetTemperature"] == ["create_HeatChill"]


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


def test_react_guard_stops_after_three_explicit_no_progress_turns() -> None:
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


def test_firewall_is_noop_without_ownership_contract() -> None:
    messages = [
        HumanMessage(content="ledger"),
        AIMessage(
            id="provider-message-noop",
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "create_Add",
                    "args": {"parent_iri": "urn:root", "isSealed": True},
                }
            ],
        ),
    ]
    assert (
        stop_repeated_committed_output_calls(
            {"messages": messages},
            argument_firewall=True,
        )
        == {}
    )
