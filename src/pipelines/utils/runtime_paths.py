"""Windows-safe runtime artifact paths.

Windows MAX_PATH (260) applies to the *entire* absolute path, not the
entity label. Scenario runtimes already consume ~150 characters:

    <repo>/scenarios/mops/runs/<run_id>/runtime/<hash>/mcp_run_ontomops/

so a readable 100-character filename is enough to overflow. This module
is the shared fix used by extension extract/KG (and main sidecar writes):

1. Cap the label-derived stem (`entity_artifact_name`).
2. Bound the final absolute path if the directory itself is still deep.
3. Use the ``\\\\?\\`` prefix for I/O so leftover long paths do not
   surface as ``FileNotFoundError``.
4. Keep the untruncated legacy name as a lookup candidate so in-flight
   runtimes written before the cap remain visible.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    safe_filename_component,
)
from src.pipelines.utils.top_entity_identity import (
    entity_artifact_name,
    entity_scope_name,
)

DEFAULT_PATH_BUDGET = 240


def windows_fs_path(path: str) -> str:
    """Return a path Python can open on Windows even past MAX_PATH."""
    absolute = os.path.abspath(os.path.normpath(str(path)))
    if os.name != "nt":
        return absolute
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def measured_path_len(path: str | Path) -> int:
    return len(os.path.abspath(os.path.normpath(str(path))))


def bounded_sidecar_path(
    directory: str,
    entity_safe: str,
    suffix: str,
    *,
    max_path_chars: int = DEFAULT_PATH_BUDGET,
) -> Path:
    """Build a deterministic sidecar path that stays below ``max_path_chars``."""
    parent = Path(directory)
    candidate = parent / f"{entity_safe}{suffix}"
    if measured_path_len(candidate) <= max_path_chars:
        return candidate

    digest = hashlib.sha256(str(entity_safe).encode("utf-8")).hexdigest()[:12]
    parent_abs = os.path.abspath(str(parent))
    fixed_chars = len(parent_abs) + 1 + 2 + len(digest) + len(suffix)
    prefix_budget = max(8, max_path_chars - fixed_chars)
    return parent / f"{entity_safe[:prefix_budget]}--{digest}{suffix}"


def bounded_runtime_file(
    path: str, *, max_path_chars: int = DEFAULT_PATH_BUDGET
) -> str:
    parent = os.path.dirname(path)
    stem, suffix = os.path.splitext(os.path.basename(path))
    return str(
        bounded_sidecar_path(
            parent, stem, suffix, max_path_chars=max_path_chars
        )
    )


def runtime_path_exists(path: str) -> bool:
    native = windows_fs_path(path)
    return os.path.exists(native) or os.path.exists(path)


def list_runtime_files(directory: str, suffix: str = ".ttl") -> list[str]:
    """List files in a runtime directory even when child names exceed MAX_PATH."""
    native = windows_fs_path(directory)
    if not os.path.isdir(native):
        return []
    try:
        names = os.listdir(native)
    except OSError:
        return []
    return [
        os.path.join(directory, name)
        for name in names
        if name.endswith(suffix)
    ]


def write_runtime_text(path: str, content: str) -> None:
    native = windows_fs_path(path)
    os.makedirs(os.path.dirname(native), exist_ok=True)
    with open(native, "w", encoding="utf-8") as handle:
        handle.write(content)


def read_runtime_text(path: str) -> str:
    with open(windows_fs_path(path), "r", encoding="utf-8") as handle:
        return handle.read()


def extension_filename_stems(label: str, entity_uri: str = "") -> list[str]:
    """Return stems the extension publisher may read.

    Main KG and MCP persist under ``entity_scope_name(label, uri)``. Extension
    extraction historically used the label-only ``entity_artifact_name`` plus
    the untruncated slug. Lookup must accept all three so a valid memory file
    is not dropped because the write stem came from the other contract.
    """
    stems: list[str] = []
    uri = str(entity_uri or "").strip()
    if uri:
        stems.append(entity_scope_name(label, uri))
    for value in (entity_artifact_name(label), safe_filename_component(label)):
        text = str(value or "").strip()
        if text and text not in stems:
            stems.append(text)
    return stems or ["entity"]


def resolve_extension_artifact(
    doi_folder: str,
    relative_template: str,
    label: str,
    *,
    entity_uri: str = "",
    max_path_chars: int = DEFAULT_PATH_BUDGET,
) -> tuple[str, list[str]]:
    """Return ``(write_path, lookup_candidates)`` for an extension artifact.

    ``write_path`` is always the bounded canonical name. Lookup also includes
    the pre-cap filename so already-written files keep matching.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    for stem in extension_filename_stems(label, entity_uri=entity_uri):
        raw = os.path.join(
            doi_folder, relative_template.replace("{entity_safe}", stem)
        )
        _add(bounded_runtime_file(raw, max_path_chars=max_path_chars))
        _add(raw)

    if not candidates:
        fallback = os.path.join(
            doi_folder, relative_template.replace("{entity_safe}", "entity")
        )
        _add(bounded_runtime_file(fallback, max_path_chars=max_path_chars))
    return candidates[0], candidates


def _artifact_template_parts(
    relative_template: str,
) -> tuple[str, str, str] | None:
    text = str(relative_template or "").replace("\\", "/")
    if "{entity_safe}" not in text:
        return None
    directory, name = os.path.split(text)
    prefix, _token, suffix = name.partition("{entity_safe}")
    return directory, prefix, suffix


def find_existing_extension_artifact(
    doi_folder: str,
    relative_template: str,
    label: str,
    *,
    entity_uri: str = "",
    max_path_chars: int = DEFAULT_PATH_BUDGET,
) -> str | None:
    """Return an on-disk extension artifact, including copied bounded names.

    ``bounded_runtime_file`` embeds ``sha256(filename_stem)`` but shortens the
    visible prefix from the *current* directory depth. Official campaign files
    copied into a shallower runtime therefore miss canonical lookup. Recover
    them by matching that path-independent digest.
    """
    _write_path, candidates = resolve_extension_artifact(
        doi_folder,
        relative_template,
        label,
        entity_uri=entity_uri,
        max_path_chars=max_path_chars,
    )
    found = first_existing_runtime_path(candidates)
    if found:
        return found
    parts = _artifact_template_parts(relative_template)
    if parts is None:
        return None
    directory_rel, name_prefix, name_suffix = parts
    directory = (
        os.path.join(doi_folder, directory_rel) if directory_rel else doi_folder
    )
    try:
        names = os.listdir(windows_fs_path(directory))
    except OSError:
        return None
    wanted = {
        hashlib.sha256((name_prefix + stem).encode("utf-8")).hexdigest()[:12]
        for stem in extension_filename_stems(label, entity_uri=entity_uri)
    }
    hits: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name_prefix and not name.startswith(name_prefix):
            continue
        if name_suffix and not name.endswith(name_suffix):
            continue
        digest = re.search(r"--([0-9a-f]{12})", name, flags=re.IGNORECASE)
        if digest is None or digest.group(1).lower() not in wanted:
            continue
        path = os.path.join(directory, name)
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        hits.append(path)
    if len(hits) == 1:
        return hits[0]
    return None


def first_existing_runtime_path(candidates: list[str]) -> str | None:
    for path in candidates:
        if runtime_path_exists(path):
            return path
    return None
