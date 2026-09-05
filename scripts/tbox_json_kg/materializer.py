"""Materialize TBox-derived canonical RDF-path JSON as RDF.

The implementation contains no ontology-specific class or property branches.
All semantic information comes from the compiler bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF


class CanonicalJsonMaterializer:
    def __init__(self, bundle: dict[str, Any], base_iri: str) -> None:
        self.bundle = bundle
        self.context: dict[str, str] = bundle["context"]
        self.properties: dict[str, dict[str, Any]] = bundle["properties"]
        self.classes: dict[str, dict[str, Any]] = bundle["classes"]
        self.base_iri = base_iri.rstrip("/") + "/"
        self.graph = Graph()
        self.defined_local_ids: set[str] = set()
        self.referenced_local_ids: set[str] = set()
        self.resource_types: dict[str, set[str]] = {}
        self.object_constraints: list[tuple[str, str, tuple[str, ...]]] = []
        self.dropped_dangling_ids: list[str] = []
        self.dropped_range_edges: list[dict[str, Any]] = []
        for prefix, namespace in self.context.items():
            self.graph.bind(prefix, namespace)

    def resolve(self, value: str) -> str:
        text = str(value).strip()
        if text.startswith(("http://", "https://", "urn:")):
            return text
        if ":" in text:
            prefix, local = text.split(":", 1)
            namespace = self.context.get(prefix)
            if namespace:
                return namespace + local
        return self._local_iri(text)

    def _local_iri(self, local_id: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9._~-]+", "-", local_id).strip("-")
        if not clean:
            clean = hashlib.sha256(local_id.encode("utf-8")).hexdigest()[:16]
        return self.base_iri + quote(clean, safe="._~-")

    def _is_local_identifier(self, value: str) -> bool:
        return not value.startswith(("http://", "https://", "urn:")) and not (
            ":" in value and value.split(":", 1)[0] in self.context
        )

    def _literal(self, value: Any, ranges: list[str]) -> Literal:
        datatype = URIRef(ranges[0]) if len(ranges) == 1 else None
        if isinstance(value, dict) and "@value" in value:
            datatype_value = value.get("@type")
            datatype = (
                URIRef(self.resolve(str(datatype_value)))
                if datatype_value
                else datatype
            )
            return Literal(value["@value"], datatype=datatype)
        return Literal(value, datatype=datatype)

    def _class_ancestors(self, class_value: str) -> set[str]:
        class_entry = self.classes.get(class_value)
        if class_entry:
            return set(class_entry.get("ancestors", []))
        resolved = self.resolve(class_value)
        for entry in self.classes.values():
            if entry.get("iri") == resolved:
                return set(entry.get("ancestors", []))
        return {resolved}

    def _validate_domain(
        self, class_value: str, property_key: str, property_spec: dict[str, Any]
    ) -> None:
        domains = set(property_spec.get("domains", []))
        if not domains:
            return
        if not self._class_ancestors(class_value).intersection(domains):
            raise ValueError(
                f"{property_key} cannot apply to {class_value}; domains={sorted(domains)}"
            )

    def _resource(self, node: dict[str, Any]) -> URIRef:
        if not isinstance(node, dict) or "@id" not in node:
            raise ValueError(f"RDF resource must be an object with @id: {node!r}")
        raw_id = str(node["@id"]).strip()
        if not raw_id:
            raise ValueError("@id cannot be empty")
        subject = URIRef(self.resolve(raw_id))

        if set(node) == {"@id"}:
            if self._is_local_identifier(raw_id):
                self.referenced_local_ids.add(raw_id)
            return subject

        class_value = str(node.get("@type", "")).strip()
        if not class_value:
            raise ValueError(f"Full resource {raw_id!r} is missing @type")
        class_iri = URIRef(self.resolve(class_value))
        class_iris = self._class_ancestors(class_value)
        class_iris.add(str(class_iri))
        for inferred_type in class_iris:
            self.graph.add((subject, RDF.type, URIRef(inferred_type)))
        self.resource_types.setdefault(str(subject), set()).update(class_iris)
        if self._is_local_identifier(raw_id):
            self.defined_local_ids.add(raw_id)

        for property_key, values in node.items():
            if property_key.startswith("@"):
                continue
            spec = self.properties.get(property_key)
            if spec is None:
                raise ValueError(f"Unknown RDF property key: {property_key}")
            if not isinstance(values, list):
                raise ValueError(f"{property_key} must be an array")
            self._validate_domain(class_value, property_key, spec)
            predicate = URIRef(spec["iri"])
            for value in values:
                if spec["kind"] == "datatype":
                    obj = self._literal(value, spec.get("ranges", []))
                else:
                    if not isinstance(value, dict):
                        raise ValueError(
                            f"Object property {property_key} requires resource objects"
                        )
                    obj = self._resource(value)
                    self.object_constraints.append(
                        (
                            str(obj),
                            property_key,
                            tuple(spec.get("ranges", [])),
                        )
                    )
                self.graph.add((subject, predicate, obj))
        return subject

    def materialize(
        self,
        document: dict[str, Any],
        *,
        dangling_policy: str = "error",
        range_policy: str = "error",
    ) -> Graph:
        roots = document.get("roots")
        if not isinstance(roots, list):
            raise ValueError("Canonical document requires a roots array")
        for root in roots:
            self._resource(root)
        dangling = sorted(self.referenced_local_ids - self.defined_local_ids)
        if dangling:
            if dangling_policy == "drop":
                self.dropped_dangling_ids = dangling
                dangling_iris = {
                    URIRef(self.resolve(local_id)) for local_id in dangling
                }
                for triple in list(self.graph):
                    if triple[2] in dangling_iris:
                        self.graph.remove(triple)
            elif dangling_policy == "error":
                raise ValueError(f"Dangling local @id references: {dangling}")
            else:
                raise ValueError(
                    f"Unknown dangling reference policy: {dangling_policy}"
                )
        for object_iri, property_key, ranges in self.object_constraints:
            if not ranges:
                continue
            if str(OWL.Thing) in ranges:
                continue
            object_types = self.resource_types.get(object_iri)
            if object_types and not object_types.intersection(ranges):
                if range_policy == "drop":
                    predicate = URIRef(self.properties[property_key]["iri"])
                    target = URIRef(object_iri)
                    for triple in list(
                        self.graph.triples((None, predicate, target))
                    ):
                        self.graph.remove(triple)
                    self.dropped_range_edges.append(
                        {
                            "property": property_key,
                            "target": object_iri,
                            "types": sorted(object_types),
                            "ranges": sorted(ranges),
                        }
                    )
                elif range_policy == "error":
                    raise ValueError(
                        f"{property_key} target {object_iri} has incompatible "
                        f"types {sorted(object_types)}; ranges={sorted(ranges)}"
                    )
                else:
                    raise ValueError(
                        f"Unknown range validation policy: {range_policy}"
                    )
        return self.graph


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-iri", required=True)
    args = parser.parse_args()

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    document = json.loads(args.input.read_text(encoding="utf-8"))
    materializer = CanonicalJsonMaterializer(bundle, args.base_iri)
    graph = materializer.materialize(document)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(args.output, format="turtle")
    print(f"Wrote {len(graph)} triples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
