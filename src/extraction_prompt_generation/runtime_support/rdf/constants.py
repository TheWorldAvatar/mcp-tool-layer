"""Shared RDF runtime constants, registry key, and sidecar anchors.

Behaves the same as a source package and as flattened `_fixed_rdf_runtime.py`.
See rdf/README.md.
"""

from __future__ import annotations

import base64
import functools
import inspect
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


_REGISTRY_NAME = "_twa_generated_rdf_graph_registry"
_SCOPE_REGISTRY_NAME = "_twa_generated_rdf_scope_registry"
_REUSE_GRANT_REGISTRY_NAME = "_twa_generated_reuse_grant_registry"
_REJECTION_REGISTRY_NAME = "_twa_generated_semantic_rejection_registry"
_HERE = Path(__file__).resolve()
if _HERE.name == "constants.py":
    # Source package: one virtual flattened-file path, not per-submodule __file__.
    _REGISTRY_ANCHOR = _HERE.parent / "_fixed_rdf_runtime.py"
    _SIDECAR_DIR = _HERE.parent.parent
    _PACKAGE_NAMESPACE = (__package__ or "").rsplit(".", 1)[0]
else:
    # Flattened generated `_fixed_rdf_runtime.py` (original Path(__file__) semantics).
    _REGISTRY_ANCHOR = _HERE
    _SIDECAR_DIR = _HERE.parent
    _PACKAGE_NAMESPACE = __name__.rsplit(".", 1)[0]
_REGISTRY_KEY = f"{_REGISTRY_ANCHOR}::{_PACKAGE_NAMESPACE}"
_GRAPH_TRANSACTION_LOCK = threading.RLock()
_TOOL_TEXT_MAX_CHARS = 46009
_NAMESPACE_SIDECAR_NAME = "_namespace.json"
_SKIP_SANITIZE_PARAMS = frozenset(
    {
        "parent_iri",
        "subject_iri",
        "doi",
        "root_iri",
        "top_level_entity_name",
        "obligation_id",
        "semantic_fingerprint",
        "requested_root_iri",
    }
)


def relationship_contract_path() -> Path:
    """Return the sidecar contract next to the runtime module (or old rdf.py dir)."""
    return _SIDECAR_DIR / "_relationship_contract.json"


def namespace_sidecar_path() -> Path:
    """Return the compiled namespace sidecar next to the runtime module."""
    return _SIDECAR_DIR / _NAMESPACE_SIDECAR_NAME


def _normalize_configured_iri(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.rstrip("/") + "/"


def _repo_namespace_config_path() -> Path | None:
    """Locate `configs/namespace.json` when this module still runs from source."""
    env_path = str(os.environ.get("TWA_NAMESPACE_CONFIG") or "").strip()
    if env_path:
        return Path(env_path)
    try:
        from models.locations import discover_repository_root

        return discover_repository_root() / "configs" / "namespace.json"
    except Exception:
        return None


def load_runtime_namespace() -> dict[str, str]:
    """Read minting IRIs from the generated sidecar, then the human namespace config."""
    merged: dict[str, str] = {}
    candidates = [_repo_namespace_config_path(), namespace_sidecar_path()]
    for path in candidates:
        if path is None or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        for key in ("instance_base_iri", "generated_graph_iri", "main_ontology_name"):
            value = str(raw.get(key) or "").strip()
            if value:
                merged[key] = value
    env_instance = str(os.environ.get("TWA_INSTANCE_BASE_IRI") or "").strip()
    if env_instance:
        merged["instance_base_iri"] = env_instance
    env_graph = str(os.environ.get("TWA_GENERATED_GRAPH_IRI") or "").strip()
    if env_graph:
        merged["generated_graph_iri"] = env_graph
    env_main = str(os.environ.get("TWA_MAIN_ONTOLOGY_NAME") or "").strip()
    if env_main:
        merged["main_ontology_name"] = env_main
    if merged.get("instance_base_iri"):
        merged["instance_base_iri"] = _normalize_configured_iri(
            merged["instance_base_iri"]
        )
    if merged.get("generated_graph_iri"):
        merged["generated_graph_iri"] = _normalize_configured_iri(
            merged["generated_graph_iri"]
        )
    return merged


def instance_base_iri() -> str:
    """Return the configured IRI prefix for minted individuals."""
    iri = str(load_runtime_namespace().get("instance_base_iri") or "").strip()
    if not iri:
        raise RuntimeError(
            "instance_base_iri is not configured; add configs/namespace.json "
            "or a generated _namespace.json sidecar"
        )
    return iri


def generated_graph_iri() -> str:
    """Return the configured fallback graph/namespace IRI."""
    iri = str(load_runtime_namespace().get("generated_graph_iri") or "").strip()
    if not iri:
        raise RuntimeError(
            "generated_graph_iri is not configured; add configs/namespace.json "
            "or a generated _namespace.json sidecar"
        )
    return iri


def configured_main_ontology_name() -> str:
    """Return the pipeline-owned main ontology name, if configured."""
    return str(load_runtime_namespace().get("main_ontology_name") or "").strip()


def om2_runtime_package() -> str:
    """Package that owns sibling ``_fixed_om2_runtime``.

    Flattened generated runtime: this module's ``__package__`` (the scripts
    package). Source ``runtime_support.rdf`` package: parent ``runtime_support``.
    """
    package = __package__ or ""
    if package.endswith(".rdf"):
        return package.rsplit(".", 1)[0]
    if ".rdf." in package:
        return package.split(".rdf.", 1)[0]
    return package


def _looks_like_iri(value: str) -> bool:
    return value.startswith(("http://", "https://", "urn:"))


def sanitize_tool_text(
    value: str,
    *,
    max_chars: int = _TOOL_TEXT_MAX_CHARS,
) -> str:
    """Cap oversized string arguments. Ordinary values are unchanged."""
    text = str(value)
    if len(text) <= max_chars or _looks_like_iri(text):
        return text
    return text[:max_chars]


def wrap_public_tool(function: Callable[..., Any]) -> Callable[..., Any]:
    """Cap long string kwargs at the public MCP tool boundary."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        for name, item in list(bound.arguments.items()):
            if name in _SKIP_SANITIZE_PARAMS or not isinstance(item, str):
                continue
            bound.arguments[name] = sanitize_tool_text(item)
        return function(*bound.args, **bound.kwargs)

    wrapped.__signature__ = signature
    return wrapped


def _short_random_iri() -> str:
    """Mint a compact 96-bit URL-safe identifier for non-deterministic instances."""
    token = base64.urlsafe_b64encode(uuid4().bytes[:12]).decode("ascii").rstrip("=")
    return f"{instance_base_iri()}{token}"


class RelationshipContractError(ValueError):
    """Structured rejection raised before an invalid relationship can mutate RDF."""

    def __init__(self, code: str, details: dict[str, Any]) -> None:
        self.code = str(code)
        self.details = dict(details)
        super().__init__(
            json.dumps(
                {"status": "rejected", "code": self.code, **self.details},
                sort_keys=True,
            )
        )
