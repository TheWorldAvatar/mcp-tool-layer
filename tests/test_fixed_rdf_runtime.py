from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import RDF, RDFS, Graph, Literal, URIRef

from src.agents.scripts_and_prompts_generation import fixed_rdf_runtime
from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    RelationshipContractError,
    _compile_entity_capabilities,
    _compile_ordered_entity_capabilities,
    _compile_datatype_capabilities,
    _compile_relationship_capabilities,
    export_graph_result,
    export_memory,
    init_memory,
    load_from_turtle_file,
    new_graph,
    reset_retained_graph,
    retained_graph,
    reset_graph,
    scoped_memory_paths,
    serialize_turtle,
    success_json,
)


def test_atomic_write_retries_transient_windows_destination_lock(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "central.ttl"
    real_replace = fixed_rdf_runtime.os.replace
    attempts = 0

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("destination temporarily open")
        return real_replace(source, destination)

    monkeypatch.setattr(fixed_rdf_runtime.os, "replace", flaky_replace)
    monkeypatch.setattr(fixed_rdf_runtime.time, "sleep", lambda _seconds: None)

    fixed_rdf_runtime._atomic_write_text(target, "stable")

    assert attempts == 3
    assert target.read_text(encoding="utf-8") == "stable"


def test_fixed_rdf_runtime_serializes_and_resets_graph() -> None:
    graph = new_graph(namespace_bindings={"ex": "https://example.com/"})
    subject = URIRef("https://example.com/item")
    graph.add((subject, RDF.type, URIRef("https://example.com/Thing")))

    ttl = serialize_turtle(graph)
    result = export_graph_result(graph, top_iri=subject)

    assert "ex:item a ex:Thing" in ttl
    assert result["status"] == "ok"
    assert result["top_iri"] == str(subject)
    assert result["ttl"] == ttl
    assert len(reset_graph(graph)) == 0


def test_fixed_runtime_export_persists_scoped_memory_and_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    graph = new_graph()
    subject = URIRef("https://example.com/item")
    graph.add((subject, RDF.type, URIRef("https://example.com/Thing")))

    result = export_graph_result(
        graph,
        top_iri=subject,
        doi="case-hash",
        scope="top",
    )

    memory_path = Path(result["memory_path"])
    export_path = Path(result["export_path"])
    assert memory_path == tmp_path / "case-hash" / "memory" / "top.ttl"
    assert memory_path.is_file()
    assert export_path.is_file()
    assert memory_path.read_text(encoding="utf-8") == result["ttl"]


@pytest.mark.skipif(
    fixed_rdf_runtime.os.name != "nt",
    reason="Windows extended-path behavior",
)
def test_fixed_runtime_export_supports_long_windows_paths(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path / ("nested_" * 12)))
    graph = new_graph()
    subject = URIRef("https://example.com/item")
    graph.add((subject, RDF.type, URIRef("https://example.com/Thing")))

    result = export_graph_result(
        graph,
        top_iri=subject,
        doi="case-hash",
        scope="https://www.theworldavatar.com/kg/instance/ChemicalSynthesis/"
        + ("a" * 120),
    )

    memory_path = "\\\\?\\" + str(Path(result["memory_path"]).resolve())
    export_path = "\\\\?\\" + str(Path(result["export_path"]).resolve())
    assert fixed_rdf_runtime.os.path.isfile(memory_path)
    assert fixed_rdf_runtime.os.path.isfile(export_path)


