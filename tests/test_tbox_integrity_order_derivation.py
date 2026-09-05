from __future__ import annotations

from src.agents.scripts_and_prompts_generation.ttl_parser import (
    extract_ontology_integrity_profile,
    parse_ontology_ttl,
)


def test_ordered_member_contract_is_derived_from_tbox_semantics(tmp_path) -> None:
    tbox = tmp_path / "ordered.ttl"
    tbox.write_text(
        """
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Container a owl:Class .
ex:Member a owl:Class .
ex:ConcreteMember a owl:Class ; rdfs:subClassOf ex:Member .
ex:hasMember a owl:ObjectProperty ;
  rdfs:domain ex:Container ;
  rdfs:range ex:Member ;
  rdfs:comment "Members are ordered using ex:sequenceIndex." .
ex:sequenceIndex a owl:DatatypeProperty ;
  rdfs:domain ex:Member ;
  rdfs:range xsd:integer ;
  rdfs:comment "Integer order index in the sequence; contiguous with no gaps." .
""",
        encoding="utf-8",
    )

    profile = extract_ontology_integrity_profile(str(tbox))

    assert profile["ordered_member_classes"] == ["ConcreteMember", "Member"]
    assert profile["single_valued_ordering_properties"] == ["sequenceIndex"]
    assert profile["individually_linked_object_properties"] == ["hasMember"]
    assert profile["parent_type_preserving_classes"] == ["ConcreteMember"]


def test_anonymous_restrictions_do_not_leak_random_blank_node_ids(tmp_path) -> None:
    tbox = tmp_path / "restriction.ttl"
    tbox.write_text(
        """
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Root a owl:Class ;
  rdfs:subClassOf [
    a owl:Restriction ;
    owl:onProperty ex:requiredName ;
    owl:minCardinality "1"^^xsd:nonNegativeInteger
  ] .
ex:requiredName a owl:DatatypeProperty .
""",
        encoding="utf-8",
    )

    first = parse_ontology_ttl(str(tbox))
    second = parse_ontology_ttl(str(tbox))

    assert first["classes"]["Root"]["parent_classes"] == []
    assert first == second
