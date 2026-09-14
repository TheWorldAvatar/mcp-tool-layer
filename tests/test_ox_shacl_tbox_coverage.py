"""SHACL generators must follow T-Box ranges and catch OntoMed datatype slips."""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import OWL, RDF, Graph, URIRef
from rdflib.namespace import SH

OX = Path(__file__).resolve().parents[1] / "src" / "kg_building" / "ontologx"
if str(OX) not in sys.path:
    sys.path.insert(0, str(OX))

from generate_medical_shacl import generate as generate_medical  # noqa: E402
from generate_ontospecies_shacl import generate as generate_species  # noqa: E402
from generate_shacl import generate as generate_ontosyn  # noqa: E402
from run_loop import _assert_medical_shacl_oracle  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ONTOSYN_TBOX = REPO / "data" / "ontologies" / "ontosynthesis.ttl"
ONTOSYN_SHACL = REPO / "src" / "kg_building" / "ontologx" / "resources" / "ontosynthesis_shacl.ttl"
SPECIES_TBOX = REPO / "data" / "ontologies" / "ontospecies-subgraph.ttl"
SPECIES_SHACL = REPO / "src" / "kg_building" / "ontologx" / "resources" / "ontospecies_shacl.ttl"
MEDICAL_TBOX = REPO / "data" / "ontologies" / "medical_case_schema_de_non_flat_v4.ttl"
MEDICAL_SHACL = REPO / "src" / "kg_building" / "ontologx" / "resources" / "medical_shacl.ttl"

ONTOSYN = "https://www.theworldavatar.com/kg/OntoSyn/"
ONTOSPECIES = "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"
MED = "https://www.theworldavatar.com/kg/medical/"

MEDICAL_TTL_PREFIX = """
@prefix medical: <https://www.theworldavatar.com/kg/medical/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix : <https://www.theworldavatar.com/kg/instance/test/> .
"""


def _tbox_properties(tbox_path: Path, ns: str) -> tuple[list[URIRef], list[URIRef]]:
    graph = Graph()
    graph.parse(str(tbox_path), format="turtle")
    objects = []
    datatypes = []
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        if isinstance(prop, URIRef) and str(prop).startswith(ns):
            objects.append(prop)
    for prop in graph.subjects(RDF.type, OWL.DatatypeProperty):
        if isinstance(prop, URIRef) and str(prop).startswith(ns):
            datatypes.append(prop)
    return objects, datatypes


def _shacl_paths(shacl: Graph) -> tuple[set[str], set[str], set[str]]:
    paths: set[str] = set()
    typed: set[str] = set()
    classed: set[str] = set()
    for _shape, _pred, bn in shacl.triples((None, SH.property, None)):
        path = shacl.value(bn, SH.path)
        if path is None:
            continue
        iri = str(path)
        paths.add(iri)
        if shacl.value(bn, SH.datatype) is not None or list(shacl.objects(bn, SH["or"])):
            typed.add(iri)
        if shacl.value(bn, SH["class"]) is not None or shacl.value(bn, SH.nodeKind) is not None:
            classed.add(iri)
    return paths, typed, classed


def _assert_tbox_covered(tbox_path: Path, shacl_path: Path, ns: str) -> None:
    shacl = Graph()
    shacl.parse(str(shacl_path), format="turtle")
    paths, typed, classed = _shacl_paths(shacl)
    objects, datatypes = _tbox_properties(tbox_path, ns)
    missing_obj = [str(prop) for prop in objects if str(prop) not in paths]
    missing_obj_class = [str(prop) for prop in objects if str(prop) not in classed]
    missing_dt = [str(prop) for prop in datatypes if str(prop) not in paths]
    missing_dt_type = [str(prop) for prop in datatypes if str(prop) not in typed]
    assert missing_obj == [], missing_obj
    assert missing_obj_class == [], missing_obj_class
    assert missing_dt == [], missing_dt
    assert missing_dt_type == [], missing_dt_type


def _conforms(data_turtle: str) -> bool:
    import pyshacl

    data = Graph()
    data.parse(data=data_turtle, format="turtle")
    ont = Graph()
    ont.parse(str(MEDICAL_TBOX), format="turtle")
    shacl = Graph()
    shacl.parse(str(MEDICAL_SHACL), format="turtle")
    conforms, _results_graph, _results_text = pyshacl.validate(
        data,
        shacl_graph=shacl,
        ont_graph=ont,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
    )
    return bool(conforms)


def _pipeline_style_graph(*, rats, alter) -> str:
    rats_ttl = rats
    alter_ttl = alter
    return f"""{MEDICAL_TTL_PREFIX}
:case a medical:MedicalCase ;
  rdfs:label "case-1" ;
  medical:hasPatientInfo :patient ;
  medical:hasSurgicalApproach :approach .

:patient a medical:PatientInfo ;
  medical:Alter {alter_ttl} .

:approach a medical:SurgicalApproach ;
  medical:RATS {rats_ttl} .
"""


