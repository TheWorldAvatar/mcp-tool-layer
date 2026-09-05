"""Safe, mandatory runtime cleanup for fresh pipeline starts."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.pipelines.utils.machine_paths import (
    configured_runtime_root,
    is_safe_external_runtime,
)


RUNTIME_MANIFEST = ".runtime_start.json"
WINDOWS_SAFE_PATH_CHARS = 259
# Reserve space for nested runtime artifacts, atomic-write PID/UUID suffixes,
# and separators. This covers the longest shared-memory temporary paths used
# by the current pipelines without coupling validation to one ontology name.
WINDOWS_RUNTIME_DESCENDANT_RESERVE = 112
_CONVERSION_ARTIFACT_SUFFIXES = (
    ".md",
    "_text.md",
    "_tables.md",
    "_vision.md",
)


def validate_runtime_path_budget(
    runtime: str | Path,
    *,
    enforce_windows: bool | None = None,
    max_path_chars: int = WINDOWS_SAFE_PATH_CHARS,
    descendant_reserve: int = WINDOWS_RUNTIME_DESCENDANT_RESERVE,
) -> Path:
    """Reject runtime roots that cannot safely host nested atomic artifacts."""
    resolved = Path(runtime).resolve()
    enforce = os.name == "nt" if enforce_windows is None else enforce_windows
    projected_length = len(str(resolved)) + int(descendant_reserve)
    if enforce and projected_length > int(max_path_chars):
        overage = projected_length - int(max_path_chars)
        raise ValueError(
            "Configured scenario runtime path is too long for safe Windows "
            f"artifact creation ({projected_length} projected characters; "
            f"limit {max_path_chars}). Shorten the run ID/path by at least "
            f"{overage} characters: {resolved}"
        )
    return resolved


def _remove_runtime_tree(runtime: Path) -> None:
    """Remove a runtime tree, including paths beyond Windows MAX_PATH."""
    deletion_target = str(runtime)
    if os.name == "nt" and not deletion_target.startswith("\\\\?\\"):
        deletion_target = "\\\\?\\" + deletion_target

    def _onerror(_function, failed_path, exc_info) -> None:
        error = exc_info[1]
        # Concurrently disappearing descendants are already clean. Windows can
        # report either ERROR_FILE_NOT_FOUND (2) or ERROR_PATH_NOT_FOUND (3).
        if isinstance(error, FileNotFoundError) or getattr(error, "winerror", None) in {
            2,
            3,
        }:
            return
        raise error

    shutil.rmtree(deletion_target, onerror=_onerror)
    if runtime.exists():
        raise OSError(f"Runtime cleanup incomplete; target still exists: {runtime}")


def _assert_safe_runtime_path(data_dir: str | Path, repository_root: Path) -> Path:
    """Resolve and validate a disposable scenario runtime directory."""
    root = repository_root.resolve()
    runtime = Path(data_dir)
    if not runtime.is_absolute():
        runtime = root / runtime
    runtime = runtime.resolve()
    validate_runtime_path_budget(runtime)

    machine_root = configured_runtime_root(root)
    if machine_root is not None and is_safe_external_runtime(runtime, machine_root):
        return runtime

    try:
        relative = runtime.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Fresh-start cleanup requires data_dir inside the repository "
            f"or under the machine runtime_root: {runtime}"
        ) from exc

    parts = relative.parts
    if (
        len(parts) < 5
        or parts[0] != "scenarios"
        or parts[2] != "runs"
        or parts[-1] != "runtime"
    ):
        raise ValueError(
            "Fresh-start cleanup only accepts "
            "<repo>/scenarios/<domain>/runs/<run_id>/runtime "
            "or <runtime_root>/<run_id>; "
            f"refusing to delete {runtime}"
        )
    if runtime == root or runtime.parent == root:
        raise ValueError(f"Refusing unsafe runtime cleanup target: {runtime}")
    return runtime


def _seed_conversion_artifacts(
    *,
    source_runtime: str | Path,
    target_runtime: Path,
    repository_root: Path,
    selected_hashes: Iterable[str] | None,
    include_stitched_markdown: bool,
) -> tuple[Path, list[str]]:
    """Copy only PDF-conversion Markdown into an otherwise clean runtime."""
    source = Path(source_runtime)
    if not source.is_absolute():
        source = repository_root.resolve() / source
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"Conversion artifact source runtime does not exist: {source}")
    if source == target_runtime:
        raise ValueError("Conversion artifact source and clean target runtime must differ")

    requested = {
        str(value).strip()
        for value in (selected_hashes or [])
        if str(value).strip()
    }
    copied: list[str] = []
    for source_doi in sorted(source.iterdir()):
        if not source_doi.is_dir():
            continue
        doi_hash = source_doi.name
        if requested and doi_hash not in requested:
            continue
        allowed_names = {
            f"{doi_hash}.md",
            f"{doi_hash}_text.md",
            f"{doi_hash}_tables.md",
            f"{doi_hash}_vision.md",
            f"{doi_hash}_si.md",
            f"{doi_hash}_si_text.md",
            f"{doi_hash}_si_tables.md",
            f"{doi_hash}_si_vision.md",
        }
        if include_stitched_markdown:
            allowed_names.add(f"{doi_hash}_stitched.md")
        for source_file in sorted(source_doi.iterdir()):
            if (
                not source_file.is_file()
                or source_file.name not in allowed_names
                or not source_file.name.endswith(_CONVERSION_ARTIFACT_SUFFIXES)
                or source_file.stat().st_size <= 0
            ):
                continue
            target_doi = target_runtime / doi_hash
            target_doi.mkdir(parents=True, exist_ok=True)
            target_file = target_doi / source_file.name
            shutil.copy2(source_file, target_file)
            copied.append(target_file.relative_to(target_runtime).as_posix())
    return source, copied


def prepare_pipeline_runtime(
    *,
    data_dir: str | Path,
    repository_root: Path,
    config_path: str | Path,
    selected_hashes: Iterable[str] | None,
    resume_existing_runtime: bool,
    reuse_conversion_artifacts_from: str | Path | list[str | Path] | None = None,
    reuse_stitched_markdown: bool = False,
) -> Path:
    """Create either a fully clean runtime or an explicit resume session.

    Fresh mode is the default and removes the entire configured runtime tree,
    including per-DOI folders, central memory, extension global state, DOI
    mappings, and completion markers. A partial hash selection never narrows
    cleanup: it only narrows work performed after the full cleanup.
    """
    if resume_existing_runtime:
        runtime = Path(data_dir)
        if not runtime.is_absolute():
            runtime = repository_root.resolve() / runtime
        runtime = runtime.resolve()
        validate_runtime_path_budget(runtime)
        runtime.mkdir(parents=True, exist_ok=True)
        return runtime

    runtime = _assert_safe_runtime_path(data_dir, repository_root)
    manifest_path = runtime / RUNTIME_MANIFEST

    if runtime.exists():
        _remove_runtime_tree(runtime)
    runtime.mkdir(parents=True, exist_ok=False)

    conversion_sources: list[Path] = []
    conversion_artifacts: list[str] = []
    if reuse_conversion_artifacts_from:
        configured_sources = (
            reuse_conversion_artifacts_from
            if isinstance(reuse_conversion_artifacts_from, list)
            else [reuse_conversion_artifacts_from]
        )
        for configured_source in configured_sources:
            source, copied = _seed_conversion_artifacts(
                source_runtime=configured_source,
                target_runtime=runtime,
                repository_root=repository_root,
                selected_hashes=selected_hashes,
                include_stitched_markdown=reuse_stitched_markdown,
            )
            conversion_sources.append(source)
            conversion_artifacts.extend(copied)
        conversion_artifacts = sorted(set(conversion_artifacts))

    manifest = {
        "schema_version": 1,
        "mode": "fresh",
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(Path(config_path).resolve()),
        "selected_hashes": list(selected_hashes or []),
        "cleanup_scope": [
            "entire_runtime",
            "all_per_doi_directories",
            "central_memory",
            "global_state.json",
            "ontospecies_global_state.json",
            "ontomops_global_state.json",
            "completion_markers",
        ],
        "reused_conversion_source": (
            str(conversion_sources[0]) if len(conversion_sources) == 1 else ""
        ),
        "reused_conversion_sources": [str(source) for source in conversion_sources],
        "reused_conversion_artifacts": conversion_artifacts,
        "reused_stitched_markdown": bool(reuse_stitched_markdown),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime
