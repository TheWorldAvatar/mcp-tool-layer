"""Audited deterministic aliases shared by official scorers and LLM policies."""

from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
from typing import Any

RESOURCE_DIR = Path(__file__).resolve().parents[1] / "resources"


def _chemical_registry_path() -> Path:
    manifest_value = os.getenv("ONTOSYN_ALIAS_REGISTRY_MANIFEST", "").strip()
    if not manifest_value:
        return RESOURCE_DIR / "chemical_species_aliases.json"
    manifest_path = Path(manifest_value).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "alias-registry-release-manifest.v1":
        raise ValueError("Unsupported alias registry release manifest")
    registry_path = manifest_path.parent / str(manifest["registry_file"])
    digest = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    if digest != manifest.get("content_sha256"):
        raise ValueError("Alias registry release SHA256 mismatch")
    return registry_path


CHEMICAL_REGISTRY_PATH = _chemical_registry_path()
FIELD_REGISTRY_PATH = RESOURCE_DIR / "field_value_aliases.json"


def _normalized_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def chemical_identity_key(value: Any) -> str:
    """Stable registry lookup key without erasing stoichiometry or hydrate counts."""
    text = str(value or "").casefold()
    text = text.replace("·", "-").replace("•", "-").replace("⋅", "-")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = re.sub(r"\bn,\s*n'\s*-", "n,n-", text)
    text = re.sub(r"\bn,\s*n\s*-", "n,n-", text)
    text = re.sub(r"\s*([,;:()\[\]{}+])\s*", r"\1", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return " ".join(text.split())


def _load_registry(path: Path, schema_version: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != schema_version:
        raise ValueError(
            f"Unsupported alias registry {path.name}: "
            f"{payload.get('schema_version')!r}"
        )
    return payload


def _build_alias_map(groups: list[dict[str, Any]], *, field: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for group in groups:
        if group.get("status") != "reviewed":
            continue
        key_fn = chemical_identity_key if field == "chemical_name" else _normalized_key
        canonical = key_fn(group.get("canonical"))
        if not canonical:
            raise ValueError(f"Empty canonical value in {field} alias registry")
        for value in [canonical, *(group.get("aliases") or [])]:
            key = key_fn(value)
            previous = aliases.get(key)
            if previous is not None and previous != canonical:
                raise ValueError(
                    f"Ambiguous {field} alias {value!r}: {previous!r} vs {canonical!r}"
                )
            aliases[key] = canonical
    return aliases


_CHEMICAL_REGISTRY = _load_registry(
    CHEMICAL_REGISTRY_PATH,
    "chemical-species-aliases.v1",
)
_FIELD_REGISTRY = _load_registry(
    FIELD_REGISTRY_PATH,
    "field-value-aliases.v1",
)
_CHEMICAL_GROUPS = list(_CHEMICAL_REGISTRY.get("species") or [])
CHEMICAL_NAME_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = tuple(
    tuple(
        dict.fromkeys(
            [
                _normalized_key(group.get("canonical")),
                *(_normalized_key(value) for value in group.get("aliases") or []),
            ]
        )
    )
    for group in _CHEMICAL_GROUPS
    if group.get("status") == "reviewed"
)
CHEMICAL_NAME_ALIAS_MAP = _build_alias_map(
    _CHEMICAL_GROUPS,
    field="chemical_name",
)
FIELD_VALUE_ALIAS_MAPS: dict[str, dict[str, str]] = {
    field: _build_alias_map(list(groups or []), field=field)
    for field, groups in (_FIELD_REGISTRY.get("fields") or {}).items()
}


def canonical_chemical_name(normalized: str) -> str:
    """Map an already-normalized chemical-name string onto its group canonical."""
    key = chemical_identity_key(normalized)
    return CHEMICAL_NAME_ALIAS_MAP.get(key, normalized)


def canonical_field_value(field: str, normalized: str) -> str:
    """Map a reviewed field alias onto its canonical value."""
    key = _normalized_key(normalized)
    return FIELD_VALUE_ALIAS_MAPS.get(field, {}).get(key, normalized)


def chemical_alias_policy_lines() -> list[str]:
    """Prompt lines for the official field-equivalence judges."""
    groups = []
    for group in CHEMICAL_NAME_ALIAS_GROUPS:
        groups.append(" = ".join(group))
    return [
        "Known same-species chemical aliases (apply the same relation to obvious "
        "punctuation variants or a trailing parenthetical short code): "
        + "; ".join(groups)
        + ".",
        "A definite hydrate count is equivalent when the only difference is "
        "hydrate punctuation such as · vs - vs • "
        "(Cu(OAc)2·H2O = Cu(OAc)2-H2O). Different hydrate counts are not equivalent.",
        "A trailing parenthetical identifier after a full name is the same species "
        "as that identifier "
        "(1,3,5-tris(4-carboxyphenyl)-benzene (H2BTB) = H2BTB).",
    ]
