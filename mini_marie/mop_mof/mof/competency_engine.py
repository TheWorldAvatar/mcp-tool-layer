"""
Workflow engine for chaining MOF TWA MCP tool calls.

Supports variable extraction from prior step outputs and passing them as inputs
to subsequent tool invocations.
"""

from __future__ import annotations

import json
import re
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from mini_marie.mop_mof.mof import mof_operations as ops

MCP_SERVER_NAME = "mof-twa"

TOOL_REGISTRY: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "get_mof_total_count": lambda **_: ops.get_mof_total_count(),
    "get_source_database_stats": lambda **_: ops.get_source_database_stats(),
    "get_tobassco_co2_coverage": lambda **_: ops.get_tobassco_co2_coverage(),
    "get_tobassco_co2_uptake_stats": lambda **_: ops.get_tobassco_co2_uptake_stats(),
    "get_top_tobassco_co2_uptake": lambda **_: ops.get_top_tobassco_co2_uptake(),
    "get_top_tobassco_co2_valid_pore_geometry": lambda **_: ops.get_top_tobassco_co2_valid_pore_geometry(),
    "get_large_pore_co2_candidates": lambda **_: ops.get_large_pore_co2_candidates(),
    "get_tobassco_topology_counts": lambda **_: ops.get_tobassco_topology_counts(),
    "get_tobassco_mofs_by_topology": lambda **kw: ops.get_tobassco_mofs_by_topology(kw["topology"]),
    "get_tobassco_mofs_by_metal_node": lambda **kw: ops.get_tobassco_mofs_by_metal_node(kw["metal_smiles"]),
    "get_mofs_by_sourcedb": lambda **kw: ops.get_mofs_by_sourcedb(kw["source_db"]),
    "lookup_mof_by_mofid_fragment": lambda **kw: ops.lookup_mof_by_mofid_fragment(kw["mofid_fragment"]),
    "get_mof_properties_by_mofid_fragment": lambda **kw: ops.get_mof_properties_by_mofid_fragment(
        kw["mofid_fragment"]
    ),
}


def _mofid_suffix(mofid: str) -> str:
    """Return the MOFid-v1.* token at the end of a MOFid string."""
    token = mofid.rsplit(" ", 1)[-1].strip()
    return token if token else mofid


def _topology_from_mofid(mofid: str) -> str:
    """Extract the first topology token embedded in a MOFid suffix."""
    suffix = _mofid_suffix(mofid)
    match = re.search(r"MOFid-v1\.([A-Za-z0-9_]+)", suffix)
    if not match:
        return ""
    token = match.group(1)
    return token.split(",")[0].split(".")[0]


