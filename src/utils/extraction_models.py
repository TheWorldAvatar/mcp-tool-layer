import json
from pathlib import Path
from functools import lru_cache


CONFIG_PATH = Path("configs") / "extraction_models.json"


@lru_cache(maxsize=1)
def _load_model_map() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"Extraction model mapping file not found: {CONFIG_PATH}")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise RuntimeError(f"Failed to read extraction model mapping: {e}")


def get_extraction_model(process_key: str) -> str:
    mapping = _load_model_map()
    if process_key not in mapping or not str(mapping.get(process_key)).strip():
        raise RuntimeError(f"Extraction model not configured for key: {process_key}")
    return str(mapping[process_key]).strip()


