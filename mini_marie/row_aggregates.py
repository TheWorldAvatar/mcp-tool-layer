"""
Generic GROUP BY-style aggregates on in-memory row pools.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ResolveFn = Callable[[Any, Dict[str, Any]], Any]

_AGG_OPS = frozenset(
    {"count", "count_distinct", "sum", "avg", "min", "max"}
)


def _field_value(row: Dict[str, Any], field: str) -> Any:
    if not field:
        return None
    if "." in field:
        cur: Any = row
        for part in field.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur
    return row.get(field)


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _group_key(row: Dict[str, Any], group_by: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(_field_value(row, g) for g in group_by)


def _run_agg(op: str, rows: Sequence[Dict[str, Any]], field: Optional[str]) -> Any:
    op = op.strip().lower()
    if op == "count":
        return len(rows)
    if op == "count_distinct":
        if not field:
            raise ValueError("count_distinct requires 'field'")
        seen = set()
        for row in rows:
            v = _field_value(row, field)
            if v is not None and str(v).strip() != "":
                seen.add(str(v))
        return len(seen)
    if not field:
        raise ValueError(f"{op} requires 'field'")
    nums = [_as_float(_field_value(row, field)) for row in rows]
    nums = [n for n in nums if n is not None]
    if op == "sum":
        return sum(nums) if nums else 0
    if op == "avg":
        return sum(nums) / len(nums) if nums else None
    if op == "min":
        return min(nums) if nums else None
    if op == "max":
        return max(nums) if nums else None
    raise ValueError(f"Unknown aggregation op: {op!r}")


def group_aggregate_rows(
    rows: Sequence[Dict[str, Any]],
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    *,
    resolve: Optional[ResolveFn] = None,
) -> List[Dict[str, Any]]:
    """
    spec keys:
      - group_by: list of field names
      - aggregations: [{op, field?, as?}, ...]
      - order_by: optional {field, order: asc|desc}
      - limit: optional int
    """
    resolve_fn = resolve or (lambda v, _vars: v)
    group_by = list(resolve_fn(spec.get("group_by") or [], variables) or [])
    if isinstance(group_by, str):
        group_by = [group_by]

    raw_aggs = spec.get("aggregations") or spec.get("metrics")
    if not raw_aggs:
        raw_aggs = [{"op": "count", "as": "count"}]
    aggs: List[Dict[str, Any]] = []
    for item in raw_aggs:
        if isinstance(item, dict):
            aggs.append(
                {
                    "op": resolve_fn(item.get("op"), variables),
                    "field": resolve_fn(item.get("field"), variables),
                    "as": resolve_fn(item.get("as"), variables),
                }
            )

    buckets: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict):
            buckets[_group_key(row, group_by)].append(row)

    out: List[Dict[str, Any]] = []
    for gkey, group in buckets.items():
        out_row: Dict[str, Any] = {}
        for i, name in enumerate(group_by):
            out_row[name] = gkey[i]
        for agg in aggs:
            op = str(agg.get("op") or "count")
            if op not in _AGG_OPS:
                raise ValueError(f"Unknown aggregation op: {op!r}")
            field = agg.get("field")
            alias = agg.get("as") or (
                f"{op}_{field}" if field else op
            )
            out_row[str(alias)] = _run_agg(op, group, str(field) if field else None)
        out.append(out_row)

    order_spec = spec.get("order_by")
    if order_spec and out:
        field = str(resolve_fn(order_spec.get("field"), variables))
        reverse = str(resolve_fn(order_spec.get("order", "desc"), variables)).lower() != "asc"

        def sort_key(r: Dict[str, Any]) -> float:
            v = _as_float(r.get(field))
            return v if v is not None else float("-inf")

        out.sort(key=sort_key, reverse=reverse)

    limit = spec.get("limit")
    if limit is not None:
        n = int(resolve_fn(limit, variables))
        out = out[:n]

    return out


def run_group_aggregate_transform(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    *,
    resolve: ResolveFn,
    format_tsv: Callable[[List[Dict[str, Any]]], str],
) -> Dict[str, Any]:
    import time

    started = time.perf_counter()
    source_var = spec["input_variable"]
    rows_in = variables.get(source_var) or []
    if not isinstance(rows_in, list):
        raise ValueError(f"group_aggregate input {source_var!r} must be a list")

    grouped = group_aggregate_rows(rows_in, spec, variables, resolve=resolve)
    out_var = spec["output_variable"]
    variables[out_var] = grouped
    if spec.get("also_store_rows"):
        variables[spec["also_store_rows"]] = grouped

    group_by = spec.get("group_by") or []
    summary = (
        f"group_aggregate {source_var} -> {out_var}: "
        f"{len(grouped)} groups from {len(rows_in)} rows (by {group_by})"
    )

    return {
        "step": step_index,
        "step_type": "transform",
        "name": spec.get("name", "group_aggregate"),
        "transform": "group_aggregate",
        "status": "pass" if grouped or not rows_in else "empty",
        "rows": grouped,
        "row_count": len(grouped),
        "tsv": format_tsv(grouped[:100]) if grouped else "",
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "input": spec,
        "error": None,
    }
