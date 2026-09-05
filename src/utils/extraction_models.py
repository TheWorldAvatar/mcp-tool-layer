import json
import os
from pathlib import Path
from functools import lru_cache


def _config_path() -> Path:
    override = (
        os.environ.get("TWA_EXTRACTION_MODELS_PATH")
        or os.environ.get("EXTRACTION_MODELS_PATH")
        or ""
    ).strip()
    return Path(override) if override else Path("configs") / "extraction_models.json"


@lru_cache(maxsize=4)
def _load_model_map(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise RuntimeError(f"Extraction model mapping file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise RuntimeError(f"Failed to read extraction model mapping: {e}")


def get_extraction_model(process_key: str) -> str:
    if str(process_key).startswith("model:"):
        model = str(process_key).split(":", 1)[1].strip()
        if not model:
            raise RuntimeError("Inline extraction model must not be empty")
        return model
    mapping = _load_model_map(str(_config_path()))
    if process_key not in mapping or not str(mapping.get(process_key)).strip():
        raise RuntimeError(f"Extraction model not configured for key: {process_key}")
    return str(mapping[process_key]).strip()


