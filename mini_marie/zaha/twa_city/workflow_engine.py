"""
Workflow engine: online probe (limited SPARQL) -> record -> offline replay (limits removed).

Supports chained steps where outputs of call N feed inputs of call N+1 via $variables.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.zaha.twa_city.city_cache import CityCache, invoke_plan
from mini_marie.cache_tiers import CacheMissError
from mini_marie.probe_sequence import (
    build_probed_sequence,
    probed_sequence_from_recording,
    seed_variables_from_recording,
    steps_from_probed_sequence,
)
from mini_marie.row_filters import run_filter_rows_transform
from mini_marie.workflow_steps import apply_step_extract
from mini_marie.zaha.twa_city.sparql_plans import DEFAULT_ONLINE_LIMIT, SparqlPlan, build_plan
from mini_marie.zaha.twa_city.twa_city_operations import format_results_as_tsv
from mini_marie.zaha.twa_city.workflow_sidecar import persist_offline_sidecar

WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"
RUNS_DIR = Path(__file__).resolve().parent / "workflow_runs"
MAX_PERSIST_ROWS = 5
MAX_PERSIST_TSV_CHARS = 8000
MAX_PERSIST_CELL_CHARS = 500


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


def run_transform_step(step_index: int, spec: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """In-memory transform between SPARQL calls (e.g. top-N from a larger list)."""
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
        out_field = spec.get("output_field", "building")
        if out_field == "*":
            output = top_rows
        else:
            output = [row.get(out_field) for row in top_rows if row.get(out_field)]
        out_var = spec["output_variable"]
        variables[out_var] = output
        if spec.get("also_store_rows"):
            variables[spec["also_store_rows"]] = top_rows
        rows_out = top_rows
        summary = f"Selected top {len(top_rows)} by {field}"
    elif transform == "filter_rows":
        return run_filter_rows_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_results_as_tsv,
        )
    elif transform == "join_rows":
        raise ValueError(
            "join_rows is disabled for city workflows; use local_join "
            "buildings_with_locations_sql or top_n_with_locations_from_cache"
        )
    else:
        raise ValueError(f"Unknown transform: {transform}")

    return {
        "step": step_index,
        "step_type": "transform",
        "name": spec.get("name", transform),
        "status": "pass" if rows_out else "empty",
        "rows": rows_out,
        "row_count": len(rows_out),
        "tsv": format_results_as_tsv(rows_out),
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "input": spec,
        "error": None,
    }


def run_local_join_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    cache: CityCache,
    *,
    mode: str,
) -> Dict[str, Any]:
    join = spec["join"]
    started = time.perf_counter()
    rows: List[Dict[str, Any]] = []
    summary = ""
    city = resolve_value(spec.get("city", "$city"), variables)

    if join == "top_n_by_height_local":
        n = int(resolve_value(spec.get("n", "$top_n"), variables))
        usage = spec.get("usage_contains")
        rows = cache.local_top_n_by_height(str(city), n, usage_contains=usage)
        summary = f"Local top-{n} by height for {city}: {len(rows)} rows"

    elif join == "building_pool_from_cache":
        rows = cache.local_all_heights(str(city))
        summary = f"Local height pool for {city}: {len(rows)} rows"

    elif join == "locations_for_buildings_local":
        iris_var = spec.get("building_iris_variable", "top_building_iris")
        iris = variables.get(iris_var) or []
        if isinstance(iris, list):
            iris_list = [str(i) for i in iris if i]
        else:
            iris_list = [str(iris)]
        rows, loc_meta = cache.resolve_locations_for_buildings(str(city), iris_list)
        resolved = loc_meta.get("resolved_buildings", 0)
        summary = (
            f"Local locations for {len(iris_list)} buildings: {len(rows)} rows "
            f"({resolved}/{len(iris_list)} buildings cached)"
        )

    elif join == "all_locations_from_cache":
        raise ValueError(
            "all_locations_from_cache is removed; use buildings_with_locations_sql or "
            "top_n_with_locations_from_cache for indexed SQL joins"
        )

    elif join == "buildings_with_locations_sql":
        iris_var = spec.get("building_iris_variable", "top_building_iris")
        iris = variables.get(iris_var) or []
        if isinstance(iris, list):
            iris_list = [str(i) for i in iris if i]
        else:
            iris_list = [str(iris)]
        rows = cache.local_buildings_with_locations_sql(str(city), iris_list)
        summary = (
            f"SQL join height+location for {len(iris_list)} buildings in {city}: "
            f"{len(rows)} rows"
        )

    elif join == "top_n_with_locations_from_cache":
        n = int(resolve_value(spec.get("n", "$top_n"), variables))
        usage = spec.get("usage_contains")
        if usage is not None:
            usage = str(resolve_value(usage, variables))
        rows = cache.local_top_n_with_locations_sql(str(city), n, usage_contains=usage)
        summary = f"SQL top-{n} buildings with locations for {city}: {len(rows)} rows"

    else:
        raise ValueError(f"Unknown local join: {join}")

    out_var = spec.get("output_variable")
    if out_var:
        variables[out_var] = rows
    pool_var = spec.get("also_store_as")
    if pool_var:
        variables[pool_var] = rows

    for var_name, extract_spec in (spec.get("extract") or {}).items():
        variables[var_name] = extract_from_step(extract_spec, {"rows": rows})

    return {
        "step": step_index,
        "step_type": "local_join",
        "join": join,
        "mode": mode,
        "status": "pass" if rows else "empty",
        "rows": rows,
        "row_count": len(rows),
        "tsv": format_results_as_tsv(rows),
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "input": spec,
        "error": None,
    }


def run_plan_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    mode: str,
    online_limit: int,
    cache: Optional[CityCache] = None,
    *,
    use_cache: bool = True,
) -> Dict[str, Any]:
    tool = spec["tool"]
    resolved_args = resolve_value(spec.get("args", {}), variables)
    city = resolved_args.get("city") or variables.get("city")
    if not city:
        raise ValueError(f"Step {step_index} missing city")
    resolved_args["city"] = city

    step_online_limit = spec.get("online_limit")
    if step_online_limit is None:
        limit_key = spec.get("limit_key")
        if limit_key and f"online_limits.{limit_key}" in variables:
            step_online_limit = variables[f"online_limits.{limit_key}"]
    if step_online_limit is None:
        step_online_limit = online_limit

    started = time.perf_counter()
    plan: Optional[SparqlPlan] = None
    cache_meta: Dict[str, Any] = {}
    try:
        if mode == "offline" and tool == "fetch_building_locations" and cache is not None:
            iris = resolved_args.get("building_iris") or []
            if isinstance(iris, list):
                iris_list = [str(i) for i in iris if i]
            else:
                iris_list = [str(iris)] if iris else []
            rows, loc_meta = cache.resolve_locations_for_buildings(str(city), iris_list)
            cache_meta = {
                "from_cache": True,
                "cache_tier": "full",
                "local_facet": True,
                "location_coverage": loc_meta,
            }
            plan = build_plan(tool, mode="warm", online_limit=int(step_online_limit), **resolved_args)
        else:
            rows, cache_meta, plan = invoke_plan(
                tool,
                resolved_args,
                mode=mode,
                online_limit=int(step_online_limit),
                use_cache=use_cache,
                cache=cache,
            )
        if (
            tool == "fetch_building_locations"
            and rows
            and cache_meta.get("location_coverage", {}).get("partial")
        ):
            status = "partial"
        else:
            status = "pass" if rows else "empty"
        error = None
    except Exception as exc:
        rows = []
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    summary = f"{tool}: {len(rows)} rows"
    cov = cache_meta.get("location_coverage")
    if cov:
        summary = (
            f"{tool}: {len(rows)} rows "
            f"({cov.get('resolved_buildings', 0)}/{cov.get('requested', 0)} buildings)"
        )
    result = {
        "step": step_index,
        "step_type": "sparql_plan",
        "tool": tool,
        "mode": mode,
        "input": resolved_args,
        "status": status,
        "rows": rows,
        "row_count": len(rows),
        "tsv": format_results_as_tsv(rows),
        "summary": summary,
        "elapsed_ms": elapsed_ms,
        "error": error,
        "record": None,
        "cache_meta": cache_meta,
    }

    if plan is not None:
        result["record"] = {
            "tool": plan.tool,
            "endpoint": plan.endpoint,
            "args": plan.args,
            "sparql_executed": plan.query,
            "limit_applied": plan.limit_applied,
            "limit_stripped": plan.limit_stripped,
            "cache_key": cache_meta.get("cache_key"),
            "from_cache": cache_meta.get("from_cache"),
            "replay_hint": (
                "offline replay removes LIMIT on list/rank steps; "
                "use transform top_n before location fetch; facets in city_cache.sqlite"
            ),
        }

    for var_name, extract_spec in (spec.get("extract") or {}).items():
        if status != "pass":
            raise RuntimeError(f"Cannot extract `{var_name}` because step {step_index} failed")
        variables[var_name] = extract_from_step(extract_spec, result)

    return result


def _truncate_value(value: Any, max_list: int = 5) -> Any:
    if isinstance(value, list):
        if len(value) <= max_list:
            return [_slim_row(r) if isinstance(r, dict) else r for r in value]
        return {
            "_row_count": len(value),
            "sample": [_slim_row(r) if isinstance(r, dict) else r for r in value[:max_list]],
        }
    return value


def _slim_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if key == "wkt" and value:
            text = str(value)
            out[key] = f"<WKT {len(text)} chars>"
            continue
        text = str(value)
        if len(text) > MAX_PERSIST_CELL_CHARS:
            out[key] = text[:MAX_PERSIST_CELL_CHARS] + "..."
        else:
            out[key] = value
    return out


def _slim_row_field(rows: List[Dict[str, Any]], *, max_rows: int = MAX_PERSIST_ROWS) -> Any:
    if not rows:
        return []
    if len(rows) <= max_rows:
        return [_slim_row(r) for r in rows]
    return {
        "_row_count": len(rows),
        "sample": [_slim_row(r) for r in rows[:max_rows]],
    }


def _slim_variables(variables: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _truncate_value(value) for key, value in variables.items()}


def _slim_call_trace_step(step: Dict[str, Any]) -> Dict[str, Any]:
    slim = {key: value for key, value in step.items() if key not in ("rows", "tsv")}
    rows = step.get("rows") or []
    slim["row_count"] = step.get("row_count", len(rows))
    if rows:
        slim["rows"] = _slim_row_field(rows)
    tsv = step.get("tsv") or ""
    if tsv:
        if len(tsv) > MAX_PERSIST_TSV_CHARS:
            slim["tsv"] = (
                tsv[:MAX_PERSIST_TSV_CHARS]
                + f"\n... ({len(tsv)} chars truncated for disk)"
            )
        else:
            slim["tsv"] = tsv
    return slim


def _slim_result_for_disk(
    result: Dict[str, Any],
    *,
    sidecar: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist offline runs as metadata + row samples; full rows live in NDJSON sidecars."""
    if result.get("mode") != "offline":
        return result
    slim = dict(result)
    slim["variables"] = _slim_variables(result.get("variables") or {})
    slim["seed_variables"] = _slim_variables(result.get("seed_variables") or {})
    slim["call_trace"] = [_slim_call_trace_step(step) for step in (result.get("call_trace") or [])]
    if sidecar:
        slim["sidecar"] = sidecar
        slim["rows_on_disk"] = "sidecar_ndjson"
    else:
        slim["rows_on_disk"] = "sample_only"
    slim["answer_digest"] = build_answer_digest(slim)
    return slim


