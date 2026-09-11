"""Top-class selection and identity sidecars. Class comes from generated contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_runtime.domain_binding import RuntimeDomain, selected_top_class
from src.extraction_runtime.names import entity_artifact_name, local_name


def resolve_top_class(config: dict) -> dict[str, str]:
    domain = config.get("domain")
    if isinstance(domain, RuntimeDomain):
        upstream = str((domain.runtime.get("binding") or {}).get("upstream_ontology") or "")
        return selected_top_class(domain.ontology_name, upstream_ontology=upstream)
    ontology = str(config.get("ontology_name") or "").strip()
    if not ontology:
        raise ValueError("ontology_name is required to resolve the generated top class")
    return selected_top_class(ontology)


def load_selected_top_class(doi_folder: str | Path) -> tuple[str, str]:
    path = Path(doi_folder) / "top_entity_selection.json"
    if not path.is_file():
        return "", ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    return (
        str(payload.get("class_iri") or "").strip(),
        str(payload.get("class_local") or "").strip(),
    )


def write_top_class_selection(
    *,
    doi_dir: str | Path,
    class_local: str,
    class_iri: str,
    source: str = "generated_contract",
) -> Path:
    path = Path(doi_dir) / "top_entity_selection.json"
    path.write_text(
        json.dumps(
            {
                "class_local": class_local,
                "class_iri": class_iri,
                "source": source,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def persist_and_validate_top_class_selection(
    *,
    doi_dir: str | Path,
    class_local: str,
    class_iri: str,
    source: str = "generated_contract",
) -> bool:
    if not class_local.strip() or not class_iri.strip():
        return False
    write_top_class_selection(
        doi_dir=doi_dir,
        class_local=class_local,
        class_iri=class_iri,
        source=source,
    )
    persisted = json.loads(
        (Path(doi_dir) / "top_entity_selection.json").read_text(encoding="utf-8")
    )
    return (
        str(persisted.get("class_local") or "").strip() == class_local.strip()
        and str(persisted.get("class_iri") or "").strip() == class_iri.strip()
    )


def top_entity_line_prefixes(class_local: str, class_iri: str) -> list[str]:
    prefixes = [name for name in (class_local, local_name(class_iri)) if name]
    return list(dict.fromkeys(prefixes)) or ["Entity"]


def safe_entity_name(label: str) -> str:
    return entity_artifact_name(label)


def load_top_entities_json(doi_hash: str, data_dir: str) -> list[dict[str, Any]]:
    folder = Path(data_dir) / doi_hash
    path = folder / "mcp_run" / "iter1_top_entities.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list) and payload:
            return payload

    listing = folder / "top_entities.txt"
    if not listing.is_file() or listing.stat().st_size <= 0:
        return []

    from src.extraction_runtime.steps.top_entity.membership import listing_labels

    class_iri, _ = load_selected_top_class(folder)
    entities: list[dict[str, Any]] = []
    for label in listing_labels(listing.read_text(encoding="utf-8")):
        safe = entity_artifact_name(label)
        entities.append(
            {
                "uri": (
                    "https://www.theworldavatar.com/kg/instance/generated/"
                    f"{doi_hash}/{safe}"
                ),
                "label": label,
                "types": [class_iri] if class_iri else [],
                "source": "top_entities.txt",
            }
        )
    if entities:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entities, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return entities