def test_fixed_lifecycle_resumes_and_exports_canonical_scope(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    subject = URIRef("https://example.com/resumed")
    persisted = Graph()
    persisted.add((subject, RDF.type, URIRef("https://example.com/Thing")))
    memory_path, _ = scoped_memory_paths("case-hash", "top")
    persisted.serialize(destination=memory_path, format="turtle")

    initialized = json.loads(init_memory("case-hash", "top"))
    exported = json.loads(export_memory("case-hash", "top"))

    assert initialized["status"] == "ok"
    assert initialized["load_state"]["loaded_triples"] == 1
    assert subject.n3() in exported["ttl"]
    assert Path(exported["memory_path"]).is_file()
    assert Path(exported["export_path"]).is_file()
    assert exported["central_memory"]["status"] == "deferred_to_pipeline"


def test_prepare_graph_for_export_repairs_order_and_prunes_orphans(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", raising=False)
    reset_retained_graph()
    root = URIRef("urn:test:root")
    step_class = URIRef("urn:test:Add")
    collection = URIRef("urn:test:hasStep")
    ordering = URIRef("urn:test:hasOrder")
    first = URIRef("urn:test:first")
    third = URIRef("urn:test:third")
    orphan = URIRef("urn:test:orphan")
    json.loads(init_memory("case-hash", "root", str(root)))
    graph = retained_graph()
    graph.add((root, RDF.type, URIRef("urn:test:ChemicalSynthesis")))
    for member, order in ((first, 1), (third, 3)):
        graph.add((root, collection, member))
        graph.add((member, RDF.type, step_class))
        graph.add((member, ordering, Literal(order)))
    graph.add((orphan, RDF.type, URIRef("urn:test:ExecutionPoint")))
    graph.add((orphan, RDFS.label, Literal("orphan")))

    result = fixed_rdf_runtime.prepare_graph_for_export(
        {
            "Add": {
                "class_iri": str(step_class),
                "parent_predicate_iri": str(collection),
                "ordering_property_iri": str(ordering),
            }
        }
    )

    assert result["status"] == "ok"
    assert result["graph_changed"] is True
    assert {int(value) for value in graph.objects(first, ordering)} == {1}
    assert {int(value) for value in graph.objects(third, ordering)} == {2}
    assert not any(graph.triples((orphan, None, None)))


def test_prepare_graph_for_export_keeps_extra_committed_roots(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", raising=False)
    reset_retained_graph()
    root = URIRef("urn:test:root")
    committed = URIRef("urn:test:committed-occurrence")
    json.loads(init_memory("case-hash", "root", str(root)))
    graph = retained_graph()
    graph.add((root, RDF.type, URIRef("urn:test:ChemicalSynthesis")))
    graph.add((committed, RDF.type, URIRef("urn:test:ExtensionFocus")))
    graph.add((committed, RDFS.label, Literal("focus")))

    result = fixed_rdf_runtime.prepare_graph_for_export(
        extra_keep_roots=[str(committed)]
    )

    assert result["status"] == "ok"
    assert (committed, RDF.type, URIRef("urn:test:ExtensionFocus")) in graph


def test_fixed_lifecycle_canonicalizes_agent_scope_alias(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME",
        "VMOT-3--07eedcbf7cb6",
    )
    reset_retained_graph()

    initialized = json.loads(init_memory("case-hash", "VMOT-3"))
    exported = json.loads(export_memory("case-hash", "VMOT-3"))

    assert initialized["top_level_entity_name"] == "VMOT-3--07eedcbf7cb6"
    assert initialized["requested_top_level_entity_name"] == "VMOT-3"
    assert initialized["scope_canonicalized"] is True
    assert Path(initialized["memory_path"]).name == "VMOT-3--07eedcbf7cb6.ttl"
    assert Path(exported["memory_path"]).name == "VMOT-3--07eedcbf7cb6.ttl"
    assert not (tmp_path / "case-hash" / "memory" / "VMOT-3.ttl").exists()


def test_init_memory_binds_root_and_canonicalizes_agent_argument(
    tmp_path: Path, monkeypatch
) -> None:
    root = "urn:root:short"
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", root)
    reset_retained_graph()

    initialized = json.loads(init_memory("case-hash", "top", root_iri=root))
    binding = fixed_rdf_runtime.bind_root_argument("urn:root:mistyped")

    assert initialized["bound_root_iri"] == root
    assert binding == {
        "requested_root_iri": "urn:root:mistyped",
        "effective_root_iri": root,
        "root_argument_canonicalized": True,
        "binding_source": "session",
    }


def test_bind_parent_occurrence_rewrites_session_root_to_unique_target(
    tmp_path: Path, monkeypatch
) -> None:
    root = "urn:root:synthesis"
    parent = "urn:parent:focus"
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", root)
    monkeypatch.setattr(
        fixed_rdf_runtime,
        "_enrichment_targets_from_global_state",
        lambda: [{"target_iri": parent, "class_iri": "urn:class:Focus"}],
    )
    reset_retained_graph()
    json.loads(init_memory("case-hash", "top", root_iri=root))

    rewritten = fixed_rdf_runtime.bind_parent_occurrence_argument(root)
    kept = fixed_rdf_runtime.bind_parent_occurrence_argument(parent)
    other = fixed_rdf_runtime.bind_parent_occurrence_argument("urn:parent:created")

    assert rewritten["effective_root_iri"] == parent
    assert rewritten["root_argument_canonicalized"] is True
    assert rewritten["binding_source"] == "enrichment_target"
    assert kept["effective_root_iri"] == parent
    assert kept["binding_source"] == "enrichment_target"
    assert other["effective_root_iri"] == "urn:parent:created"
    assert other["binding_source"] == "parent_occurrence"


def test_bind_parent_occurrence_unbound_without_unique_target(
    tmp_path: Path, monkeypatch
) -> None:
    root = "urn:root:synthesis"
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", root)
    monkeypatch.setattr(
        fixed_rdf_runtime,
        "_enrichment_targets_from_global_state",
        lambda: [],
    )
    reset_retained_graph()
    json.loads(init_memory("case-hash", "top", root_iri=root))

    unbound = fixed_rdf_runtime.bind_parent_occurrence_argument(root)
    assert unbound["effective_root_iri"] == ""
    assert unbound["binding_source"] == "unbound"


def test_skip_requires_registered_explicit_authorization(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", raising=False)
    reset_retained_graph()
    json.loads(init_memory("case-hash", "top", root_iri="urn:root"))
    required = "a" * 64
    optional = "b" * 64
    fixed_rdf_runtime.register_semantic_rejection(
        required,
        {"code": "SUBJECT_TYPE_MISSING", "tool_name": "create_Add"},
        skippable=False,
    )
    fixed_rdf_runtime.register_semantic_rejection(
        optional,
        {
            "code": "INVALID_OM2_QUANTITY",
            "tool_name": "create_quantity",
            "facet": "hasStepDuration",
            "source_value": "overnight",
        },
        skippable=True,
    )

    denied = json.loads(
        fixed_rdf_runtime.resolve_semantic_skip(required, "skip it")
    )
    accepted = json.loads(
        fixed_rdf_runtime.resolve_semantic_skip(optional, "not source-grounded")
    )
    unknown = json.loads(
        fixed_rdf_runtime.resolve_semantic_skip("c" * 64, "skip it")
    )

    assert denied["code"] == "SKIP_NOT_AUTHORIZED"
    assert denied["retryable"] is True
    assert denied["skippable"] is False
    assert accepted["status"] == "skipped"
    assert accepted["policy_valid"] is True
    assert accepted["skip_receipt"] == {
        "policy": "parser_verified_unrepresentable_facet",
        "controlled": True,
        "evidence": {
            "code": "INVALID_OM2_QUANTITY",
            "facet": "hasStepDuration",
            "source_value": "overnight",
        },
    }
    assert unknown["code"] == "UNKNOWN_SEMANTIC_OBLIGATION"


def test_init_memory_clears_retained_graph_when_doi_changes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME", raising=False)
    reset_retained_graph()
    first = URIRef("https://example.com/first-document-only")
    retained_graph().add(
        (first, RDF.type, URIRef("https://example.com/Thing"))
    )
    json.loads(init_memory("first-doi", "top"))

    initialized = json.loads(init_memory("second-doi", "top"))

    assert initialized["cross_document_reset"] is True
    assert initialized["previous_doi"] == "first-doi"
    assert (first, None, None) not in retained_graph()


def test_init_memory_materializes_empty_bound_root_for_export(
    tmp_path: Path, monkeypatch
) -> None:
    root = "urn:root:upstream"
    class_iri = "urn:schema:BoundRoot"
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", root)
    monkeypatch.setattr(
        fixed_rdf_runtime, "_package_top_entity_class_iri", lambda: class_iri
    )
    reset_retained_graph()

    initialized = json.loads(init_memory("case-hash", "top", root_iri=root))
    prepared = fixed_rdf_runtime.prepare_graph_for_export()

    assert initialized["bound_root_seed"]["applied"] is True
    assert initialized["bound_root_seed"]["class_iri"] == class_iri
    assert (URIRef(root), RDF.type, URIRef(class_iri)) in retained_graph()
    assert (URIRef(root), RDFS.label, Literal("top")) in retained_graph()
    assert prepared["status"] == "ok"


def test_init_memory_seeds_bound_enrichment_target_without_create(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME", raising=False)
    monkeypatch.setattr(
        fixed_rdf_runtime, "_package_ontology_name", lambda: "ontospecies"
    )
    target_iri = "https://example.test/output/1"
    class_iri = (
        "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species"
    )
    (tmp_path / "ontospecies_global_state.json").write_text(
        json.dumps(
            {
                "enrichment_targets": [
                    {"target_iri": target_iri, "class_iri": class_iri}
                ]
            }
        ),
        encoding="utf-8",
    )
    reset_retained_graph()

    initialized = json.loads(init_memory("case-hash", "top"))

    seed = initialized["enrichment_target_seed"]
    assert seed["applied"] is True
    assert seed["seeded"] == [target_iri]
    assert (
        URIRef(target_iri),
        RDF.type,
        URIRef(class_iri),
    ) in retained_graph()


def test_init_memory_restores_domain_neutral_prior_neighborhood(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    memory_path, _ = scoped_memory_paths("case-hash", "top")
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    top = URIRef("https://example.com/top")
    neighbor = URIRef("https://example.com/prior")
    top_type = URIRef("https://example.com/Top")
    neighbor_type = URIRef("https://example.com/ReusablePrior")
    predicate = URIRef("https://example.com/hasPrior")
    sidecar = memory_path.with_name(f"{memory_path.stem}.identity.json")
    sidecar.write_text(
        json.dumps(
            {
                "identity": {
                    "uri": str(top),
                    "label": "Top label",
                    "types": [str(top_type)],
                    "dossier": {
                        "explicit_iteration_1_facts": [
                            {
                                "predicate_iri": str(predicate),
                                "value_kind": "iri",
                                "object_iri": str(neighbor),
                                "object_types": [str(neighbor_type)],
                                "object_labels": ["Prior label"],
                            }
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    initialized = json.loads(init_memory("case-hash", "top"))
    graph = retained_graph()

    assert (top, RDF.type, top_type) in graph
    assert (top, predicate, neighbor) in graph
    assert (neighbor, RDF.type, neighbor_type) in graph
    assert (neighbor, RDFS.label, Literal("Prior label")) in graph
    assert initialized["identity_seed"]["restored_explicit_facts"] == 1
    assert initialized["identity_seed"]["restored_neighbor_types"] == 1
    assert initialized["identity_seed"]["restored_neighbor_labels"] == 1


def test_fixed_runtime_success_json_matches_public_creator_transport() -> None:
    payload = json.loads(
        success_json(iri="urn:test:entity", message="created", created=True)
    )

    assert payload == {
        "created": True,
        "iri": "urn:test:entity",
        "message": "created",
        "status": "ok",
    }


def test_reusable_entity_creator_rejects_invalid_labels_and_reuses_identity() -> None:
    reset_retained_graph()
    class_iri = "https://example.com/Owned"
    creator = _compile_entity_capabilities(
        {
            "classes": [{"class_iri": class_iri}],
            "reuse_policy": {
                "classes": [{"class_iri": class_iri, "reusable": True}]
            },
        }
    )[class_iri]
    graph = retained_graph()

    for invalid in ("", "   ", None, 7):
        before = set(graph)
        with pytest.raises(RelationshipContractError):
            creator(invalid)  # type: ignore[arg-type]
        assert set(graph) == before

    first = creator(" Stable identity ")
    second = creator("Stable identity")

    assert first == second
    assert first.startswith("https://")
    assert list(graph.objects(URIRef(first), RDFS.label)) == [Literal("Stable identity")]


def test_non_reusable_entity_creator_always_mints_fresh_identity() -> None:
    reset_retained_graph()
    class_iri = "https://example.com/Occurrence"
    creator = _compile_entity_capabilities(
        {
            "classes": [{"class_iri": class_iri}],
            "reuse_policy": {
                "classes": [{"class_iri": class_iri, "reusable": False}]
            },
        }
    )[class_iri]

    first = creator("Same contextual label")
    second = creator("Same contextual label")

    assert first != second
    assert list(retained_graph().subjects(RDF.type, URIRef(class_iri))) == [
        URIRef(first),
        URIRef(second),
    ]


def test_entity_creator_explicitly_asserts_subclass_ancestors() -> None:
    reset_retained_graph()
    child_iri = "https://example.com/Child"
    parent_iri = "https://example.com/Parent"
    creator = _compile_entity_capabilities(
        {
            "classes": [{"class_iri": child_iri}],
            "subclass_closure": [
                {
                    "class_iri": child_iri,
                    "superclass_iris": [child_iri, parent_iri],
                }
            ],
        }
    )[child_iri]

    subject = URIRef(creator("Child instance"))

    assert (subject, RDF.type, URIRef(child_iri)) in retained_graph()
    assert (subject, RDF.type, URIRef(parent_iri)) in retained_graph()


def test_parent_creator_does_not_reuse_subclass_identity_from_ancestor_type() -> None:
    reset_retained_graph()
    child_iri = "https://example.com/Child"
    parent_iri = "https://example.com/Parent"
    creators = _compile_entity_capabilities(
        {
            "classes": [
                {"class_iri": child_iri},
                {"class_iri": parent_iri},
            ],
            "subclass_closure": [
                {
                    "class_iri": child_iri,
                    "superclass_iris": [child_iri, parent_iri],
                },
                {
                    "class_iri": parent_iri,
                    "superclass_iris": [parent_iri],
                },
            ],
            "reuse_policy": {
                "classes": [
                    {"class_iri": child_iri, "reusable": True},
                    {"class_iri": parent_iri, "reusable": True},
                ]
            },
        }
    )

    child = creators[child_iri]("Same label")
    parent = creators[parent_iri]("Same label")

    assert child != parent
    assert creators[child_iri]("Same label") == child
    assert creators[parent_iri]("Same label") == parent


def test_package_om2_creator_is_bounded_to_relationship_range_classes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path = tmp_path / "_relationship_contract.json"
    allowed = (
        "http://www.ontology-of-units-of-measure.org/resource/om-2/Duration"
    )
    contract_path.write_text(
        json.dumps(
            {
                "object_properties": [
                    {
                        "property_iri": "https://example.com/hasDuration",
                        "range_iris": [allowed],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fixed_rdf_runtime, "__file__", str(tmp_path / "runtime.py"))
    reset_retained_graph()
    creator = fixed_rdf_runtime.package_om2_quantity_creator()

    quantity = URIRef(creator(allowed, "5 min"))

    assert (quantity, RDF.type, URIRef(allowed)) in retained_graph()
    before = set(retained_graph())
    with pytest.raises(RelationshipContractError):
        creator("https://example.com/NotAllowed", "5 min")
    assert set(retained_graph()) == before


def test_fixed_runtime_create_link_export_share_retained_graph() -> None:
    reset_retained_graph()
    creators = _compile_entity_capabilities(
        {
            "classes": [
                {"class_iri": "https://example.com/Parent"},
                {"class_iri": "https://example.com/Child"},
            ]
        }
    )
    subject = creators["https://example.com/Parent"]("parent")
    child = creators["https://example.com/Child"]("child")
    writers = _compile_relationship_capabilities(
        {
            "subclass_closure": [],
            "object_properties": [
                {
                    "property_iri": "https://example.com/hasChild",
                    "domain_iris": ["https://example.com/Parent"],
                    "range_iris": ["https://example.com/Child"],
                }
            ],
        }
    )
    writers["https://example.com/hasChild"](subject, child)

    result = export_graph_result(retained_graph(), top_iri=subject)

    assert result["ttl"].strip()
    graph = Graph().parse(data=result["ttl"], format="turtle")
    assert (
        URIRef(subject),
        URIRef("https://example.com/hasChild"),
        URIRef(child),
    ) in graph


def test_fixed_runtime_clones_occurrence_reuse_across_step_role_group() -> None:
    reset_retained_graph()
    parent_class = "https://example.com/Process"
    step_a_class = "https://example.com/StepA"
    step_b_class = "https://example.com/StepB"
    occurrence_class = "https://example.com/Occurrence"
    ownership_role = "https://example.com/hasOccurrence"
    role_a = "https://example.com/hasRoleAOccurrence"
    role_b = "https://example.com/hasRoleBOccurrence"
    creators = _compile_entity_capabilities(
        {
            "classes": [
                {"class_iri": parent_class},
                {"class_iri": step_a_class},
                {"class_iri": step_b_class},
                {"class_iri": occurrence_class},
            ],
            "reuse_policy": {
                "classes": [
                    {"class_iri": occurrence_class, "reusable": False}
                ]
            },
        }
    )
    parent = creators[parent_class]("process")
    step_a = creators[step_a_class]("step A")
    step_b = creators[step_b_class]("step B")
    occurrence = creators[occurrence_class]("same chemical")
    fresh_occurrence = creators[occurrence_class]("same chemical")
    contract = {
        "reuse_policy": {
            "classes": [
                {"class_iri": occurrence_class, "reusable": False}
            ]
        },
        "ordered_entity_creators": [
            {"class_iri": step_a_class},
            {"class_iri": step_b_class},
        ],
        "object_properties": [
            {
                "property_iri": ownership_role,
                "domain_iris": [parent_class],
                "range_iris": [occurrence_class],
            },
            {
                "property_iri": role_a,
                "domain_iris": [step_a_class, step_b_class],
                "range_iris": [occurrence_class],
            },
            {
                "property_iri": role_b,
                "domain_iris": [step_b_class],
                "range_iris": [occurrence_class],
            },
        ],
    }
    writers = _compile_relationship_capabilities(contract)

    assert writers[ownership_role](parent, occurrence)["status"] == "ok"
    assert writers[role_a](step_a, occurrence)["status"] == "ok"
    assert writers[role_a](step_a, occurrence)["status"] == "ok"
    assert writers[role_b](step_b, occurrence)["status"] == "ok"
    reused = writers[role_a](step_b, occurrence)

    assert reused["status"] == "ok"
    assert reused["auto_cloned_occurrence"] is True
    assert reused["requested_object_iri"] == occurrence
    assert reused["cloned_object_iri"] != occurrence
    assert reused["reason"] == "OBJECT_OCCURRENCE_REUSE_FORBIDDEN"
    assert reused["triple"] == [step_b, role_a, reused["cloned_object_iri"]]
    graph = retained_graph()
    cloned = URIRef(reused["cloned_object_iri"])
    assert (URIRef(step_a), URIRef(role_a), URIRef(occurrence)) in graph
    assert (URIRef(step_b), URIRef(role_a), cloned) in graph
    assert (URIRef(step_b), URIRef(role_a), URIRef(occurrence)) not in graph
    assert set(graph.objects(cloned, RDF.type)) == set(
        graph.objects(URIRef(occurrence), RDF.type)
    )
    assert set(graph.objects(cloned, RDFS.label)) == set(
        graph.objects(URIRef(occurrence), RDFS.label)
    )
    assert writers[role_a](step_b, fresh_occurrence)["status"] == "ok"


def test_fixed_runtime_rejects_wrong_range_without_mutating_graph() -> None:
    reset_retained_graph()
    creators = _compile_entity_capabilities(
        {
            "classes": [
                {"class_iri": "https://example.com/Parent"},
                {"class_iri": "https://example.com/Other"},
            ]
        }
    )
    subject = creators["https://example.com/Parent"]("Parent")
    wrong_object = creators["https://example.com/Other"]("Other")
    predicate = "https://example.com/hasObject"
    writers = _compile_relationship_capabilities(
        {
            "subclass_closure": [],
            "object_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": ["https://example.com/Parent"],
                    "range_iris": ["https://example.com/Child"],
                }
            ],
        }
    )

    with pytest.raises(RelationshipContractError) as exc:
        writers[predicate](subject, wrong_object)

    assert exc.value.code == "RANGE_TYPE_MISMATCH"
    assert exc.value.details["recovery"] == {
        "action": "use_compatible_binding_or_skip_relationship",
        "do_not_retry_object_iri": wrong_object,
        "compatible_bindings": [],
        "instruction": (
            "Do not retry the rejected predicate with this object or another object of the "
            "same type. Follow compatible_bindings when non-empty. For an atomic_creator "
            "binding, a successful owner creator already completed the edge. Otherwise use "
            "the listed standalone tool. If no source-grounded compatible binding applies, "
            "skip this relationship and continue remaining obligations without repeating "
            "prior successes."
        ),
    }
    assert (URIRef(subject), URIRef(predicate), URIRef(wrong_object)) not in retained_graph()


def test_missing_subject_returns_domain_filtered_session_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    parent_class = "https://example.com/Parent"
    child_class = "https://example.com/Child"
    predicate = "https://example.com/hasChild"
    root = "urn:root:short"
    child = "urn:child:short"
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", raising=False)
    reset_retained_graph()
    json.loads(init_memory("case-hash", "top", root_iri=root))
    retained_graph().add((URIRef(root), RDF.type, URIRef(parent_class)))
    retained_graph().add((URIRef(root), RDFS.label, Literal("Bound root")))
    retained_graph().add((URIRef(child), RDF.type, URIRef(child_class)))
    writers = _compile_relationship_capabilities(
        {
            "subclass_closure": [],
            "object_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": [parent_class],
                    "range_iris": [child_class],
                }
            ],
        }
    )

    with pytest.raises(RelationshipContractError) as exc:
        writers[predicate]("urn:root:mistyped", child)

    assert exc.value.code == "SUBJECT_TYPE_MISSING"
    recovery = exc.value.details["recovery"]
    assert recovery["bound_root_iri"] == root
    assert recovery["candidate_subjects"] == [
        {
            "iri": root,
            "type_iris": [parent_class],
            "labels": ["Bound root", "top"],
            "is_bound_root": True,
        }
    ]


def test_domain_mismatch_reports_compatible_atomic_binding() -> None:
    reset_retained_graph()
    parent_class = "https://example.com/Parent"
    step_class = "https://example.com/Step"
    input_class = "https://example.com/Input"
    top_predicate = "https://example.com/hasInput"
    step_predicate = "https://example.com/hasStepInput"
    creators = _compile_entity_capabilities(
        {
            "classes": [
                {"class_iri": parent_class},
                {"class_iri": step_class},
                {"class_iri": input_class},
            ]
        }
    )
    step = creators[step_class]("Step")
    material = creators[input_class]("Input")
    writers = _compile_relationship_capabilities(
        {
            "subclass_closure": [],
            "object_properties": [
                {
                    "property_iri": top_predicate,
                    "domain_iris": [parent_class],
                    "range_iris": [input_class],
                },
                {
                    "property_iri": step_predicate,
                    "domain_iris": [step_class],
                    "range_iris": [input_class],
                },
            ],
            "creator_owned_relationships": {
                step_predicate: [
                    {
                        "public_tool": "create_Step",
                        "owner_class_iri": step_class,
                        "role": "owned_dependent",
                    }
                ]
            },
        }
    )

    with pytest.raises(RelationshipContractError) as exc:
        writers[top_predicate](step, material)

    assert exc.value.code == "DOMAIN_TYPE_MISMATCH"
    assert exc.value.details["recovery"]["compatible_bindings"] == [
        {
            "predicate_iri": step_predicate,
            "operation": {
                "mode": "atomic_creator",
                "public_tools": ["create_Step"],
                "instruction": (
                    "This edge is creator-owned. If its owner was already created "
                    "successfully, the binding is already complete; do not call or "
                    "invent a standalone relationship writer."
                ),
            },
        }
    ]


def test_fixed_runtime_accepts_subclass_in_declared_range() -> None:
    reset_retained_graph()
    creators = _compile_entity_capabilities(
        {
            "classes": [
                {"class_iri": "https://example.com/Parent"},
                {"class_iri": "https://example.com/SpecialChild"},
            ]
        }
    )
    subject = creators["https://example.com/Parent"]("Parent")
    child = creators["https://example.com/SpecialChild"]("Special child")
    predicate = "https://example.com/hasObject"
    writers = _compile_relationship_capabilities(
        {
            "subclass_closure": [
                {
                    "class_iri": "https://example.com/SpecialChild",
                    "superclass_iris": [
                        "https://example.com/SpecialChild",
                        "https://example.com/Child",
                    ],
                }
            ],
            "object_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": ["https://example.com/Parent"],
                    "range_iris": ["https://example.com/Child"],
                }
            ],
        }
    )

    result = writers[predicate](subject, child)

    assert result["status"] == "ok"
    assert (URIRef(subject), URIRef(predicate), URIRef(child)) in retained_graph()


def test_relationship_hydrates_reusable_central_object_for_range_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    parent_iri = "https://example.com/Parent"
    equipment_iri = "https://example.com/Equipment"
    lab_equipment_iri = "https://example.com/LabEquipment"
    predicate = "https://example.com/usesEquipment"
    subject = URIRef("https://example.com/parent")
    reusable_object = URIRef("https://example.com/shared-equipment")
    retained_graph().add((subject, RDF.type, URIRef(parent_iri)))

    central_source = new_graph()
    central_source.add(
        (reusable_object, RDF.type, URIRef(lab_equipment_iri))
    )
    central_source.add(
        (reusable_object, RDFS.label, Literal("Shared vacuum line"))
    )
    fixed_rdf_runtime.publish_reusable_entities_to_central_memory(
        ontology_name="test-ontology",
        source_graph=central_source,
        reusable_class_iris=[lab_equipment_iri],
        doi="10.test/example",
        top_level_entity_name="source-scope",
    )
    writers = _compile_relationship_capabilities(
        {
            "ontology_name": "test-ontology",
            "reuse_policy": {
                "classes": [
                    {
                        "class_iri": lab_equipment_iri,
                        "reusable": True,
                    }
                ]
            },
            "subclass_closure": [
                {
                    "class_iri": lab_equipment_iri,
                    "superclass_iris": [
                        lab_equipment_iri,
                        equipment_iri,
                    ],
                }
            ],
            "object_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": [parent_iri],
                    "range_iris": [equipment_iri],
                }
            ],
        }
    )

    fixed_rdf_runtime.init_memory("current-document", "current-scope")
    with pytest.raises(RelationshipContractError) as exc:
        writers[predicate](str(subject), str(reusable_object))
    assert exc.value.code == "CENTRAL_REUSE_NOT_AUTHORIZED"

    token = fixed_rdf_runtime.register_central_reuse_authorization(
        candidate_iri=str(reusable_object),
        pair_id="p0001",
        judgement={
            "reuse_authorized": True,
            "same_real_world_entity": True,
            "context_independent_identity": True,
            "match_basis_satisfied": True,
            "confidence": 0.99,
        },
    )
    result = writers[predicate](str(subject), str(reusable_object), token)

    assert result["status"] == "ok"
    assert result["object_type_source"] == "central_memory"
    assert result["hydrated_type_iris"] == [lab_equipment_iri]
    assert result["reuse_authorization_pair_id"] == "p0001"
    assert (
        reusable_object,
        RDF.type,
        URIRef(lab_equipment_iri),
    ) in retained_graph()
    assert (
        reusable_object,
        RDFS.label,
        Literal("Shared vacuum line"),
    ) in retained_graph()
    assert (
        subject,
        URIRef(predicate),
        reusable_object,
    ) in retained_graph()


def test_relationship_accepts_scoped_dossier_object_also_present_in_central_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    parent_iri = "https://example.com/Parent"
    document_iri = "https://example.com/Document"
    predicate = "https://example.com/retrievedFrom"
    subject = URIRef("https://example.com/synthesis")
    dossier_object = URIRef("https://example.com/document")
    retained_graph().add((subject, RDF.type, URIRef(parent_iri)))
    retained_graph().add((dossier_object, RDF.type, URIRef(document_iri)))

    central_source = new_graph()
    central_source.add((dossier_object, RDF.type, URIRef(document_iri)))
    fixed_rdf_runtime.publish_reusable_entities_to_central_memory(
        ontology_name="test-ontology",
        source_graph=central_source,
        reusable_class_iris=[document_iri],
        doi="10.test/example",
        top_level_entity_name="source-scope",
    )
    writers = _compile_relationship_capabilities(
        {
            "ontology_name": "test-ontology",
            "reuse_policy": {
                "classes": [
                    {
                        "class_iri": document_iri,
                        "reusable": True,
                    }
                ]
            },
            "subclass_closure": [],
            "object_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": [parent_iri],
                    "range_iris": [document_iri],
                }
            ],
        }
    )

    result = writers[predicate](str(subject), str(dossier_object))

    assert result["status"] == "ok"
    assert "object_type_source" not in result
    assert (subject, URIRef(predicate), dossier_object) in retained_graph()


def test_relationship_does_not_hydrate_non_reusable_central_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    parent_iri = "https://example.com/Parent"
    child_iri = "https://example.com/Child"
    predicate = "https://example.com/hasChild"
    subject = URIRef("https://example.com/parent")
    central_object = URIRef("https://example.com/non-reusable-child")
    retained_graph().add((subject, RDF.type, URIRef(parent_iri)))
    central_source = new_graph()
    central_source.add((central_object, RDF.type, URIRef(child_iri)))
    fixed_rdf_runtime.publish_reusable_entities_to_central_memory(
        ontology_name="test-ontology",
        source_graph=central_source,
        reusable_class_iris=[child_iri],
        doi="10.test/example",
        top_level_entity_name="source-scope",
    )
    writers = _compile_relationship_capabilities(
        {
            "ontology_name": "test-ontology",
            "reuse_policy": {
                "classes": [
                    {
                        "class_iri": child_iri,
                        "reusable": False,
                    }
                ]
            },
            "subclass_closure": [],
            "object_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": [parent_iri],
                    "range_iris": [child_iri],
                }
            ],
        }
    )

    with pytest.raises(RelationshipContractError) as exc:
        writers[predicate](str(subject), str(central_object))

    assert exc.value.code == "OBJECT_TYPE_MISSING"
    assert (
        central_object,
        RDF.type,
        URIRef(child_iri),
    ) not in retained_graph()
    assert (
        subject,
        URIRef(predicate),
        central_object,
    ) not in retained_graph()


def test_central_reuse_grant_is_bound_to_candidate_and_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    fixed_rdf_runtime.init_memory("document-1", "scope-1")
    token = fixed_rdf_runtime.register_central_reuse_authorization(
        candidate_iri="https://example.com/candidate-1",
        pair_id="p0001",
        judgement={
            "reuse_authorized": True,
            "same_real_world_entity": True,
            "context_independent_identity": True,
            "match_basis_satisfied": True,
            "confidence": 0.99,
        },
    )

    with pytest.raises(RelationshipContractError) as candidate_exc:
        fixed_rdf_runtime._validate_central_reuse_authorization(
            "https://example.com/candidate-2",
            token,
        )
    assert candidate_exc.value.code == "CENTRAL_REUSE_NOT_AUTHORIZED"

    fixed_rdf_runtime.init_memory("document-2", "scope-2")
    with pytest.raises(RelationshipContractError) as scope_exc:
        fixed_rdf_runtime._validate_central_reuse_authorization(
            "https://example.com/candidate-1",
            token,
        )
    assert scope_exc.value.code == "CENTRAL_REUSE_NOT_AUTHORIZED"


def test_fixed_runtime_does_not_compile_incomplete_property_contract() -> None:
    writers = _compile_relationship_capabilities(
        {
            "object_properties": [
                {
                    "property_iri": "https://example.com/hasUnknown",
                    "domain_iris": ["https://example.com/Parent"],
                    "range_iris": [],
                }
            ]
        }
    )

    assert writers == {}


def test_fixed_runtime_datatype_capability_rejects_wrong_value_without_mutation() -> None:
    reset_retained_graph()
    creators = _compile_entity_capabilities(
        {"classes": [{"class_iri": "https://example.com/Step"}]}
    )
    subject = creators["https://example.com/Step"]("Step")
    predicate = "https://example.com/hasOrder"
    writers = _compile_datatype_capabilities(
        {
            "subclass_closure": [],
            "datatype_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": ["https://example.com/Step"],
                    "range_iris": ["http://www.w3.org/2001/XMLSchema#integer"],
                }
            ],
        }
    )

    with pytest.raises(RelationshipContractError) as exc:
        writers[predicate](subject, "first")

    assert exc.value.code == "DATATYPE_MISMATCH"
    assert not list(retained_graph().objects(URIRef(subject), URIRef(predicate)))


def test_fixed_runtime_datatype_setter_replaces_prior_value() -> None:
    reset_retained_graph()
    class_iri = "https://example.com/Step"
    predicate = "https://example.com/hasOrder"
    subject = _compile_entity_capabilities(
        {"classes": [{"class_iri": class_iri}]}
    )[class_iri]("Step")
    writer = _compile_datatype_capabilities(
        {
            "subclass_closure": [],
            "datatype_properties": [
                {
                    "property_iri": predicate,
                    "domain_iris": [class_iri],
                    "range_iris": ["http://www.w3.org/2001/XMLSchema#integer"],
                }
            ],
        }
    )[predicate]

    writer(subject, 7)
    writer(subject, 2)

    assert list(retained_graph().objects(URIRef(subject), URIRef(predicate))) == [
        Literal(2, datatype=URIRef("http://www.w3.org/2001/XMLSchema#integer"))
    ]


def test_ordered_entity_creator_writes_identity_and_order_atomically() -> None:
    reset_retained_graph()
    class_iri = "https://example.com/Step"
    predicate = "https://example.com/hasOrder"
    contract = {
        "classes": [{"class_iri": class_iri}],
        "subclass_closure": [],
        "datatype_properties": [
            {
                "property_iri": predicate,
                "domain_iris": [class_iri],
                "range_iris": ["http://www.w3.org/2001/XMLSchema#integer"],
            }
        ],
        "ordered_entity_creators": [
            {
                "class_iri": class_iri,
                "ordering_property_iri": predicate,
            }
        ],
    }
    creator = _compile_ordered_entity_capabilities(contract)[class_iri]

    subject = URIRef(creator("Add reagent", 3))

    assert (subject, RDF.type, URIRef(class_iri)) in retained_graph()
    assert (subject, RDFS.label, Literal("Add reagent")) in retained_graph()
    assert (
        subject,
        URIRef(predicate),
        Literal(3, datatype=URIRef("http://www.w3.org/2001/XMLSchema#integer")),
    ) in retained_graph()
    before = set(retained_graph())
    with pytest.raises(RelationshipContractError):
        creator("Invalid step", 0)
    assert set(retained_graph()) == before


def test_fixed_runtime_loads_persisted_turtle_for_resume(tmp_path) -> None:
    source = tmp_path / "memory.ttl"
    source.write_text(
        "<https://example.com/item> a <https://example.com/Thing> .\n",
        encoding="utf-8",
    )
    reset_retained_graph()

    result = load_from_turtle_file(str(source), behavior="merge")

    assert result["status"] == "ok"
    assert result["total_triples"] == 1
    assert (
        URIRef("https://example.com/item"),
        RDF.type,
        URIRef("https://example.com/Thing"),
    ) in retained_graph()


def test_fixed_om2_public_adapter_returns_rejection_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(_quantity_class_iri: str, _label: str) -> str:
        raise RelationshipContractError(
            "OM2_QUANTITY_CLASS_NOT_ALLOWED",
            {"quantity_class_iri": "https://example.invalid/NotAllowed"},
        )

    monkeypatch.setattr(
        fixed_rdf_runtime,
        "package_om2_quantity_creator",
        lambda: reject,
    )
    result = json.loads(
        fixed_rdf_runtime.create_om2_quantity(
            "https://example.invalid/NotAllowed",
            "1 s",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "OM2_QUANTITY_CLASS_NOT_ALLOWED"


def test_error_result_ignores_status_overwrite_in_metadata() -> None:
    payload = fixed_rdf_runtime.error_result(
        code="PROPOSED_ENTITY_EVIDENCE_REQUIRED",
        message="proposed entity evidence is required",
        status="PROPOSED_ENTITY_EVIDENCE_REQUIRED",
        extra="kept",
    )
    assert payload["status"] == "rejected"
    assert payload["code"] == "PROPOSED_ENTITY_EVIDENCE_REQUIRED"
    assert payload["message"] == "proposed entity evidence is required"
    assert payload["extra"] == "kept"

    parsed = json.loads(
        fixed_rdf_runtime.error_json(
            code="PROPOSED_ENTITY_EVIDENCE_REQUIRED",
            message="proposed entity evidence is required",
            status="PROPOSED_ENTITY_EVIDENCE_REQUIRED",
        )
    )
    assert parsed["status"] == "rejected"
    assert parsed["code"] == "PROPOSED_ENTITY_EVIDENCE_REQUIRED"


def test_success_result_ignores_status_overwrite_in_metadata() -> None:
    payload = fixed_rdf_runtime.success_result(
        iri="https://example.test/x",
        message="ok",
        status="rejected",
        note="kept",
    )
    assert payload["status"] == "ok"
    assert payload["iri"] == "https://example.test/x"
    assert payload["note"] == "kept"
