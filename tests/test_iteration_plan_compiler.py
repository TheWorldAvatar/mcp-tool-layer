from __future__ import annotations

import pytest

from src.agents.scripts_and_prompts_generation.iteration_plan_compiler import (
    compile_iteration_plan,
)


def _parsed() -> dict:
    return {
        "classes": {
            "Root": {"iri": "https://example.test/Root"},
            "Child": {"iri": "https://example.test/Child"},
        },
        "properties": {
            "hasChild": {"iri": "https://example.test/hasChild"},
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
    assert compiled["provenance"]["scheduling_intent"]["sha256"] == "abc"
