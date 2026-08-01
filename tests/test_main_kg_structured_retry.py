from rdflib import Graph, Literal, RDF, RDFS, URIRef

from models.BaseAgent import _summarize_react_tool_activity
from src.pipelines.main_kg_building.build import (
    _augment_kg_prompt_with_runtime_rules,
    _artifact_fingerprints,
    _build_kg_recovery_prompt,
    _persisted_abox_entity_inventory,
    _persist_structured_turtle_result,
    _tool_trace_has_structured_failure,
    _validate_canonical_entity_identity,
    _write_json_atomic,
    _seed_entity_canonical_memory,
    _write_entity_checkpoint,
    write_global_state,
)
from src.pipelines.utils.top_entity_identity import entity_scope_name


def test_persisted_abox_inventory_exposes_reusable_identity(tmp_path) -> None:
    ttl_path = tmp_path / "memory.ttl"
    graph = Graph()
    entity = URIRef("https://example.test/output")
    entity_type = URIRef("https://example.test/ChemicalOutput")
    graph.add((entity, RDF.type, entity_type))
    graph.add((entity, RDFS.label, Literal("CO-Product-1")))
    graph.serialize(destination=ttl_path, format="turtle")

    assert _persisted_abox_entity_inventory([str(ttl_path)]) == [
        {
            "iri": str(entity),
            "types": [str(entity_type)],
            "labels": ["CO-Product-1"],
        }
    ]


def test_structured_tool_failure_does_not_read_response_prose() -> None:
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "status": "",
                    "content": '{"ok": false, "error_type": "ToolError"}',
                    "structured_content": {
                        "ok": False,
                        "error_type": "ToolError",
                    },
                }
            ]
        }
    }

    assert _tool_trace_has_structured_failure(metadata)
    assert not _tool_trace_has_structured_failure(
        {"tool_activity": {"tool_outputs": []}}
    )


def test_unrecovered_rejected_envelope_is_failure() -> None:
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "status": "success",
                    "structured_content": {
                        "status": "rejected",
                        "code": "SUBJECT_TYPE_MISSING",
                    },
                }
            ]
        }
    }

    assert _tool_trace_has_structured_failure(metadata)


def test_successful_final_export_recovers_prior_rejected_call() -> None:
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "name": "create_om2_quantity",
                    "status": "success",
                    "structured_content": {"status": "rejected"},
                },
                {
                    "name": "export_memory",
                    "status": "success",
                    "structured_content": {
                        "status": "ok",
                        "ttl": (
                            "@prefix ex: <https://example.test/> .\n"
                            "ex:subject ex:predicate ex:object .\n"
                        ),
                    },
                },
            ]
        }
    }

    assert not _tool_trace_has_structured_failure(metadata)


def test_resume_recovery_never_requests_memory_reset() -> None:
    prompt = _build_kg_recovery_prompt(
        base_prompt="Build the graph.",
        entity_label="example",
        entity_uri="https://example.test/entity",
        graph_mode="resume_existing",
        prior_attempt_trace={"artifact_found": True},
    )

    assert "idempotent and accepts no reset/replace mode" in prompt
    assert "load_from_turtle_file" not in prompt
    assert "with the document id" not in prompt


def test_runtime_rules_require_export_memory_as_final_tool_call() -> None:
    prompt = _augment_kg_prompt_with_runtime_rules(
        kg_prompt="Build the graph.",
        entity_label="example",
        entity_uri="https://example.test/entity",
        doi_hash="abc123",
        main_entity_policy={},
        hints_content="",
    )

    assert "final MCP tool call must be `export_memory`" in prompt
    assert "Do not call any tool after it" in prompt
    assert "c/min" in prompt
    assert "isolated yield" not in prompt


def test_runtime_rules_require_atomic_ordered_creator() -> None:
    prompt = _augment_kg_prompt_with_runtime_rules(
        kg_prompt="Build the graph.",
        entity_label="example",
        entity_uri="https://example.test/entity",
        doi_hash="abc123",
        main_entity_policy={},
        hints_content='{"sequenceIndex": 1}',
        ontology_contract={
            "ordered_member_profile": {
                "single_valued_ordering_properties": ["sequenceIndex"]
            }
        },
    )

    assert "`sequenceIndex`" in prompt
    assert "hasOrder" not in prompt
    assert "directly in that creator call" in prompt
    assert "writes identity and order atomically" in prompt
    assert "separate order setter" in prompt


def test_tool_trace_preserves_calls_and_structured_results() -> None:
    from langchain_core.messages import AIMessage, ToolMessage

    trace = _summarize_react_tool_activity(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "export_memory",
                        "args": {"format": "turtle"},
                    }
                ],
            ),
            ToolMessage(
                content='{"ok": true, "ttl": "@prefix ex: <https://example.test/> ."}',
                tool_call_id="call-1",
                name="export_memory",
            ),
        ]
    )

    assert trace["planned_tool_calls"][0]["args"] == {"format": "turtle"}
    assert trace["tool_outputs"][0]["structured_content"]["ok"] is True


