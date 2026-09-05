from __future__ import annotations

from pathlib import Path

from rdflib import Literal, URIRef
from rdflib.namespace import RDF, XSD

from scripts.tbox_json_kg.compiler import TBoxCompiler
from scripts.tbox_json_kg.materializer import CanonicalJsonMaterializer


TOY_TBOX = """
@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Parent a owl:Class .
ex:Child a owl:Class .
ex:hasChild a owl:ObjectProperty ;
  rdfs:domain ex:Parent ;
  rdfs:range ex:Child .
ex:hasCount a owl:DatatypeProperty ;
  rdfs:domain ex:Child ;
  rdfs:range xsd:integer .
"""


def test_compiler_and_materializer_are_tbox_driven(tmp_path: Path) -> None:
    tbox = tmp_path / "toy.ttl"
    tbox.write_text(TOY_TBOX, encoding="utf-8")
    compiler = TBoxCompiler([tbox])
    bundle = compiler.compile(
        ["https://example.org/Parent", "https://example.org/Child"],
        reference_only=True,
    )

    parent_def = bundle["classes"]["ex:Parent"]["definition"]
    parent_properties = bundle["json_schema"]["schema"]["$defs"][parent_def][
        "properties"
    ]
    assert "ex:hasChild" in parent_properties
    assert "ex:hasCount" not in parent_properties

    document = {
        "roots": [
            {
                "@id": "parent-1",
                "@type": "ex:Parent",
                "rdfs:label": ["parent"],
                "ex:hasChild": [{"@id": "child-1"}],
            },
            {
                "@id": "child-1",
                "@type": "ex:Child",
                "rdfs:label": ["child"],
                "ex:hasCount": [2],
            },
        ]
    }
    materializer = CanonicalJsonMaterializer(
        bundle, "https://example.org/generated/test/"
    )
    graph = materializer.materialize(document)

    parent = URIRef("https://example.org/generated/test/parent-1")
    child = URIRef("https://example.org/generated/test/child-1")
    assert (parent, RDF.type, URIRef("https://example.org/Parent")) in graph
    assert (parent, URIRef("https://example.org/hasChild"), child) in graph
    assert (
        child,
        URIRef("https://example.org/hasCount"),
        Literal(2, datatype=XSD.integer),
    ) in graph


def test_materializer_rejects_dangling_local_reference(
    tmp_path: Path,
) -> None:
    tbox = tmp_path / "toy.ttl"
    tbox.write_text(TOY_TBOX, encoding="utf-8")
    bundle = TBoxCompiler([tbox]).compile(
        ["https://example.org/Parent"], reference_only=True
    )
    document = {
        "roots": [
            {
                "@id": "parent-1",
                "@type": "ex:Parent",
                "rdfs:label": [],
                "ex:hasChild": [{"@id": "missing-child"}],
            }
        ]
    }
    materializer = CanonicalJsonMaterializer(
        bundle, "https://example.org/generated/test/"
    )
    try:
        materializer.materialize(document)
    except ValueError as exc:
        assert "Dangling local @id" in str(exc)
    else:
        raise AssertionError("Expected dangling-reference validation failure")

    dropping_materializer = CanonicalJsonMaterializer(
        bundle, "https://example.org/generated/test/"
    )
    graph = dropping_materializer.materialize(
        document, dangling_policy="drop"
    )
    assert dropping_materializer.dropped_dangling_ids == ["missing-child"]
    assert not list(
        graph.triples(
            (
                None,
                URIRef("https://example.org/hasChild"),
                None,
            )
        )
    )
