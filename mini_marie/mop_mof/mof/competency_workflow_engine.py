"""
Competency workflows: online probe (cached atomics) -> offline replay (raised limits + local joins).
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.mop_mof.mof.competency_cache import (
    DEFAULT_ONLINE_LIMIT,
    CompetencyCache,
    invoke_tool,
)
from mini_marie.mop_mof.mof.competency_engine import extract_from_step, resolve_value
from mini_marie.mop_mof.mof.mof_operations import format_results_as_tsv
from mini_marie.row_filters import run_filter_rows_transform
from mini_marie.row_aggregates import run_group_aggregate_transform
from mini_marie.row_joins import run_join_rows_transform, run_multi_join_rows_transform
from mini_marie.mop_mof.mof.competency_cache import invoke_residual_sparql
from mini_marie.probe_sequence import (
    build_probed_sequence,
    probed_sequence_from_recording,
    seed_variables_from_recording,
    steps_from_probed_sequence,
)
from mini_marie.warm_manifest import collect_atomic_specs_from_workflow
from mini_marie.workflow_steps import apply_step_extract

MANIFEST_PATH = Path(__file__).resolve().parent / "workflows" / "competency_suite.json"
RUNS_DIR = Path(__file__).resolve().parent / "competency_runs"


def run_local_join_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    cache: CompetencyCache,
    *,
    mode: str,
) -> Dict[str, Any]:
    """Join cached facet tables without SPARQL."""
    join = spec["join"]
    started = time.perf_counter()
    rows: List[Dict[str, Any]] = []
    summary = ""

    if join == "topology_from_identity":
        ref_name = resolve_value(spec.get("reference_name", ""), variables)
        id_rows = cache.local_identity(str(ref_name))
        topos = sorted({_norm_topo(r) for r in id_rows if r.get("topology")})
        variables[spec["output_variable"]] = topos[0] if topos else ""
        rows = [{"topology": variables[spec["output_variable"]], "sources": len(id_rows)}]
        summary = f"Resolved topology for {ref_name}: {variables[spec['output_variable']]!r}"

    elif join == "same_topology_sample_local":
        topology = resolve_value(spec.get("topology", "$topology"), variables)
        ref_name = spec.get("exclude_reference")
        limit = int(resolve_value(spec.get("limit", 100), variables))
        rows = cache.local_topology_sample(
            str(topology),
            limit=limit,
            exclude_name=str(ref_name) if ref_name else None,
        )
        summary = f"Local topology sample {topology}: {len(rows)} rows"

    elif join == "same_topology_count_local":
        topology = resolve_value(spec.get("topology", "$topology"), variables)
        ref_name = spec.get("exclude_reference")
        count = cache.local_topology_count(
            str(topology),
            exclude_name=str(ref_name) if ref_name else None,
        )
        rows = [{"count": str(count), "topology": str(topology)}]
        summary = f"Local topology count {topology}: {count}"

    elif join == "synthesis_by_refcodes_local":
        mof_name = resolve_value(spec.get("mof_name", ""), variables)
        refcodes = variables.get(spec.get("refcodes_variable", "refcodes"))
        if isinstance(refcodes, list):
            ref_list = [str(r) for r in refcodes if r]
        else:
            ref_list = None
        rows = cache.local_synthesis_for_name(str(mof_name), refcodes=ref_list)
        summary = f"Local synthesis for {mof_name}: {len(rows)} rows"

    elif join == "metal_sources_local":
        metal = resolve_value(spec.get("metal", ""), variables)
        rows = cache.local_metal_sources(str(metal))
        summary = f"Local metal source counts for {metal}: {len(rows)} rows"

    else:
        raise ValueError(f"Unknown local join: {join}")

    out_var = spec.get("output_variable")
    if out_var and join not in ("topology_from_identity",):
        variables[out_var] = rows

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


def _norm_topo(row: Dict[str, Any]) -> str:
    return str(row.get("topology") or "").strip().lower()


def run_transform_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
) -> Dict[str, Any]:
    transform = spec.get("transform")
    if transform == "filter_rows":
        return run_filter_rows_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_results_as_tsv,
        )
    if transform == "join_rows":
        return run_join_rows_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_results_as_tsv,
        )
    if transform == "multi_join_rows":
        return run_multi_join_rows_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_results_as_tsv,
        )
    if transform == "group_aggregate":
        return run_group_aggregate_transform(
            step_index,
            spec,
            variables,
            resolve=resolve_value,
            format_tsv=format_results_as_tsv,
        )
    raise ValueError(f"Unknown transform: {transform}")


def run_sparql_step(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    cache: CompetencyCache,
    *,
    mode: str,
    online_limit: int,
    use_cache: bool,
) -> Dict[str, Any]:
    """Residual SPARQL step (cached by query text; remote on online/warm only)."""
    started = time.perf_counter()
    raw_query = spec.get("query")
    if raw_query is None and spec.get("query_variable"):
        raw_query = variables.get(spec["query_variable"])
    query = resolve_value(raw_query or "", variables)
    if not str(query).strip():
        raise ValueError("sparql step requires query or query_variable")

    timeout = int(spec.get("timeout", 120))
    try:
        rows, meta = invoke_residual_sparql(
            str(query),
            mode=mode,
            online_limit=online_limit,
            use_cache=use_cache,
            cache=cache,
            timeout=timeout,
        )
        status = "pass" if rows else "empty"
        error = None
    except Exception as exc:
        rows = []
        meta = {}
        status = "error"
        error = f"{type(exc).__name__}: {exc}"

    return {
        "step": step_index,
        "step_type": "sparql",
        "tool": "residual_sparql",
        "mode": mode,
        "input": {"query": str(query)[:500]},
        "status": status,
        "rows": rows,
        "row_count": len(rows),
        "tsv": format_results_as_tsv(rows),
        "summary": f"residual_sparql: {len(rows)} rows",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "cache_meta": meta,
        "error": error,
    }


def run_tool_step_cached(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    cache: CompetencyCache,
    *,
    mode: str,
    online_limit: int,
    use_cache: bool,
) -> Dict[str, Any]:
    tool = spec["tool"]
    resolved_args = resolve_value(spec.get("args", {}), variables)
    started = time.perf_counter()
    try:
        rows, meta = invoke_tool(
            tool,
            resolved_args,
            mode=mode,
            online_limit=online_limit,
            use_cache=use_cache,
            cache=cache,
        )
        status = "pass" if rows else "empty"
        error = None
    except Exception as exc:
        rows = []
        meta = {}
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    result = {
        "step": step_index,
        "step_type": "tool",
        "tool": tool,
        "mode": mode,
        "input": resolved_args,
        "status": status,
        "rows": rows,
        "row_count": len(rows),
        "tsv": format_results_as_tsv(rows),
        "summary": f"{tool}: {len(rows)} rows",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "cache_meta": meta,
        "error": error,
    }

    for var_name, extract_spec in (spec.get("extract") or {}).items():
        if status != "pass":
            raise RuntimeError(f"Cannot extract `{var_name}` because step {step_index} failed")
        variables[var_name] = extract_from_step(extract_spec, result)

    return result


def run_competency_workflow(
    workflow: Dict[str, Any],
    *,
    mode: str = "online",
    online_limit: Optional[int] = None,
    offline_cap: Optional[int] = None,
    use_cache: bool = True,
    force_refresh: bool = False,
    steps_override: Optional[List[Dict[str, Any]]] = None,
    seed_variables: Optional[Dict[str, Any]] = None,
    replayed_from: Optional[str] = None,
) -> Dict[str, Any]:
    del offline_cap  # deprecated; offline is cache-only
    from mini_marie.warm_manifest import seed_variables_from_workflow

    variables: Dict[str, Any] = dict(
        seed_variables or seed_variables_from_workflow(workflow)
    )
    online_limit = int(online_limit or workflow.get("online_limit") or DEFAULT_ONLINE_LIMIT)
    steps = steps_override if steps_override is not None else workflow.get("steps", [])

    cache = CompetencyCache()
    cache_stats: Dict[str, Any] = {}
    call_trace: List[Dict[str, Any]] = []
    started = time.perf_counter()

    try:
        for index, step in enumerate(steps, start=1):
            step_type = step.get("type", "tool")
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
            elif step_type == "sparql":
                if mode == "online" and step.get("offline_only"):
                    result = {
                        "step": index,
                        "step_type": "sparql",
                        "status": "skipped",
                        "summary": "Skipped offline-only residual SPARQL during online probe",
                        "rows": [],
                        "row_count": 0,
                        "elapsed_ms": 0,
                    }
                else:
                    result = run_sparql_step(
                        index,
                        step,
                        variables,
                        cache,
                        mode=mode,
                        online_limit=online_limit,
                        use_cache=use_cache and not force_refresh,
                    )
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
                result = run_tool_step_cached(
                    index,
                    step,
                    variables,
                    cache,
                    mode=mode,
                    online_limit=online_limit,
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
        if any(c.get("status") == "error" for c in call_trace):
            status = "error"
        elif all(c.get("status") in ("empty", "skipped") for c in call_trace):
            status = "empty"

    except Exception as exc:
        status = "error"
        answer = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        cache_stats = cache.stats()
        cache.close()

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        "workflow_id": workflow.get("id"),
        "question": workflow.get("question"),
        "mode": mode,
        "online_limit": online_limit,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "answer": answer,
        "seed_variables": dict(variables),
        "variables": variables,
        "call_trace": call_trace,
        "cache_stats": cache_stats,
        "workflow_definition": workflow,
        "replayed_from": replayed_from,
    }
    if mode == "online" and steps_override is None:
        payload["probed_sequence"] = build_probed_sequence(workflow, call_trace)
    if steps_override is not None:
        payload["probed_sequence"] = steps_override
    payload["answer_digest"] = build_answer_digest(payload)
    return payload


def replay_competency_from_recording(
    recorded: Dict[str, Any],
    workflow: Optional[Dict[str, Any]] = None,
    *,
    online_limit: Optional[int] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Offline replay of the online-probed call sequence (full-tier cache only)."""
    sequence = probed_sequence_from_recording(recorded)
    if not sequence:
        raise ValueError("Recording has no probed_sequence; run online probe first")
    wf = workflow or recorded.get("workflow_definition") or {}
    steps = steps_from_probed_sequence(sequence)
    return run_competency_workflow(
        wf,
        mode="offline",
        online_limit=online_limit or recorded.get("online_limit"),
        use_cache=use_cache,
        force_refresh=False,
        steps_override=steps,
        seed_variables=seed_variables_from_recording(recorded, wf),
        replayed_from="probed_sequence",
    )


