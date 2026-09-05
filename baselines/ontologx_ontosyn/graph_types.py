"""Minimal graph types so the adapter does not need langchain_community."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    id: str
    type: str
    properties: dict = field(default_factory=dict)
    extra_types: list[str] = field(default_factory=list)


@dataclass
class Relationship:
    source: Node
    target: Node
    type: str


@dataclass
class Document:
    page_content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class GraphDocument:
    nodes: list[Node]
    relationships: list[Relationship]
    source: Document
