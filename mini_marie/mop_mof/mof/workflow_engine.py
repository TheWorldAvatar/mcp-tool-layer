"""
MOF workflow engine: online probe (limited SPARQL) -> record -> offline replay.

Supports chained steps, $variables, transform steps, lightweight tool steps, and SPARQL plans.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mini_marie.mop_mof.mof import mof_operations as ops
from mini_marie.mop_mof.mof.sparql_plans import (
    DEFAULT_OFFLINE_CAP,
    DEFAULT_ONLINE_LIMIT,
    SparqlPlan,
    build_plan,
)

WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"
RUNS_DIR = Path(__file__).resolve().parent / "workflow_runs"

TOOL_REGISTRY: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "get_mof_total_count": lambda **_: ops.get_mof_total_count(),
    "get_tobassco_co2_coverage": lambda **_: ops.get_tobassco_co2_coverage(),
    "get_tobassco_co2_uptake_stats": lambda **_: ops.get_tobassco_co2_uptake_stats(),
    "get_source_database_stats": lambda **_: ops.get_source_database_stats(),
}


def resolve_value(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("$") and value[1:] in variables:
            return variables[value[1:]]
        out = value
        for key, resolved in variables.items():
            if isinstance(resolved, (str, int, float)):
                out = out.replace(f"${key}", str(resolved))
        return out
    if isinstance(value, list):
        return [resolve_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {k: resolve_value(v, variables) for k, v in value.items()}
    return value


def extract_from_step(spec: Dict[str, Any], step_result: Dict[str, Any]) -> Any:
    rows: List[Dict[str, Any]] = step_result.get("rows") or []
    pick = spec.get("pick", "row_field")

    if pick == "all_rows":
        return rows

    if pick == "column":
        field = spec["field"]
        return [row.get(field) for row in rows if row.get(field)]

    if pick == "top_n_by_field":
        field = spec["field"]
        n = int(spec.get("n", 10))
        reverse = spec.get("order", "desc") == "desc"
        sorted_rows = sorted(
            rows,
            key=lambda r: float(r.get(field) or 0),
            reverse=reverse,
        )
        return sorted_rows[:n]

    index = int(spec.get("index", 0))
    if not rows:
        raise ValueError("No rows available for extraction")
    field = spec.get("field")
    if pick == "row_field":
        if field:
            return rows[index].get(field)
        return rows[index]
    raise ValueError(f"Unknown pick mode: {pick}")


def _mofid_suffix(mofid: str) -> str:
    token = str(mofid).rsplit(" ", 1)[-1].strip()
    return token if token else str(mofid)


TRANSFORMS = {
    "mofid_suffix": _mofid_suffix,
    "strip": lambda value: str(value).strip(),
    "lower": lambda value: str(value).lower(),
}


def run_transform_step(step_index: int, spec: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    transform = spec["transform"]
    started = time.perf_counter()

    if transform == "top_n_by_field":
        source_var = spec["input_variable"]
        rows = variables.get(source_var) or []
        n = int(resolve_value(spec.get("n", 10), variables))
        field = spec["field"]
        top_rows = extract_from_step(
            {"pick": "top_n_by_field", "field": field, "n": n, "order": spec.get("order", "desc")},
            {"rows": rows},
        )
        out_field = spec.get("output_field", "mofid")
        if out_field == "*":
            output = top_rows
        else:
            output = [row.get(out_field) for row in top_rows if row.get(out_field)]
        variables[spec["output_variable"]] = output
        if spec.get("also_store_rows"):
            variables[spec["also_store_rows"]] = top_rows
        rows_out = top_rows
        summary = f"Selected top {len(top_rows)} by {field}"
    elif transform == "first_field_transform":
        source_var = spec["input_variable"]
        rows = variables.get(source_var) or []
        if not rows:
            raise ValueError(f"No rows in {source_var}")
        field = spec["field"]
        value = rows[0].get(field)
        fn = TRANSFORMS.get(spec.get("apply", "strip"))
        if fn:
            value = fn(value)
        out_var = spec["output_variable"]
        variables[out_var] = value
        rows_out = [{out_var: value}]
        summary = f"Transformed {field} -> {out_var}={str(value)[:80]}"
    else:
        raise ValueError(f"Unknown transform: {transform}")

    dict_rows = rows_out if isinstance(rows_out, list) and rows_out and isinstance(rows_out[0], dict) else []
    return {
        "step": step_index,
        "step_type": "transform",
        "name": spec.get("name", transform),
        "status": "pass" if dict_rows or variables.get(spec.get("output_variable")) else "empty",
        "rows": dict_rows,
        "row_count": len(dict_rows) if dict_rows else 1,
        "tsv": ops.format_results_as_tsv(dict_rows or [{"result": summary}]),
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "input": spec,
        "error": None,
    }


def run_tool_step(step_index: int, spec: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    tool = spec["tool"]
    fn = TOOL_REGISTRY.get(tool)
    if fn is None:
        raise ValueError(f"Unknown tool step: {tool}")

    resolved_args = resolve_value(spec.get("args", {}), variables)
    started = time.perf_counter()
    try:
        rows = fn(**resolved_args)
        status = "pass" if rows else "empty"
        error = None
    except Exception as exc:
        rows = []
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    result = {
        "step": step_index,
        "step_type": "tool",
        "tool": tool,
        "input": resolved_args,
        "status": status,
        "rows": rows,
        "row_count": len(rows),
        "tsv": ops.format_results_as_tsv(rows),
        "summary": f"{tool}: {len(rows)} rows",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "error": error,
        "record": {"tool": tool, "args": resolved_args, "note": "fixed-limit MCP tool (not replay-scaled)"},
    }

    for var_name, extract_spec in (spec.get("extract") or {}).items():
        if status != "pass":
            raise RuntimeError(f"Cannot extract `{var_name}` because step {step_index} failed")
        variables[var_name] = extract_from_step(extract_spec, result)

    return result


def run_plan_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    mode: str,
    online_limit: int,
    offline_cap: int,
) -> Dict[str, Any]:
    tool = spec["tool"]
    resolved_args = resolve_value(spec.get("args", {}), variables)
    step_online_limit = spec.get("online_limit")
    if step_online_limit is None:
        limit_key = spec.get("limit_key")
        if limit_key and f"online_limits.{limit_key}" in variables:
            step_online_limit = variables[f"online_limits.{limit_key}"]
    if step_online_limit is None:
        step_online_limit = online_limit

    started = time.perf_counter()
    plan = None
    try:
        plan = build_plan(
            tool=tool,
            mode=mode,
            online_limit=int(step_online_limit),
            offline_cap=offline_cap,
            **resolved_args,
        )
        rows = plan.execute()
        status = "pass" if rows else "empty"
        error = None
    except Exception as exc:
        rows = []
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    result = {
        "step": step_index,
        "step_type": "sparql_plan",
        "tool": tool,
        "mode": mode,
        "input": resolved_args,
        "status": status,
        "rows": rows,
        "row_count": len(rows),
        "tsv": ops.format_results_as_tsv(rows),
        "summary": f"{tool}: {len(rows)} rows",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "error": error,
        "record": None,
    }

    if plan is not None:
        result["record"] = {
            "tool": plan.tool,
            "endpoint": plan.endpoint,
            "args": plan.args,
            "sparql_executed": plan.query,
            "limit_applied": plan.limit_applied,
            "limit_stripped": plan.limit_stripped,
        }

    for var_name, extract_spec in (spec.get("extract") or {}).items():
        if status != "pass":
            raise RuntimeError(f"Cannot extract `{var_name}` because step {step_index} failed")
        variables[var_name] = extract_from_step(extract_spec, result)

    return result


def run_workflow(
    workflow: Dict[str, Any],
    mode: str = "online",
    online_limit: Optional[int] = None,
    offline_cap: Optional[int] = None,
    workflow_name: Optional[str] = None,
    embed_definition: bool = True,
) -> Dict[str, Any]:
    variables: Dict[str, Any] = dict(workflow.get("seed_variables") or {})
    variables["top_n"] = workflow.get("top_n", variables.get("top_n", DEFAULT_ONLINE_LIMIT))
    if workflow.get("topology") is not None:
        variables["topology"] = workflow.get("topology")

    limits = workflow.get("online_limits") or {}
    for key, val in limits.items():
        variables[f"online_limits.{key}"] = val

    online_limit = int(online_limit or workflow.get("online_limit") or DEFAULT_ONLINE_LIMIT)
    offline_cap = int(offline_cap or workflow.get("offline_cap") or DEFAULT_OFFLINE_CAP)

    call_trace: List[Dict[str, Any]] = []
    started = time.perf_counter()

    try:
        for index, step in enumerate(workflow.get("steps", []), start=1):
            if step.get("type") == "transform":
                result = run_transform_step(index, step, variables)
            elif step.get("tool") in TOOL_REGISTRY:
                result = run_tool_step(index, step, variables)
            else:
                result = run_plan_step(index, step, variables, mode, online_limit, offline_cap)
            call_trace.append(result)

        final_summary = workflow.get("final_summary")
        if final_summary:
            answer = resolve_value(final_summary, variables)
        elif call_trace:
            answer = call_trace[-1].get("summary", "")
        else:
            answer = "No steps executed."

        status = "pass"
        if any(call.get("status") == "error" for call in call_trace):
            status = "error"
        elif all(call.get("status") == "empty" for call in call_trace):
            status = "empty"

    except Exception as exc:
        status = "error"
        answer = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    result: Dict[str, Any] = {
        "workflow_id": workflow.get("id"),
        "workflow_name": workflow_name,
        "question": workflow.get("question"),
        "mode": mode,
        "online_limit": online_limit,
        "offline_cap": offline_cap,
        "status": status,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "answer": answer,
        "variables": {k: v for k, v in variables.items() if not str(k).startswith("online_limits.")},
        "call_trace": call_trace,
    }
    if embed_definition:
        result["workflow_definition"] = dict(workflow)
    return result


def save_run(result: Dict[str, Any], path: Optional[Path] = None) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        wf_id = result.get("workflow_id") or "workflow"
        mode = result.get("mode") or "online"
        path = RUNS_DIR / f"{wf_id}_{mode}_{int(time.time())}.json"
    path = Path(path)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def load_run(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_workflow(name: str) -> Dict[str, Any]:
    path = WORKFLOWS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_workflow_path(path: Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def discover_workflow_files() -> List[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.json"))


def discover_workflow_catalog() -> Dict[str, Dict[str, Any]]:
    catalog: Dict[str, Dict[str, Any]] = {}
    for path in discover_workflow_files():
        name = path.stem
        workflow = load_workflow_path(path)
        catalog[name] = {
            "id": workflow.get("id", name),
            "description": workflow.get("question") or workflow.get("description") or "",
            "path": str(path),
        }
    return catalog


def find_workflow_names_by_id(workflow_id: str) -> List[str]:
    if not workflow_id:
        return []
    matches: List[str] = []
    for path in discover_workflow_files():
        workflow = load_workflow_path(path)
        if workflow.get("id") == workflow_id:
            matches.append(path.stem)
    return matches


def resolve_workflow_for_replay(
    recorded: Dict[str, Any],
    *,
    workflow_name: Optional[str] = None,
    workflow_path: Optional[Path] = None,
) -> tuple[Dict[str, Any], str]:
    if workflow_path is not None:
        return load_workflow_path(workflow_path), f"path:{workflow_path}"

    if workflow_name:
        return load_workflow(workflow_name), f"name:{workflow_name}"

    embedded = recorded.get("workflow_definition")
    if isinstance(embedded, dict) and embedded.get("steps"):
        source = recorded.get("workflow_name") or recorded.get("workflow_id") or "embedded"
        return dict(embedded), f"embedded:{source}"

    recorded_name = recorded.get("workflow_name")
    if recorded_name:
        return load_workflow(str(recorded_name)), f"recording_name:{recorded_name}"

    wf_id = recorded.get("workflow_id")
    matches = find_workflow_names_by_id(wf_id)
    if len(matches) == 1:
        return load_workflow(matches[0]), f"id:{wf_id}->{matches[0]}"
    if len(matches) > 1:
        raise ValueError(
            f"workflow_id {wf_id!r} matches multiple workflow files: {matches}. "
            "Pass workflow_name explicitly."
        )

    raise ValueError(
        "Cannot resolve workflow for replay. Provide workflow_name / workflow_path, "
        "or use a recording with workflow_definition / workflow_name."
    )
