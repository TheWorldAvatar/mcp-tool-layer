from __future__ import annotations

import pytest

from src.agents.scripts_and_prompts_generation.iteration_plan_compiler import (
    _infer_linked_materialization_classes,
    compile_iteration_plan,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _iteration_owned_scope,
)


def _parsed() -> dict:
    return {
        "classes": {
            "Root": {"iri": "https://example.test/Root"},
            "Child": {
                "iri": "https://example.test/Child",
                "datatype_properties": {"hasName": {"range": "xsd:string"}},
            },
        },
        "properties": {
            "hasChild": {"iri": "https://example.test/hasChild"},
            "hasName": {
                "iri": "https://example.test/hasName",
                "kind": "datatype",
            },
        },
    }


def _contract() -> dict:
    return {
        "top_entity": {"class_iri": "https://example.test/Root"},
        "required_links": [
            {
                "subject_class_iri": "https://example.test/Root",
                "predicate_iri": "https://example.test/hasChild",
            }
        ],
        "external_class_creators": [],
    }


def test_compiler_accepts_property_only_semantic_scope() -> None:
    compiled = compile_iteration_plan(
        blueprint={
            "iterations": [
                {
                    "iteration_number": 3,
                    "responsibilities": {
                        "classes": [],
                        "object_properties": ["hasChild"],
                    },
                }
            ]
        },
        parsed=_parsed(),
        contract=_contract(),
        ontology_name="fixture",
        blueprint_provenance={"source": "test"},
    )
    assert compiled["iterations"][0]["semantic_scope"]["classes"] == []


def test_compiler_rejects_fully_empty_semantic_scope() -> None:
    with pytest.raises(ValueError, match="empty semantic scope"):
        compile_iteration_plan(
            blueprint={
                "iterations": [
                    {
                        "iteration_number": 3,
                        "slot_kind": "ordered",
                        "responsibilities": {
                            "classes": [],
                            "object_properties": [],
                        },
                    }
                ]
            },
            parsed=_parsed(),
            contract={"top_entity": _contract()["top_entity"], "required_links": []},
            ontology_name="fixture",
            blueprint_provenance={"source": "test"},
        )


def test_compiler_rejects_non_tbox_symbols() -> None:
    with pytest.raises(ValueError, match="absent from the active T-Box"):
        compile_iteration_plan(
            blueprint={
                "iterations": [
                    {
                        "iteration_number": 2,
                        "responsibilities": {
                            "classes": ["Invented"],
                            "object_properties": ["hasChild"],
                        },
                    }
                ]
            },
            parsed=_parsed(),
            contract=_contract(),
            ontology_name="fixture",
            blueprint_provenance={"source": "test"},
        )


def test_compiler_rejects_duplicate_ownership() -> None:
    with pytest.raises(ValueError, match="ownership must be unique"):
        compile_iteration_plan(
            blueprint={
                "iterations": [
                    {
                        "iteration_number": 2,
                        "responsibilities": {
                            "classes": ["Child"],
                            "object_properties": ["hasChild"],
                        },
                    },
                    {
                        "iteration_number": 3,
                        "responsibilities": {
                            "classes": ["Child"],
                            "object_properties": [],
                        },
                    },
                ]
            },
            parsed=_parsed(),
            contract=_contract(),
            ontology_name="fixture",
            blueprint_provenance={"source": "test"},
        )


def test_compiler_rejects_unassigned_tbox_required_link() -> None:
    with pytest.raises(ValueError, match="omits T-Box-required"):
        compile_iteration_plan(
            blueprint={
                "iterations": [
                    {
                        "iteration_number": 2,
                        "responsibilities": {
                            "classes": ["Child"],
                            "object_properties": [],
                        },
                    }
                ]
            },
            parsed=_parsed(),
            contract=_contract(),
            ontology_name="fixture",
            blueprint_provenance={"source": "test"},
        )


def test_compiler_adds_tbox_iris_and_provenance() -> None:
    compiled = compile_iteration_plan(
        blueprint={
            "iterations": [
                {
                    "iteration_number": 2,
                    "responsibilities": {
                        "classes": ["Child"],
                        "object_properties": ["hasChild"],
                    },
                }
            ]
        },
        parsed=_parsed(),
        contract=_contract(),
        ontology_name="fixture",
        blueprint_provenance={"source": "test", "sha256": "abc"},
    )

    scope = compiled["iterations"][0]["semantic_scope"]
    assert scope["classes"] == [
        {"local": "Child", "iri": "https://example.test/Child"}
    ]
    assert scope["object_properties"] == [
        {"local": "hasChild", "iri": "https://example.test/hasChild"}
    ]
    assert scope["property_closure"] == [
        {
            "local": "hasName",
            "iri": "https://example.test/hasName",
            "kind": "datatype",
            "source_classes": ["Child"],
            "explicit_owner": False,
        }
    ]
    assert compiled["provenance"]["scheduling_intent"]["sha256"] == "abc"


