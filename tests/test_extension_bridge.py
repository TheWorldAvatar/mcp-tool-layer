from __future__ import annotations

from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from src.pipelines.utils.extension_bridge import apply_extension_bridge


ROOT = Path(__file__).resolve().parents[1]
SPECIES = "https://example.test/output/1"
SPECIES_B = "https://example.test/output/2"
HNMR = "https://example.test/hnmr/1"
MOP = "https://example.test/mop/1"
CBU = "https://example.test/cbu/1"
SPECIES_CLASS = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species"
)
HNMR_CLASS = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#HNMRData"
)
HAS_HNMR = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#hasHNMRData"
)
MOP_CLASS = "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron"
CBU_CLASS = "https://www.theworldavatar.com/kg/ontomops/ChemicalBuildingUnit"
HAS_CBU = "https://www.theworldavatar.com/kg/ontomops/hasChemicalBuildingUnit"
ONTOSPECIES_TBOX = ROOT / "data" / "ontologies" / "ontospecies-subgraph.ttl"
ONTOMOPS_TBOX = ROOT / "data" / "ontologies" / "ontomops-subgraph.ttl"


def _ttl(*triples: str) -> str:
    return "\n".join(["@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .", *triples])


def test_bridge_seeds_bound_iri_and_attaches_unlinked_range_object() -> None:
    content = _ttl(
        f"<{HNMR}> a <{HNMR_CLASS}> ."
    )
    targets = [{"target_iri": SPECIES, "class_iri": SPECIES_CLASS}]

    finalized, report = apply_extension_bridge(
        content,
        targets,
        tbox_path=str(ONTOSPECIES_TBOX),
    )

    assert report["status"] == "applied"
    assert SPECIES in report["seed"]["seeded"]
    assert report["attach"]["attached"] == 1
    graph = Graph().parse(data=finalized, format="turtle")
    assert (URIRef(SPECIES), RDF.type, URIRef(SPECIES_CLASS)) in graph
    assert (URIRef(SPECIES), URIRef(HAS_HNMR), URIRef(HNMR)) in graph


def test_bridge_skips_attach_when_bound_class_is_ambiguous() -> None:
    content = _ttl(
        f"<{HNMR}> a <{HNMR_CLASS}> ."
    )
    targets = [
        {"target_iri": SPECIES, "class_iri": SPECIES_CLASS},
        {"target_iri": SPECIES_B, "class_iri": SPECIES_CLASS},
    ]

    finalized, report = apply_extension_bridge(
        content,
        targets,
        tbox_path=str(ONTOSPECIES_TBOX),
    )

    assert report["status"] == "applied"
    assert SPECIES_CLASS in report["attach"]["skipped_ambiguous_class"]
    assert report["attach"]["attached"] == 0
    graph = Graph().parse(data=finalized, format="turtle")
    assert (URIRef(SPECIES), RDF.type, URIRef(SPECIES_CLASS)) in graph
    assert (URIRef(SPECIES_B), RDF.type, URIRef(SPECIES_CLASS)) in graph
    assert (URIRef(SPECIES), URIRef(HAS_HNMR), URIRef(HNMR)) not in graph
    assert (URIRef(SPECIES_B), URIRef(HAS_HNMR), URIRef(HNMR)) not in graph


def test_bridge_attaches_unlinked_cbu_to_unique_bound_mop() -> None:
    content = _ttl(
        f"<{CBU}> a <{CBU_CLASS}> ."
    )
    targets = [{"target_iri": MOP, "class_iri": MOP_CLASS}]

    finalized, report = apply_extension_bridge(
        content,
        targets,
        tbox_path=str(ONTOMOPS_TBOX),
    )

    assert report["status"] == "applied"
    assert MOP in report["seed"]["seeded"]
    assert report["attach"]["attached"] == 1
    assert HAS_CBU in report["attach"]["used_properties"]
    graph = Graph().parse(data=finalized, format="turtle")
    assert (URIRef(MOP), RDF.type, URIRef(MOP_CLASS)) in graph
    assert (URIRef(MOP), URIRef(HAS_CBU), URIRef(CBU)) in graph


def test_bridge_does_not_duplicate_existing_link() -> None:
    content = _ttl(
        f"<{SPECIES}> a <{SPECIES_CLASS}> ;",
        f"    <{HAS_HNMR}> <{HNMR}> .",
        f"<{HNMR}> a <{HNMR_CLASS}> .",
    )
    targets = [{"target_iri": SPECIES, "class_iri": SPECIES_CLASS}]

    finalized, report = apply_extension_bridge(
        content,
        targets,
        tbox_path=str(ONTOSPECIES_TBOX),
    )

    assert report["attach"]["attached"] == 0
    graph = Graph().parse(data=finalized, format="turtle")
    links = list(graph.triples((URIRef(SPECIES), URIRef(HAS_HNMR), URIRef(HNMR))))
    assert len(links) == 1