def _first_row_with_positive_pld(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for row in rows:
        pld = row.get("pld")
        if pld is None:
            continue
        try:
            if float(pld) > 0:
                return row
        except (TypeError, ValueError):
            continue
    return rows[0] if rows else None


TRANSFORMS: Dict[str, Callable[[Any], Any]] = {
    "mofid_suffix": _mofid_suffix,
    "topology_from_mofid": _topology_from_mofid,
    "strip": lambda value: str(value).strip(),
    "lower": lambda value: str(value).lower(),
}


def resolve_value(value: Any, variables: Dict[str, Any]) -> Any:
    """Replace `$name` placeholders recursively in strings, lists, and dicts."""
    if isinstance(value, str):
        if value.startswith("$") and value[1:] in variables:
            return variables[value[1:]]
        out = value
        for key, resolved in variables.items():
            out = out.replace(f"${key}", str(resolved))
        return out
    if isinstance(value, list):
        return [resolve_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {k: resolve_value(v, variables) for k, v in value.items()}
    return value


def extract_from_step(spec: Dict[str, Any], step_result: Dict[str, Any]) -> Any:
    """Extract values from a completed step (rows list, column, or scalar field)."""
    rows: List[Dict[str, Any]] = step_result.get("rows") or []
    pick = spec.get("pick", "row_field")

    if pick == "all_rows":
        value = rows
    elif pick == "column":
        field = spec["field"]
        value = [row.get(field) for row in rows if row.get(field)]
    elif pick == "top_n_by_field":
        field = spec["field"]
        n = int(spec.get("n", 10))
        reverse = spec.get("order", "desc") == "desc"
        sorted_rows = sorted(
            rows,
            key=lambda r: float(r.get(field) or 0),
            reverse=reverse,
        )
        value = sorted_rows[:n]
    elif pick == "first_valid_pld_row":
        row = _first_row_with_positive_pld(rows)
        if row is None:
            raise ValueError("No rows available for extraction")
        value = row.get(spec["field"])
    else:
        index = int(spec.get("index", 0))
        if not rows:
            raise ValueError("No rows available for extraction")
        if index >= len(rows):
            raise ValueError(f"Row index {index} out of range ({len(rows)} rows)")
        field = spec.get("field")
        if field:
            value = rows[index].get(field)
        else:
            value = rows[index]

    transform = spec.get("transform")
    if transform:
        fn = TRANSFORMS.get(transform)
        if fn is None:
            raise ValueError(f"Unknown transform: {transform}")
        value = fn(value)
    return value


def run_aggregate_step(spec: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """Compute derived values from variables populated by earlier steps."""
    name = spec["name"]
    agg_type = spec["aggregate_type"]

    if agg_type == "tobassco_co2_share":
        total = float(variables["total_mofs"])
        tobassco = float(variables["tobassco_with_co2"])
        pct = round((tobassco / total) * 100, 2)
        rows = [{"total_mofs": str(int(total)), "tobassco_with_co2": str(int(tobassco)), "share_pct": str(pct)}]
        summary = f"Tobassco CO2-labelled MOFs are {pct}% of the full TWA ({int(tobassco)}/{int(total)})."

    elif agg_type == "co2_ranking_comparison":
        raw = float(variables.get("raw_top_co2", 0))
        valid = float(variables.get("valid_geom_top_co2", 0))
        realistic = float(variables.get("realistic_top_co2", 0))
        rows = [
            {
                "raw_top_co2_mmolg": str(raw),
                "valid_geom_top_co2_mmolg": str(valid),
                "realistic_top_co2_mmolg": str(realistic),
            }
        ]
        summary = (
            f"Raw top={raw}, valid-geometry top={valid}, large-pore realistic top={realistic} mmol/g. "
            "Use realistic ranking when raw hits the model cap."
        )

    elif agg_type == "topology_branch_summary":
        topology = variables.get("branch_topology", "")
        branch_top = variables.get("branch_top_co2", "")
        seed_top = variables.get("seed_top_co2", "")
        rows = [
            {
                "branch_topology": str(topology),
                "seed_top_co2_mmolg": str(seed_top),
                "branch_top_co2_mmolg": str(branch_top),
            }
        ]
        summary = (
            f"Large-pore seed topology `{topology}` branch top CO2={branch_top} mmol/g "
            f"(seed candidate was {seed_top} mmol/g)."
        )

    elif agg_type == "property_highlights":
        props = variables.get("_property_rows", [])
        wanted = {
            "hasMofidV1": "mofid",
            "hasRCSRSym": "topology",
            "hasNodeSmile": "node_smiles",
            "hasPLD": "pld",
            "hasLCD": "lcd",
            "hasPredAdsorptionUptake_CO2P15T298mmolg": "co2_uptake",
            "hasSourcedb": "source_db",
        }
        extracted: Dict[str, str] = {}
        for row in props:
            predicate = str(row.get("p", "")).split("/")[-1]
            if predicate in wanted and wanted[predicate] not in extracted:
                extracted[wanted[predicate]] = str(row.get("o", ""))
        rows = [extracted] if extracted else []
        summary = json.dumps(extracted) if extracted else "No property highlights extracted."

    else:
        raise ValueError(f"Unknown aggregate type: {agg_type}")

    return {
        "step_type": "aggregate",
        "name": name,
        "status": "pass" if rows else "empty",
        "rows": rows,
        "tsv": ops.format_results_as_tsv(rows),
        "summary": summary,
        "elapsed_ms": 0,
        "input": {"type": agg_type, "variables_used": list(spec.get("uses", []))},
        "error": None,
    }


def run_tool_step(step_index: int, spec: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one MCP tool call."""
    tool_name = spec["tool"]
    fn = TOOL_REGISTRY.get(tool_name)
    if fn is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    resolved_args = resolve_value(spec.get("args", {}), variables)
    started = time.perf_counter()
    try:
        rows = fn(**resolved_args)
        tsv = ops.format_results_as_tsv(rows)
        status = "pass" if rows else "empty"
        error = None
    except Exception as exc:
        rows = []
        tsv = ""
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    result = {
        "step": step_index,
        "step_type": "tool",
        "mcp_tool": tool_name,
        "input": resolved_args,
        "status": status,
        "rows": rows,
        "row_count": len(rows),
        "tsv": tsv,
        "summary": _summarize_tool_output(tool_name, rows),
        "elapsed_ms": elapsed_ms,
        "error": error,
    }

    for var_name, extract_spec in (spec.get("extract") or {}).items():
        if status != "pass":
            raise RuntimeError(f"Cannot extract `{var_name}` because step {step_index} failed")
        if var_name == "_property_rows":
            variables[var_name] = rows
        else:
            variables[var_name] = extract_from_step(extract_spec, result)

    return result


def _summarize_tool_output(tool_name: str, rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No results returned."
    first = rows[0]
    if "count" in first and len(first) == 1:
        key = next(iter(first))
        return f"{key} = {first[key]}"
    if "with_co2" in first:
        return f"with_co2 = {first['with_co2']}"
    if "avg_co2" in first:
        return f"avg={first['avg_co2']}, min={first['min_co2']}, max={first['max_co2']}"
    if "topology" in first and "count" in first:
        return f"Top topology: {first['topology']} ({first['count']})"
    if "db" in first and "count" in first:
        return f"Top source DB: {first['db']} ({first['count']})"
    if "co2_uptake" in first:
        return f"Top CO2 uptake: {first['co2_uptake']} mmol/g ({len(rows)} rows)"
    if "p" in first and "o" in first:
        return f"{len(rows)} property triples returned"
    return f"{len(rows)} rows returned"


def run_workflow(question: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a multi-step workflow for one complex competency question."""
    variables: Dict[str, Any] = dict(question.get("seed_variables") or {})
    call_trace: List[Dict[str, Any]] = []
    started = time.perf_counter()

    try:
        for index, step in enumerate(question.get("steps", []), start=1):
            if step.get("type") == "aggregate":
                result = run_aggregate_step(step, variables)
                result["step"] = index
            else:
                result = run_tool_step(index, step, variables)
            call_trace.append(result)

        final_summary = question.get("final_summary")
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

    return {
        "id": question["id"],
        "category": question.get("category", "complex"),
        "question": question["question"],
        "status": status,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "answer": answer,
        "variables": {k: v for k, v in variables.items() if not k.startswith("_")},
        "call_trace": call_trace,
        "total_tool_calls": sum(1 for call in call_trace if call.get("step_type") == "tool"),
    }
