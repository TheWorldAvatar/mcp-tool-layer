"""Stream full-scale offline workflow rows to NDJSON sidecars (JSON manifest stays compact)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

RowDict = Dict[str, Any]
RowLike = Union[RowDict, str, int, float, bool, None]

DEFAULT_SIDECAR_THRESHOLD = 5


def _safe_slug(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name.strip()).strip("_") or "rows"


def _row_to_ndjson_obj(row: RowLike) -> Any:
    if isinstance(row, dict):
        return row
    return {"_value": row}


def write_ndjson_rows(path: Path, rows: Iterable[RowLike]) -> int:
    """Write one JSON object per line; returns row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_row_to_ndjson_obj(row), ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


def count_ndjson_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def iter_ndjson_rows(path: Path) -> Iterable[Any]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def persist_offline_sidecar(
    result: Dict[str, Any],
    json_path: Path,
    *,
    row_threshold: int = DEFAULT_SIDECAR_THRESHOLD,
) -> Dict[str, Any]:
    """
    Persist large offline row lists next to the workflow JSON recording.

    Full-scale computation stays in memory during the run; this streams rows to
    disk without building one giant JSON blob.
    """
    base = json_path.with_suffix("")
    artifacts: List[Dict[str, Any]] = []

    for step in result.get("call_trace") or []:
        rows = step.get("rows") or []
        if not isinstance(rows, list) or len(rows) <= row_threshold:
            continue
        step_no = step.get("step", "x")
        tool = step.get("tool") or step.get("name") or step.get("transform") or "step"
        fname = f"{base.name}_step{step_no}_{_safe_slug(str(tool))}.ndjson"
        path = json_path.parent / fname
        row_count = write_ndjson_rows(path, rows)
        artifacts.append(
            {
                "kind": "call_trace",
                "step": step_no,
                "tool": tool,
                "path": str(path.resolve()),
                "row_count": row_count,
            }
        )

    for name, value in (result.get("variables") or {}).items():
        if str(name).startswith("online_limits."):
            continue
        if not isinstance(value, list) or len(value) <= row_threshold:
            continue
        fname = f"{base.name}_var_{_safe_slug(str(name))}.ndjson"
        path = json_path.parent / fname
        row_count = write_ndjson_rows(path, value)
        artifacts.append(
            {
                "kind": "variable",
                "name": name,
                "path": str(path.resolve()),
                "row_count": row_count,
            }
        )

    if not artifacts:
        return {}

    return {
        "format": "ndjson",
        "row_threshold": row_threshold,
        "artifacts": artifacts,
    }
