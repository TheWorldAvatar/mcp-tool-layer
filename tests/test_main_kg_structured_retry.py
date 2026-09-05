import json
import os
from pathlib import Path

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef

from models.BaseAgent import _summarize_react_tool_activity
from src.pipelines.main_kg_building.build import (
    _apply_entity_context_runtime_env,
    _augment_kg_prompt_with_runtime_rules,
    _resolve_kg_attempt_limit,
    _resolve_kg_audit_policy,
    _attempt_trace,
    _artifact_fingerprints,
    _build_kg_recovery_prompt,
    _can_accept_kg_after_audit_exhaustion,
    _semantic_audit_nonblocking,
    _compact_attempt_trace_for_recovery,
    _continuity_repair_prompt,
    _find_hints_file,
    _load_entity_ref_registry,
    _materialize_om2_quantities_from_hints,
    _merge_attempt_termination_trace,
    _persist_partial_kg_attempt,
    _persisted_abox_entity_inventory,
    _persist_entity_ref_registry,
    _persist_structured_turtle_result,
    _publish_central_memory_after_semantic_commit,
    _purge_entity_canonical_persistence,
    _next_post_publish_feedback_attempt,
    _post_publish_repair_prompt,
    _select_post_publish_repair_context,
    _select_continuity_repair_context,
    _snapshot_entity_retry_state,
    _successful_mutation_tools,
    _tool_trace_has_structured_failure,
    _unresolved_structured_obligations,
    _validate_canonical_entity_identity,
    _validate_hint_fidelity,
    _validate_hint_relation_contract,
    _write_post_publish_feedback,
    _write_json_atomic,
    _seed_entity_canonical_memory,
    _select_richest_entity_checkpoint,
    _write_entity_checkpoint,
    _restore_entity_retry_state,
    _restore_entity_iteration_checkpoint,
    write_global_state,
)
from src.pipelines.utils.top_entity_identity import (
    entity_artifact_name,
    entity_scope_name,
)


def test_failed_kg_attempt_state_can_be_rolled_back(tmp_path: Path) -> None:
    doi_folder = tmp_path / "runtime" / "doi"
    memory = doi_folder / "memory"
    memory.mkdir(parents=True)
    central = doi_folder.parent / "central_memory"
    central.mkdir()
    exports = doi_folder / "exports"
    exports.mkdir()
    canonical = memory / "route--scope.ttl"
    canonical.write_text("prior graph", encoding="utf-8")
    checkpoint = memory / "route--scope.checkpoint.json"
    checkpoint.write_text('{"prior": true}', encoding="utf-8")
    central_ttl = central / "example_reusable_entities.ttl"
    central_ttl.write_text("prior central graph", encoding="utf-8")
    prior_export = exports / "route--scope_20260101_000000.ttl"
    prior_export.write_text("prior export", encoding="utf-8")

    snapshot = _snapshot_entity_retry_state(
        doi_folder=str(doi_folder),
        entity_safe="route--scope",
        entity_label="route name",
        ontology_name="example",
    )
    canonical.write_text("polluted graph", encoding="utf-8")
    checkpoint.write_text('{"polluted": true}', encoding="utf-8")
    central_ttl.write_text("polluted central graph", encoding="utf-8")
    alias = memory / "route_name.ttl"
    alias.write_text("wrong-scope graph", encoding="utf-8")
    new_export = exports / "route_name_20260101_000001.ttl"
    new_export.write_text("polluted export", encoding="utf-8")

    _restore_entity_retry_state(snapshot)

    assert canonical.read_text(encoding="utf-8") == "prior graph"
    assert checkpoint.read_text(encoding="utf-8") == '{"prior": true}'
    assert central_ttl.read_text(encoding="utf-8") == "prior central graph"
    assert prior_export.read_text(encoding="utf-8") == "prior export"
    assert not new_export.exists()
    assert not alias.exists()


