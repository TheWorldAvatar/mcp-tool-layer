"""
TTL publishing utilities for the pipeline.

Goal: provide a super reliable mechanism to "publish" the latest ontology TTLs
into a deterministic output folder with deterministic filenames.

Key requirements:
- Do NOT hardcode ontology names in execution scripts.
- Output locations/names are driven by config (meta_task_config) with safe defaults.
- Robust to different MCP server persistence conventions (memory/ + exports/ fallbacks).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class OutputNamingConfig:
    output_dir: str
    top_ttl_name: str
    entity_ttl_pattern: str


def load_meta_task_config(config_path: str = "configs/meta_task/meta_task_config.json") -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def get_main_ontology_name(meta_cfg: dict, default: str = "ontosynthesis") -> str:
    try:
        return (meta_cfg.get("ontologies", {}).get("main", {}).get("name") or default).strip() or default
    except Exception:
        return default


def _render(template: str, **kwargs: str) -> str:
    s = template or ""
    try:
        return s.format(**kwargs)
    except Exception:
        # fall back to raw string if formatting fails
        return s


def get_output_naming_config(
    *,
    meta_cfg: dict,
    ontology_name: str,
    default_output_dir: Optional[str] = None,
) -> OutputNamingConfig:
    main_cfg = (meta_cfg or {}).get("ontologies", {}).get("main", {}) or {}

    # Support multiple possible schema keys (backward/forward compatible).
    output_dir_tpl = (
        main_cfg.get("output_dir")
        or (main_cfg.get("output", {}) or {}).get("dir")
        or default_output_dir
        or "{ontology_name}_output"
    )
    top_ttl_name = (
        main_cfg.get("top_ttl_name")
        or (main_cfg.get("output", {}) or {}).get("top_ttl_name")
        or "top.ttl"
    )
    entity_ttl_pattern = (
        main_cfg.get("entity_ttl_pattern")
        or (main_cfg.get("output", {}) or {}).get("entity_ttl_pattern")
        or "{entity_safe}.ttl"
    )

    output_dir = _render(str(output_dir_tpl), ontology_name=ontology_name).strip() or f"{ontology_name}_output"
    top_ttl_name = str(top_ttl_name).strip() or "top.ttl"
    entity_ttl_pattern = str(entity_ttl_pattern).strip() or "{entity_safe}.ttl"

    return OutputNamingConfig(output_dir=output_dir, top_ttl_name=top_ttl_name, entity_ttl_pattern=entity_ttl_pattern)


def _latest_export(exports_dir: str, prefix: str) -> Optional[str]:
    try:
        if not os.path.isdir(exports_dir):
            return None
        cands = [
            os.path.join(exports_dir, f)
            for f in os.listdir(exports_dir)
            if f.lower().startswith(prefix.lower() + "_") and f.lower().endswith(".ttl")
        ]
        if not cands:
            return None
        cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return cands[0]
    except Exception:
        return None


def publish_ttl(
    *,
    doi_hash: str,
    ontology_name: str,
    entity_safe: str,
    data_dir: str = "data",
    meta_cfg: Optional[dict] = None,
    src_candidates: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """
    Publish the best-available entity TTL to `data/<hash>/<output_dir>/<entity_filename>`.

    Returns the destination path on success, None on failure.
    """
    meta_cfg = meta_cfg or load_meta_task_config()
    naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)

    doi_folder = os.path.join(data_dir, doi_hash)
    out_dir = os.path.join(doi_folder, naming.output_dir)
    out_name = _render(naming.entity_ttl_pattern, entity_safe=entity_safe, ontology_name=ontology_name).strip()
    out_name = out_name or f"{entity_safe}.ttl"
    dest_path = os.path.join(out_dir, out_name)

    mem_path = os.path.join(doi_folder, "memory", f"{entity_safe}.ttl")
    exports_dir = os.path.join(doi_folder, "exports")
    exp_path = _latest_export(exports_dir=exports_dir, prefix=entity_safe)

    candidates: list[str] = []
    # Prefer canonical MCP persistence locations first (memory/ then latest exports/).
    # `src_candidates` is treated as a fallback, not an override, to avoid publishing
    # stale intermediate snapshots when memory is healthier/newer.
    candidates.extend([mem_path, exp_path or ""])
    if src_candidates:
        candidates.extend([c for c in src_candidates if c])

    src_path = next((p for p in candidates if p and os.path.exists(p)), None)
    if not src_path:
        return None

    try:
        os.makedirs(out_dir, exist_ok=True)
        shutil.copy2(src_path, dest_path)
        return dest_path
    except Exception:
        return None


def publish_top_ttl(
    *,
    doi_hash: str,
    ontology_name: str,
    data_dir: str = "data",
    meta_cfg: Optional[dict] = None,
    src_candidates: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """
    Publish the best-available "top" TTL to `data/<hash>/<output_dir>/<top_ttl_name>`.
    """
    meta_cfg = meta_cfg or load_meta_task_config()
    naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)

    doi_folder = os.path.join(data_dir, doi_hash)
    out_dir = os.path.join(doi_folder, naming.output_dir)
    dest_path = os.path.join(out_dir, naming.top_ttl_name)

    mem_path = os.path.join(doi_folder, "memory", "top.ttl")
    exports_dir = os.path.join(doi_folder, "exports")
    exp_path = _latest_export(exports_dir=exports_dir, prefix="top")

    candidates: list[str] = []
    candidates.extend([mem_path, exp_path or ""])
    if src_candidates:
        candidates.extend([c for c in src_candidates if c])

    src_path = next((p for p in candidates if p and os.path.exists(p)), None)
    if not src_path:
        return None

    try:
        os.makedirs(out_dir, exist_ok=True)
        shutil.copy2(src_path, dest_path)
        return dest_path
    except Exception:
        return None

