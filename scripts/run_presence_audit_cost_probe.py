"""Isolated KG iter3 cost probe: presence audit vs official framework integrity.

Does not touch scored run artifacts. Copies one completed paper/entity into a
private runtime and re-runs only KG iteration 3 with the default presence gate.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC_RUN = REPO / "scenarios/mops/runs/20260819_eval30_ontosyn-kg-queue"
PROBE_ROOT = REPO / "scenarios/mops/runs/20260820_presence_audit_cost_probe"

CASES = {
    "cr1": {
        "paper": "9e93418f",
        "label": "Synthesis of Cr-1",
        "kg_stem": "Synthesis_of_Cr-1--671dc4b1a117",
        "official_fi": SRC_RUN
        / "runtime/9e93418f/responses/iter3_kg_building"
        / "Synthesis_of_Cr-1--671dc4b1a117.attempt_1.framework_integrity_audit.json",
    },
    "vmoc4": {
        "paper": "88c21a74",
        "label": "VMOC-4",
        "kg_stem": "VMOC-4--5024ac3dbc60",
        "official_fi": SRC_RUN
        / "runtime/88c21a74/responses/iter3_kg_building"
        / "VMOC-4--5024ac3dbc60.attempt_1.framework_integrity_audit.json",
    },
}


def _official_fi_tokens(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    usage = data.get("token_usage") or {}
    return {
        "accepted": bool(data.get("accepted")),
        "elapsed_seconds": data.get("elapsed_seconds"),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "panels": len(data.get("microjudge_panels") or []),
    }


def prepare(case_name: str) -> dict:
    case = CASES[case_name]
    paper = case["paper"]
    stem = case["kg_stem"]
    runtime = PROBE_ROOT / case_name / "runtime"
    dest = runtime / paper
    src = SRC_RUN / "runtime" / paper
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("exports"))
    central_src = SRC_RUN / "runtime" / "central_memory"
    central_dest = runtime / "central_memory"
    if central_src.exists() and not central_dest.exists():
        shutil.copytree(central_src, central_dest)
    (dest / ".main_kg_building_done").unlink(missing_ok=True)
    for path in (dest / "responses" / "iter3_kg_building").glob(f"{stem}*"):
        path.unlink()
    for path in (dest / "intermediate_ttl_files").glob(f"iteration_3_{stem}*"):
        path.unlink()

    mcp_config = PROBE_ROOT / case_name / "mcp_config.json"
    mcp_config.write_text(
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
                        "TWA_AGENTIC_DATA_DIR": str(runtime),
                    },
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    from src.pipelines.main_kg_building.build import _safe_name

    return {
        "case": case_name,
        "paper": paper,
        "entity_label": case["label"],
        "entity_safe": _safe_name(case["label"]),
        "kg_stem": stem,
        "runtime": str(runtime),
        "mcp_config": str(mcp_config),
        "official_fi_iter3": _official_fi_tokens(case["official_fi"]),
    }


def kg_config(info: dict) -> dict:
    return {
        "project_root": str(REPO),
        "data_dir": info["runtime"],
        "meta_task_config": "configs/meta_task/meta_task_config.json",
        "ontology_name": "ontosynthesis",
        "test_mcp_config": info["mcp_config"],
        "start_main_kg_iteration": 3,
        "stop_main_kg_iteration": 3,
        "only_entity_safe": info["entity_safe"],
    }


def summarize_probe_cost(runtime: Path) -> dict:
    cost_path = runtime / "reports" / "openrouter_costs.jsonl"
    usd = 0.0
    calls = 0
    inp = 0
    out = 0
    if cost_path.is_file():
        for line in cost_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event") != "completed":
                continue
            calls += 1
            usd += float(row.get("actual_cost_usd") or row.get("cost_usd") or 0)
            usage = row.get("token_usage") or row.get("usage") or {}
            inp += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            out += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return {
        "calls": calls,
        "usd": usd,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "cost_log": str(cost_path),
    }


def main() -> None:
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(CASES))
    args = parser.parse_args()

    os.chdir(REPO)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(
        REPO / "ai_generated_contents_ontosyn_regen_v3"
    )
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env", override=True)
    info = prepare(args.case)
    print(json.dumps({"phase": "prepared", **info}, indent=2))
    from src.pipelines.main_kg_building.build import run_step as run_kg

    ok = run_kg(info["paper"], kg_config(info))
    cost = summarize_probe_cost(Path(info["runtime"]))
    print(
        json.dumps(
            {
                "phase": "done",
                "ok": bool(ok),
                "case": args.case,
                "official_fi_iter3": info["official_fi_iter3"],
                "probe_cost": cost,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
