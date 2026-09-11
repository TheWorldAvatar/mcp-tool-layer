"""Repository path helpers. Relative paths resolve from the repo root, not cwd."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional at import time
    load_dotenv = None

_REPO_MARKERS = ("pyproject.toml", "configs/domains", "data/ontologies")


def _looks_like_repository(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (
        path / "configs" / "domains"
    ).is_dir()


def discover_repository_root(start: Path | None = None) -> Path:
    """Locate the clone root from this file, ROOT_DIR, or a starting path."""
    env = str(os.getenv("ROOT_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(start or __file__).resolve()
    if here.is_file():
        here = here.parent
    for candidate in (here, *here.parents):
        if _looks_like_repository(candidate):
            return candidate
    return Path(__file__).resolve().parents[1]


def _load_repo_dotenv(root: Path) -> None:
    if load_dotenv is None:
        return
    env_file = root / ".env"
    if env_file.is_file():
        load_dotenv(env_file)


_DISCOVERED_ROOT = discover_repository_root()
_load_repo_dotenv(_DISCOVERED_ROOT)
# ROOT_DIR in .env wins after dotenv load.
ROOT_DIR = str(discover_repository_root())
DATA_DIR = os.getenv("DATA_DIR", str(Path(ROOT_DIR) / "data"))
CONFIGS_DIR = os.getenv("CONFIGS_DIR", str(Path(ROOT_DIR) / "configs"))
MCP_CONFIGS_DIR = os.getenv(
    "MCP_CONFIGS_DIR",
    str(Path(CONFIGS_DIR) / "mcp"),
)
DATA_LOG_DIR = os.getenv("DATA_LOG_DIR", str(Path(ROOT_DIR) / "data" / "log"))
CBU_DATABASE_PATH = os.getenv(
    "CBU_DATABASE_PATH",
    str(Path(ROOT_DIR) / "data" / "cbu_database.csv"),
)
DATA_CCDC_DIR = os.getenv(
    "DATA_CCDC_DIR",
    str(Path(DATA_DIR) / "ontologies" / "ccdc"),
)


def repository_root() -> Path:
    return Path(ROOT_DIR).resolve()


def default_output_root() -> Path:
    override = str(os.getenv("TWA_GENERATED_ARTIFACT_ROOT") or "").strip()
    if override:
        path = Path(override).expanduser()
        return path.resolve() if path.is_absolute() else (repository_root() / path).resolve()
    return repository_root() / "generated"


def resolve_under_repository(value: str | Path) -> Path:
    """Resolve a path against the repository root. Absolute paths are kept."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    repo_candidate = (repository_root() / path).resolve()
    if repo_candidate.exists():
        return repo_candidate
    cwd_candidate = path.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return repo_candidate