def _truncate_value(value: Any, max_list: int = 5) -> Any:
    if isinstance(value, list):
        if len(value) <= max_list:
            return value
        return {"_list_len": len(value), "sample": value[:max_list]}
    if isinstance(value, dict):
        return {k: _truncate_value(v, max_list) for k, v in value.items()}
    return value


def build_answer_digest(result: Dict[str, Any]) -> Dict[str, Any]:
    """Compact structured answer for e2e reports (offline = authoritative when complete)."""
    trace = result.get("call_trace") or []
    steps_out: List[Dict[str, Any]] = []
    authoritative: Dict[str, Any] = {}

    count_candidates: List[tuple[str, Any, bool]] = []

    for step in trace:
        name = step.get("tool") or step.get("join") or step.get("step_type")
        step_input = step.get("input") or {}
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
        if rows:
            if len(rows) == 1:
                entry["result"] = rows[0]
            else:
                entry["result_sample"] = rows[:3]
        steps_out.append(entry)

        if not rows:
            continue
        first = rows[0]
        if "count" in first:
            is_aggregate = bool(
                step_input.get("count_only")
                or step_input.get("experimental_only")
                or name.startswith("count_")
            )
            is_source_breakdown = bool(step_input.get("list_sources"))
            count_candidates.append((name, first.get("count"), is_aggregate and not is_source_breakdown))
        for key in ("avgPLD", "variance", "maxLCD", "avgDensity", "avgPLD"):
            if key in first:
                authoritative[key] = first.get(key)
        if "avgPLD" in first or "maxLCD" in first:
            authoritative["pore_metrics"] = first

    variables = result.get("variables") or {}
    if variables.get("sparql_count") is not None:
        authoritative["count"] = variables["sparql_count"]
        authoritative["count_source"] = "sparql_count_only"
    elif count_candidates:
        agg = [c for c in count_candidates if c[2]]
        pick = agg[-1] if agg else count_candidates[-1]
        authoritative["count"] = pick[1]
        authoritative["count_source"] = pick[0]

    for key in ("topology", "refcodes", "local_count"):
        if key in variables:
            authoritative[key] = _truncate_value(variables[key])

    return {
        "mode": result.get("mode"),
        "summary_text": result.get("answer"),
        "authoritative": authoritative,
        "steps": steps_out,
        "variables": _truncate_value(variables),
        "total_elapsed_ms": result.get("elapsed_ms"),
    }


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def load_workflow(workflow_id: str) -> Dict[str, Any]:
    manifest = load_manifest()
    for wf in manifest.get("workflows", []):
        if wf.get("id") == workflow_id:
            return wf
    raise KeyError(f"Workflow not found: {workflow_id}")


