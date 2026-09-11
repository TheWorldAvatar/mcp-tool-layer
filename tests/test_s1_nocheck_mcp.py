"""Paper-faithful ablation: semantic checks off, agent inputs accepted."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from rdflib import RDF, RDFS, URIRef

REPO = Path(r"D:\MCP-enhanced-MOPs-Extraction_clean-v2")
ABLATED = REPO / "generated" / "runs" / "0908-fullpack-s1_newmcp_nocheck"
SCRIPTS = ABLATED / "scripts"
ONTOSYN = SCRIPTS / "ontosynthesis"
DURATION = "http://www.ontology-of-units-of-measure.org/resource/om-2/Duration"
ADD = "https://www.theworldavatar.com/kg/OntoSyn/Add"
VESSEL = "https://www.theworldavatar.com/kg/OntoSyn/Vessel"
HAS_ORDER = "https://www.theworldavatar.com/kg/OntoSyn/hasOrder"
HAS_STEP = "https://www.theworldavatar.com/kg/OntoSyn/hasSynthesisStep"
HAS_ADDED = "https://www.theworldavatar.com/kg/OntoSyn/hasAddedChemicalInput"


pytestmark = pytest.mark.skipif(
    not (ONTOSYN / "_constraint_feedback_ablation.py").is_file(),
    reason="run generated/campaigns/make_s1_nocheck_mcp.py first",
)


@pytest.fixture(scope="module")
def ontosyn(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("nck_mcp")
    os.environ["TWA_AGENTIC_DATA_DIR"] = str(data_dir)
    os.environ.pop("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", None)
    sys.path.insert(0, str(SCRIPTS))
    for key in list(sys.modules):
        if key == "ontosynthesis" or key.startswith("ontosynthesis."):
            del sys.modules[key]
    from ontosynthesis import (  # type: ignore
        _fixed_rdf_runtime as rdf_runtime,
        ontosynthesis_creation_entities as entities,
        ontosynthesis_creation_relationships as relationships,
        ontosynthesis_occurrence_operations as operations,
    )

    return rdf_runtime, entities, operations, relationships


def _parsed(payload: str) -> dict:
    return json.loads(payload)


def _init(rdf_runtime, root: str) -> None:
    _parsed(
        rdf_runtime.init_memory(
            doi="10.nck/ablation",
            top_level_entity_name="UMC-1",
            root_iri=root,
        )
    )


def test_error_json_is_not_genericized(ontosyn) -> None:
    rdf_runtime, *_ = ontosyn
    payload = _parsed(
        rdf_runtime.error_json(
            code="INVALID_ORDER",
            message="Ordered members must be contiguous 1..N",
            recovery={"action": "renumber"},
        )
    )
    assert payload["code"] == "INVALID_ORDER"
    assert payload["message"] != "Tool call failed."


def test_invalid_order_is_written_and_export_keeps_it(ontosyn) -> None:
    rdf_runtime, entities, operations, _ = ontosyn
    root = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis_nck"
    _init(rdf_runtime, root)
    created = _parsed(entities.create_Add(label="Add DMF", hasOrder=0, parent_iri=root))
    assert created["status"] == "ok"
    iri = URIRef(created["iri"])
    graph = rdf_runtime.retained_graph()
    assert (iri, RDF.type, URIRef(ADD)) in graph
    assert 0 in [value.toPython() for value in graph.objects(iri, URIRef(HAS_ORDER))]

    public = _parsed(operations.create_Add(label="Add extra", parent_iri=root, hasOrder=-3))
    assert public["status"] == "ok"
    assert public.get("code") != "ERROR"

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
    assert export.get("ablation") == "no-constraint-checks"
    assert 0 in [value.toPython() for value in graph.objects(iri, URIRef(HAS_ORDER))]


def test_empty_label_is_accepted(ontosyn) -> None:
    rdf_runtime, entities, _, _ = ontosyn
    root = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis_nck_label"
    _init(rdf_runtime, root)
    created = _parsed(entities.create_Add(label="   ", hasOrder=2, parent_iri=root))
    assert created["status"] == "ok"
    assert created.get("iri")
    graph = rdf_runtime.retained_graph()
    iri = URIRef(created["iri"])
    assert (iri, RDF.type, URIRef(ADD)) in graph


def test_domain_mismatch_still_writes_triple(ontosyn) -> None:
    rdf_runtime, entities, _, relationships = ontosyn
    root = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis_nck_rel"
    _init(rdf_runtime, root)
    step = _parsed(entities.create_Add(label="Add solvent", hasOrder=1, parent_iri=root))
    vessel = _parsed(entities.create_Vessel(label="flask"))
    assert step["status"] == "ok"
    assert vessel["status"] == "ok"
    linked = _parsed(
        relationships.add_hasAddedChemicalInput(step["iri"], vessel["iri"])
    )
    assert linked.get("status") in {"ok", "error"}
    assert linked.get("code") != "DOMAIN_TYPE_MISMATCH"
    graph = rdf_runtime.retained_graph()
    assert (
        URIRef(step["iri"]),
        URIRef(HAS_ADDED),
        URIRef(vessel["iri"]),
    ) in graph
    assert (URIRef(vessel["iri"]), RDF.type, URIRef(VESSEL)) in graph


def test_invalid_om2_quantity_is_minted(ontosyn) -> None:
    rdf_runtime, _, operations, _ = ontosyn
    payload = _parsed(rdf_runtime.create_om2_quantity(DURATION, "not-a-real-unit"))
    assert payload["status"] == "ok"
    assert payload.get("code") != "INVALID_OM2_QUANTITY"
    graph = rdf_runtime.retained_graph()
    iri = URIRef(payload["iri"])
    assert (iri, RDF.type, URIRef(DURATION)) in graph
    assert "not-a-real-unit" in [str(value) for value in graph.objects(iri, RDFS.label)]
    assert operations._try_attach_quantity(
        "https://example.test/step",
        "hasStepDuration",
        "https://www.theworldavatar.com/kg/OntoSyn/hasStepDuration",
        DURATION,
        "also-invalid",
    ) is None
