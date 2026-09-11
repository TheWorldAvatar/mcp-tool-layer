"""Batch layout for generated extraction-prompt packages.

Each campaign writes a self-contained package under
`generated/runs/<YYYYMMDD_HHMMSS>_<tag>/`. `generated/current.json` points at
the active batch so the extraction runtime can pick one tree without mixing
results from another model or retry.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.locations import repository_root, resolve_under_repository

GENERATED_DIRNAME = "generated"
RUNS_DIRNAME = "runs"
CURRENT_POINTER_NAME = "current.json"
BATCH_FILENAME = "batch.json"
BATCH_SCHEMA = "generation-batch.v1"
CURRENT_SCHEMA = "generation-current.v1"
PACKAGE_MARKERS = (
    "prompts",
    "iterations",
    "derived_inputs",
    "ontology_structures",
    "semantic_planning",
)


def generated_home(repository: Path | None = None) -> Path:
    """Return `<repo>/generated`, the parent of batch runs."""
    return (repository or repository_root()) / GENERATED_DIRNAME


def runs_dir(home: Path | None = None) -> Path:
    return (home or generated_home()) / RUNS_DIRNAME


def sanitize_run_tag(tag: str) -> str:
    text = str(tag or "").strip()
    cleaned = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text
    )
    return cleaned.strip("._") or "gen"


def mint_run_id(tag: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = sanitize_run_tag(tag)
    return f"{stamp}_{slug}"


def looks_like_package(path: Path) -> bool:
    root = Path(path)
    if (root / BATCH_FILENAME).is_file():
        return True
    return any((root / name).is_dir() for name in PACKAGE_MARKERS)


def _is_generated_home(path: Path, home: Path) -> bool:
    try:
        return path.resolve() == home.resolve()
    except OSError:
        return False


def _relative_to_repo(path: Path, repository: Path | None = None) -> str:
    repo = repository or repository_root()
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_batch_manifest(run_root: Path) -> dict[str, Any]:
    return read_json_object(Path(run_root) / BATCH_FILENAME)


def update_batch_manifest(
    run_root: Path,
    *,
    tag: str = "",
    model: str = "",
    ontologies: list[str] | None = None,
    settings: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)
    existing = read_batch_manifest(root)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    names = [
        str(item).strip()
        for item in (
            list(existing.get("ontologies") or []) + list(ontologies or [])
        )
        if str(item).strip()
    ]
    unique_names: list[str] = []
    for name in names:
        if name not in unique_names:
            unique_names.append(name)
    merged_settings = dict(existing.get("settings") or {})
    merged_settings.update(settings or {})
    payload: dict[str, Any] = {
        **existing,
        "schema_version": BATCH_SCHEMA,
        "run_id": str(existing.get("run_id") or root.name),
        "tag": str(tag or existing.get("tag") or "").strip(),
        "model": str(model or existing.get("model") or "").strip(),
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "ontologies": unique_names,
        "settings": merged_settings,
        "path": _relative_to_repo(root),
    }
    if extra:
        payload.update(extra)
    write_json_object(root / BATCH_FILENAME, payload)
    return payload


def write_current_pointer(
    run_root: Path,
    *,
    home: Path | None = None,
    repository: Path | None = None,
) -> Path:
    repo = repository or repository_root()
    generated = home or generated_home(repo)
    root = Path(run_root).resolve()
    pointer = generated / CURRENT_POINTER_NAME
    write_json_object(
        pointer,
        {
            "schema_version": CURRENT_SCHEMA,
            "run_id": root.name,
            "path": _relative_to_repo(root, repo),
        },
    )
    return pointer


def read_current_pointer(
    home: Path | None = None,
    *,
    repository: Path | None = None,
) -> Path | None:
    repo = repository or repository_root()
    generated = home or generated_home(repo)
    payload = read_json_object(generated / CURRENT_POINTER_NAME)
    raw = str(payload.get("path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (repo / path).resolve()
    return path if path.is_dir() else None


def list_generation_runs(home: Path | None = None) -> list[Path]:
    folder = runs_dir(home)
    if not folder.is_dir():
        return []
    runs = [path for path in folder.iterdir() if path.is_dir()]
    return sorted(runs, key=lambda path: path.name, reverse=True)


def find_runs_by_tag(tag: str, home: Path | None = None) -> list[Path]:
    slug = sanitize_run_tag(tag)
    matched: list[Path] = []
    for path in list_generation_runs(home):
        if path.name == slug or path.name.endswith(f"_{slug}"):
            matched.append(path)
    return matched


def find_generation_run(
    selector: str,
    *,
    home: Path | None = None,
    repository: Path | None = None,
) -> Path:
    text = str(selector or "").strip()
    if not text:
        raise FileNotFoundError("generation run selector is empty")
    repo = repository or repository_root()
    generated = home or generated_home(repo)
    folded = text.casefold()
    if folded in {"current", "latest", "legacy", "home"}:
        if folded == "current":
            current = read_current_pointer(generated, repository=repo)
            if current is None:
                raise FileNotFoundError("generated/current.json does not point at a run")
            return current
        if folded in {"legacy", "home"}:
            return generated.resolve()
        latest = list_generation_runs(generated)
        if not latest:
            raise FileNotFoundError(f"no generation runs under {runs_dir(generated)}")
        return latest[0]

    as_path = Path(text)
    if as_path.is_absolute() and as_path.is_dir():
        return as_path.resolve()
    repo_candidate = resolve_under_repository(text)
    if repo_candidate.is_dir():
        return repo_candidate

    folder = runs_dir(generated)
    exact = folder / text
    if exact.is_dir():
        return exact.resolve()

    tagged = find_runs_by_tag(text, generated)
    if len(tagged) == 1:
        return tagged[0].resolve()
    if len(tagged) > 1:
        names = ", ".join(path.name for path in tagged)
        raise FileNotFoundError(
            f"generation run tag {text!r} is ambiguous: {names}"
        )
    raise FileNotFoundError(f"generation run not found: {text}")


def mint_generation_run(
    *,
    tag: str,
    model: str = "",
    settings: dict[str, Any] | None = None,
    ontologies: list[str] | None = None,
    set_current: bool = True,
    home: Path | None = None,
    repository: Path | None = None,
    run_id: str | None = None,
) -> Path:
    repo = repository or repository_root()
    generated = home or generated_home(repo)
    folder = runs_dir(generated)
    folder.mkdir(parents=True, exist_ok=True)
    ident = str(run_id or "").strip() or mint_run_id(tag)
    root = folder / ident
    if root.exists():
        raise FileExistsError(f"generation run already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    update_batch_manifest(
        root,
        tag=sanitize_run_tag(tag),
        model=model,
        ontologies=ontologies,
        settings=settings,
    )
    if set_current:
        write_current_pointer(root, home=generated, repository=repo)
    return root


def _coerce_to_package(path: Path, home: Path, repository: Path) -> Path:
    resolved = path.resolve()
    if looks_like_package(resolved) and not _is_generated_home(resolved, home):
        return resolved
    if _is_generated_home(resolved, home) or (
        resolved.is_dir() and ((resolved / RUNS_DIRNAME).is_dir() or (resolved / CURRENT_POINTER_NAME).is_file())
    ):
        current = read_current_pointer(home if _is_generated_home(resolved, home) else resolved, repository=repository)
        if current is not None:
            return current
        latest = list_generation_runs(home if _is_generated_home(resolved, home) else resolved)
        if latest:
            return latest[0]
    return resolved


def resolve_generated_package_root(
    *,
    explicit: str | Path | None = None,
    generation_run: str | None = None,
    home: Path | None = None,
    repository: Path | None = None,
    env_override: bool = True,
) -> Path:
    """Resolve the active generated package (one batch), not the `generated/` home."""
    repo = repository or repository_root()
    generated = home or generated_home(repo)
    if explicit:
        return _coerce_to_package(
            resolve_under_repository(explicit)
            if not Path(explicit).is_absolute()
            else Path(explicit),
            generated,
            repo,
        )
    if generation_run:
        return find_generation_run(
            str(generation_run), home=generated, repository=repo
        )
    if env_override:
        env = str(os.environ.get("TWA_GENERATED_ARTIFACT_ROOT") or "").strip()
        if env:
            path = Path(env).expanduser()
            path = (
                path.resolve()
                if path.is_absolute()
                else (repo / path).resolve()
            )
            return _coerce_to_package(path, generated, repo)
    current = read_current_pointer(generated, repository=repo)
    if current is not None:
        return current
    latest = list_generation_runs(generated)
    if latest:
        return latest[0]
    return generated


def is_run_package(path: Path, home: Path | None = None) -> bool:
    generated = home or generated_home()
    try:
        return path.resolve().is_relative_to(runs_dir(generated).resolve())
    except (OSError, ValueError):
        return False


def resolve_generation_output_root(
    *,
    output_root: str | Path | None = None,
    generation_run: str | None = None,
    use_current: bool = False,
    tag: str = "",
    model: str = "",
    settings: dict[str, Any] | None = None,
    set_current: bool = True,
    home: Path | None = None,
    repository: Path | None = None,
) -> Path:
    """Pick an existing batch or mint a new `generated/runs/<stamp>_<tag>/`."""
    repo = repository or repository_root()
    generated = home or generated_home(repo)
    if output_root:
        root = (
            Path(output_root)
            if Path(output_root).is_absolute()
            else resolve_under_repository(output_root)
        )
        root.mkdir(parents=True, exist_ok=True)
        update_batch_manifest(
            root,
            tag=tag or root.name,
            model=model,
            settings=settings,
        )
        if set_current:
            write_current_pointer(root, home=generated, repository=repo)
        return root.resolve()

    if generation_run:
        root = find_generation_run(
            str(generation_run), home=generated, repository=repo
        )
        update_batch_manifest(root, tag=tag, model=model, settings=settings)
        if set_current:
            write_current_pointer(root, home=generated, repository=repo)
        return root

    if use_current:
        current = read_current_pointer(generated, repository=repo)
        if current is None:
            raise FileNotFoundError(
                "generated/current.json is missing; pass --tag to mint a run"
            )
        update_batch_manifest(current, tag=tag, model=model, settings=settings)
        return current

    slug = sanitize_run_tag(tag) if tag else ""
    if slug:
        matches = find_runs_by_tag(slug, generated)
        if len(matches) == 1:
            root = matches[0]
            update_batch_manifest(root, tag=slug, model=model, settings=settings)
            if set_current:
                write_current_pointer(root, home=generated, repository=repo)
            return root
        if len(matches) > 1:
            names = ", ".join(path.name for path in matches)
            raise FileExistsError(
                f"generation tag {slug!r} matches multiple runs: {names}. "
                "Pass --generation-run or --output-root."
            )

    current = read_current_pointer(generated, repository=repo)
    if current is not None and not slug:
        update_batch_manifest(current, tag=tag, model=model, settings=settings)
        return current

    return mint_generation_run(
        tag=slug or "gen",
        model=model,
        settings=settings,
        set_current=set_current,
        home=generated,
        repository=repo,
    )


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def campaign_log_path(tag: str, home: Path | None = None) -> Path:
    generated = home or generated_home()
    safe = _SAFE_NAME.sub("_", sanitize_run_tag(tag))
    return generated / "campaigns" / f"{safe}.log"