def list_workflow_ids() -> List[str]:
    manifest = load_manifest()
    return [wf["id"] for wf in manifest.get("workflows", [])]


def save_run(result: Dict[str, Any], path: Optional[Path] = None) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        wf_id = result.get("workflow_id") or "competency"
        mode = result.get("mode") or "online"
        path = RUNS_DIR / f"{wf_id}_{mode}_{int(time.time())}.json"
    path = Path(path)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def load_run(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_atomic_specs(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Unique {tool, args} from workflow tool steps (resolved seed variables only)."""
    return collect_atomic_specs_from_workflow(workflow, resolve=resolve_value)


def run_suite_probe(
    *,
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    force: bool = False,
) -> Dict[str, Any]:
    """Online-probe every workflow in the manifest (populates shared cache)."""
    manifest = load_manifest()
    suite_results: List[Dict[str, Any]] = []
    for wf in manifest.get("workflows", []):
        result = run_competency_workflow(
            wf,
            mode="online",
            online_limit=online_limit,
            use_cache=not force,
            force_refresh=force,
        )
        path = save_run(result)
        suite_results.append(
            {
                "workflow_id": wf.get("id"),
                "status": result["status"],
                "recording": str(path),
                "answer": result.get("answer"),
            }
        )
    return {
        "suite_id": manifest.get("id"),
        "mode": "online",
        "workflows": suite_results,
    }
