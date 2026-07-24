"""MCP-safe workflow execution helpers (compact agent responses, full data on disk)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.zaha.twa_city.twa_city_operations import format_results_as_tsv
from mini_marie.zaha.twa_city.workflow_engine import (
    discover_workflow_catalog,
    load_run,
    load_workflow,
    resolve_workflow_for_replay,
    run_workflow,
    save_run,
)

MCP_ONLINE_LIMIT = 10
MCP_OFFLINE_CAP = 500_000


def list_workflows_text() -> str:
    lines = ["workflow_name\tcity\tdescription"]
    for name, meta in discover_workflow_catalog().items():
        lines.append(f"{name}\t{meta['city']}\t{meta['description']}")
    return "\n".join(lines)


def _compact_rows(rows: List[Dict[str, Any]], max_rows: int = 10) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows[:max_rows]:
        compact = {}
        for k, v in row.items():
            if k == "wkt" and v:
                compact[k] = f"<WKT {len(str(v))} chars>"
            elif k == "building" and v:
                compact["uuid"] = str(v).rsplit("/", 1)[-1]
                compact["building"] = str(v)
            else:
                compact[k] = v
        out.append(compact)
    return out


def format_workflow_mcp_response(result: Dict[str, Any], recording_path: Path) -> str:
    """Compact TSV/text for Cursor agent context (no raw WKT blobs)."""
    lines = [
        f"status\t{result.get('status')}",
        f"mode\t{result.get('mode')}",
        f"workflow_id\t{result.get('workflow_id')}",
        f"workflow_name\t{result.get('workflow_name', '')}",
        f"city\t{result.get('city')}",
        f"online_limit\t{result.get('online_limit')}",
        f"offline_cap\t{result.get('offline_cap')}",
        f"elapsed_ms\t{result.get('elapsed_ms')}",
        f"recording_path\t{recording_path.resolve()}",
        f"answer\t{result.get('answer')}",
        "",
        "step\ttype\ttool_or_name\tstatus\trow_count\telapsed_ms\tsummary",
    ]
    for step in result.get("call_trace", []):
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
        rec = step.get("record") or {}
        if rec.get("limit_applied") is not None:
            lines.append(f"  limit_applied\t{rec.get('limit_applied')}")
        if rec.get("limit_stripped"):
            lines.append("  limit_stripped\ttrue")

    lines.append("")
    lines.append("sample_results")
    for step in result.get("call_trace", []):
        rows = _compact_rows(step.get("rows") or [], max_rows=5)
        if rows:
            lines.append(f"--- step {step.get('step')} ---")
            lines.append(format_results_as_tsv(rows))

    lines.append("")
    if result.get("sidecar", {}).get("artifacts"):
        lines.append("full_scale_sidecar")
        for art in result["sidecar"]["artifacts"]:
            lines.append(
                "\t".join(
                    [
                        str(art.get("kind", "")),
                        str(art.get("step") or art.get("name") or ""),
                        str(art.get("row_count", "")),
                        str(art.get("path", "")),
                    ]
                )
            )
        lines.append("")
    lines.append(
        "next_step\tCall replay_workflow_offline with recording_path for full-scale results"
    )
    return "\n".join(lines)


def run_workflow_online(
    workflow_name: str,
    online_limit: int = MCP_ONLINE_LIMIT,
) -> str:
    workflow = load_workflow(workflow_name)
    workflow["online_limit"] = online_limit
    if workflow.get("online_limits"):
        workflow["online_limits"] = {k: online_limit for k in workflow["online_limits"]}
    result = run_workflow(
        workflow,
        mode="online",
        online_limit=online_limit,
        workflow_name=workflow_name,
        use_cache=True,
    )
    path = save_run(result)
    return format_workflow_mcp_response(result, path)


def replay_workflow_offline(
    recording_path: str,
    offline_cap: int = MCP_OFFLINE_CAP,
    workflow_name: Optional[str] = None,
    workflow_path: Optional[str] = None,
) -> str:
    recorded = load_run(Path(recording_path))
    workflow, _source = resolve_workflow_for_replay(
        recorded,
        workflow_name=workflow_name,
        workflow_path=Path(workflow_path) if workflow_path else None,
    )
    result = run_workflow(
        workflow,
        mode="offline",
        offline_cap=offline_cap or recorded.get("offline_cap", MCP_OFFLINE_CAP),
        workflow_name=workflow_name or recorded.get("workflow_name"),
        use_cache=True,
        force_refresh=False,
    )
    result["replayed_from"] = str(Path(recording_path).resolve())
    result["workflow_source"] = _source
    path = save_run(result)
    return format_workflow_mcp_response(result, path)
