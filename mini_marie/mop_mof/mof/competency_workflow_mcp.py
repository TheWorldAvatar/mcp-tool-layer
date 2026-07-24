"""MCP-safe MOF competency workflow execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from mini_marie.mop_mof.mof.competency_workflow_engine import (
    load_manifest,
    load_workflow,
    replay_competency_from_recording,
    run_competency_workflow,
    save_run,
)
from mini_marie.mop_mof.mof.mof_operations import format_results_as_tsv

MCP_ONLINE_LIMIT = 10


def list_competency_workflows_text() -> str:
    manifest = load_manifest()
    lines = ["workflow_id\tquestion"]
    for wf in manifest.get("workflows") or []:
        q = (wf.get("question") or "")[:100]
        lines.append(f"{wf.get('id', '')}\t{q}")
    return "\n".join(lines)


def format_competency_mcp_response(result: Dict[str, Any], recording_path: Path) -> str:
    lines = [
        f"status\t{result.get('status')}",
        f"mode\t{result.get('mode')}",
        f"workflow_id\t{result.get('workflow_id')}",
        f"elapsed_ms\t{result.get('elapsed_ms')}",
        f"recording_path\t{recording_path.resolve()}",
        f"answer\t{result.get('answer')}",
        "",
        "step\ttype\ttool_or_name\tstatus\trow_count\telapsed_ms\tsummary",
    ]
    for step in result.get("call_trace") or []:
        tool = step.get("tool") or step.get("name") or step.get("step_type")
        lines.append(
            "\t".join(
                [
                    str(step.get("step", "")),
                    str(step.get("step_type", "")),
                    str(tool),
                    str(step.get("status", "")),
                    str(step.get("row_count", "")),
                    str(step.get("elapsed_ms", "")),
                    str(step.get("summary", "")).replace("\t", " "),
                ]
            )
        )

    lines.append("")
    lines.append("sample_results")
    for step in result.get("call_trace") or []:
        rows = (step.get("rows") or [])[:5]
        if rows:
            lines.append(f"--- step {step.get('step')} ---")
            lines.append(format_results_as_tsv(rows))

    lines.append("")
    lines.append(
        "next_step\tOffline full answer is produced by replay_competency_offline(recording_path) outside the LLM"
    )
    return "\n".join(lines)


def run_competency_online(workflow_id: str, online_limit: int = MCP_ONLINE_LIMIT) -> str:
    wf = load_workflow(workflow_id)
    result = run_competency_workflow(
        wf,
        mode="online",
        online_limit=min(online_limit, MCP_ONLINE_LIMIT),
    )
    path = save_run(result)
    return format_competency_mcp_response(result, path)


def replay_competency_offline(recording_path: str) -> str:
    path = Path(recording_path)
    recorded = json.loads(path.read_text(encoding="utf-8"))
    wf = recorded.get("workflow_definition") or load_workflow(str(recorded.get("workflow_id")))
    result = replay_competency_from_recording(recorded, wf)
    result["replayed_from"] = str(path.resolve())
    out_path = save_run(result)
    return format_competency_mcp_response(result, out_path)