def test_ontosyn_shacl_covers_tbox_ranges() -> None:
    _assert_tbox_covered(ONTOSYN_TBOX, ONTOSYN_SHACL, ONTOSYN)
    generated = generate_ontosyn()
    committed = ONTOSYN_SHACL.read_text(encoding="utf-8")
    assert generated == committed


def test_ontospecies_shacl_covers_tbox_ranges() -> None:
    _assert_tbox_covered(SPECIES_TBOX, SPECIES_SHACL, ONTOSPECIES)
    assert generate_species() == SPECIES_SHACL.read_text(encoding="utf-8")


def test_medical_shacl_is_generated_from_tbox() -> None:
    generated = generate_medical()
    committed = MEDICAL_SHACL.read_text(encoding="utf-8")
    assert generated == committed
    _assert_tbox_covered(MEDICAL_TBOX, MEDICAL_SHACL, MED)
    assert "sh:datatype xsd:integer" in committed
    assert 'sh:in ( "1"^^xsd:string )' in committed
    assert "ExclusiveSurgicalApproachShape" in committed
    assert "medical:MedicalCaseShape" in committed


def test_medical_shacl_rejects_boolean_and_string_integer_literals() -> None:
    assert _conforms(
        _pipeline_style_graph(rats='"1"^^xsd:string', alter='"70"^^xsd:integer')
    )
    assert not _conforms(
        _pipeline_style_graph(rats="true", alter='"70"^^xsd:integer')
    )
    assert not _conforms(
        _pipeline_style_graph(rats='"1"^^xsd:integer', alter='"70"^^xsd:integer')
    )
    assert not _conforms(
        _pipeline_style_graph(rats='"1"^^xsd:string', alter='"70"^^xsd:string')
    )


def test_medical_shacl_rejects_exclusive_approach_clash() -> None:
    turtle = f"""{MEDICAL_TTL_PREFIX}
:case a medical:MedicalCase ;
  rdfs:label "case-1" ;
  medical:hasSurgicalApproach :approach .

:approach a medical:SurgicalApproach ;
  medical:offen "1"^^xsd:string ;
  medical:VATS "1"^^xsd:string .
"""
    assert not _conforms(turtle)


def test_medical_shacl_allows_omitted_false_checklist() -> None:
    turtle = f"""{MEDICAL_TTL_PREFIX}
:case a medical:MedicalCase ;
  rdfs:label "case-1" ;
  medical:hasSurgicalApproach :approach .

:approach a medical:SurgicalApproach ;
  medical:RATS "1"^^xsd:string .
"""
    assert _conforms(turtle)


def test_medical_ox_refuses_label_only_stub(tmp_path: Path) -> None:
    stub = tmp_path / "medical_shacl.ttl"
    stub.write_text(
        """@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix medical: <https://www.theworldavatar.com/kg/medical/> .

medical:MedicalCaseShape a sh:NodeShape ;
  sh:targetClass medical:MedicalCase ;
  sh:property [
    sh:path rdfs:label ;
    sh:minCount 1
  ] .
""",
        encoding="utf-8",
    )
    try:
        _assert_medical_shacl_oracle(stub)
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    _assert_medical_shacl_oracle(MEDICAL_SHACL)
    source = (REPO / "src" / "kg_building" / "ontologx" / "run_loop.py").read_text(
        encoding="utf-8"
    )
    assert "write_medical_shapes" in source
    assert "_assert_medical_shacl_oracle" in source


def test_ttl_export_boolean_rats_fails_medical_shacl() -> None:
    from graph_types import GraphDocument, Node, Relationship
    from shacl_validate import validate_graph

    case = Node(id="case", type="medical:MedicalCase", properties={"rdfs:label": "case-1"})
    approach = Node(id="approach", type="medical:SurgicalApproach", properties={"medical:RATS": True})
    graph = GraphDocument(
        nodes=[case, approach],
        relationships=[Relationship(source=case, target=approach, type="medical:hasSurgicalApproach")],
        source=None,
    )
    conforms, _messages, _ratio = validate_graph(
        graph,
        ontology_path=MEDICAL_TBOX,
        shacl_path=MEDICAL_SHACL,
        paper_hash="testhash",
    )
    assert conforms is False

    approach.properties["medical:RATS"] = "1"
    conforms_ok, _messages_ok, _ratio_ok = validate_graph(
        graph,
        ontology_path=MEDICAL_TBOX,
        shacl_path=MEDICAL_SHACL,
        paper_hash="testhash",
    )
    assert conforms_ok is True
