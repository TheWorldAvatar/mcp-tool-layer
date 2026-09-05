"""Isolated presence-coverage repair probe: one paper, one entity, retry loops.

Does not touch scored run artifacts. Copies 1b9180ec into a new runtime, then:
1. re-runs iter2 extraction with the presence tool gate
2. re-runs iter3 KG from the iter2 checkpoint with the presence fact gate
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC_PAPER = (
    REPO
    / "scenarios/mops/runs/20260819_eval30_ontosyn-kg-queue/runtime/1b9180ec"
)
PROBE = REPO / "scenarios/mops/runs/20260820_presence_repair_probe"
RUNTIME = PROBE / "runtime"
PAPER = RUNTIME / "1b9180ec"
MCP_CONFIG = REPO / "configs/test_mcp_config_ontosynthesis_20260820_presence_repair.json"


def _safe_names(label: str) -> dict[str, str]:
    from src.pipelines.main_kg_building.build import _safe_name as kg_safe
    from src.pipelines.main_ontology_extractions.extract import _safe_name as extract_safe

    return {"kg": kg_safe(label), "extract": extract_safe(label)}


def prepare(*, keep_runtime: bool = False) -> dict:
    PROBE.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if keep_runtime:
        if not PAPER.exists():
            raise FileNotFoundError(f"probe runtime missing: {PAPER}")
    else:
        if PAPER.exists():
            shutil.rmtree(PAPER)
        shutil.copytree(SRC_PAPER, PAPER, ignore=shutil.ignore_patterns("exports"))
        for path in (PAPER / "mcp_run").glob("iter3_hints_*928519bcf8b6*"):
            path.unlink()
        for path in (PAPER / "responses" / "iter3_extraction").glob("*928519bcf8b6*"):
            path.unlink()
        for path in (PAPER / "prompts" / "iter3_extraction").glob("*928519bcf8b6*"):
            path.unlink()
    (PAPER / ".main_kg_building_done").unlink(missing_ok=True)
    (PAPER / ".main_ontology_extractions_done").unlink(missing_ok=True)

    entities = json.loads((PAPER / "mcp_run" / "iter1_top_entities.json").read_text(encoding="utf-8"))
    target = next(
        entity
        for entity in entities
        if "ADBDC" in str(entity.get("label") or "") and "VMOT-3" in str(entity.get("label") or "")
    )
    names = _safe_names(target["label"])
    entity_safe = names["kg"]
    extract_safe = names["extract"]
    for path in (PAPER / "responses" / "iter3_kg_building").glob("*5566651d8e0f*"):
        path.unlink()
    for path in (PAPER / "intermediate_ttl_files").glob("iteration_3_*5566651d8e0f*"):
        path.unlink()

    MCP_CONFIG.write_text(
        json.dumps(
            {
                "llm_created_mcp": {
                    "command": r"C:\Users\xz378\AppData\Local\anaconda3\envs\mcp_layer\python.exe",
                    "args": [
                        str(
                            REPO
                            / "ai_generated_contents_ontosyn_regen_v3/_launch_ontosynthesis_mcp.py"
                        )
                    ],
                    "transport": "stdio",
                    "cwd": str(REPO),
                    "env": {
                        "PYTHONPATH": str(REPO),
                        "PYTHONIOENCODING": "utf-8",
                        "TWA_GENERATED_ARTIFACT_ROOT": str(
                            REPO / "ai_generated_contents_ontosyn_regen_v3"
                        ),
                        "TWA_AGENTIC_DATA_DIR": str(RUNTIME),
                    },
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "entity_label": target["label"],
        "entity_safe": entity_safe,
        "extract_safe": extract_safe,
        "entity_uri": target.get("uri"),
    }


def extraction_config() -> dict:
    return {
        "project_root": str(REPO),
        "data_dir": str(RUNTIME),
        "meta_task_config": "configs/meta_task/meta_task_config.json",
        "ontology_name": "ontosynthesis",
        "test_mcp_config": str(MCP_CONFIG),
        "only_extraction_iterations": [3],
        "only_entity_safe": None,  # filled after prepare
        "presence_coverage_audit": {
            "enabled": True,
            "mcp_groups": [
                {
                    "name": "identity",
                    "any_of": [
                        "search_pubchem_by_name",
                        "search_pubchem_by_smiles",
                        "search_pubchem_advanced",
                        "get_pubchem_compound_by_cid",
                    ],
                    "applies": "when_configured",
                }
            ],
        },
    }


def kg_config(entity_safe: str) -> dict:
    return {
        "project_root": str(REPO),
        "data_dir": str(RUNTIME),
        "meta_task_config": "configs/meta_task/meta_task_config.json",
        "ontology_name": "ontosynthesis",
        "test_mcp_config": str(MCP_CONFIG),
        "start_main_kg_iteration": 3,
        "stop_main_kg_iteration": 3,
        "only_entity_safe": entity_safe,
        "presence_coverage_audit": {
            "enabled": True,
            "replace_llm_audits": True,
            "model": "gpt-4o",
            "mcp_groups": [],
        },
    }


def main() -> None:
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kg-only",
        action="store_true",
        help="Skip extraction and only retry KG iter3 from the copied iter2 baseline.",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only retry iter3 extraction with the tool-activity gate.",
    )
    parser.add_argument(
        "--keep-runtime",
        action="store_true",
        help="Do not recopy the paper; keep current extract hints and only refresh KG iter3.",
    )
    args = parser.parse_args()

    os.chdir(REPO)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(
        REPO / "ai_generated_contents_ontosyn_regen_v3"
    )
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env", override=True)
    info = prepare(keep_runtime=args.keep_runtime)
    print(json.dumps({"phase": "prepared", **info}, ensure_ascii=False, indent=2))
    extract_ok = None
    kg_ok = None
    if not args.kg_only:
        from src.pipelines.main_ontology_extractions.extract import run_step as run_extract

        ext_cfg = extraction_config()
        ext_cfg["only_entity_safe"] = info["extract_safe"]
        print("=== extraction iter3 presence repair ===")
        extract_ok = run_extract("1b9180ec", ext_cfg)
        print(json.dumps({"phase": "extraction", "ok": extract_ok}, indent=2))
    if not args.extract_only:
        from src.pipelines.main_kg_building.build import run_step as run_kg

        print("=== kg iter3 presence repair ===")
        kg_ok = run_kg("1b9180ec", kg_config(info["entity_safe"]))
        print(json.dumps({"phase": "kg", "ok": kg_ok}, indent=2))
    print(
        json.dumps(
            {"phase": "done", "ok": True, "extract_ok": extract_ok, "kg_ok": kg_ok, **info},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