def test_attempt_trace_json_is_written_atomically(tmp_path) -> None:
    target = tmp_path / "attempt.trace.json"

    _write_json_atomic(str(target), {"attempt": 1, "ok": False})

    assert target.read_text(encoding="utf-8").startswith("{")
    assert not list(tmp_path.glob("*.tmp"))


def test_structured_turtle_result_is_parsed_and_persisted_atomically(
    tmp_path,
) -> None:
    target = tmp_path / "memory" / "example.ttl"
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "name": "export_memory",
                    "status": "success",
                    "structured_content": {
                        "ttl": (
                            "@prefix ex: <https://example.test/> .\n"
                            "ex:subject ex:predicate ex:object .\n"
                        )
                    },
                }
            ]
        }
    }

    persisted, message = _persist_structured_turtle_result(
        metadata, target_path=str(target)
    )

    assert persisted
    assert message == ""
    assert "ex:subject" in target.read_text(encoding="utf-8")
    assert not list(target.parent.glob("*.tmp"))


def test_structured_turtle_result_rejects_invalid_payload(tmp_path) -> None:
    target = tmp_path / "memory" / "example.ttl"
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "status": "success",
                    "structured_content": {"ttl": "this is not turtle"},
                }
            ]
        }
    }

    persisted, message = _persist_structured_turtle_result(
        metadata, target_path=str(target)
    )

    assert not persisted
    assert "could not be parsed" in message
    assert not target.exists()


def test_artifact_fingerprints_distinguish_stale_and_fresh_files(tmp_path) -> None:
    target = tmp_path / "memory.ttl"
    target.write_text("first", encoding="utf-8")
    before = _artifact_fingerprints([str(target)])

    assert _artifact_fingerprints([str(target)]) == before

    target.write_text("second payload", encoding="utf-8")
    after = _artifact_fingerprints([str(target)])

    assert after[str(target)] != before[str(target)]


def test_entity_scope_distinguishes_equal_labels_by_uri() -> None:
    first = entity_scope_name("same label", "https://example.test/entity/1")
    second = entity_scope_name("same label", "https://example.test/entity/2")

    assert first.startswith("same_label--")
    assert second.startswith("same_label--")
    assert first != second


def test_seed_and_checkpoint_preserve_exact_iter1_identity(tmp_path) -> None:
    entity = URIRef("https://example.test/entity/1")
    entity_type = URIRef("https://example.test/TopEntity")
    graph = Graph()
    graph.add((entity, RDF.type, entity_type))
    graph.add((entity, RDFS.label, Literal("Entity One")))
    graph.serialize(destination=tmp_path / "iteration_1.ttl", format="turtle")
    scope = entity_scope_name("Entity One", str(entity))

    canonical = _seed_entity_canonical_memory(
        doi_folder=str(tmp_path),
        entity_scope=scope,
        entity_uri=str(entity),
        entity_label="Entity One",
        entity_types=[str(entity_type)],
        top_class_iri=str(entity_type),
    )
    _write_entity_checkpoint(
        doi_folder=str(tmp_path),
        doi_hash="paper",
        entity_scope=scope,
        entity_label="Entity One",
        entity_uri=str(entity),
        entity_types=[str(entity_type)],
        canonical_ttl=canonical,
        iteration=1,
    )

    seeded = Graph().parse(canonical, format="turtle")
    assert (entity, RDF.type, entity_type) in seeded
    checkpoint = next((tmp_path / "memory").glob("*.checkpoint.json"))
    assert str(entity) in checkpoint.read_text(encoding="utf-8")


def test_global_state_uses_active_data_root(tmp_path) -> None:
    write_global_state(
        "paper",
        "entity",
        "https://example.test/entity",
        data_dir=str(tmp_path),
    )

    assert (tmp_path / "global_state.json").is_file()


def test_identity_gate_rejects_export_without_exact_root(tmp_path) -> None:
    graph = Graph()
    graph.add(
        (
            URIRef("https://example.test/other"),
            RDF.type,
            URIRef("https://example.test/TopEntity"),
        )
    )
    target = tmp_path / "wrong.ttl"
    graph.serialize(destination=target, format="turtle")

    ok, messages = _validate_canonical_entity_identity(
        ttl_path=str(target),
        entity_uri="https://example.test/expected",
        entity_types=["https://example.test/TopEntity"],
        top_class_iri="https://example.test/TopEntity",
    )

    assert not ok
    assert "lacks exact entity URI" in messages[0]
