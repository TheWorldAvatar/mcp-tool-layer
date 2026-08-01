from __future__ import annotations

import json
from types import SimpleNamespace

from rdflib import Graph, Literal, RDF, URIRef

from src.agents.scripts_and_prompts_generation.creator_atomicity import (
    creator_call_recipe,
    probe_generated_creator_atomicity,
)


CLASS = URIRef("https://example.org/Class")
PROP = URIRef("https://example.org/value")


class Runtime:
    def __init__(self) -> None:
        self.graph = Graph()

    def retained_graph(self) -> Graph:
        return self.graph


def _contract() -> list[dict[str, object]]:
    return [
        {
            "public_tool": "create_Class",
            "datatype_inputs": [
                {
                    "property_local": "value",
                    "property_iri": str(PROP),
                    "python_type": "float",
                    "required": False,
                }
            ],
        }
    ]


def test_atomic_probe_accepts_prevalidated_creator() -> None:
    runtime = Runtime()

    def create_Class(label: str, *, value: float | None = None) -> str:
        if not isinstance(label, str) or not label.strip():
            return json.dumps({"status": "error"})
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            return json.dumps({"status": "error"})
        iri = URIRef("https://example.org/" + label.replace(" ", "_"))
        runtime.graph.add((iri, RDF.type, CLASS))
        if value is not None:
            runtime.graph.add((iri, PROP, Literal(value)))
        return json.dumps({"status": "ok", "iri": str(iri)})

    report = probe_generated_creator_atomicity(
        module=SimpleNamespace(create_Class=create_Class),
        runtime=runtime,
        creator_contracts=_contract(),
    )
    assert report["ok"] is True
    assert len(runtime.graph) == 0


def test_atomic_probe_rejects_partial_mutation() -> None:
    runtime = Runtime()

    def create_Class(label: str, *, value: float | None = None) -> str:
        iri = URIRef("https://example.org/" + label.replace(" ", "_"))
        runtime.graph.add((iri, RDF.type, CLASS))
        if not isinstance(value, (int, float)):
            return json.dumps({"status": "error"})
        runtime.graph.add((iri, PROP, Literal(value)))
        return json.dumps({"status": "ok", "iri": str(iri)})

    report = probe_generated_creator_atomicity(
        module=SimpleNamespace(create_Class=create_Class),
        runtime=runtime,
        creator_contracts=_contract(),
    )
    assert report["ok"] is False
    assert (
        report["failures"]["create_Class"]["phase"]
        == "invalid_input_no_mutation"
    )
    assert len(runtime.graph) == 0


def test_creator_call_recipe_supplies_required_order() -> None:
    def create_Add(
        label: str,
        order: int,
        *,
        value: float | None = None,
    ) -> str:
        return json.dumps({"status": "ok", "iri": "https://example.org/add"})

    contract = {
        "public_tool": "create_Add",
        "ordered_member": True,
        "ordering_property_local": "hasOrder",
        "datatype_inputs": [
            {
                "property_local": "hasOrder",
                "python_type": "int",
                "required": True,
            },
            {
                "property_local": "value",
                "python_type": "float",
                "required": False,
            },
        ],
    }
    recipe = creator_call_recipe(
        contract,
        create_Add,
        label="Shared graph probe",
        include_optional_datatypes=False,
    )
    assert recipe["kwargs"] == {
        "label": "Shared graph probe",
        "order": 1,
    }