def test_failed_attempt_preserves_quarantined_partial_graph(
    tmp_path: Path,
) -> None:
    doi_folder = tmp_path / "runtime" / "doi"
    source = doi_folder / "exports" / "entity_20260902.ttl"
    source.parent.mkdir(parents=True)
    source.write_text(
        "@prefix ex: <https://example.com/> . ex:root ex:hasStep ex:step .",
        encoding="utf-8",
    )
    trace = {
        "structured_tool_failure": True,
        "unresolved_obligations": [
            {
                "identity": "create_Add:1",
                "code": "TOOL_ARGUMENT_VALIDATION",
                "retryable": True,
            }
        ],
    }

    outcome = _persist_partial_kg_attempt(
        doi_folder=str(doi_folder),
        entity_safe="entity--scope",
        entity_label="Entity",
        entity_uri="https://example.com/root",
        iter_num=2,
        attempt=1,
        candidate_paths=[str(source)],
        trace=trace,
    )

    assert outcome["status"] == "partial_recoverable"
    assert outcome["canonical_publish_allowed"] is False
    assert outcome["automatic_retry_eligible"] is True
    assert outcome["triple_count"] == 1
    assert Path(outcome["partial_ttl"]).is_file()
    manifest = json.loads(Path(outcome["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["unresolved_obligations"][0]["retryable"] is True


def test_long_entity_hints_use_shared_extraction_artifact_name(tmp_path: Path) -> None:
    label = (
        "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(NDBDC)3}"
        "-6CH3OH-3DMF (TMA-VMOC-P-2)"
    )
    artifact_name = entity_artifact_name(label)
    expected = tmp_path / f"iter2_hints_{artifact_name}.txt"
    expected.write_text("SEMANTIC_HINTS_V1", encoding="utf-8")

    located = _find_hints_file(
        mcp_run_dir=str(tmp_path),
        iter_num=2,
        entity_safe=artifact_name,
    )

    assert Path(located) == expected
    assert artifact_name.endswith("--b08803d5a18f")


def test_entity_runtime_env_does_not_bind_semantic_ordered_heading_manifest() -> None:
    os.environ.pop("TWA_MCP_EXPECTED_ORDERED_MEMBERS_JSON", None)
    _apply_entity_context_runtime_env(
        main_entity_policy={},
        entity_safe="scope",
        entity_uri="urn:root",
    )

    assert "TWA_MCP_EXPECTED_ORDERED_MEMBERS_JSON" not in os.environ
    assert os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI"] == "urn:root"


def test_kg_attempt_limit_defaults_to_one_and_clamps() -> None:
    assert _resolve_kg_attempt_limit({}) == 1
    assert _resolve_kg_attempt_limit(None) == 1
    assert _resolve_kg_attempt_limit({"max_attempts": 3}) == 3
    assert _resolve_kg_attempt_limit({"max_attempts": 0}) == 1
    assert _resolve_kg_attempt_limit(
        {"presence_coverage_audit": {"max_attempts": 2}}
    ) == 2


def test_kg_audit_defaults_to_presence_and_keeps_framework_integrity_opt_in() -> None:
    presence, legacy = _resolve_kg_audit_policy({})
    assert legacy is False
    assert presence["enabled"] is True
    assert presence["replace_llm_audits"] is True
    assert presence["model"] == "gpt-4o"

    _, legacy_on = _resolve_kg_audit_policy(
        {"legacy_llm_framework_integrity": True}
    )
    assert legacy_on is True


def test_semantic_audit_exhaustion_accepts_only_persisted_valid_candidate() -> None:
    common = {
        "policy": {
            "semantic_audit": {
                "nonblocking_after_semantic_exhaustion": True,
            }
        },
        "final_attempt": True,
        "current_attempt_artifacts": ["candidate.ttl"],
        "structured_tool_failure": False,
        "blocker_declared": False,
        "semantic_complete": False,
        "framework_integrity_report": {"accepted": False},
        "framework_ok": False,
        "semantic_graph_report": None,
        "semantic_graph_ok": False,
    }

    assert _can_accept_kg_after_audit_exhaustion(**common)
    assert not _can_accept_kg_after_audit_exhaustion(
        **{**common, "structured_tool_failure": True}
    )
    assert not _can_accept_kg_after_audit_exhaustion(
        **{**common, "current_attempt_artifacts": []}
    )
    assert not _can_accept_kg_after_audit_exhaustion(
        **{**common, "final_attempt": False}
    )


def test_all_semantic_audits_share_nonblocking_exhaustion_policy() -> None:
    assert _semantic_audit_nonblocking(
        {
            "semantic_audit": {
                "nonblocking_after_semantic_exhaustion": True,
            }
        }
    )
    assert not _semantic_audit_nonblocking({})


def test_continuity_repair_routes_to_regression_owner_iteration() -> None:
    report = {
        "confirmed_regressions": [
            {
                "iteration": 2,
                "source_item_id": "Equipment: mixer",
                "aspect_id": "entity_presence",
            }
        ]
    }
    contexts = [
        {"iteration": 3, "hints_content": "later"},
        {"iteration": 2, "hints_content": "owner"},
    ]

    selected = _select_continuity_repair_context(
        continuity_report=report,
        contexts=contexts,
    )
    prompt = _continuity_repair_prompt(report)

    assert selected == contexts[1]
    assert "Equipment: mixer" in prompt
    assert "orphan is not sufficient" in prompt
    assert "export_memory" in prompt


def test_pipeline_publishes_central_memory_only_from_committed_ttl(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_root = tmp_path / "artifacts"
    contract_dir = artifact_root / "scripts" / "example"
    contract_dir.mkdir(parents=True)
    reusable_class = URIRef("https://example.com/Reusable")
    (contract_dir / "_relationship_contract.json").write_text(
        json.dumps(
            {
                "reuse_policy": {
                    "classes": [
                        {
                            "class_iri": str(reusable_class),
                            "reusable": True,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    committed = tmp_path / "committed.ttl"
    graph = Graph()
    entity = URIRef("https://example.com/entity")
    graph.add((entity, RDF.type, reusable_class))
    graph.add((entity, RDFS.label, Literal("committed reusable entity")))
    graph.serialize(destination=committed, format="turtle")
    monkeypatch.setenv("TWA_GENERATED_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("TWA_CENTRAL_MEMORY_DIR", str(tmp_path / "central_memory"))

    result = _publish_central_memory_after_semantic_commit(
        ttl_path=str(committed),
        ontology_name="example",
        doi_hash="case",
        entity_scope="scope",
    )

    assert result["status"] == "ok"
    central = Graph()
    central.parse(result["central_graph_path"], format="turtle")
    assert (entity, RDF.type, reusable_class) in central
    provenance = json.loads(
        Path(result["central_provenance_path"]).read_text(encoding="utf-8")
    )
    assert provenance[str(entity)] == [
        {"doi": "case", "top_level_entity_name": "scope"}
    ]


def test_semantic_commit_routes_document_scope_out_of_global_memory(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_root = tmp_path / "artifacts"
    contract_dir = artifact_root / "scripts" / "example"
    contract_dir.mkdir(parents=True)
    global_class = URIRef("https://example.com/Global")
    document_class = URIRef("https://example.com/DocumentScoped")
    scoped_class = URIRef("https://example.com/TopEntityScoped")
    (contract_dir / "_relationship_contract.json").write_text(
        json.dumps(
            {
                "reuse_policy": {
                    "classes": [
                        {
                            "class_iri": str(global_class),
                            "reusable": True,
                            "reuse_scope": "global",
                        },
                        {
                            "class_iri": str(document_class),
                            "reusable": True,
                            "reuse_scope": "document",
                        },
                        {
                            "class_iri": str(scoped_class),
                            "reusable": True,
                            "reuse_scope": "top_entity",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    committed = tmp_path / "committed-routed.ttl"
    graph = Graph()
    global_node = URIRef("https://example.com/global-node")
    document_node = URIRef("https://example.com/document-node")
    dual_scope_node = URIRef("https://example.com/dual-scope-node")
    graph.add((global_node, RDF.type, global_class))
    graph.add((document_node, RDF.type, document_class))
    graph.add((dual_scope_node, RDF.type, document_class))
    graph.add((dual_scope_node, RDF.type, scoped_class))
    graph.serialize(destination=committed, format="turtle")
    monkeypatch.setenv("TWA_GENERATED_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("TWA_CENTRAL_MEMORY_DIR", str(tmp_path / "central"))

    result = _publish_central_memory_after_semantic_commit(
        ttl_path=str(committed),
        ontology_name="example",
        doi_hash="case",
        entity_scope="scope",
    )

    central = Graph().parse(
        result["central"]["central_graph_path"], format="turtle"
    )
    document = Graph().parse(
        result["document"]["document_graph_path"], format="turtle"
    )
    assert (global_node, RDF.type, global_class) in central
    assert (document_node, RDF.type, document_class) not in central
    assert (document_node, RDF.type, document_class) in document
    assert (global_node, RDF.type, global_class) not in document
    assert (dual_scope_node, RDF.type, document_class) not in document


def test_clean_entity_rebuild_purges_ref_and_checkpoint_sidecars(
    tmp_path: Path,
) -> None:
    doi_folder = tmp_path / "runtime" / "doi"
    memory = doi_folder / "memory"
    memory.mkdir(parents=True)
    scope = "Synthesis_of_MOP-MIA--scope"
    for suffix in (".ttl", ".refs.json", ".checkpoint.json", ".identity.json"):
        (memory / f"{scope}{suffix}").write_text("stale", encoding="utf-8")

    deleted = _purge_entity_canonical_persistence(
        doi_folder=str(doi_folder),
        entity_label="Synthesis of MOP-MIA",
        entity_safe=scope,
    )

    assert deleted == 3
    assert [path.name for path in memory.iterdir()] == [f"{scope}.identity.json"]


def test_hint_relation_contract_rejects_wrong_prior_ref_range() -> None:
    hints = json.dumps(
        {
            "entities": [
                {
                    "ref": "separate-1",
                    "class": "Separate",
                    "label": "separated within 24 hrs",
                    "datatype_properties": {"hasStepDuration": "24 hrs"},
                }
            ],
            "relations": [
                {
                    "subject_ref": "separate-1",
                    "property": "hasStepDuration",
                    "object_ref": "cheminput-6",
                }
            ],
        }
    )
    contract = {
        "object_properties": [
            {
                "property_iri": "https://example.test/hasStepDuration",
                "domain_iris": ["https://example.test/Separate"],
                "range_iris": ["https://example.test/Duration"],
            }
        ]
    }
    registry = {
        "refs": {
            "cheminput-6": {
                "iri": "https://example.test/input/6",
                "class": "ChemicalInput",
            }
        }
    }

    violations = _validate_hint_relation_contract(
        hints_content=hints,
        ontology_contract=contract,
        prior_ref_registry=registry,
        iteration=3,
    )

    assert violations == [
        {
            "schema_version": "kg-hint-contract-violation.v1",
            "code": "HINT_RELATION_RANGE_MISMATCH",
            "iteration": 3,
            "relation_index": 0,
            "property": "hasStepDuration",
            "subject_ref": "separate-1",
            "object_ref": "cheminput-6",
            "endpoint_role": "object",
            "actual_class": "ChemicalInput",
            "expected_classes": ["Duration"],
            "repair_action": (
                "Revise the extraction hint. Do not force KG building to "
                "materialize a relation whose endpoint class violates the "
                "immutable T-Box contract."
            ),
        }
    ]


def test_hint_relation_contract_accepts_subclass_endpoint() -> None:
    hints = json.dumps(
        {
            "entities": [{"ref": "add-1", "class": "Add"}],
            "relations": [
                {
                    "subject_ref": "route-1",
                    "property": "hasSynthesisStep",
                    "object_ref": "add-1",
                }
            ],
        }
    )
    contract = {
        "object_properties": [
            {
                "property_iri": "https://example.test/hasSynthesisStep",
                "range_iris": ["https://example.test/SynthesisStep"],
            }
        ],
        "subclass_closure": [
            {
                "class_iri": "https://example.test/Add",
                "superclass_iris": [
                    "https://example.test/Add",
                    "https://example.test/SynthesisStep",
                ],
            }
        ],
    }

    assert not _validate_hint_relation_contract(
        hints_content=hints,
        ontology_contract=contract,
    )


def test_main_kg_identity_validation_and_checkpoint_support_long_windows_paths(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        return
    nested = tmp_path.joinpath(*(["long_runtime_segment"] * 8))
    native_nested = "\\\\?\\" + str(nested.resolve())
    os.makedirs(native_nested, exist_ok=True)
    entity = URIRef("https://example.test/synthesis/long")
    entity_type = URIRef("https://example.test/ChemicalSynthesis")
    graph = Graph()
    graph.add((entity, RDF.type, entity_type))
    graph.add((entity, RDFS.label, Literal("long route")))
    ttl_path = nested / ("iteration_2_" + ("route_" * 20) + ".ttl")
    with open("\\\\?\\" + str(ttl_path.resolve()), "w", encoding="utf-8") as handle:
        handle.write(str(graph.serialize(format="turtle")))

    ok, messages = _validate_canonical_entity_identity(
        ttl_path=str(ttl_path),
        entity_uri=str(entity),
        entity_types=[str(entity_type)],
        top_class_iri=str(entity_type),
    )
    assert ok, messages

    scope = "route_" * 20
    _write_entity_checkpoint(
        doi_folder=str(nested),
        doi_hash="case",
        entity_scope=scope,
        entity_label="long route",
        entity_uri=str(entity),
        entity_types=[str(entity_type)],
        canonical_ttl=str(ttl_path),
        iteration=2,
    )
    checkpoint = nested / "memory" / f"{scope}.checkpoint.json"
    assert os.path.isfile("\\\\?\\" + str(checkpoint.resolve()))


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


def test_ref_registry_enriches_persisted_inventory_and_rejects_rebinding(
    tmp_path: Path,
) -> None:
    ttl_path = tmp_path / "memory.ttl"
    top = URIRef("https://example.test/synthesis")
    input_iri = URIRef("https://example.test/input")
    has_input = URIRef("https://example.test/hasChemicalInput")
    has_amount = URIRef("https://example.test/hasAmount")
    graph = Graph()
    graph.add((top, RDF.type, URIRef("https://example.test/ChemicalSynthesis")))
    graph.add((input_iri, RDF.type, URIRef("https://example.test/ChemicalInput")))
    graph.add((input_iri, RDFS.label, Literal("copper(II) nitrate")))
    graph.add((input_iri, has_amount, Literal("0.1 mmol (24.1 mg)")))
    graph.add((top, has_input, input_iri))
    graph.serialize(destination=ttl_path, format="turtle")

    registry_path = _persist_entity_ref_registry(
        doi_folder=str(tmp_path),
        entity_scope="MOP-EIA",
        iteration=2,
        resolved_refs={
            "input-1": {
                "iri": str(input_iri),
                "class": "ChemicalInput",
                "label": "copper(II) nitrate",
                "datatype_properties": {
                    "hasAmount": "0.1 mmol (24.1 mg)"
                },
            }
        },
    )
    assert Path(registry_path).is_file()
    registry = _load_entity_ref_registry(str(tmp_path), "MOP-EIA")
    inventory = _persisted_abox_entity_inventory(
        [str(ttl_path)],
        ref_registry=registry,
    )
    input_entry = next(item for item in inventory if item["iri"] == str(input_iri))
    assert input_entry["refs"] == ["input-1"]
    assert input_entry["datatype_values"][str(has_amount)] == [
        "0.1 mmol (24.1 mg)"
    ]
    assert input_entry["incoming_relations"] == [
        {"subject_iri": str(top), "property_iri": str(has_input)}
    ]

    try:
        _persist_entity_ref_registry(
            doi_folder=str(tmp_path),
            entity_scope="MOP-EIA",
            iteration=3,
            resolved_refs={
                "input-1": {
                    "iri": "https://example.test/different-input",
                    "class": "ChemicalInput",
                    "label": "copper(II) nitrate",
                    "datatype_properties": {},
                }
            },
        )
    except ValueError as exc:
        assert "already bound" in str(exc)
    else:
        raise AssertionError("Conflicting ref rebinding must fail closed")


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


def test_successful_final_export_does_not_recover_unrelated_rejected_call() -> None:
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "name": "create_om2_quantity",
                    "status": "success",
                    "structured_content": {
                        "status": "rejected",
                        "semantic_fingerprint": "quantity-obligation",
                    },
                },
                {
                    "name": "export_memory",
                    "status": "success",
                    "structured_content": {
                        "status": "ok",
                        "semantic_fingerprint": "export-obligation",
                        "ttl": (
                            "@prefix ex: <https://example.test/> .\n"
                            "ex:subject ex:predicate ex:object .\n"
                        ),
                    },
                },
            ]
        }
    }

    assert _tool_trace_has_structured_failure(metadata)
    trace = _attempt_trace(metadata, artifact_found=True)
    assert trace["structured_tool_failure"] is True
    assert trace["unresolved_obligations"][0]["identity"] == "quantity-obligation"


def test_same_obligation_success_recovers_structured_rejection() -> None:
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "name": "add_relation",
                    "status": "success",
                    "structured_content": {
                        "status": "rejected",
                        "obligation_id": "relation-7",
                    },
                },
                {
                    "name": "add_relation",
                    "status": "success",
                    "structured_content": {
                        "status": "ok",
                        "obligation_id": "relation-7",
                    },
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
    assert _attempt_trace(metadata, artifact_found=True)[
        "unresolved_obligations"
    ] == []


def test_legacy_same_name_and_canonical_args_recovers_rejection() -> None:
    args = {"subject": "urn:s", "object": "urn:o"}
    metadata = {
        "tool_activity": {
            "planned_tool_calls": [
                {"id": "failed", "name": "add_relation", "args": args},
                {"id": "repair", "name": "add_relation", "args": args},
            ],
            "tool_outputs": [
                {
                    "tool_call_id": "failed",
                    "name": "add_relation",
                    "status": "success",
                    "structured_content": {"status": "rejected"},
                },
                {
                    "tool_call_id": "repair",
                    "name": "add_relation",
                    "status": "success",
                    "structured_content": {"status": "ok"},
                },
            ],
        }
    }

    assert not _tool_trace_has_structured_failure(metadata)


def test_same_obligation_policy_valid_skip_recovers_rejection() -> None:
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "name": "add_relation",
                    "structured_content": {
                        "status": "rejected",
                        "fingerprint": "edge-3",
                    },
                },
                {
                    "name": "add_relation",
                    "structured_content": {
                        "status": "skipped",
                        "policy_valid": True,
                        "fingerprint": "edge-3",
                    },
                },
            ]
        }
    }

    assert not _tool_trace_has_structured_failure(metadata)


def test_termination_trace_merges_without_overwriting_existing_trace(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "attempt.trace.json"
    _write_json_atomic(
        str(trace_path),
        {
            "attempt": 1,
            "planned_tool_calls": [{"name": "existing"}],
            "semantic_commit": {"complete": False},
        },
    )

    class Interrupted(RuntimeError):
        pass

    error = Interrupted("recursion limit reached")
    error.metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "name": "add_relation",
                    "structured_content": {
                        "status": "rejected",
                        "obligation_id": "edge-9",
                    },
                }
            ]
        }
    }
    _merge_attempt_termination_trace(
        str(trace_path),
        base={"attempt": 2, "graph_mode": "open_or_resume"},
        exc=error,
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["attempt"] == 1
    assert trace["planned_tool_calls"] == [{"name": "existing"}]
    assert trace["termination_reason"] == "recursion_limit"
    assert trace["unresolved_obligations"][0]["identity"] == "edge-9"
    assert trace["semantic_commit"]["complete"] is False


def test_hint_fidelity_rejects_shell_and_accepts_complete_graph(tmp_path) -> None:
    top = URIRef("https://example.test/top")
    chemical = URIRef("https://example.test/input")
    top_type = URIRef("https://example.test/Top")
    chemical_type = URIRef("https://example.test/ChemicalInput")
    amount = URIRef("https://example.test/hasAmount")
    has_input = URIRef("https://example.test/hasChemicalInput")
    graph = Graph()
    graph.add((top, RDF.type, top_type))
    graph.add((top, RDFS.label, Literal("Top")))
    ttl_path = tmp_path / "scope.ttl"
    graph.serialize(destination=ttl_path, format="turtle")
    hints = json.dumps(
        {
            "entities": [
                {
                    "ref": "top",
                    "class": "Top",
                    "label": "Top",
                    "datatype_properties": {},
                },
                {
                    "ref": "input-1",
                    "class": "ChemicalInput",
                    "label": "linker",
                    "datatype_properties": {"hasAmount": "0.1 mmol"},
                },
            ],
            "relations": [
                {
                    "subject_ref": "top",
                    "property": "hasChemicalInput",
                    "object_ref": "input-1",
                }
            ],
        }
    )
    contract = {
        "classes": [
            {"class_iri": str(top_type)},
            {"class_iri": str(chemical_type)},
        ],
        "datatype_properties": [{"property_iri": str(amount)}],
        "object_properties": [{"property_iri": str(has_input)}],
    }

    ok, messages, expectations = _validate_hint_fidelity(
        ttl_path=str(ttl_path),
        hints_content=hints,
        ontology_contract=contract,
        owned_classes={"ChemicalInput"},
        owned_object_properties={"hasChemicalInput"},
    )
    assert not ok
    assert expectations == 3
    assert any("input-1" in message for message in messages)

    graph.add((chemical, RDF.type, chemical_type))
    graph.add((chemical, RDFS.label, Literal("linker")))
    graph.add((chemical, amount, Literal("0.1 mmol")))
    graph.add((top, has_input, chemical))
    graph.serialize(destination=ttl_path, format="turtle")
    ok, messages, expectations = _validate_hint_fidelity(
        ttl_path=str(ttl_path),
        hints_content=hints,
        ontology_contract=contract,
        owned_classes={"ChemicalInput"},
        owned_object_properties={"hasChemicalInput"},
    )
    assert ok, messages
    assert expectations == 3


def test_hint_fidelity_compares_boolean_literals_by_datatype(
    tmp_path: Path,
) -> None:
    entity = URIRef("https://example.test/step")
    entity_type = URIRef("https://example.test/Step")
    sealed = URIRef("https://example.test/isSealed")
    graph = Graph()
    graph.add((entity, RDF.type, entity_type))
    graph.add((entity, RDFS.label, Literal("sealed step")))
    graph.add((entity, sealed, Literal(True)))
    ttl_path = tmp_path / "boolean.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    ok, messages, expectations = _validate_hint_fidelity(
        ttl_path=str(ttl_path),
        hints_content=json.dumps(
            {
                "entities": [
                    {
                        "ref": "step",
                        "class": "Step",
                        "label": "sealed step",
                        "datatype_properties": {"isSealed": True},
                    }
                ],
                "relations": [],
            }
        ),
        ontology_contract={
            "classes": [{"class_iri": str(entity_type)}],
            "datatype_properties": [{"property_iri": str(sealed)}],
            "object_properties": [],
        },
        owned_classes={"Step"},
        owned_object_properties=set(),
    )

    assert ok, messages
    assert expectations == 1


def test_hint_fidelity_resolves_relation_only_prior_ref_from_registry(
    tmp_path: Path,
) -> None:
    top = URIRef("https://example.test/synthesis/mop-eia")
    input_iri = URIRef("https://example.test/input/copper")
    add_iri = URIRef("https://example.test/step/add-1")
    top_type = URIRef("https://example.test/ChemicalSynthesis")
    input_type = URIRef("https://example.test/ChemicalInput")
    add_type = URIRef("https://example.test/Add")
    has_added_input = URIRef("https://example.test/hasAddedChemicalInput")
    graph = Graph()
    graph.add((top, RDF.type, top_type))
    graph.add((input_iri, RDF.type, input_type))
    graph.add((input_iri, RDFS.label, Literal("copper(II) nitrate")))
    graph.add((add_iri, RDF.type, add_type))
    graph.add((add_iri, RDFS.label, Literal("Add copper(II) nitrate")))
    graph.add((add_iri, has_added_input, input_iri))
    ttl_path = tmp_path / "iter3.ttl"
    graph.serialize(destination=ttl_path, format="turtle")
    hints = json.dumps(
        {
            "entities": [
                {
                    "ref": "add-1",
                    "class": "Add",
                    "label": "Add copper(II) nitrate",
                    "datatype_properties": {},
                }
            ],
            "relations": [
                {
                    "subject_ref": "add-1",
                    "property": "hasAddedChemicalInput",
                    "object_ref": "input-1",
                }
            ],
        }
    )
    contract = {
        "classes": [
            {"class_iri": str(top_type)},
            {"class_iri": str(input_type)},
            {"class_iri": str(add_type)},
        ],
        "datatype_properties": [],
        "object_properties": [{"property_iri": str(has_added_input)}],
    }
    common = {
        "ttl_path": str(ttl_path),
        "hints_content": hints,
        "ontology_contract": contract,
        "owned_classes": {"Add"},
        "owned_object_properties": {"hasAddedChemicalInput"},
    }

    ok, messages, expectations = _validate_hint_fidelity(**common)
    assert not ok
    assert expectations == 2
    assert any("input-1" in message for message in messages)

    resolved_refs: dict[str, dict] = {}
    ok, messages, expectations = _validate_hint_fidelity(
        **common,
        prior_ref_registry={
            "refs": {
                "input-1": {
                    "iri": str(input_iri),
                    "class": "ChemicalInput",
                    "label": "copper(II) nitrate",
                    "datatype_properties": {
                        "hasAmount": "0.1 mmol (24.1 mg)"
                    },
                }
            }
        },
        resolved_refs_out=resolved_refs,
    )
    assert ok, messages
    assert expectations == 2
    assert resolved_refs["add-1"]["iri"] == str(add_iri)


def test_mop_eia_iter2_fidelity_ignores_later_iteration_mop_link(
    tmp_path: Path,
) -> None:
    ontosyn = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    top = URIRef("https://example.test/synthesis/mop-eia")
    document = URIRef("https://example.test/document/mop-eia")
    output = URIRef("https://example.test/output/mop-eia")
    input_specs = [
        ("input-1", "copper(II) nitrate", "0.1 mmol (24.1 mg)"),
        ("input-2", "5-ethoxy isophthalic acid", "0.2 mmol"),
        ("input-3", "DMF", "4 mL"),
        ("input-4", "H2O", "0.5 mL"),
    ]
    input_nodes = {
        ref: URIRef(f"https://example.test/input/{ref}") for ref, _, _ in input_specs
    }
    graph = Graph()
    graph.add((top, RDF.type, ontosyn.ChemicalSynthesis))
    graph.add((document, RDF.type, ontosyn.Document))
    graph.add((document, RDFS.label, Literal("MOP-EIA Document")))
    graph.add((output, RDF.type, ontosyn.ChemicalOutput))
    graph.add((output, RDFS.label, Literal("MOP-EIA")))
    graph.add((top, ontosyn.hasChemicalOutput, output))
    graph.add((top, ontosyn.retrievedFrom, document))
    for ref, label, amount_value in input_specs:
        node = input_nodes[ref]
        graph.add((node, RDF.type, ontosyn.ChemicalInput))
        graph.add((node, RDFS.label, Literal(label)))
        if ref != "input-1":
            graph.add((node, ontosyn.hasAmount, Literal(amount_value)))
        graph.add((top, ontosyn.hasChemicalInput, node))

    ttl_path = tmp_path / "mop-eia-iter2.ttl"
    graph.serialize(destination=ttl_path, format="turtle")
    hints = {
        "entities": [
            *[
                {
                    "ref": ref,
                    "class": "ChemicalInput",
                    "label": label,
                    "datatype_properties": {"hasAmount": amount_value},
                }
                for ref, label, amount_value in input_specs
            ],
            {
                "ref": "output-1",
                "class": "ChemicalOutput",
                "label": "MOP-EIA",
                "datatype_properties": {},
            },
            {
                "ref": "mop-1",
                "class": "MetalOrganicPolyhedron",
                "label": "MOP-EIA",
                "datatype_properties": {"hasCCDCNumber": "1497169"},
            },
            {
                "ref": "doc-1",
                "class": "Document",
                "label": "MOP-EIA Document",
                "datatype_properties": {},
            },
        ],
        "relations": [
            *[
                {
                    "subject_ref": str(top),
                    "property": "hasChemicalInput",
                    "object_ref": ref,
                }
                for ref, _, _ in input_specs
            ],
            {
                "subject_ref": str(top),
                "property": "hasChemicalOutput",
                "object_ref": "output-1",
            },
            {
                "subject_ref": str(top),
                "property": "retrievedFrom",
                "object_ref": "doc-1",
            },
            {
                "subject_ref": "output-1",
                "property": "isRepresentedBy",
                "object_ref": "mop-1",
            },
        ],
    }
    contract = {
        "classes": [
            {"class_iri": str(ontosyn.ChemicalSynthesis)},
            {"class_iri": str(ontosyn.ChemicalInput)},
            {"class_iri": str(ontosyn.ChemicalOutput)},
            {"class_iri": str(ontosyn.Document)},
            {"class_iri": str(ontosyn.MetalOrganicPolyhedron)},
        ],
        "datatype_properties": [
            {"property_iri": str(ontosyn.hasAmount)},
            {"property_iri": str(ontosyn.hasCCDCNumber)},
        ],
        "object_properties": [
            {"property_iri": str(ontosyn.hasChemicalInput)},
            {"property_iri": str(ontosyn.hasChemicalOutput)},
            {"property_iri": str(ontosyn.retrievedFrom)},
            {"property_iri": str(ontosyn.isRepresentedBy)},
        ],
    }
    kwargs = {
        "ttl_path": str(ttl_path),
        "hints_content": json.dumps(hints),
        "ontology_contract": contract,
        "owned_classes": {
            "ChemicalInput",
            "ChemicalOutput",
            "DocumentContext",
            "Supplier",
        },
        "owned_object_properties": {
            "hasChemicalInput",
            "hasChemicalOutput",
            "hasDocumentContext",
            "isSuppliedBy",
            "referencesMaterial",
            "retrievedFrom",
        },
    }

    ok, messages, expectations = _validate_hint_fidelity(**kwargs)
    assert not ok
    assert expectations == 12
    assert any("input-1" in message for message in messages)
    assert not any("mop-1" in message for message in messages)

    graph.add(
        (
            input_nodes["input-1"],
            ontosyn.hasAmount,
            Literal("0.1 mmol (24.1 mg)"),
        )
    )
    graph.serialize(destination=ttl_path, format="turtle")
    ok, messages, expectations = _validate_hint_fidelity(**kwargs)
    assert ok, messages
    assert expectations == 12


def test_successful_mutation_tools_excludes_fallback_export() -> None:
    metadata = {
        "tool_activity": {
            "tool_outputs": [
                {
                    "name": "create_ChemicalInput",
                    "status": "success",
                    "structured_content": {"status": "ok"},
                },
                {
                    "name": "add_hasChemicalInput",
                    "status": "success",
                    "structured_content": {"status": "rejected"},
                },
                {
                    "name": "export_memory",
                    "status": "success",
                    "structured_content": {"status": "ok"},
                },
            ]
        }
    }

    assert _successful_mutation_tools(metadata) == ["create_ChemicalInput"]


def test_resume_recovery_never_requests_memory_reset() -> None:
    prompt = _build_kg_recovery_prompt(
        base_prompt="Build the graph.",
        entity_label="example",
        entity_uri="https://example.test/entity",
        prior_attempt_trace={"artifact_found": True},
    )

    assert "idempotent and accepts no reset/replace mode" in prompt
    assert "Read `semantic_feedback`" in prompt
    assert "failed attempt was rolled back" in prompt
    assert "load_from_turtle_file" not in prompt
    assert "with the document id" not in prompt


def test_framework_recovery_rebuilds_from_rolled_back_baseline() -> None:
    prompt = _build_kg_recovery_prompt(
        base_prompt="Build the graph.",
        entity_label="example",
        entity_uri="https://example.test/entity",
        prior_attempt_trace={
            "artifact_found": True,
            "semantic_commit": {
                "complete": False,
                "hint_fidelity_ok": False,
                "validation_policy": "llm_framework_integrity_audit",
                "hint_fidelity_messages": [],
            },
        },
    )

    assert "failed attempt was rolled back" in prompt
    assert "restored iteration baseline" in prompt
    assert "Re-apply every source-grounded semantic fact" in prompt
    assert "structurally rejected graph remains persisted" not in prompt
    assert "repairs in place" not in prompt


def test_recovery_trace_omits_large_success_payloads_and_keeps_failures() -> None:
    compact = _compact_attempt_trace_for_recovery(
        {
            "artifact_found": False,
            "structured_tool_failure": True,
            "planned_tool_calls": [
                {
                    "name": "check_existing_Supplier",
                    "args": {"label": "N/A"},
                }
            ],
            "tool_outputs": [
                {
                    "name": "export_memory",
                    "status": "success",
                    "structured_content": {
                        "status": "ok",
                        "ttl": "x" * 100_000,
                    },
                },
                {
                    "name": "add_hasChemicalInput",
                    "status": "success",
                    "structured_content": {
                        "status": "rejected",
                        "code": "CENTRAL_REUSE_NOT_AUTHORIZED",
                    },
                },
            ],
        }
    )

    rendered = json.dumps(compact)
    assert len(rendered) < 2000
    assert "CENTRAL_REUSE_NOT_AUTHORIZED" in rendered
    assert "label" in rendered
    assert "x" * 100 not in rendered


def test_recovery_trace_keeps_complete_concise_llm_framework_feedback() -> None:
    compact = _compact_attempt_trace_for_recovery(
        {
            "artifact_found": True,
            "structured_tool_failure": False,
            "semantic_commit": {
                "complete": False,
                "hint_fidelity_ok": False,
                "validation_policy": "llm_framework_integrity_audit",
                "hint_fidelity_messages": [
                    json.dumps(
                        {
                            "check_id": "semantic_abox.missing",
                            "subject_key": "member-1",
                            "evidence": {
                                "description": "The member is typed but detached.",
                                "abox_evidence": "<urn:member> a <urn:Member> .",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "check_id": "semantic_abox.critical",
                            "subject_key": "root-1",
                            "evidence": {
                                "reason": "The required ownership relation is absent."
                            },
                        }
                    ),
                ],
            },
        }
    )

    feedback = compact["semantic_feedback"]
    assert feedback["accepted"] is False
    assert feedback["validation_policy"] == "llm_framework_integrity_audit"
    assert [item["subject_key"] for item in feedback["findings"]] == [
        "member-1",
        "root-1",
    ]
    rendered = json.dumps(feedback)
    assert "typed but detached" in rendered
    assert "ownership relation is absent" in rendered
    assert "deductions" not in rendered


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
    assert "only when that candidate includes a `reuse_authorization_token`" in prompt
    assert "never invoke a creator again for that same ref" in prompt
    assert "c/min" in prompt
    assert "Every OM-2 quantity is an occurrence-local relationship target" in prompt
    assert "even when class, label, numerical value, and unit are identical" in prompt
    assert "`OBJECT_OCCURRENCE_REUSE_FORBIDDEN`" in prompt
    assert "never retry the same object IRI" in prompt
    assert "isolated yield" not in prompt


def test_semantic_hints_defer_domain_rules_to_active_contract() -> None:
    prompt = _augment_kg_prompt_with_runtime_rules(
        kg_prompt="Build the graph.",
        entity_label="example",
        entity_uri="https://example.test/entity",
        doi_hash="abc123",
        main_entity_policy={},
        hints_content=(
            "SEMANTIC_HINTS_V1\n"
            "Filter the crystals and wash them with DMF."
        ),
    )

    assert "audited semantic ledger" in prompt
    assert "active T-Box comments" in prompt
    assert "Do not introduce a domain-specific rule" in prompt
    assert "ChemicalInput" not in prompt
    assert "hasWashingSolvent" not in prompt
    assert "never demand refs" in prompt
    assert "never invoke a creator again for that same ref" not in prompt


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


def test_runtime_rules_separate_lexical_object_fields_from_creator_args() -> None:
    prompt = _augment_kg_prompt_with_runtime_rules(
        kg_prompt="Build the graph.",
        entity_label="example",
        entity_uri="https://example.test/entity",
        doi_hash="abc123",
        main_entity_policy={},
        hints_content=json.dumps(
            {
                "entities": [
                    {
                        "ref": "member1",
                        "class": "Process",
                        "label": "Process",
                        "datatype_properties": {"hasQuantity": "90 degC"},
                    }
                ],
                "relations": [],
            }
        ),
        ontology_contract={
            "object_properties": [
                {"property_iri": "https://example.test/hasQuantity"}
            ]
        },
    )

    assert "`hasQuantity`" in prompt
    assert "Do not pass them as entity-creator keyword arguments" in prompt
    assert "deterministic quantity processor" in prompt


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


def test_corrected_occurrence_retry_clears_pre_execution_validation(
    monkeypatch,
) -> None:
    from langchain_core.messages import AIMessage, ToolMessage

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
    identity = {"parent_iri": "urn:synthesis:1", "hasOrder": 2}
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "bad",
                    "name": "create_Add",
                    "args": {
                        **identity,
                        "label": "ZrT-2",
                        "hasTargetTemperature": "120 °C",
                    },
                }
            ],
        ),
        ToolMessage(
            content=(
                "1 validation error for create_Add\n"
                "hasTargetTemperature\nExtra inputs are not permitted "
                "[type=extra_forbidden]"
            ),
            tool_call_id="bad",
            name="create_Add",
            status="error",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "good",
                    "name": "create_Add",
                    "args": {**identity, "label": "ZrT-2"},
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "status": "ok",
                    "iri": "urn:add:2",
                    "graph_changed": True,
                }
            ),
            tool_call_id="good",
            name="create_Add",
        ),
    ]

    trace = _summarize_react_tool_activity(messages)
    failed = trace["tool_outputs"][0]["structured_content"]
    assert failed["code"] == "TOOL_ARGUMENT_VALIDATION"
    assert failed["pre_execution_error"] is True
    assert failed["graph_changed"] is False
    assert (
        trace["tool_outputs"][0]["semantic_fingerprint"]
        == trace["tool_outputs"][1]["semantic_fingerprint"]
    )
    assert _unresolved_structured_obligations({"tool_activity": trace}) == []


def test_attempt_trace_preserves_argument_firewall_warning() -> None:
    warning = {
        "call_id": "sanitized-separate",
        "tool_name": "create_Separate",
        "removed_arguments": ["removesSpecies_label"],
        "argument_owners": {
            "removesSpecies_label": ["create_Evaporate"],
        },
    }
    trace = _attempt_trace(
        {
            "tool_activity": {
                "planned_tool_calls": [
                    {
                        "id": "sanitized-separate",
                        "name": "create_Separate",
                        "args": {
                            "parent_iri": "urn:root",
                            "hasOrder": 2,
                            "label": "Separate",
                        },
                        "argument_firewall_warning": warning,
                    }
                ],
                "tool_outputs": [
                    {
                        "tool_call_id": "sanitized-separate",
                        "name": "create_Separate",
                        "structured_content": {
                            "status": "ok",
                            "graph_changed": True,
                        },
                    }
                ],
            }
        },
        artifact_found=True,
    )

    assert trace["structured_tool_failure"] is False
    assert trace["argument_firewall_warnings"] == [warning]


def test_attempt_trace_records_resolved_argument_owner_mismatch() -> None:
    fingerprint = "f" * 64
    trace = _attempt_trace(
        {
            "tool_activity": {
                "planned_tool_calls": [],
                "tool_outputs": [
                    {
                        "tool_call_id": "bad-separate",
                        "name": "create_Separate",
                        "semantic_fingerprint": fingerprint,
                        "structured_content": {
                            "status": "error",
                            "code": "ARGUMENT_OWNER_MISMATCH",
                            "retryable": True,
                            "graph_changed": False,
                            "semantic_fingerprint": fingerprint,
                            "invalid_arguments": [
                                "removesSpecies_label"
                            ],
                            "argument_owners": {
                                "removesSpecies_label": [
                                    "create_Evaporate"
                                ]
                            },
                        },
                    },
                    {
                        "tool_call_id": "fixed-separate",
                        "name": "create_Separate",
                        "semantic_fingerprint": fingerprint,
                        "structured_content": {
                            "status": "ok",
                            "graph_changed": True,
                            "semantic_fingerprint": fingerprint,
                        },
                    },
                ],
            }
        },
        artifact_found=True,
    )

    assert trace["structured_tool_failure"] is False
    assert trace["unresolved_obligations"] == []
    assert trace["argument_owner_repairs"] == [
        {
            "semantic_fingerprint": fingerprint,
            "rejected_tool_call_id": "bad-separate",
            "repaired_tool_call_id": "fixed-separate",
            "tool_name": "create_Separate",
            "invalid_arguments": ["removesSpecies_label"],
            "argument_owners": {
                "removesSpecies_label": ["create_Evaporate"]
            },
            "status": "repaired",
        }
    ]


def test_quantity_facet_warning_remains_until_same_facet_is_repaired() -> None:
    fingerprint = "a" * 64
    failed_facet_output = {
        "tool_call_id": "heat-bad-unit",
        "name": "create_HeatChill",
        "semantic_fingerprint": fingerprint,
        "structured_content": {
            "status": "ok",
            "graph_changed": True,
            "semantic_fingerprint": fingerprint,
            "omitted_facet": True,
            "facet_warnings": [
                {
                    "facet": "hasTargetTemperature",
                    "omitted_facet": True,
                    "code": "QUANTITY_FACET_OMITTED",
                    "message": "Unsupported unit",
                }
            ],
        },
    }
    metadata = {
        "tool_activity": {
            "planned_tool_calls": [
                {
                    "id": "heat-bad-unit",
                    "name": "create_HeatChill",
                    "args": {
                        "parent_iri": "urn:root",
                        "hasOrder": 1,
                        "label": "Heat",
                        "hasTargetTemperature": "60 oC",
                    },
                }
            ],
            "tool_outputs": [failed_facet_output],
        }
    }

    unresolved = _unresolved_structured_obligations(metadata)
    assert len(unresolved) == 1
    assert unresolved[0]["facet"] == "hasTargetTemperature"
    assert unresolved[0]["retryable"] is True
    assert unresolved[0]["skippable"] is False

    metadata["tool_activity"]["planned_tool_calls"].append(
        {
            "id": "heat-fixed-unit",
            "name": "create_HeatChill",
            "args": {
                "parent_iri": "urn:root",
                "hasOrder": 1,
                "label": "Heat",
                "hasTargetTemperature": "60 °C",
            },
        }
    )
    metadata["tool_activity"]["tool_outputs"].append(
        {
            "tool_call_id": "heat-fixed-unit",
            "name": "create_HeatChill",
            "semantic_fingerprint": fingerprint,
            "structured_content": {
                "status": "ok",
                "graph_changed": True,
                "semantic_fingerprint": fingerprint,
                "omitted_facet": False,
                "facet_warnings": [],
            },
        }
    )

    assert _unresolved_structured_obligations(metadata) == []


def test_skippable_unrepresentable_quantity_facet_can_be_resolved() -> None:
    fingerprint = "a" * 64
    obligation_id = "b" * 64
    metadata = {
        "tool_activity": {
            "planned_tool_calls": [],
            "tool_outputs": [
                {
                    "tool_call_id": "dry-qualitative-pressure",
                    "name": "create_Dry",
                    "semantic_fingerprint": fingerprint,
                    "structured_content": {
                        "status": "ok",
                        "semantic_fingerprint": fingerprint,
                        "facet_warnings": [
                            {
                                "facet": "hasDryingPressure",
                                "code": "INVALID_OM2_QUANTITY",
                                "message": "vacuum is not a numeric pressure",
                                "obligation_id": obligation_id,
                                "skippable": True,
                            }
                        ],
                    },
                },
                {
                    "tool_call_id": "skip-pressure",
                    "name": "skip_semantic_obligation",
                    "structured_content": {
                        "status": "skipped",
                        "policy_valid": True,
                        "obligation_id": obligation_id,
                    },
                },
            ],
        }
    }

    assert _unresolved_structured_obligations(metadata) == []


def test_facet_skip_clears_only_the_exact_obligation() -> None:
    fingerprint = "a" * 64
    pressure_id = "b" * 64
    duration_id = "c" * 64
    warning_output = {
        "tool_call_id": "two-bad-facets",
        "name": "create_Dry",
        "semantic_fingerprint": fingerprint,
        "structured_content": {
            "status": "ok",
            "semantic_fingerprint": fingerprint,
            "facet_warnings": [
                {
                    "facet": "hasDryingPressure",
                    "obligation_id": pressure_id,
                    "skippable": True,
                },
                {
                    "facet": "hasStepDuration",
                    "obligation_id": duration_id,
                    "skippable": True,
                },
            ],
        },
    }
    metadata = {
        "tool_activity": {
            "planned_tool_calls": [],
            "tool_outputs": [
                warning_output,
                {
                    "tool_call_id": "skip-pressure",
                    "name": "skip_semantic_obligation",
                    "structured_content": {
                        "status": "skipped",
                        "policy_valid": True,
                        "obligation_id": pressure_id,
                    },
                },
            ],
        }
    }

    unresolved = _unresolved_structured_obligations(metadata)
    assert [item["identity"] for item in unresolved] == [duration_id]


def test_attempt_trace_json_is_written_atomically(tmp_path) -> None:
    target = tmp_path / "attempt.trace.json"

    _write_json_atomic(str(target), {"attempt": 1, "ok": False})

    assert target.read_text(encoding="utf-8").startswith("{")
    assert not list(tmp_path.glob("*.tmp"))


def test_post_publish_failure_routes_to_property_owner() -> None:
    predicate = "https://example.test/retrievedFrom"
    context, required_links = _select_post_publish_repair_context(
        messages=[
            f"Missing ontology-required link {predicate} on urn:entity: "
            "expected >= 1, found 0"
        ],
        ontology_contract={
            "required_links": [
                {
                    "predicate_iri": predicate,
                    "target_class_iri": "https://example.test/Document",
                    "min_count": 1,
                }
            ]
        },
        contexts=[
            {"iteration": 3, "owned_properties": ["hasStep"]},
            {"iteration": 2, "owned_properties": ["retrievedFrom"]},
        ],
    )

    assert context is not None
    assert context["iteration"] == 2
    assert required_links[0]["predicate_iri"] == predicate


def test_resume_checkpoint_prefers_richest_exact_entity_graph(tmp_path) -> None:
    entity = URIRef("urn:entity")
    entity_type = URIRef("https://example.test/Root")
    memory_dir = tmp_path / "memory"
    output_dir = tmp_path / "output"
    intermediate_dir = tmp_path / "intermediate"
    memory_dir.mkdir()
    output_dir.mkdir()
    intermediate_dir.mkdir()
    shell = Graph()
    shell.add((entity, RDF.type, entity_type))
    shell.serialize(destination=memory_dir / "entity.ttl", format="turtle")
    complete = Graph()
    complete.add((entity, RDF.type, entity_type))
    complete.add((entity, RDFS.label, Literal("entity")))
    complete.add(
        (entity, URIRef("https://example.test/required"), URIRef("urn:target"))
    )
    complete.serialize(destination=output_dir / "entity.ttl", format="turtle")

    selected = _select_richest_entity_checkpoint(
        doi_folder=str(tmp_path),
        entity_uri=str(entity),
        ontology_output_dir=str(output_dir),
        intermediate_ttl_dir=str(intermediate_dir),
    )

    assert Path(selected) == output_dir / "entity.ttl"


def test_post_publish_repair_feedback_overrides_conflicting_prompt(tmp_path) -> None:
    context = {
        "iteration": 2,
        "prompt_path": "/generated/KG_BUILDING_ITER_2.md",
    }
    prompt = _post_publish_repair_prompt(
        messages=["Missing required relation"],
        required_links=[
            {
                "predicate_iri": "https://example.test/retrievedFrom",
                "target_class_iri": "https://example.test/Document",
            }
        ],
    )
    path = _write_post_publish_feedback(
        doi_folder=str(tmp_path),
        entity_scope="entity-scope",
        entity_label="entity",
        entity_uri="urn:entity",
        attempt=1,
        messages=["Missing required relation"],
        required_links=[],
        repair_context=context,
        retry_status="resolved",
    )

    assert "HIGHEST PRIORITY" in prompt
    assert "overriding any earlier prompt instruction" in prompt
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["priority"] == "highest"
    assert payload["priority_rank"] == 0
    assert payload["retry_status"] == "resolved"
    assert payload["repair_owner"]["iteration"] == 2


def test_post_publish_feedback_attempts_are_append_only(tmp_path) -> None:
    feedback_dir = tmp_path / "post_publish_feedback" / "entity-scope"
    feedback_dir.mkdir(parents=True)
    (feedback_dir / "structural_attempt_1.json").write_text(
        "{}", encoding="utf-8"
    )
    (feedback_dir / "structural_attempt_4.json").write_text(
        "{}", encoding="utf-8"
    )

    assert (
        _next_post_publish_feedback_attempt(
            doi_folder=str(tmp_path), entity_scope="entity-scope"
        )
        == 5
    )


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
    document = URIRef("https://example.test/document/1")
    document_type = URIRef("http://purl.org/ontology/bibo/Document")
    retrieved_from = URIRef("https://example.test/retrievedFrom")
    graph = Graph()
    graph.add((entity, RDF.type, entity_type))
    graph.add((entity, RDFS.label, Literal("Entity One")))
    graph.add((entity, retrieved_from, document))
    graph.add((document, RDF.type, document_type))
    graph.add((document, RDFS.label, Literal("Entity One Document")))
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
    refs_path = tmp_path / "memory" / f"{scope}.refs.json"
    refs_path.write_text(
        json.dumps({"refs": {"doc-1": {"iri": str(document), "class": "Document"}}}),
        encoding="utf-8",
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
    assert (entity, retrieved_from, document) in seeded
    assert (document, RDF.type, document_type) in seeded
    assert (document, RDFS.label, Literal("Entity One Document")) in seeded
    checkpoint = next((tmp_path / "memory").glob("*.checkpoint.json"))
    assert str(entity) in checkpoint.read_text(encoding="utf-8")

    Path(canonical).write_text("polluted", encoding="utf-8")
    refs_path.write_text('{"refs": {}}', encoding="utf-8")
    restored = _restore_entity_iteration_checkpoint(
        doi_folder=str(tmp_path),
        entity_scope=scope,
        iteration=1,
    )
    assert Path(restored).is_file()
    restored_graph = Graph().parse(canonical, format="turtle")
    assert (entity, retrieved_from, document) in restored_graph
    assert json.loads(refs_path.read_text(encoding="utf-8"))["refs"]["doc-1"][
        "iri"
    ] == str(document)


def test_global_state_uses_active_data_root(tmp_path) -> None:
    write_global_state(
        "paper",
        "entity",
        "https://example.test/entity",
        data_dir=str(tmp_path),
    )

    assert (tmp_path / "global_state.json").is_file()


def test_global_state_retries_transient_windows_replace(tmp_path, monkeypatch) -> None:
    from src.pipelines.utils import atomic_replace
    from src.pipelines.main_kg_building import build as kg_build

    attempts = 0
    real_replace = os.replace

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("destination temporarily open")
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_replace.os, "replace", flaky_replace)
    monkeypatch.setattr(kg_build.os, "replace", flaky_replace)
    monkeypatch.setattr(atomic_replace.time, "sleep", lambda _seconds: None)

    write_global_state(
        "paper",
        "entity",
        "https://example.test/entity",
        data_dir=str(tmp_path),
    )

    assert attempts == 3
    payload = json.loads((tmp_path / "global_state.json").read_text(encoding="utf-8"))
    assert payload["doi"] == "paper"
    leftover = list(tmp_path.glob("*.json.tmp"))
    assert leftover == []


def test_hinted_temperature_and_duration_are_materialized_as_om2(
    tmp_path: Path,
) -> None:
    ontosyn = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    om2 = Namespace(
        "http://www.ontology-of-units-of-measure.org/resource/om-2/"
    )
    step = URIRef("https://example.test/step/5")
    ttl_path = tmp_path / "entity.ttl"
    graph = Graph()
    graph.add((step, RDF.type, ontosyn.HeatChill))
    graph.add((step, ontosyn.hasOrder, Literal(5)))
    graph.serialize(destination=ttl_path, format="turtle")
    hints = json.dumps(
        {
            "entities": [
                {
                    "ref": "step-5",
                    "class": "HeatChill",
                    "label": "Heat at 90 degC",
                    "datatype_properties": {
                        "hasOrder": 5,
                        "hasTargetTemperature": "90 degC",
                        "hasStepDuration": "24 hrs",
                    },
                }
            ]
        }
    )
    contract = {
        "classes": [
            {
                "class_iri": str(ontosyn.HeatChill),
            }
        ],
        "datatype_properties": [
            {
                "property_iri": str(ontosyn.hasOrder),
                "domain_iris": [str(ontosyn.HeatChill)],
                "range_iris": ["http://www.w3.org/2001/XMLSchema#integer"],
            }
        ],
        "object_properties": [
            {
                "property_iri": str(ontosyn.hasTargetTemperature),
                "domain_iris": [str(ontosyn.HeatChill)],
                "range_iris": [str(om2.Temperature)],
            },
            {
                "property_iri": str(ontosyn.hasStepDuration),
                "domain_iris": [str(ontosyn.HeatChill)],
                "range_iris": [str(om2.Duration)],
            },
        ],
    }

    ok, messages = _materialize_om2_quantities_from_hints(
        ttl_path=str(ttl_path),
        raw_hints=[hints],
        ontology_contract=contract,
    )

    assert ok, messages
    repaired = Graph().parse(ttl_path, format="turtle")
    temperatures = list(repaired.objects(step, ontosyn.hasTargetTemperature))
    durations = list(repaired.objects(step, ontosyn.hasStepDuration))
    assert len(temperatures) == 1
    assert len(durations) == 1
    assert (temperatures[0], RDF.type, om2.Temperature) in repaired
    assert (durations[0], RDF.type, om2.Duration) in repaired
    assert (temperatures[0], om2.hasNumericalValue, None) in repaired
    assert (durations[0], om2.hasNumericalValue, None) in repaired


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
