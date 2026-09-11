"""Runtime extraction-model map. Separate from domain-config generation models."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from models.locations import resolve_under_repository
from models.locked_llm import LOCKED_EXTRACTION_MODEL, canonicalize_chat_model


def _config_path() -> Path:
    return resolve_under_repository(Path("configs") / "extraction_models.json")


@lru_cache(maxsize=4)
def _load_model_map(config_path: str) -> dict[str, str]:
    path = Path(config_path)
    if not path.is_file():
        raise RuntimeError(f"Extraction model mapping file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Extraction model mapping must be a JSON object")
    return {str(key): str(value).strip() for key, value in payload.items()}


def get_extraction_model(process_key: str, *, default: str = "") -> str:
    if str(process_key).startswith("model:"):
        model = str(process_key).split(":", 1)[1].strip()
        if not model:
            raise RuntimeError("Inline extraction model must not be empty")
        return canonicalize_chat_model(model)
    mapping = _load_model_map(str(_config_path()))
    if process_key in mapping and mapping[process_key]:
        return canonicalize_chat_model(mapping[process_key])
    if default:
        return canonicalize_chat_model(default)
    if "default_model" in mapping:
        return canonicalize_chat_model(mapping["default_model"])
    return LOCKED_EXTRACTION_MODEL
