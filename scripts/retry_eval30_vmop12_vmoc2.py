"""Retry the two official-run failures in place: VMOP-12 extract+KG, VMOC-2 KG."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = (
    REPO / "scenarios/mops/runs/20260820_eval30_pubchem-dedup-e2e/runtime"
)
KG_MCP = REPO / "configs/test_mcp_config_ontosynthesis_20260820_eval30_pubchem-dedup-e2e.json"


def _base_config() -> dict:
    return {
        "project_root": str(REPO),
        "data_dir": str(RUNTIME),
        "meta_task_config": "configs/meta_task/meta_task_config.json",
        "ontology_name": "ontosynthesis",
        "only_extraction_iterations": [2, 3, 4],
        "start_main_kg_iteration": 2,
    }


def _extraction_config(entity_safe: str) -> dict:
    config = _base_config()
    config["only_entity_safe"] = entity_safe
    return config


def _kg_config(entity_safe: str) -> dict:
    config = _base_config()
    config["only_entity_safe"] = entity_safe
    config["test_mcp_config"] = str(KG_MCP)
    return config


def _clear_vmop12_later_artifacts() -> list[str]:
    paper = RUNTIME / "7ba809dd"
    removed: list[str] = []
    patterns = [
        "mcp_run/iter2_hints_VMOP-12.txt",
        "mcp_run/iter3_hints_VMOP-12.txt",
        "mcp_run/iter4_hints_VMOP-12.txt",
        "mcp_run/iter3_hints_VMOP-12.txt.semantic_audit.json",
        "mcp_run/iter4_hints_VMOP-12.txt.semantic_audit.json",
    ]
    for relative in patterns:
        path = paper / relative
        if path.is_file():
            path.unlink()
            removed.append(relative)
    return removed


def _hint_summary(hash_value: str, entity_safe: str) -> dict:
    paper = RUNTIME / hash_value
    rows = {}
    for iteration in (2, 3, 4):
        path = paper / "mcp_run" / f"iter{iteration}_hints_{entity_safe}.txt"
        if not path.is_file():
            rows[f"iter{iteration}"] = None
            continue
        text = path.read_text(encoding="utf-8")
        rows[f"iter{iteration}"] = {
            "chars": len(text),
            "kb": round(len(text) / 1024, 1),
            "purity_loop": "99.999" in text,
        }
    return rows


def _published(hash_value: str, needle: str) -> list[str]:
    output = RUNTIME / hash_value / "ontosynthesis_output"
    if not output.is_dir():
        return []
    return sorted(path.name for path in output.glob(f"*{needle}*.ttl"))


def main() -> None:
    os.chdir(REPO)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(
        REPO / "ai_generated_contents_ontosyn_regen_v3"
    )
    os.environ["TWA_AGENTIC_DATA_DIR"] = str(RUNTIME)
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env", override=True)

    from src.pipelines.main_kg_building.build import run_step as run_kg
    from src.pipelines.main_ontology_extractions.extract import run_step as run_extract

    cleared = _clear_vmop12_later_artifacts()
    print(json.dumps({"phase": "prepared", "cleared_vmop12": cleared}, indent=2), flush=True)

    print("=== VMOC-2 KG iter2-4 ===", flush=True)
    vmoc2_ok = run_kg("88c21a74", _kg_config("VMOC-2"))
    print(
        json.dumps(
            {
                "phase": "vmoc2_kg",
                "ok": vmoc2_ok,
                "published": _published("88c21a74", "VMOC-2"),
            },
            indent=2,
        ),
        flush=True,
    )

    print("=== VMOP-12 extraction iter2-4 ===", flush=True)
    vmop12_extract_ok = run_extract("7ba809dd", _extraction_config("VMOP-12"))
    hints = _hint_summary("7ba809dd", "VMOP-12")
    print(
        json.dumps(
            {"phase": "vmop12_extract", "ok": vmop12_extract_ok, "hints": hints},
            indent=2,
        ),
        flush=True,
    )
    if not vmop12_extract_ok or (hints.get("iter2") or {}).get("purity_loop"):
        raise SystemExit("VMOP-12 extraction failed or still looped")

    print("=== VMOP-12 KG iter2-4 ===", flush=True)
    vmop12_kg_ok = run_kg("7ba809dd", _kg_config("VMOP-12"))
    print(
        json.dumps(
            {
                "phase": "vmop12_kg",
                "ok": vmop12_kg_ok,
                "published": _published("7ba809dd", "VMOP-12"),
            },
            indent=2,
        ),
        flush=True,
    )

    summary = {
        "phase": "done",
        "vmoc2_kg_ok": vmoc2_ok,
        "vmop12_extract_ok": vmop12_extract_ok,
        "vmop12_kg_ok": vmop12_kg_ok,
        "vmop12_hints": hints,
        "published_vmoc2": _published("88c21a74", "VMOC-2"),
        "published_vmop12": _published("7ba809dd", "VMOP-12"),
    }
    print(json.dumps(summary, indent=2), flush=True)
    if not vmoc2_ok or not vmop12_kg_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
