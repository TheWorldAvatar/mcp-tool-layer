"""
Generic row filtering for workflow transforms (any field, numeric or string).

Used by TWA city and MOF competency workflow engines on in-memory row pools
from cached atomics — no new SPARQL per threshold or string constraint.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

ResolveFn = Callable[[Any, Dict[str, Any]], Any]

# Aliases map to canonical op names
_OP_ALIASES = {
    "==": "eq",
    "=": "eq",
    "!=": "ne",
    "<>": "ne",
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
    "equals": "eq",
    "not_equals": "ne",
    "greater_than": "gt",
    "greater_or_equal": "gte",
    "less_than": "lt",
    "less_or_equal": "lte",
    "contains": "contains",
    "icontains": "icontains",
    "startswith": "startswith",
    "endswith": "endswith",
    "starts_with": "startswith",
    "ends_with": "endswith",
    "in": "in",
    "not_in": "not_in",
    "regex": "regex",
    "matches": "regex",
    "empty": "empty",
    "not_empty": "not_empty",
    "is_empty": "empty",
    "is_not_empty": "not_empty",
}


def _norm_op(op: Any) -> str:
    key = str(op or "eq").strip().lower()
    return _OP_ALIASES.get(key, key)


def _field_value(row: Dict[str, Any], field: str) -> Any:
    if not field or field == "*":
        return row
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


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _compare_numeric(left: Any, right: Any, op: str) -> bool:
    a, b = _as_float(left), _as_float(right)
    if a is not None and b is not None:
        if op == "gt":
            return a > b
        if op == "gte":
            return a >= b
        if op == "lt":
            return a < b
        if op == "lte":
            return a <= b
        if op == "eq":
            return a == b
        if op == "ne":
            return a != b
    # Fallback: string compare for non-numeric
    ls, rs = _as_str(left), _as_str(right)
    if op == "gt":
        return ls > rs
    if op == "gte":
        return ls >= rs
    if op == "lt":
        return ls < rs
    if op == "lte":
        return ls <= rs
    if op == "eq":
        return ls == rs
    if op == "ne":
        return ls != rs
    return False


def _match_clause(row: Dict[str, Any], clause: Dict[str, Any]) -> bool:
    op = _norm_op(clause.get("op"))
    field = str(clause.get("field", ""))
    expected = clause.get("value")
    actual = _field_value(row, field)

    if op == "empty":
        return _is_empty(actual)
    if op == "not_empty":
        return not _is_empty(actual)

    if op in ("gt", "gte", "lt", "lte"):
        return _compare_numeric(actual, expected, op)

    if op == "eq":
        if _as_float(actual) is not None and _as_float(expected) is not None:
            return _as_float(actual) == _as_float(expected)
        return _as_str(actual).lower() == _as_str(expected).lower()

    if op == "ne":
        if _as_float(actual) is not None and _as_float(expected) is not None:
            return _as_float(actual) != _as_float(expected)
        return _as_str(actual).lower() != _as_str(expected).lower()

    if op == "contains":
        return _as_str(expected) in _as_str(actual)

    if op == "icontains":
        return _as_str(expected).lower() in _as_str(actual).lower()

    if op == "startswith":
        return _as_str(actual).lower().startswith(_as_str(expected).lower())

    if op == "endswith":
        return _as_str(actual).lower().endswith(_as_str(expected).lower())

    if op == "in":
        if not isinstance(expected, (list, tuple, set)):
            expected = [expected]
        actual_s = _as_str(actual).lower()
        return any(_as_str(item).lower() == actual_s for item in expected)

    if op == "not_in":
        if not isinstance(expected, (list, tuple, set)):
            expected = [expected]
        actual_s = _as_str(actual).lower()
        return all(_as_str(item).lower() != actual_s for item in expected)

    if op == "regex":
        pattern = _as_str(expected)
        if not pattern:
            return False
        return re.search(pattern, _as_str(actual), flags=re.IGNORECASE) is not None

    raise ValueError(f"Unknown filter op: {op!r}")


def normalize_filters(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Accept `filters` list or a single `filter` object."""
    if "filters" in spec:
        raw = spec["filters"]
        if isinstance(raw, dict):
            return [raw]
        return list(raw)
    if "filter" in spec:
        f = spec["filter"]
        return [f] if isinstance(f, dict) else list(f)
    raise ValueError("filter_rows requires 'filters' or 'filter'")


def filter_rows(
    rows: Sequence[Dict[str, Any]],
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    *,
    resolve: Optional[ResolveFn] = None,
) -> List[Dict[str, Any]]:
    """
    Apply generic field filters to row dicts.

    spec keys:
      - filters | filter: list or dict of {field, op, value}
      - logic: "and" (default) | "or"
    """
    resolve_fn = resolve or (lambda v, _vars: v)
    clauses: List[Dict[str, Any]] = []
    for raw in normalize_filters(spec):
        clause = {
            "field": raw.get("field", ""),
            "op": raw.get("op", "eq"),
            "value": resolve_fn(raw.get("value"), variables),
        }
        clauses.append(clause)

    logic = str(spec.get("logic", "and")).strip().lower()
    if logic not in ("and", "or"):
        raise ValueError(f"filter logic must be 'and' or 'or', got {logic!r}")

    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        matches = [_match_clause(row, c) for c in clauses]
        if not matches:
            out.append(dict(row))
            continue
        ok = all(matches) if logic == "and" else any(matches)
        if ok:
            out.append(dict(row))
    return out


def run_filter_rows_transform(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    *,
    resolve: ResolveFn,
    format_tsv: Callable[[List[Dict[str, Any]]], str],
) -> Dict[str, Any]:
    """Workflow transform step: filter_rows on a variable-held row pool."""
    import time

    started = time.perf_counter()
    source_var = spec["input_variable"]
    rows_in = variables.get(source_var) or []
    if not isinstance(rows_in, list):
        raise ValueError(f"filter_rows input {source_var!r} must be a list of rows")

    filtered = filter_rows(rows_in, spec, variables, resolve=resolve)
    out_var = spec["output_variable"]
    variables[out_var] = filtered
    if spec.get("also_store_rows"):
        variables[spec["also_store_rows"]] = filtered

    clauses = normalize_filters(spec)
    summary = f"filter_rows {source_var} -> {out_var}: {len(filtered)}/{len(rows_in)} rows ({len(clauses)} clause(s))"

    return {
        "step": step_index,
        "step_type": "transform",
        "name": spec.get("name", "filter_rows"),
        "transform": "filter_rows",
        "status": "pass" if filtered or not rows_in else "empty",
        "rows": filtered,
        "row_count": len(filtered),
        "tsv": format_tsv(filtered[:100]) if filtered else "",
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "input": spec,
        "error": None,
    }
