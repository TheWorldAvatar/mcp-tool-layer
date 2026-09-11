"""Write v2 one-shot main-KG token traces for OntoLogX budget matching."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_runtime.names import entity_scope_name

MAIN_KG_TRACE_DIR = "main_kg_building"


def write_main_kg_token_trace(
    *,
    doi_folder: Path,
    entity_label: str,
    entity_uri: str,
    metadata: dict[str, Any] | None,
) -> Path:
    """Persist Pipeline KG agent tokens so OX can use an equivalent budget."""
    usage = dict((metadata or {}).get("aggregated_usage") or {})
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or prompt + completion
    calls = int(usage.get("calls") or 0)
    scope = entity_scope_name(entity_label, entity_uri)
    dest_dir = Path(doi_folder) / "responses" / MAIN_KG_TRACE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{scope}.trace.json"
    payload = {
        "schema_version": "pipeline-kg-token-trace.v1",
        "scope": scope,
        "entity_label": entity_label,
        "entity_uri": entity_uri,
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "calls": calls,
        },
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  [OK] Wrote KG token trace {dest.name} total_tokens={total}")
    return dest
