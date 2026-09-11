"""Write published TTL files from an in-memory graph or existing artifact."""

from __future__ import annotations

import shutil
from pathlib import Path

from rdflib import Graph


def publish_ttl(source: str | Path | Graph, destination: str | Path) -> Path:
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, Graph):
        dest.write_text(source.serialize(format="turtle"), encoding="utf-8")
        return dest
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(f"TTL source not found: {src}")
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest


def publish_top_ttl(doi_folder: str | Path, source: str | Path | Graph) -> Path:
    return publish_ttl(source, Path(doi_folder) / "top.ttl")
