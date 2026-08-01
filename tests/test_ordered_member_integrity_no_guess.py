from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.pipelines.utils.ordered_member_integrity import (
    enforce_ordered_member_integrity,
)


def test_missing_orders_are_reported_without_guessing() -> None:
    graph = Graph()
    parent = URIRef("https://example.test/parent")
    member = URIRef("https://example.test/member")
    parent_class = URIRef("https://example.test/Procedure")
    member_class = URIRef("https://example.test/Step")
    collection = URIRef("https://example.test/hasStep")
    order = URIRef("https://example.test/hasOrder")
    graph.add((parent, RDF.type, parent_class))
    graph.add((member, RDF.type, member_class))
    graph.add((member, RDFS.label, Literal("Step 7")))
    graph.add((parent, collection, member))
    profile = {
        "ordered_collection_contracts": [
            {
                "collection_property_iri": str(collection),
                "ordering_property_iris": [str(order)],
                "member_class_iri": str(member_class),
                "member_class_descendant_iris": [str(member_class)],
            }
        ]
    }

    _, report = enforce_ordered_member_integrity(
        graph, profile, top_entity=parent
    )

    assert report["status"] == "failed"
    assert not list(graph.objects(member, order))
    assert any("Cannot infer" in message for message in report["messages"])
