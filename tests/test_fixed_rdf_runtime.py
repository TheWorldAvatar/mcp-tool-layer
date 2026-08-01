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
from src.agents.scripts_and_prompts_generation import fixed_rdf_runtime


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


def test_entity_creator_rejects_invalid_labels_without_mutation_and_reuses_identity() -> None:
    reset_retained_graph()
    class_iri = "https://example.com/Owned"
    creator = _compile_entity_capabilities(
        {"classes": [{"class_iri": class_iri}]}
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
    assert list(graph.objects(URIRef(first), RDFS.label)) == [Literal("Stable identity")]


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
    assert (URIRef(subject), URIRef(predicate), URIRef(wrong_object)) not in retained_graph()


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
