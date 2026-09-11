"""Constraint-feedback ablation on the copied s1 OntoSynthesis MCP."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from rdflib import RDF, RDFS, URIRef

REPO = Path(r"D:\MCP-enhanced-MOPs-Extraction_clean-v2")
ABLATED = REPO / "generated" / "runs" / "0908-fullpack-s1_newmcp_nofeedback"
SCRIPTS = ABLATED / "scripts"
ONTOSYN = SCRIPTS / "ontosynthesis"
DURATION = "http://www.ontology-of-units-of-measure.org/resource/om-2/Duration"
ADD = "https://www.theworldavatar.com/kg/OntoSyn/Add"
HAS_ORDER = "https://www.theworldavatar.com/kg/OntoSyn/hasOrder"
HAS_STEP = "https://www.theworldavatar.com/kg/OntoSyn/hasSynthesisStep"


pytestmark = pytest.mark.skipif(
    not (ONTOSYN / "_constraint_feedback_ablation.py").is_file(),
    reason="run generated/campaigns/make_s1_nofeedback_mcp.py first",
)


@pytest.fixture(scope="module")
def ontosyn(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("nfb_mcp")
    os.environ["TWA_AGENTIC_DATA_DIR"] = str(data_dir)
    os.environ.pop("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", None)
    sys.path.insert(0, str(SCRIPTS))
    for key in list(sys.modules):
        if key == "ontosynthesis" or key.startswith("ontosynthesis."):
            del sys.modules[key]
    from ontosynthesis import (  # type: ignore
        _fixed_rdf_runtime as rdf_runtime,
        ontosynthesis_creation_entities as entities,
        ontosynthesis_occurrence_operations as operations,
    )

    return rdf_runtime, entities, operations


def _parsed(payload: str) -> dict:
    return json.loads(payload)


def test_error_json_is_generic_only(ontosyn) -> None:
    rdf_runtime, _, _ = ontosyn
    payload = _parsed(
        rdf_runtime.error_json(
            code="INVALID_ORDER",
            message="Ordered members must be contiguous 1..N",
            recovery={"action": "renumber"},
            skippable=True,
        )
    )
    assert payload == {
        "status": "rejected",
        "code": "ERROR",
        "message": "Tool call failed.",
    }


def test_invalid_order_still_writes_and_export_does_not_repair(ontosyn) -> None:
    rdf_runtime, entities, _ = ontosyn
    root = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis_nfb"
    _parsed(
        rdf_runtime.init_memory(
            doi="10.nfb/ablation",
            top_level_entity_name="UMC-1",
            root_iri=root,
        )
    )
    created = _parsed(
        entities.create_Add(label="Add DMF", hasOrder=0, parent_iri=root)
    )
    assert created["status"] == "ok"
    iri = URIRef(created["iri"])
    graph = rdf_runtime.retained_graph()
    assert (iri, RDF.type, URIRef(ADD)) in graph
    orders = [value.toPython() for value in graph.objects(iri, URIRef(HAS_ORDER))]
    assert 0 in orders
    export = rdf_runtime.prepare_graph_for_export(
        {
            "Add": {
                "class_iri": ADD,
                "parent_predicate_iri": HAS_STEP,
                "ordering_property_iri": HAS_ORDER,
            }
        }
    )
    assert export["status"] == "ok"
    assert export.get("ablation") == "no-constraint-feedback"
    still = [value.toPython() for value in graph.objects(iri, URIRef(HAS_ORDER))]
    assert still == orders


def test_invalid_om2_quantity_does_not_return_unit_feedback(ontosyn) -> None:
    rdf_runtime, _, operations = ontosyn
    payload = _parsed(
        rdf_runtime.create_om2_quantity(DURATION, "not-a-real-unit")
    )
    assert payload["status"] == "ok"
    assert "Unsupported OM-2 unit" not in json.dumps(payload)
    assert payload.get("code") != "INVALID_OM2_QUANTITY"
    graph = rdf_runtime.retained_graph()
    iri = URIRef(payload["iri"])
    assert (iri, RDF.type, URIRef(DURATION)) in graph
    labels = [str(value) for value in graph.objects(iri, RDFS.label)]
    assert "not-a-real-unit" in labels

    warning = operations._try_attach_quantity(
        "https://example.test/step",
        "hasStepDuration",
        "https://www.theworldavatar.com/kg/OntoSyn/hasStepDuration",
        DURATION,
        "also-invalid",
    )
    assert warning is None
