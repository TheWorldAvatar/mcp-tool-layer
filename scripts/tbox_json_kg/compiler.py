"""Compile OWL/RDFS TBoxes into canonical RDF-path JSON Schemas.

The compiler is ontology-agnostic.  It reads classes and properties from one
or more RDF graphs and emits a bundle containing:

* a reversible CURIE context;
* a property registry used by the materializer; and
* a strict, layered JSON Schema whose keys are RDF property CURIEs.

No JSON-to-RDF field mapping is handwritten: JSON nesting follows object
property ranges and primitive fields follow datatype property ranges.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from rdflib import BNode, Graph, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, XSD


BUILTIN_PREFIXES = {
    "rdf": str(RDF),
    "rdfs": str(RDFS),
    "owl": str(OWL),
    "xsd": str(XSD),
}

JSON_TYPES = {
    str(XSD.string): "string",
    str(XSD.boolean): "boolean",
    str(XSD.integer): "integer",
    str(XSD.int): "integer",
    str(XSD.long): "integer",
    str(XSD.nonNegativeInteger): "integer",
    str(XSD.positiveInteger): "integer",
    str(XSD.float): "number",
    str(XSD.double): "number",
    str(XSD.decimal): "number",
    str(XSD.date): "string",
    str(XSD.dateTime): "string",
    str(XSD.anyURI): "string",
}


@dataclass(frozen=True)
class PropertySpec:
    iri: str
    kind: str
    domains: tuple[str, ...]
    ranges: tuple[str, ...]
    comment: str


def _safe_name(iri: str) -> str:
    local = re.split(r"[/#]", iri.rstrip("/#"))[-1] or "Resource"
    clean = re.sub(r"[^A-Za-z0-9_]", "_", local)
    digest = hashlib.sha1(iri.encode("utf-8")).hexdigest()[:8]
    return f"{clean}_{digest}"


class TBoxCompiler:
    """Generic TBox compiler for canonical RDF-path JSON."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self.graph = Graph()
        self.paths = [Path(path) for path in paths]
        for path in self.paths:
            self.graph.parse(path, format="turtle")
        self.context = self._build_context()
        self.classes = self._collect_classes()
        self.direct_parents = self._collect_parents()
        self.properties = self._collect_properties()

    def _build_context(self) -> dict[str, str]:
        context = dict(BUILTIN_PREFIXES)
        used_namespaces = {
            str(term)
            for triple in self.graph
            for term in triple
            if isinstance(term, URIRef)
        }
        for raw_prefix, namespace in self.graph.namespaces():
            prefix = str(raw_prefix or "").strip()
            ns = str(namespace)
            if not prefix or prefix in context or not any(
                iri.startswith(ns) for iri in used_namespaces
            ):
                continue
            context[prefix] = ns
        return dict(sorted(context.items()))

    def curie(self, iri: str) -> str:
        matches = [
            (prefix, namespace)
            for prefix, namespace in self.context.items()
            if iri.startswith(namespace)
        ]
        if not matches:
            return iri
        prefix, namespace = max(matches, key=lambda item: len(item[1]))
        return f"{prefix}:{iri[len(namespace):]}"

    def _collect_classes(self) -> set[str]:
        classes = {
            str(node)
            for node in self.graph.subjects(RDF.type, OWL.Class)
            if isinstance(node, URIRef)
        }
        for predicate in (RDFS.domain, RDFS.range, RDFS.subClassOf):
            for node in self.graph.objects(None, predicate):
                if isinstance(node, URIRef) and not str(node).startswith(str(XSD)):
                    classes.add(str(node))
        return classes

    def _collect_parents(self) -> dict[str, set[str]]:
        parents: dict[str, set[str]] = {class_iri: set() for class_iri in self.classes}
        for child, parent in self.graph.subject_objects(RDFS.subClassOf):
            if isinstance(child, URIRef) and isinstance(parent, URIRef):
                parents.setdefault(str(child), set()).add(str(parent))
                self.classes.add(str(child))
                self.classes.add(str(parent))
        return parents

    def _expand_class_expression(self, node: URIRef | BNode) -> list[str]:
        if isinstance(node, URIRef):
            return [str(node)]
        union_head = self.graph.value(node, OWL.unionOf)
        if union_head is None:
            return []
        return [
            str(member)
            for member in Collection(self.graph, union_head)
            if isinstance(member, URIRef)
        ]

    def _collect_properties(self) -> dict[str, PropertySpec]:
        properties: dict[str, PropertySpec] = {}
        kinds = (
            (OWL.ObjectProperty, "object"),
            (OWL.DatatypeProperty, "datatype"),
        )
        for rdf_type, kind in kinds:
            for prop in self.graph.subjects(RDF.type, rdf_type):
                if not isinstance(prop, URIRef):
                    continue
                domains: list[str] = []
                ranges: list[str] = []
                for domain in self.graph.objects(prop, RDFS.domain):
                    domains.extend(self._expand_class_expression(domain))
                for range_node in self.graph.objects(prop, RDFS.range):
                    ranges.extend(self._expand_class_expression(range_node))
                comment = str(self.graph.value(prop, RDFS.comment) or "").strip()
                properties[str(prop)] = PropertySpec(
                    iri=str(prop),
                    kind=kind,
                    domains=tuple(dict.fromkeys(domains)),
                    ranges=tuple(dict.fromkeys(ranges)),
                    comment=comment,
                )
        properties[str(RDFS.label)] = PropertySpec(
            iri=str(RDFS.label),
            kind="datatype",
            domains=(),
            ranges=(str(XSD.string),),
            comment="Human-readable label.",
        )
        return properties

    def ancestors(self, class_iri: str) -> set[str]:
        result = {class_iri}
        queue = deque([class_iri])
        while queue:
            current = queue.popleft()
            for parent in self.direct_parents.get(current, set()):
                if parent not in result:
                    result.add(parent)
                    queue.append(parent)
        return result

    def descendants(self, class_iri: str) -> set[str]:
        return {
            candidate
            for candidate in self.classes
            if class_iri in self.ancestors(candidate)
        }

    def applicable_properties(self, class_iri: str) -> list[PropertySpec]:
        ancestors = self.ancestors(class_iri)
        return sorted(
            (
                spec
                for spec in self.properties.values()
                if not spec.domains or ancestors.intersection(spec.domains)
            ),
            key=lambda spec: spec.iri,
        )

    def _reachable_classes(self, roots: Iterable[str]) -> set[str]:
        reachable: set[str] = set()
        queue = deque(roots)
        while queue:
            class_iri = queue.popleft()
            if class_iri in reachable:
                continue
            reachable.add(class_iri)
            for spec in self.applicable_properties(class_iri):
                if spec.kind != "object":
                    continue
                for range_iri in spec.ranges:
                    if range_iri not in self.classes:
                        continue
                    for candidate in self.descendants(range_iri):
                        if candidate not in reachable:
                            queue.append(candidate)
        return reachable

    def _datatype_schema(self, ranges: tuple[str, ...]) -> dict[str, Any]:
        types = sorted({JSON_TYPES.get(range_iri, "string") for range_iri in ranges})
        if not types:
            types = ["string"]
        if len(types) == 1:
            return {"type": types[0]}
        return {"type": types}

    def compile(
        self, roots: Iterable[str], *, reference_only: bool = False
    ) -> dict[str, Any]:
        root_iris = [str(root) for root in roots]
        missing = [root for root in root_iris if root not in self.classes]
        if missing:
            raise ValueError(f"Root classes not found in TBox: {missing}")

        reachable = (
            set(root_iris) if reference_only else self._reachable_classes(root_iris)
        )
        def_names = {class_iri: _safe_name(class_iri) for class_iri in reachable}
        definitions: dict[str, Any] = {
            "ResourceReference": {
                "type": "object",
                "properties": {"@id": {"type": "string"}},
                "required": ["@id"],
                "additionalProperties": False,
            }
        }

        registry: dict[str, Any] = {}
        for spec in self.properties.values():
            registry[self.curie(spec.iri)] = {
                "iri": spec.iri,
                "kind": spec.kind,
                "domains": list(spec.domains),
                "ranges": list(spec.ranges),
            }

        for class_iri in sorted(reachable):
            properties: dict[str, Any] = {
                "@id": {
                    "type": "string",
                    "description": (
                        "Stable local identifier or absolute IRI. Reuse the same "
                        "identifier when referring to the same entity."
                    ),
                },
                "@type": {"type": "string", "const": self.curie(class_iri)},
            }
            required = ["@id", "@type"]
            for spec in self.applicable_properties(class_iri):
                key = self.curie(spec.iri)
                if spec.kind == "datatype":
                    item_schema = self._datatype_schema(spec.ranges)
                else:
                    candidates: list[dict[str, str]] = [
                        {"$ref": "#/$defs/ResourceReference"}
                    ]
                    if not reference_only:
                        target_classes: set[str] = set()
                        for range_iri in spec.ranges:
                            if range_iri in self.classes:
                                target_classes.update(self.descendants(range_iri))
                        for target in sorted(target_classes):
                            if target in def_names:
                                candidates.append(
                                    {"$ref": f"#/$defs/{def_names[target]}"}
                                )
                    item_schema = (
                        candidates[0]
                        if len(candidates) == 1
                        else {"anyOf": candidates}
                    )
                field_schema: dict[str, Any] = {
                    "type": "array",
                    "items": item_schema,
                }
                descriptions: list[str] = []
                if spec.comment:
                    descriptions.append(spec.comment[:1000])
                if spec.ranges:
                    descriptions.append(
                        "RDF range: "
                        + ", ".join(self.curie(value) for value in spec.ranges)
                        + ". Referenced local IDs must be defined with a compatible "
                        "@type and must not be reused for an incompatible range."
                    )
                if descriptions:
                    field_schema["description"] = " ".join(descriptions)[:1600]
                properties[key] = field_schema
                required.append(key)

            class_comment = str(
                self.graph.value(URIRef(class_iri), RDFS.comment) or ""
            ).strip()
            class_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
            if class_comment:
                class_schema["description"] = class_comment[:2000]
            definitions[def_names[class_iri]] = class_schema

        root_refs = [
            {"$ref": f"#/$defs/{def_names[root_iri]}"} for root_iri in root_iris
        ]
        root_item: dict[str, Any] = (
            root_refs[0] if len(root_refs) == 1 else {"anyOf": root_refs}
        )
        json_schema = {
            "name": "tbox_canonical_rdf_paths",
            "strict": True,
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "roots": {
                        "type": "array",
                        "items": root_item,
                    }
                },
                "required": ["roots"],
                "additionalProperties": False,
                "$defs": definitions,
            },
        }
        return {
            "version": 1,
            "object_mode": "reference-only" if reference_only else "nested",
            "tboxes": [str(path) for path in self.paths],
            "roots": root_iris,
            "context": self.context,
            "classes": {
                self.curie(class_iri): {
                    "iri": class_iri,
                    "definition": def_names[class_iri],
                    "ancestors": sorted(self.ancestors(class_iri)),
                }
                for class_iri in sorted(reachable)
            },
            "properties": registry,
            "json_schema": json_schema,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tbox", type=Path, action="append", required=True)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--reference-only",
        action="store_true",
        help="Generate layered class schemas whose object properties use @id references.",
    )
    args = parser.parse_args()

    compiler = TBoxCompiler(args.tbox)
    roots = [
        compiler.context.get(root.split(":", 1)[0], "")
        + root.split(":", 1)[1]
        if ":" in root and not root.startswith(("http://", "https://"))
        else root
        for root in args.root
    ]
    bundle = compiler.compile(roots, reference_only=args.reference_only)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Wrote {len(bundle['classes'])} class schemas and "
        f"{len(bundle['properties'])} properties to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
