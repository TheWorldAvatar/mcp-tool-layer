"""Shared data/cache directory resolution (local repo or Docker volume)."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Project root (parent of the ``mini_marie`` Python package). Same path for dev and deploy."""
    return Path(__file__).resolve().parents[1]


def configs_dir() -> Path:
    """MCP / agent JSON configs (``configs/`` under deploy root)."""
    return repo_root() / "configs"


def merged_tll_dir() -> Path:
    """Local MOP synthesis RDF corpus (``evaluation/data/merged_tll``)."""
    override = os.environ.get("MINI_MARIE_MERGED_TLL_DIR", "").strip()
    if override:
        return Path(override)
    return repo_root() / "evaluation" / "data" / "merged_tll"


def data_dir() -> Path:
    """Persistent data root; override with MINI_MARIE_DATA_DIR in Docker."""
    override = os.environ.get("MINI_MARIE_DATA_DIR", "").strip()
    if override:
        path = Path(override)
    else:
        path = repo_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def mini_marie_cache_root() -> Path:
    root = data_dir() / "mini_marie_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_runtime_dirs() -> None:
    """Create directories expected by ``models.locations`` and caches."""
    root = repo_root()
    for rel in (
        "data",
        "data/log",
        "raw_data",
        "configs",
        "evaluation/data/merged_tll",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)
