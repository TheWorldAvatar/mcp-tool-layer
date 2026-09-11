"""Human namespace IRIs used when minting generated individuals.

The only tracked source is `configs/namespace.json`. Compile copies a sidecar
next to generated runtimes so flattened packages do not hardcode a host IRI.
See config/README.md and docs/CONFIGS.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models.locations import discover_repository_root


SCHEMA_VERSION = "namespace-config.v1"
REQUIRED_IRI_KEYS = ("instance_base_iri", "generated_graph_iri")


def default_namespace_config_path(repository_root: str | Path | None = None) -> Path:
    """Return `configs/namespace.json` under the clone root."""
    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else discover_repository_root()
    )
    return root / "configs" / "namespace.json"


def _normalize_iri(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://", "urn:")):
        raise ValueError(f"namespace IRI must be absolute: {text!r}")
    return text.rstrip("/") + "/"


def load_namespace_config(
    path: str | Path | None = None, *, repository_root: str | Path | None = None
) -> dict[str, str]:
    """Load the human namespace config. Missing required IRIs are an error."""
    config_path = (
        Path(path).resolve()
        if path is not None
        else default_namespace_config_path(repository_root)
    )
    if not config_path.is_file():
        raise FileNotFoundError(f"namespace config not found: {config_path}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("namespace config must be a JSON object")
    if str(raw.get("schema_version") or "").strip() != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported namespace config schema_version: {raw.get('schema_version')!r}"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "instance_base_iri": _normalize_iri(raw.get("instance_base_iri")),
        "generated_graph_iri": _normalize_iri(raw.get("generated_graph_iri")),
    }
    missing = [key for key in REQUIRED_IRI_KEYS if not payload.get(key)]
    if missing:
        raise ValueError(
            "namespace config missing required IRI fields: " + ", ".join(missing)
        )
    return payload


def runtime_namespace_sidecar_payload(
    *,
    main_ontology_name: str = "",
    repository_root: str | Path | None = None,
) -> dict[str, str]:
    """Namespace IRIs plus the compile-time main-ontology name for memory paths."""
    payload = dict(load_namespace_config(repository_root=repository_root))
    main_name = str(main_ontology_name or "").strip()
    if main_name:
        payload["main_ontology_name"] = main_name
    return payload


def write_runtime_namespace_sidecar(
    destination_dir: str | Path,
    *,
    main_ontology_name: str = "",
    repository_root: str | Path | None = None,
) -> Path:
    """Write `_namespace.json` next to a flattened generated runtime."""
    path = Path(destination_dir) / "_namespace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            runtime_namespace_sidecar_payload(
                main_ontology_name=main_ontology_name,
                repository_root=repository_root,
            ),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path.resolve()
