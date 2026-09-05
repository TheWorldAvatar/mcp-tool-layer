from pathlib import Path

from rdflib import RDF, RDFS, Graph, Literal, URIRef

from src.pipelines.top_entity_kg_building.build import (
    _seed_iter1_top_entity_lock,
    bind_iter1_runtime_context,
)


def test_iter1_without_slots_gets_complete_bootstrap_boundary() -> None:
    rendered = bind_iter1_runtime_context(
        "Build top roots.",
        doi_hash="paper-doi",
        paper_content="Paper text.",
        top_entities="Alpha\nBeta",
    )

    assert "PIPELINE-INJECTED ITER1 BOOTSTRAP CONTEXT" in rendered
    assert "Document DOI/hash: paper-doi" in rendered
    assert "Paper/source text:\nPaper text." in rendered
    assert "Upstream top-entity labels:\nAlpha\nBeta" in rendered
    assert "Never invent one shared entity IRI for multiple labels" in rendered


def test_iter1_declared_slots_are_bound_once_but_boundary_is_retained() -> None:
    rendered = bind_iter1_runtime_context(
        "{doi}\n{paper_content}\n{top_entities}",
        doi_hash="paper-doi",
        paper_content="Paper text.",
        top_entities="Alpha\nBeta",
    )

    assert rendered.count("paper-doi") == 1
    assert rendered.count("Paper text.") == 1
    assert rendered.count("Alpha\nBeta") == 1
    assert "Bootstrap rules:" in rendered
    assert "Document DOI/hash:" not in rendered
    assert "Paper/source text:" not in rendered
    assert "Upstream top-entity labels:" not in rendered


def test_iter1_lock_preseeds_one_deterministic_document_per_doi(
    tmp_path: Path,
) -> None:
    doi = "case-hash"
    top_class = "https://example.test/ChemicalSynthesis"
    entities = [
        {
            "label": "Product A",
            "uri": "https://example.test/synthesis/a",
        },
        {
            "label": "Product B",
            "uri": "https://example.test/synthesis/b",
        },
    ]

    _seed_iter1_top_entity_lock(
        doi_hash=doi,
        doi_folder=str(tmp_path / doi),
        top_entities=entities,
        top_class_iri=top_class,
        entity_context_name="top",
        entity_context_aliases=["top"],
        seed_doi_document=True,
    )

    top = Graph()
    top.parse(tmp_path / doi / "memory" / "top.ttl", format="turtle")
    document = Graph()
    document.parse(
        tmp_path / doi / "memory" / "document.ttl", format="turtle"
    )
    document_class = URIRef("http://purl.org/ontology/bibo/Document")
    document_nodes = set(document.subjects(RDF.type, document_class))

    assert len(document_nodes) == 1
    document_node = next(iter(document_nodes))
    assert (document_node, RDFS.label, Literal(doi)) in document
    assert (document_node, RDF.type, document_class) in top