def test_compiler_drops_classes_absent_from_creator_surface() -> None:
    compiled = compile_iteration_plan(
        blueprint={
            "iterations": [
                {
                    "iteration_number": 2,
                    "responsibilities": {
                        "classes": ["Alpha", "Beta"],
                        "object_properties": ["usesAlpha"],
                    },
                }
            ]
        },
        parsed={
            "classes": {
                "Alpha": {"iri": "https://example.test/Alpha"},
                "Beta": {
                    "iri": "https://example.test/Beta",
                    "parent_classes": ["Alpha"],
                },
            },
            "properties": {
                "usesAlpha": {
                    "iri": "https://example.test/usesAlpha",
                    "kind": "object",
                },
            },
        },
        contract={
            "top_entity": {"class_iri": "https://example.test/Root"},
            "required_links": [],
            "external_class_creators": [],
            "ordered_member_profile": {
                "most_specific_subclass_targets": {"Alpha": ["Beta"]},
            },
        },
        ontology_name="fixture",
        blueprint_provenance={"source": "test"},
    )

    responsibilities = compiled["iterations"][0]["responsibilities"]
    assert responsibilities["classes"] == ["Beta"]
    assert responsibilities["object_properties"] == ["usesAlpha"]
    assert compiled["iterations"][0]["semantic_scope"]["classes"] == [
        {"local": "Beta", "iri": "https://example.test/Beta"}
    ]


def test_iteration_owned_scope_drops_classes_without_creators() -> None:
    scope = _iteration_owned_scope(
        {
            "responsibilities": {
                "classes": ["Alpha", "Beta"],
                "object_properties": ["usesAlpha"],
            }
        },
        materializable_class_locals={"Beta"},
    )
    assert scope["classes"] == ["Beta"]
    assert scope["object_properties"] == ["usesAlpha"]


def test_compiler_includes_materialization_class_datatypes_in_property_closure() -> None:
    compiled = compile_iteration_plan(
        blueprint={
            "iterations": [
                {
                    "iteration_number": 2,
                    "responsibilities": {
                        "classes": ["Child"],
                        "object_properties": ["hasChild"],
                    },
                },
                {
                    "iteration_number": 3,
                    "linked_materialization_classes": ["Child"],
                    "responsibilities": {
                        "classes": [],
                        "object_properties": [],
                    },
                },
            ]
        },
        parsed=_parsed(),
        contract=_contract(),
        ontology_name="fixture",
        blueprint_provenance={"source": "test"},
    )

    iter2_scope = compiled["iterations"][0]["semantic_scope"]
    iter3_scope = compiled["iterations"][1]["semantic_scope"]
    assert iter3_scope["classes"] == []
    assert {item["local"] for item in iter2_scope["property_closure"]} == {"hasName"}
    assert {
        item["local"]: item
        for item in iter3_scope["property_closure"]
    }["hasName"] == {
        "local": "hasName",
        "iri": "https://example.test/hasName",
        "kind": "datatype",
        "source_classes": ["Child"],
        "explicit_owner": False,
    }


def test_compiler_infers_non_reusable_earlier_ranges_as_linked_materialization() -> None:
    inferred = _infer_linked_materialization_classes(
        object_properties=["usesChild", "usesTool"],
        owned_classes={"Action"},
        earlier_classes={"Child", "Tool"},
        parsed_classes={
            "Action": {"parent_classes": []},
            "Child": {"parent_classes": []},
            "Tool": {"parent_classes": []},
        },
        parsed_properties={
            "usesChild": {
                "kind": "object",
                "domains": ["Action"],
                "range": "Child",
            },
            "usesTool": {
                "kind": "object",
                "domains": ["Action"],
                "range": "Tool",
            },
        },
        contract={
            "reuse_policy": {
                "classes": [
                    {"class_local": "Child", "reusable": False},
                    {"class_local": "Tool", "reusable": True},
                ]
            }
        },
    )
    assert inferred == ["Child"]
