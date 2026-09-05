from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Literal, URIRef
from rdflib.namespace import RDF, XSD

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from generate_shacl import generate  # noqa: E402
from graph_types import Document, GraphDocument, Node  # noqa: E402
from shacl_validate import validate_graph  # noqa: E402
from ttl_export import OM2, graph_to_rdflib  # noqa: E402


def test_generated_shacl_requires_step_labels() -> None:
    text = generate()
    for shape in ("ontosyn:AddShape", "ontosyn:HeatChillShape", "ontosyn:SynthesisStepShape"):
        block = text.split(f"{shape} a sh:NodeShape ;", 1)[1].split("\n\n", 1)[0]
        assert "sh:path rdfs:label ;\n    sh:datatype xsd:string ;\n    sh:minCount 1 ;\n    sh:maxCount 1" in block


def test_generated_shacl_requires_has_unit_iri() -> None:
    text = generate()
    assert "sh:path om-2:hasUnit ;\n    sh:datatype xsd:string" not in text
    assert "sh:path om-2:hasUnit ;\n    sh:class om-2:TemperatureRateUnit ;\n    sh:nodeKind sh:IRI" in text
    assert "om-2:degreeCelsiusPerHour" in text
    assert "NumericMeasureNeedsIriUnitShape" in text


def test_ontologx_export_writes_om2_unit_iri() -> None:
    graph_doc = GraphDocument(
        nodes=[
            Node(
                id="trate1",
                type="om-2:TemperatureRate",
                properties={
                    "rdfs:label": "10 degC h-1",
                    "om-2:hasNumericalValue": 10.0,
                    "om-2:hasUnit": "degC h-1",
                },
            )
        ],
        relationships=[],
        source=Document(page_content=""),
    )
    rdf = graph_to_rdflib(graph_doc, "1b9180ec")
    rate = next(rdf.subjects(RDF.type, OM2.TemperatureRate))
    assert list(rdf.objects(rate, OM2.hasUnit)) == [OM2.degreeCelsiusPerHour]
    assert not any(
        isinstance(value, Literal) and value.datatype == XSD.string
        for value in rdf.objects(rate, OM2.hasUnit)
    )


def test_ontologx_export_keeps_unknown_unit_as_literal() -> None:
    graph_doc = GraphDocument(
        nodes=[
            Node(
                id="temp1",
                type="om-2:Temperature",
                properties={"om-2:hasNumericalValue": 25.0, "om-2:hasUnit": "not-a-unit"},
            )
        ],
        relationships=[],
        source=Document(page_content=""),
    )
    rdf = graph_to_rdflib(graph_doc, "1b9180ec")
    temp = next(rdf.subjects(RDF.type, OM2.Temperature))
    values = list(rdf.objects(temp, OM2.hasUnit))
    assert len(values) == 1
    assert isinstance(values[0], Literal)
    assert str(values[0]) == "not-a-unit"
    assert not isinstance(values[0], URIRef)


def test_shacl_accepts_om2_minute_curie() -> None:
    graph_doc = GraphDocument(
        nodes=[
            Node(
                id="d1",
                type="om-2:Duration",
                properties={
                    "rdfs:label": "30 min",
                    "om-2:hasNumericalValue": 30.0,
                    "om-2:hasUnit": "om2:minute",
                },
            )
        ],
        relationships=[],
        source=Document(page_content=""),
    )
    conforms, messages, _ratio = validate_graph(
        graph_doc,
        REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl",
        ADAPTER / "resources" / "ontosynthesis_shacl.ttl",
        "1b9180ec",
    )
    assert conforms, messages
