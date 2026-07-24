"""
Online probe records a validated call sequence; offline replays that sequence (cache-only).

The sequence is the source of truth for offline — not a re-interpretation of workflow JSON.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional


def build_probed_sequence(
    workflow: Dict[str, Any],
    call_trace: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge workflow step definitions with online call_trace (resolved args, step types).

    Stored on online recordings as `probed_sequence` for offline replay.
    """
    step_defs = workflow.get("steps") or []
    if len(step_defs) != len(call_trace):
        raise ValueError(
            f"call_trace length {len(call_trace)} != workflow steps {len(step_defs)} "
            "(ensure every step appends to call_trace, including skipped offline_only)"
        )

    sequence: List[Dict[str, Any]] = []
    for step_def, trace in zip(step_defs, call_trace):
        step_type = step_def.get("type") or trace.get("step_type") or "tool"
        entry: Dict[str, Any] = {
            "step": trace.get("step"),
            "step_type": step_type,
            "status_online": trace.get("status"),
        }
        if step_type == "local_join":
            entry["join"] = step_def.get("join")
            for key in (
                "reference_name",
                "topology",
                "exclude_reference",
                "output_variable",
                "extract",
                "offline_only",
                "mof_name",
                "refcodes_variable",
                "metal",
                "limit",
                "building_iris_variable",
                "city",
                "n",
                "usage_contains",
            ):
                if key in step_def:
                    entry[key] = step_def[key]
        elif step_type == "sparql":
            entry["query"] = step_def.get("query")
            for key in ("query_variable", "timeout", "output_variable", "offline_only", "extract", "id", "comment"):
                if key in step_def:
                    entry[key] = step_def[key]
        elif step_type == "transform":
            for key in (
                "name",
                "transform",
                "input_variable",
                "field",
                "n",
                "order",
                "output_field",
                "output_variable",
                "also_store_rows",
                "filters",
                "filter",
                "logic",
                "left_variable",
                "right_variable",
                "left_key",
                "right_key",
                "on",
                "key",
                "label_key",
                "how",
                "right_is_list",
                "right_prefix",
                "keys",
                "joins",
                "group_by",
                "aggregations",
                "metrics",
                "order_by",
                "limit",
                "extract",
                "offline_only",
            ):
                if key in step_def:
                    entry[key] = step_def[key]
        else:
            entry["tool"] = trace.get("tool") or step_def.get("tool")
            entry["args"] = deepcopy(trace.get("input") or {})
            for key in ("extract", "limit_key", "online_limit", "id", "comment"):
                if key in step_def:
                    entry[key] = step_def[key]
        sequence.append(entry)
    return sequence


def probed_sequence_from_recording(recorded: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    seq = recorded.get("probed_sequence")
    if isinstance(seq, list) and seq:
        return seq
    return None


def steps_from_probed_sequence(sequence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert recorded probed_sequence back into executable workflow steps."""
    steps: List[Dict[str, Any]] = []
    skip = {"step", "step_type", "status_online"}
    for entry in sequence:
        step_type = entry.get("step_type", "tool")
        if step_type == "local_join":
            step = {"type": "local_join", "join": entry["join"]}
            for key, val in entry.items():
                if key not in skip | {"join"}:
                    step[key] = val
        elif step_type == "sparql":
            step = {"type": "sparql", "query": entry.get("query")}
            for key, val in entry.items():
                if key not in skip | {"query"}:
                    step[key] = val
        elif step_type == "transform":
            step = {"type": "transform", "transform": entry["transform"]}
            for key, val in entry.items():
                if key not in skip | {"transform"}:
                    step[key] = val
        else:
            step = {"tool": entry["tool"], "args": deepcopy(entry.get("args") or {})}
            for key in ("extract", "limit_key", "online_limit", "id", "comment"):
                if key in entry:
                    step[key] = entry[key]
        steps.append(step)
    return steps


def seed_variables_from_recording(
    recorded: Dict[str, Any],
    workflow: Dict[str, Any],
) -> Dict[str, Any]:
    """Restore workflow seed context for replay."""
    seed = dict(recorded.get("seed_variables") or workflow.get("seed_variables") or {})
    if workflow.get("city"):
        seed["city"] = workflow["city"]
    if workflow.get("top_n") is not None:
        seed["top_n"] = workflow["top_n"]
    if workflow.get("usage_type") is not None:
        seed["usage_type"] = workflow["usage_type"]
    for scalar in (
        "min_height_m",
        "max_height_m",
        "min_height",
        "max_height",
        "usage_contains",
        "metal",
        "db_substring",
        "min_gsa",
    ):
        if workflow.get(scalar) is not None:
            seed[scalar] = workflow[scalar]
    limits = workflow.get("online_limits") or {}
    for key, val in limits.items():
        seed[f"online_limits.{key}"] = val
    return seed
