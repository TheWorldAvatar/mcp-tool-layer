"""Machine-local short runtime roots.

Windows MAX_PATH is the full absolute path. Scenario runtimes nested under
the repo already spend ~150 characters before any entity filename. Point
``runtime_root`` at a short directory *anywhere* on the machine:

    { "runtime_root": "C:/twa/r" }

Search order (first hit wins):

1. ``TWA_RUNTIME_ROOT``
2. ``TWA_MACHINE_CONFIG`` (JSON file)
3. ``~/.twa/config.json``
4. ``<repo>/twa.local.json``
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

MACHINE_CONFIG_NAME = "twa.local.json"
USER_CONFIG_PATH = Path.home() / ".twa" / "config.json"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._+-]+$")
ORIGIN_NAME = "runtime.origin.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def machine_config_candidates(repo: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    override = os.environ.get("TWA_MACHINE_CONFIG", "").strip()
    if override:
        candidates.append(Path(override))
    candidates.append(USER_CONFIG_PATH)
    if repo is not None:
        candidates.append(Path(repo) / MACHINE_CONFIG_NAME)
    return candidates


def load_machine_config(repo: Path | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in reversed(machine_config_candidates(repo)):
        if path.is_file():
            merged.update(_read_json(path))
    return merged


def configured_runtime_root(repo: Path | None = None) -> Path | None:
    raw = os.environ.get("TWA_RUNTIME_ROOT", "").strip()
    if not raw:
        raw = str(load_machine_config(repo).get("runtime_root") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _looks_populated(runtime: Path) -> bool:
    if not runtime.is_dir():
        return False
    if (runtime / "doi_to_hash.json").is_file():
        return True
    return any(child.is_dir() and RUN_ID_RE.fullmatch(child.name) for child in runtime.iterdir())


def write_runtime_origin(*, run_dir: Path, runtime: Path, runtime_root: Path | None) -> Path:
    payload = {
        "runtime": str(runtime.resolve()),
        "runtime_root": str(runtime_root.resolve()) if runtime_root else None,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / ORIGIN_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def link_run_dir_runtime(run_dir: Path, runtime: Path) -> Path | None:
    """Best-effort view at ``<run_dir>/runtime`` pointing at the real tree."""
    link = run_dir / "runtime"
    target = runtime.resolve()
    if link.exists() or link.is_symlink():
        try:
            if link.resolve() == target:
                return link
        except OSError:
            return None
        return None
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                return None
        else:
            link.symlink_to(target, target_is_directory=True)
    except OSError:
        return None
    return link if link.exists() else None


def is_safe_external_runtime(runtime: Path, runtime_root: Path) -> bool:
    try:
        relative = runtime.resolve().relative_to(runtime_root.resolve())
    except ValueError:
        return False
    return len(relative.parts) == 1 and bool(RUN_ID_RE.fullmatch(relative.parts[0]))


def resolve_scenario_runtime(
    *,
    repo: Path,
    run_dir: Path,
    run_id: str,
    configured_data_dir: str | Path | None = None,
) -> Path:
    """Resolve the paper runtime tree, preferring a short machine root.

    If a previous in-repo runtime already has papers and the short location
    is empty, keep the existing tree so an old run is not silently orphaned.
    """
    fallback = Path(configured_data_dir) if configured_data_dir else (run_dir / "runtime")
    if not fallback.is_absolute():
        fallback = repo / fallback

    root = configured_runtime_root(repo)
    if root is None:
        return fallback

    if not run_id or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"Refusing to place runtime under machine root; invalid run_id: {run_id!r}")

    short = root / run_id
    if _looks_populated(fallback) and not _looks_populated(short):
        return fallback
    return short
