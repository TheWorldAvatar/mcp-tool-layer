"""Canonical filename policy and scoped/central/document memory paths.

Used by `init_memory` / `export_memory` and reuse publish. See rdf/README.md.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import configured_main_ontology_name, relationship_contract_path


def _package_relationship_contract() -> dict[str, Any]:
    contract_path = relationship_contract_path()
    if not contract_path.is_file():
        return {}
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return contract if isinstance(contract, dict) else {}


def _package_ontology_name() -> str:
    return str(_package_relationship_contract().get("ontology_name") or "").strip()


def safe_filename_component(value: str) -> str:
    """Normalize a runtime scope using the pipeline's canonical filename policy."""
    normalized = unicodedata.normalize("NFKC", str(value or "").strip())
    chars: list[str] = []
    for char in normalized:
        if ord(char) < 128:
            chars.append(char)
            continue
        try:
            char_name = unicodedata.name(char)
        except ValueError:
            chars.append("_")
            continue
        if char_name.startswith("GREEK ") and " LETTER " in char_name:
            chars.append(char_name.rsplit(" LETTER ", 1)[-1].lower())
        else:
            chars.append("_")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", "".join(chars)).strip("._")
    return text or "entity"


def resolve_case_dirname(doi_value: str) -> str:
    """Resolve a DOI/document identifier to the pipeline's canonical case folder."""
    raw = str(doi_value or "").strip() or "unknown"
    safe = safe_filename_component(raw)
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    mapping_path = root / "doi_to_hash.json"
    if not mapping_path.exists():
        return safe
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return safe
    hashes = {str(value).strip() for value in mapping.values() if str(value).strip()}
    if safe in hashes:
        return safe
    candidates = {
        raw,
        safe,
        raw.replace("_", "/"),
        raw.replace("/", "_"),
        safe.replace("_", "/"),
    }
    for doi_key, hash_value in mapping.items():
        key = str(doi_key or "").strip()
        hashed = str(hash_value or "").strip()
        if not key or not hashed:
            continue
        key_us = key.replace("/", "_")
        if (
            key in candidates
            or key_us in candidates
            or safe_filename_component(key_us) == safe
        ):
            return hashed
    return safe


def scoped_memory_paths(
    doi: str,
    top_level_entity_name: str,
) -> tuple[Path, Path]:
    """Return canonical memory and timestamped export paths for one scope.

    The pipeline-owned main ontology keeps the historical ``memory/`` directory.
    Extension packages persist under ``memory_<ontology>/`` so the pipeline can
    validate and promote them without reading the shared main A-Box memory.
    """
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    case_dir = root / resolve_case_dirname(doi)
    ontology_name = _package_ontology_name()
    main_ontology = configured_main_ontology_name()
    if ontology_name and main_ontology and ontology_name != main_ontology:
        memory_dir = case_dir / f"memory_{safe_filename_component(ontology_name)}"
        exports_dir = case_dir / f"exports_{safe_filename_component(ontology_name)}"
    else:
        memory_dir = case_dir / "memory"
        exports_dir = case_dir / "exports"
    memory_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    safe_entity = safe_filename_component(top_level_entity_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        memory_dir / f"{safe_entity}.ttl",
        exports_dir / f"{safe_entity}_{timestamp}.ttl",
    )


def central_memory_paths(ontology_name: str) -> tuple[Path, Path]:
    """Return ontology-wide reusable-entity graph and provenance paths."""
    configured = str(os.environ.get("TWA_CENTRAL_MEMORY_DIR") or "").strip()
    root = (
        Path(configured)
        if configured
        else Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data") / "central_memory"
    )
    root.mkdir(parents=True, exist_ok=True)
    safe_ontology = safe_filename_component(ontology_name)
    return (
        root / f"{safe_ontology}_reusable_entities.ttl",
        root / f"{safe_ontology}_reusable_entities.provenance.json",
    )


def document_memory_paths(
    ontology_name: str,
    doi: str,
) -> tuple[Path, Path]:
    """Return one DOI-owned reusable-entity graph and provenance path."""
    safe_ontology = safe_filename_component(ontology_name)
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    case_dir = root / resolve_case_dirname(doi)
    main_ontology = configured_main_ontology_name()
    memory_dir = (
        case_dir / f"memory_{safe_ontology}"
        if safe_ontology and main_ontology and safe_ontology != main_ontology
        else case_dir / "memory"
    )
    memory_dir.mkdir(parents=True, exist_ok=True)
    graph_path = memory_dir / "document.ttl"
    return graph_path, graph_path.with_suffix(".provenance.json")
