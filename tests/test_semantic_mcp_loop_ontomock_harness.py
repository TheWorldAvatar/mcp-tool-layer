from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontomock import (
    DOMAIN_CONFIG,
    FIXTURE,
    TBOX_PATHS,
    run_harness,
)


ROOT = Path(__file__).resolve().parents[1]
MOCK = Namespace("https://example.test/ontomock/")
EXTERNAL = Namespace("https://example.test/external/")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")


@pytest.fixture(scope="module")
def harness_result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    output_root = tmp_path_factory.mktemp("ontomock_harness")
    result = run_harness(output_root=output_root)
    assert result["ok"], json.dumps(result, indent=2, ensure_ascii=False)
    return result


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def abox(harness_result: dict) -> Graph:
    graph = Graph()
    graph.parse(harness_result["abox"]["abox_path"], format="turtle")
    return graph


def test_tracked_tbox_bundle_and_iri_provenance(harness_result: dict) -> None:
    config = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    assert config["tbox"] == {
        "primary": "tests/fixtures/tbox/ontomock.ttl",
        "supporting": ["tests/fixtures/tbox/om2_mock.ttl"],
    }
    for path in TBOX_PATHS:
        assert path.is_file()
        assert path.is_relative_to(ROOT)

    bundle = harness_result["tbox_bundle"]
    assert bundle["schema_version"] == "iri-aware-tbox-bundle.v1"
    assert bundle["identity_key"] == "absolute_iri"
    assert bundle["local_name_merge"] is False
    assert bundle["primary"]["sha256"] == hashlib.sha256(
        TBOX_PATHS[0].read_bytes()
    ).hexdigest()
    assert bundle["supporting"][0]["sha256"] == hashlib.sha256(
        TBOX_PATHS[1].read_bytes()
    ).hexdigest()
    assert OM2.Duration in {
        URIRef(iri)
        for iri in bundle["supporting"][0]["iri_inventory"]["class_iris"]
    }
    assert bundle["reasoner_paths"] == [str(path.resolve()) for path in TBOX_PATHS]


def test_reuse_review_covers_metric_without_schema_only_creators() -> None:
    review_path = ROOT / "configs" / "meta_task" / "ontomock_reuse_review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    decisions = {
        item["class_local"]: item for item in review.get("classes") or []
    }
    assert decisions["ExternalMetric"]["reusable"] is True
    assert decisions["ExternalMetric"]["reuse_scope"] == "document"
    assert "ActionBase" not in decisions
    assert "ProhibitedType" not in decisions


def test_fixture_is_minimal_complete_and_source_grounded(fixture_data: dict) -> None:
    run = fixture_data["process_run"]
    source = fixture_data["document_md"]
    assert len(run["actions"]) == 2
    assert [item["class_local"] for item in run["actions"]] == ["DoStep", "DoAlt"]
    assert [item["order"] for item in run["actions"]] == [1, 2]
    assert run["actions"][0]["is_enabled"] is True
    assert run["actions"][0]["duration"]["label"] == "30 minute"
    assert len(run["action_inputs"]) == 1
    assert run["actions"][1]["uses_input"] == run["foundation_input"]["label"]
    for value in (
        run["label"],
        run["foundation_input"]["label"],
        run["foundation_input"]["alias"],
        run["output"]["label"],
        run["output"]["title"],
        run["source_doc"]["label"],
        run["vendor"]["label"],
        run["tool"]["label"],
        run["external_metric"]["label"],
        *(item["label"] for item in run["action_inputs"]),
    ):
        assert value in source
    assert set(fixture_data["coverage"]) == {
        "ProcessRun",
        "Input",
        "Output",
        "SourceDoc",
        "Vendor",
        "DoStep",
        "DoAlt",
        "Tool",
        "Duration",
        "ExternalMetric",
    }