def build_answer_digest(result: Dict[str, Any]) -> Dict[str, Any]:
    trace = result.get("call_trace") or []
    steps_out: List[Dict[str, Any]] = []
    authoritative: Dict[str, Any] = {}

    for step in trace:
        name = step.get("tool") or step.get("join") or step.get("name") or step.get("step_type")
        rows = step.get("rows") or []
        entry: Dict[str, Any] = {
            "step": step.get("step"),
            "type": step.get("step_type"),
            "name": name,
            "status": step.get("status"),
            "row_count": step.get("row_count"),
            "elapsed_ms": step.get("elapsed_ms"),
            "from_cache": (step.get("cache_meta") or {}).get("from_cache"),
        }
        if isinstance(rows, dict) and rows.get("sample") is not None:
            entry["result_sample"] = rows.get("sample")
        elif rows:
            if len(rows) == 1:
                entry["result"] = rows[0]
            else:
                entry["result_sample"] = rows[:3]
        steps_out.append(entry)

    variables = result.get("variables") or {}
    if variables.get("top_building_rows"):
        authoritative["top_buildings"] = _truncate_value(variables["top_building_rows"])
    if variables.get("location_rows"):
        locs = variables["location_rows"]
        authoritative["location_count"] = len(locs) if isinstance(locs, list) else 0
        authoritative["locations_sample"] = _truncate_value(locs)
    if variables.get("top_building_iris"):
        authoritative["top_building_iris"] = _truncate_value(variables["top_building_iris"])
    if variables.get("buildings_with_wkt"):
        authoritative["buildings_with_wkt"] = _truncate_value(variables["buildings_with_wkt"])
    if variables.get("location_join_rows"):
        authoritative["location_join_rows"] = _truncate_value(variables["location_join_rows"])
    if variables.get("all_location_rows"):
        locs = variables["all_location_rows"]
        authoritative["all_location_count"] = len(locs) if isinstance(locs, list) else 0
        authoritative["all_location_sample"] = _truncate_value(locs)

    for step in trace:
        if step.get("tool") in (
            "list_buildings_with_height",
            "rank_buildings_by_height",
            "buildings_by_usage",
        ):
            authoritative["rank_pool_rows"] = step.get("row_count")
            break

    return {
        "mode": result.get("mode"),
        "city": result.get("city"),
        "summary_text": result.get("answer"),
        "authoritative": authoritative,
        "steps": steps_out,
        "variables": _slim_variables(variables),
        "total_elapsed_ms": result.get("elapsed_ms"),
    }


