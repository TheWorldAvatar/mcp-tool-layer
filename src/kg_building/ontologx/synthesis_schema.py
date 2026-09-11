"""Structured-output tool schema for OntoLogX.

This file only describes the LLM tool shape (allowed node / property /
relationship names). Graph correctness is SHACL plus attach/complete_delta.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from graph_types import Document, GraphDocument, Node as LibNode, Relationship as LibRelationship


class BaseSynthesisGraph(BaseModel):
    nodes: list
    relationships: list
    remove_relationships: list = Field(default_factory=list)

    def graph(self, source_event: str, context: dict) -> GraphDocument:
        nodes_dict = {
            node.id: LibNode(
                id=node.id,
                type=node.type.value,
                properties={prop.type.value: prop.value for prop in node.properties} if node.properties else {},
            )
            for node in self.nodes
        }
        relationships = [
            LibRelationship(source=nodes_dict[rel.source_id], target=nodes_dict[rel.target_id], type=rel.type.value)
            for rel in self.relationships
            if rel.source_id in nodes_dict and rel.target_id in nodes_dict
        ]
        return GraphDocument(
            nodes=list(nodes_dict.values()),
            relationships=relationships,
            source=Document(page_content=source_event, metadata={"context": context}),
        )


class _OntologyValidValues:
    def __init__(self, ontology: GraphDocument):
        self.ontology = ontology

    @property
    def node_types(self) -> list[str]:
        return [node.type for node in self.ontology.nodes if not node.type.startswith("rdfs")]

    @property
    def relationship_types(self) -> list[str]:
        return [rel.type for rel in self.ontology.relationships if not rel.type.startswith("rdfs")]

    @property
    def structural_triples(self) -> list[tuple[str, str, str]]:
        return [
            (rel.source.type, rel.type, rel.target.type)
            for rel in self.ontology.relationships
            if rel.type.startswith("rdfs")
        ]

    @property
    def triples(self) -> list[tuple[str, str, str]]:
        return [
            (rel.source.type, rel.type, rel.target.type)
            for rel in self.ontology.relationships
            if not rel.type.startswith("rdfs")
        ]

    @property
    def properties_per_node(self) -> dict[str, list[str]]:
        return {
            node.type: [key for key in node.properties if not key.startswith("rdfs:sub")]
            for node in self.ontology.nodes
            if not node.type.startswith("rdfs")
        }

    @property
    def properties(self) -> list[str]:
        return sorted({prop for props in self.properties_per_node.values() for prop in props})

    @property
    def properties_schema(self) -> list[str]:
        return [f"{node}:{props}" for node, props in self.properties_per_node.items()]


def build_dynamic_model(ontology: GraphDocument) -> type[BaseSynthesisGraph]:
    valid = _OntologyValidValues(ontology)
    NodeType = Enum("NodeType", {node: node for node in valid.node_types}, type=str)
    PropertyType = Enum("PropertyType", {prop: prop for prop in valid.properties}, type=str)
    RelationshipType = Enum("RelationshipType", {rel: rel for rel in valid.relationship_types}, type=str)

    class Property(BaseModel):
        type: PropertyType = Field(description=f"Type of the property. Must be one of {valid.properties}.")
        value: str | int | float | bool = Field(description="Extracted value of the property.")

    class Node(BaseModel):
        id: str = Field(description="Unique identifier for the node.")
        type: NodeType = Field(description=f"Type of the node. Must be one of {valid.node_types}.")
        properties: list[Property] | None = Field(default=None, description="List of properties of the node.")
        __doc__ = (
            "A node in the OntoSynthesis graph. "
            f"The allowed properties for each node type are: {valid.properties_schema}. "
            f"Structural relationships: {valid.structural_triples}."
        )

    class Relationship(BaseModel):
        source_id: str = Field(description="Unique identifier of source node.")
        target_id: str = Field(description="Unique identifier of target node.")
        type: RelationshipType = Field(
            description=f"Type of the relationship. Must be one of {valid.relationship_types}."
        )
        __doc__ = (
            "A relationship between two nodes. Allowed triples "
            f"(source type, relationship type, target type): {valid.triples}."
        )

    class SynthesisGraph(BaseSynthesisGraph):
        nodes: list[Node] = Field(description="List of nodes in the graph.")
        relationships: list[Relationship] = Field(description="List of relationships in the graph.")
        remove_relationships: list[Relationship] = Field(
            default_factory=list,
            description=(
                "Exact relationships to remove from the existing prior-layer graph before "
                "attaching this output. Use only existing source_id/type/target_id triples."
            ),
        )

    return SynthesisGraph
