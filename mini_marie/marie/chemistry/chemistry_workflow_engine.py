"""Competency workflows: online probe (cached) -> offline replay (cache-only)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.marie.chemistry.chemistry_cache import (
    ChemistryCache,
    invoke_tool,
)
from mini_marie.marie.chemistry.limits import DEFAULT_ONLINE_PROBE_LIMIT
from mini_marie.marie.chemistry.sparql import format_tsv
from mini_marie.probe_sequence import (
    build_probed_sequence,
    probed_sequence_from_recording,
    seed_variables_from_recording,
    steps_from_probed_sequence,
)
from mini_marie.row_joins import run_join_rows_transform
from mini_marie.row_filters import run_filter_rows_transform
from mini_marie.row_aggregates import run_group_aggregate_transform

MANIFEST_PATH = Path(__file__).resolve().parent / "workflows" / "competency_suite.json"
RUNS_DIR = Path(__file__).resolve().parent / "competency_runs"


def resolve_value(expr: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(expr, str) and expr.startswith("$"):
        return variables.get(expr[1:], expr)
    return expr


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def list_workflow_ids() -> List[str]:
    return [w["id"] for w in load_manifest().get("workflows", [])]


def load_workflow(workflow_id: str) -> Dict[str, Any]:
    for wf in load_manifest().get("workflows", []):
        if wf.get("id") == workflow_id:
            return wf
    raise KeyError(f"Unknown workflow: {workflow_id}")


def _run_tool_step(
    step_index: int,
    spec: Dict[str, Any],
    *,
    mode: str,
    online_limit: int,
    force_refresh: bool,
    cache: ChemistryCache,
) -> Dict[str, Any]:
    tool = spec["tool"]
    args = dict(spec.get("args") or {})
    started = time.perf_counter()
    try:
        rows, meta = invoke_tool(
            tool,
            args,
            mode=mode,
            online_limit=online_limit,
            use_cache=not force_refresh,
            cache=cache,
        )
        status = "pass" if rows else "empty"
        err = None
    except Exception as exc:
        rows = []
        meta = {"tool": tool, "mode": mode}
        status = "error"
        err = str(exc)

    return {
        "step": step_index,
        "step_type": "tool",
        "tool": tool,
        "input": args,
        "mode": mode,
        "status": status,
        "rows": rows,
        "row_count": len(rows),
        "tsv": format_tsv(rows) if rows else "No results",
        "meta": meta,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "error": err,
    }


def _run_intersect_keys_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
) -> Dict[str, Any]:
    started = time.perf_counter()
    left_var = spec["left_variable"]
    right_var = spec["right_variable"]
    key = str(resolve_value(spec.get("key") or "mechanism", variables))
    label_key = str(spec.get("label_key") or "mechanismLabel")
    left_rows = variables.get(left_var) or []
    right_rows = variables.get(right_var) or []
    left_keys = {str(r.get(key, "")).strip() for r in left_rows if str(r.get(key, "")).strip()}
    right_keys = {str(r.get(key, "")).strip() for r in right_rows if str(r.get(key, "")).strip()}
    both = sorted(left_keys & right_keys)
    labels: Dict[str, str] = {}
    for row in left_rows:
        mech = str(row.get(key, "")).strip()
        if mech in both and mech not in labels:
            labels[mech] = str(row.get(label_key, "") or "")
    rows = [{key: mech, label_key: labels.get(mech, "")} for mech in both]
    out_var = spec["output_variable"]
    variables[out_var] = rows
    return {
        "step": step_index,
        "step_type": "transform",
        "transform": "intersect_keys",
        "status": "pass" if rows else "empty",
        "rows": rows,
        "row_count": len(rows),
        "tsv": format_tsv(rows) if rows else "No results",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "error": None,
    }


def _run_transform_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
) -> Dict[str, Any]:
    transform = spec.get("transform")
    if transform == "intersect_keys":
        return _run_intersect_keys_step(step_index, spec, variables)
    if transform == "join_rows":
        return run_join_rows_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_tsv,
        )
    if transform == "filter_rows":
        return run_filter_rows_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_tsv,
        )
    if transform == "group_aggregate":
        return run_group_aggregate_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_tsv,
        )
    if transform == "top_n_by_field":
        started = time.perf_counter()
        source_var = spec["input_variable"]
        rows_in = variables.get(source_var) or []
        field = str(spec["field"])
        n = int(resolve_value(spec.get("n", 10), variables))
        reverse = str(spec.get("order", "desc")).lower() != "asc"

        def sort_key(row: Dict[str, Any]) -> float:
            try:
                return float(row.get(field) or 0)
            except (TypeError, ValueError):
                return 0.0

        top_rows = sorted(rows_in, key=sort_key, reverse=reverse)[:n]
        out_var = spec["output_variable"]
        variables[out_var] = top_rows
        return {
            "step": step_index,
            "step_type": "transform",
            "transform": "top_n_by_field",
            "status": "pass" if top_rows else "empty",
            "rows": top_rows,
            "row_count": len(top_rows),
            "tsv": format_tsv(top_rows) if top_rows else "No results",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error": None,
        }
    raise ValueError(f"Unsupported transform: {transform}")


def run_competency_workflow(
    workflow: Dict[str, Any],
    *,
    mode: str = "online",
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force_refresh: bool = False,
    recording: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cache = ChemistryCache()
    variables: Dict[str, Any] = {}
    call_trace: List[Dict[str, Any]] = []

    if recording and mode == "offline":
        variables = seed_variables_from_recording(recording, workflow)
        probed = probed_sequence_from_recording(recording)
        steps = steps_from_probed_sequence(probed)
    else:
        steps = workflow.get("steps", [])

    try:
        for i, step in enumerate(steps, 1):
            if step.get("offline_only") and mode == "online":
                call_trace.append(
                    {
                        "step": i,
                        "step_type": step.get("type", "tool"),
                        "status": "skipped",
                        "rows": [],
                        "row_count": 0,
                        "tsv": "",
                        "error": None,
                    }
                )
                continue

            step_type = step.get("type") or "tool"
            if step_type == "transform":
                result = _run_transform_step(i, step, variables)
            else:
                result = _run_tool_step(
                    i,
                    step,
                    mode=mode,
                    online_limit=online_limit,
                    force_refresh=force_refresh,
                    cache=cache,
                )
                out_var = step.get("output_variable")
                if out_var and result.get("rows") is not None:
                    variables[out_var] = result["rows"]

            call_trace.append(result)

        answer_var = workflow.get("answer_variable")
        answer = variables.get(answer_var) if answer_var else None
        statuses = [s.get("status") for s in call_trace if s.get("status") != "skipped"]
        overall = "error" if "error" in statuses else ("empty" if statuses and all(s == "empty" for s in statuses) else "pass")

        result: Dict[str, Any] = {
            "workflow_id": workflow.get("id"),
            "title": workflow.get("title"),
            "mode": mode,
            "status": overall,
            "answer": answer,
            "answer_tsv": format_tsv(answer) if isinstance(answer, list) and answer else "",
            "call_trace": call_trace,
            "cache_stats": cache.stats(),
        }
        if mode == "online":
            result["probed_sequence"] = build_probed_sequence(workflow, call_trace)
        return result
    finally:
        cache.close()


def save_run(result: Dict[str, Any]) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    wf_id = result.get("workflow_id", "unknown")
    mode = result.get("mode", "online")
    ts = int(time.time())
    path = RUNS_DIR / f"{wf_id}_{mode}_{ts}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def run_suite_probe(
    *,
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force: bool = False,
) -> Dict[str, Any]:
    results = []
    for wf_id in list_workflow_ids():
        wf = load_workflow(wf_id)
        out = run_competency_workflow(
            wf, mode="online", online_limit=online_limit, force_refresh=force
        )
        save_run(out)
        results.append({"workflow_id": wf_id, "status": out["status"]})
    return {"workflows": results}


def replay_from_recording(recording_path: Path) -> Dict[str, Any]:
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    wf_id = recording.get("workflow_id")
    wf = load_workflow(str(wf_id))
    return run_competency_workflow(wf, mode="offline", recording=recording)