def run_workflow(
    workflow: Dict[str, Any],
    mode: str = "online",
    online_limit: Optional[int] = None,
    offline_cap: Optional[int] = None,
    workflow_name: Optional[str] = None,
    embed_definition: bool = True,
    use_cache: bool = True,
    force_refresh: bool = False,
    *,
    steps_override: Optional[List[Dict[str, Any]]] = None,
    seed_variables: Optional[Dict[str, Any]] = None,
    replayed_from: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute workflow steps.

    Online: validates call sequence (probe tier, remote SPARQL) and records `probed_sequence`.
    Offline: replays `steps_override` / recording sequence from cache only.
    """
    del offline_cap  # deprecated
    variables: Dict[str, Any] = dict(seed_variables or workflow.get("seed_variables") or {})
    variables["city"] = workflow.get("city", variables.get("city"))
    variables["top_n"] = workflow.get("top_n", variables.get("top_n", DEFAULT_ONLINE_LIMIT))
    if workflow.get("usage_type") is not None:
        variables["usage_type"] = workflow.get("usage_type")
    for scalar in (
        "min_height_m",
        "max_height_m",
        "min_height",
        "max_height",
        "usage_contains",
    ):
        if workflow.get(scalar) is not None:
            variables[scalar] = workflow.get(scalar)

    limits = workflow.get("online_limits") or {}
    for key, val in limits.items():
        variables[f"online_limits.{key}"] = val

    online_limit = int(online_limit or workflow.get("online_limit") or DEFAULT_ONLINE_LIMIT)
    steps = steps_override if steps_override is not None else workflow.get("steps", [])

    cache = CityCache()
    cache_stats: Dict[str, Any] = {}
    call_trace: List[Dict[str, Any]] = []
    started = time.perf_counter()

    try:
        for index, step in enumerate(steps, start=1):
            step_type = step.get("type")
            if step_type == "transform":
                if mode == "online" and step.get("offline_only"):
                    result = {
                        "step": index,
                        "step_type": "transform",
                        "status": "skipped",
                        "summary": "Skipped offline-only transform during online probe",
                        "rows": [],
                        "row_count": 0,
                        "elapsed_ms": 0,
                    }
                else:
                    result = run_transform_step(index, step, variables)
                apply_step_extract(step, result, variables, extract_from_step)
            elif step_type == "local_join":
                if mode == "online" and step.get("offline_only"):
                    result = {
                        "step": index,
                        "step_type": "local_join",
                        "status": "skipped",
                        "summary": "Skipped offline-only local join during online probe",
                        "rows": [],
                        "row_count": 0,
                        "elapsed_ms": 0,
                    }
                else:
                    result = run_local_join_step(index, step, variables, cache, mode=mode)
                apply_step_extract(step, result, variables, extract_from_step)
            else:
                result = run_plan_step(
                    index,
                    step,
                    variables,
                    mode,
                    online_limit,
                    cache,
                    use_cache=use_cache and not force_refresh,
                )
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
        elif any(call.get("status") == "partial" for call in call_trace):
            status = "partial"
        elif all(call.get("status") in ("empty", "skipped") for call in call_trace):
            status = "empty"

    except Exception as exc:
        status = "error"
        answer = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        cache_stats = cache.stats()
        cache.close()

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    result: Dict[str, Any] = {
        "workflow_id": workflow.get("id"),
        "workflow_name": workflow_name,
        "question": workflow.get("question"),
        "city": workflow.get("city"),
        "mode": mode,
        "online_limit": online_limit,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "answer": answer,
        "seed_variables": {
            k: v for k, v in variables.items() if not str(k).startswith("online_limits.")
        },
        "variables": {k: v for k, v in variables.items() if not str(k).startswith("online_limits.")},
        "call_trace": call_trace,
        "cache_stats": cache_stats,
        "replayed_from": replayed_from,
    }
    if mode == "online" and steps_override is None:
        result["probed_sequence"] = build_probed_sequence(workflow, call_trace)
    if steps_override is not None:
        result["probed_sequence"] = steps_override
    result["answer_digest"] = build_answer_digest(result)
    if embed_definition:
        result["workflow_definition"] = dict(workflow)
    return result


def replay_workflow_from_recording(
    recorded: Dict[str, Any],
    workflow: Dict[str, Any],
    *,
    workflow_name: Optional[str] = None,
    online_limit: Optional[int] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Offline replay: same call sequence as online probe, full-tier cache only."""
    sequence = probed_sequence_from_recording(recorded)
    if not sequence:
        raise ValueError(
            "Recording has no probed_sequence; run online probe first or pass workflow steps"
        )
    steps = steps_from_probed_sequence(sequence)
    return run_workflow(
        workflow,
        mode="offline",
        online_limit=online_limit or recorded.get("online_limit"),
        workflow_name=workflow_name or recorded.get("workflow_name"),
        use_cache=use_cache,
        force_refresh=False,
        steps_override=steps,
        seed_variables=seed_variables_from_recording(recorded, workflow),
        replayed_from="probed_sequence",
    )


def save_run(result: Dict[str, Any], path: Optional[Path] = None) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        wf_id = result.get("workflow_id") or "workflow"
        mode = result.get("mode") or "online"
        path = RUNS_DIR / f"{wf_id}_{mode}_{int(time.time())}.json"
    path = Path(path)
    sidecar = None
    if result.get("mode") == "offline":
        sidecar = persist_offline_sidecar(result, path, row_threshold=MAX_PERSIST_ROWS)
    payload = _slim_result_for_disk(result, sidecar=sidecar or None)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if sidecar:
        result["sidecar"] = sidecar
        result["rows_on_disk"] = "sidecar_ndjson"
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
    """All workflow JSON files under workflows/."""
    return sorted(WORKFLOWS_DIR.glob("*.json"))


def discover_workflow_catalog() -> Dict[str, Dict[str, Any]]:
    """Build name -> metadata catalog by scanning workflows/*.json."""
    catalog: Dict[str, Dict[str, Any]] = {}
    for path in discover_workflow_files():
        name = path.stem
        workflow = load_workflow_path(path)
        catalog[name] = {
            "id": workflow.get("id", name),
            "city": workflow.get("city", ""),
            "description": workflow.get("question") or workflow.get("description") or "",
            "path": str(path),
        }
    return catalog


def find_workflow_names_by_id(workflow_id: str) -> List[str]:
    """Return workflow file stems whose JSON `id` matches workflow_id."""
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
    """
    Resolve a workflow definition for offline replay.

    Priority:
    1. Explicit workflow_path
    2. Explicit workflow_name (workflows/{name}.json)
    3. workflow_name stored in the recording (workflows/{name}.json on disk)
    4. Embedded workflow_definition in the recording
    5. Scan workflows/*.json by workflow_id
    """
    if workflow_path is not None:
        return load_workflow_path(workflow_path), f"path:{workflow_path}"

    if workflow_name:
        return load_workflow(workflow_name), f"name:{workflow_name}"

    recorded_name = recorded.get("workflow_name")
    if recorded_name:
        wf_path = WORKFLOWS_DIR / f"{recorded_name}.json"
        if wf_path.exists():
            return load_workflow(str(recorded_name)), f"recording_name:{recorded_name}"

    embedded = recorded.get("workflow_definition")
    if isinstance(embedded, dict) and embedded.get("steps"):
        source = recorded.get("workflow_name") or recorded.get("workflow_id") or "embedded"
        return dict(embedded), f"embedded:{source}"

    wf_id = recorded.get("workflow_id")
    matches = find_workflow_names_by_id(wf_id)
    if len(matches) == 1:
        return load_workflow(matches[0]), f"id:{wf_id}->{matches[0]}"
    if len(matches) > 1:
        raise ValueError(
            f"workflow_id {wf_id!r} matches multiple workflow files: {matches}. "
            "Pass --workflow or workflow_name explicitly."
        )

    raise ValueError(
        "Cannot resolve workflow for replay. Provide --workflow / workflow_name, "
        "or use a recording that includes workflow_definition / workflow_name, "
        f"or add workflows/{{name}}.json with id={wf_id!r}."
    )
