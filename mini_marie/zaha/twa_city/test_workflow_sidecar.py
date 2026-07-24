"""Unit tests for offline NDJSON sidecar persistence."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from mini_marie.zaha.twa_city.workflow_sidecar import (
    count_ndjson_rows,
    iter_ndjson_rows,
    persist_offline_sidecar,
    write_ndjson_rows,
)


def test_write_and_count_ndjson() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rows.ndjson"
        rows = [{"building": f"http://ex/{i}", "height": i} for i in range(100)]
        assert write_ndjson_rows(path, rows) == 100
        assert count_ndjson_rows(path) == 100
        loaded = list(iter_ndjson_rows(path))
        assert len(loaded) == 100
        assert loaded[0]["height"] == 0


def test_persist_offline_sidecar_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "WF_TEST_offline_1.json"
        big_rows = [{"k": "x" * 50} for _ in range(20)]
        result = {
            "mode": "offline",
            "call_trace": [{"step": 1, "tool": "demo_tool", "rows": big_rows, "row_count": 20}],
            "variables": {"big_pool": big_rows},
        }
        manifest = persist_offline_sidecar(result, json_path, row_threshold=5)
        assert manifest["format"] == "ndjson"
        assert len(manifest["artifacts"]) == 2
        for art in manifest["artifacts"]:
            path = Path(art["path"])
            assert path.exists()
            assert count_ndjson_rows(path) == art["row_count"] == 20

        payload = {"sidecar": manifest, "rows_on_disk": "sidecar_ndjson"}
        json_path.write_text(json.dumps(payload), encoding="utf-8")
        assert json_path.stat().st_size < 5000
