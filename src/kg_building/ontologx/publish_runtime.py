"""Write a Pipeline-shaped runtime so merge/score and mop derivation can run."""

from __future__ import annotations

import json
from pathlib import Path

from graph_merge import scope_token
from graph_types import GraphDocument
from ttl_export import write_ttl


def entity_write_scope(entity_uri: str = "", slug: str = "", entity_label: str = "") -> str:
    """Bound-root token used when writing occurrence-local ids."""
    return entity_uri or slug or entity_label


def publish_stem(entity_label: str, entity_uri: str = "", slug: str = "") -> str:
    """TTL filename stem: prefer bound-root token, never raw labels with slashes."""
    for candidate in (entity_uri, slug, entity_label):
        token = scope_token(candidate)
        if token and token != "entity":
            return token
    return scope_token(entity_label or slug or "entity")


def publish_spliced_runtime(
    *,
    dest_root: Path,
    paper_hash: str,
    entity_label: str,
    spliced: GraphDocument,
    main: GraphDocument | None = None,
    write_extensions: bool = False,
    entity_uri: str = "",
    slug: str = "",
    main_output_dir: str = "ontosynthesis_output",
) -> dict[str, str]:
    """Emit one-entity main-ontology TTL. Extension folders only when requested.

    Official convert reads ``ontosynthesis_output/*.ttl`` (plus extension dirs)
    and ignores ``<hash>/<hash>.ttl``. Medical scoring reads ``medical_output``.
    Occurrence-local ids are scoped to the bound root so two syntheses cannot
    share ``ChemicalOutput-1`` after rdflib merge. Reusable inventory ids are
    left unchanged.
    """
    paper_dir = dest_root / paper_hash
    paper_dir.mkdir(parents=True, exist_ok=True)
    stem = publish_stem(entity_label, entity_uri=entity_uri, slug=slug)
    scope = entity_write_scope(entity_uri, slug, entity_label)
    paths: dict[str, str] = {}
    main_graph = main if main is not None else spliced
    folder = str(main_output_dir or "ontosynthesis_output").strip() or "ontosynthesis_output"
    if main_graph is not None:
        paths["main"] = str(
            write_ttl(
                main_graph,
                paper_hash,
                paper_dir / folder / f"{stem}.ttl",
                scope=scope,
            )
        )
        if folder == "ontosynthesis_output":
            paths["ontosynthesis"] = paths["main"]
    if not write_extensions:
        return paths
    paths["ontospecies"] = str(
        write_ttl(
            spliced,
            paper_hash,
            paper_dir / "ontospecies_output" / f"{stem}.ttl",
            scope=scope,
        )
    )
    paths["ontomops"] = str(
        write_ttl(
            spliced,
            paper_hash,
            paper_dir / "ontomops_output" / f"ontomops_extension_{stem}.ttl",
            scope=scope,
        )
    )
    mapping = paper_dir / "ontomops_output" / "ontomops_output_mapping.json"
    mapping.parent.mkdir(parents=True, exist_ok=True)
    payload = {}
    if mapping.is_file():
        payload = json.loads(mapping.read_text(encoding="utf-8"))
    payload[entity_label] = paths["ontomops"]
    mapping.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return paths