def test_abox_foundation_and_relationship_directions(
    abox: Graph,
    harness_result: dict,
) -> None:
    iris = harness_result["abox"]["iris"]
    top = URIRef(iris["process_run"])
    foundation = URIRef(iris["foundation_input"])
    output = URIRef(iris["output"])
    source = URIRef(iris["source_doc"])
    vendor = URIRef(iris["vendor"])
    metric = URIRef(iris["external_metric"])

    assert list(abox.subjects(RDF.type, MOCK.ProcessRun)) == [top]
    assert (top, MOCK.hasInput, foundation) in abox
    assert len(list(abox.objects(top, MOCK.hasInput))) >= 1
    assert (foundation, MOCK.suppliedBy, vendor) in abox
    assert (vendor, MOCK.suppliedBy, foundation) not in abox
    assert (top, MOCK.hasOutput, output) in abox
    assert (top, MOCK.retrievedFrom, source) in abox
    assert (top, MOCK.hasMetric, metric) in abox
    assert (metric, RDF.type, EXTERNAL.ExternalMetric) in abox
    assert (foundation, MOCK.hasAlias, Literal("A-1")) in abox
    assert (output, MOCK.hasTitle, Literal("Final product")) in abox


def test_ordered_actions_step_local_values_and_duration(
    abox: Graph,
    harness_result: dict,
) -> None:
    iris = harness_result["abox"]["iris"]
    top = URIRef(iris["process_run"])
    step, alt = (URIRef(value) for value in iris["actions"])
    tool = URIRef(iris["tool"])
    duration = URIRef(iris["durations"][0])

    assert (step, RDF.type, MOCK.DoStep) in abox
    assert (alt, RDF.type, MOCK.DoAlt) in abox
    assert {(top, MOCK.hasAction, step), (top, MOCK.hasAction, alt)} <= set(abox)
    assert (step, MOCK.hasAction, top) not in abox
    assert (alt, MOCK.hasAction, top) not in abox

    orders = []
    for action in (step, alt):
        values = list(abox.objects(action, MOCK.hasOrder))
        assert len(values) == 1
        assert values[0].datatype == XSD.integer
        orders.append(int(values[0]))
        assert len(list(abox.objects(action, MOCK.usesInput))) == 1
        assert (action, MOCK.usesTool, tool) in abox
    assert orders == [1, 2]

    enabled = list(abox.objects(step, MOCK.isEnabled))
    assert enabled == [Literal(True, datatype=XSD.boolean)]
    assert list(abox.objects(alt, MOCK.isEnabled)) == []
    assert (step, MOCK.hasDuration, duration) in abox
    assert (duration, RDF.type, OM2.Duration) in abox
    assert list(abox.objects(duration, OM2.hasNumericalValue)) == [
        Literal(30.0, datatype=XSD.double)
    ]
    assert list(abox.objects(duration, OM2.hasUnit)) == [OM2.minute]


def test_no_abstract_only_or_prohibited_instances(abox: Graph) -> None:
    assert list(abox.subjects(RDF.type, MOCK.ProhibitedType)) == []
    concrete_actions = set(abox.subjects(RDF.type, MOCK.DoStep)) | set(
        abox.subjects(RDF.type, MOCK.DoAlt)
    )
    action_base_instances = set(abox.subjects(RDF.type, MOCK.ActionBase))
    assert action_base_instances
    assert action_base_instances <= concrete_actions
    assert not any(
        (subject, RDFS.label, None) in abox and subject not in concrete_actions
        for subject in action_base_instances
    )


def test_primary_creator_contract_and_full_bundle_reasoner(
    harness_result: dict,
) -> None:
    bundle_contract = harness_result["tbox_bundle"]
    assert len(bundle_contract["reasoner_paths"]) == 2
    reasoner = harness_result["reasoner"]
    assert reasoner["tbox_paths"] == [str(path) for path in TBOX_PATHS]
    assert reasoner["ok"] is True
    assert reasoner["triples"]["inferred"] > 0
    assert reasoner["details"] == {
        "unknown_types": [],
        "unknown_properties": [],
        "domain_violations": [],
        "range_violations": [],
        "om2_quantity_violations": [],
    }

    contract_path = (
        Path(harness_result["abox"]["abox_path"]).parent
        / "ontology_structures"
        / "ontomock"
        / "generation_contract.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    duration = contract["relationship_tool_contracts"]["hasDuration"]
    metric = contract["relationship_tool_contracts"]["hasMetric"]
    assert duration["target_handling"] == "fixed_runtime_creator"
    assert duration["creator_tools"] == ["create_om2_quantity"]
    assert metric["creator_tools"] == ["create_ExternalMetric"]
    assert metric["target_handling"] == "generated_creator"
