"""Parse local T-Box OWL/TTL files into class and property summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from rdflib import Graph, OWL, RDF, RDFS, URIRef

OWL_CLASS = OWL.Class
RDFS_CLASS = RDFS.Class


def _local_name(uri: str) -> str:
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rstrip("/").rsplit("/", 1)[-1]


def summarize_tbox(paths: List[Path]) -> Dict[str, Any]:
    g = Graph()
    for path in paths:
        if not path.exists():
            continue
        g.parse(path)

    classes: List[Dict[str, str]] = []
    for s in set(g.subjects(RDF.type, OWL_CLASS)) | set(g.subjects(RDF.type, RDFS_CLASS)):
        if not isinstance(s, URIRef):
            continue
        label = g.value(s, RDFS.label)
        classes.append(
            {
                "class": str(s),
                "local_name": _local_name(str(s)),
                "label": str(label) if label else "",
            }
        )
    classes.sort(key=lambda x: x["local_name"].lower())

    properties: List[Dict[str, str]] = []
    for ptype in (OWL.ObjectProperty, OWL.DatatypeProperty, RDF.Property):
        for s in g.subjects(RDF.type, ptype):
            if not isinstance(s, URIRef):
                continue
            label = g.value(s, RDFS.label)
            domain = g.value(s, RDFS.domain)
            rng = g.value(s, RDFS.range)
            properties.append(
                {
                    "property": str(s),
                    "local_name": _local_name(str(s)),
                    "label": str(label) if label else "",
                    "domain": str(domain) if domain else "",
                    "range": str(rng) if rng else "",
                    "type": ptype.split("#")[-1] if "#" in str(ptype) else str(ptype),
                }
            )
    properties.sort(key=lambda x: x["local_name"].lower())

    return {
        "files": [str(p) for p in paths if p.exists()],
        "class_count": len(classes),
        "property_count": len(properties),
        "classes": classes,
        "properties": properties,
    }


def list_classes_text(paths: List[Path], limit: int = 50) -> str:
    summary = summarize_tbox(paths)
    lines = [f"classes: {summary['class_count']}", "local_name\tlabel\tclass_iri"]
    for row in summary["classes"][:limit]:
        lines.append(f"{row['local_name']}\t{row['label']}\t{row['class']}")
    if summary["class_count"] > limit:
        lines.append(f"... ({summary['class_count'] - limit} more)")
    return "\n".join(lines)


def list_properties_text(paths: List[Path], limit: int = 50) -> str:
    summary = summarize_tbox(paths)
    lines = [f"properties: {summary['property_count']}", "local_name\tlabel\ttype\tdomain\trange"]
    for row in summary["properties"][:limit]:
        lines.append(
            f"{row['local_name']}\t{row['label']}\t{row['type']}\t{row['domain']}\t{row['range']}"
        )
    if summary["property_count"] > limit:
        lines.append(f"... ({summary['property_count'] - limit} more)")
    return "\n".join(lines)
