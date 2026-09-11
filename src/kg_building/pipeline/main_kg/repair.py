"""Optional post-publish SPARQL repair. Fail closed when SPARQL is required."""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph

from src.extraction_runtime.artifact_root import sparql_dir


def load_repair_query(ontology_name: str) -> str:
    directory = sparql_dir(ontology_name)
    for name in ("repair.sparql", "post_publish_repair.sparql"):
        path = directory / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def graph_is_parseable(ttl_path: Path) -> bool:
    try:
        Graph().parse(str(ttl_path), format="turtle")
    except Exception:
        return False
    return True
