"""
Generic in-memory row joins for workflow transforms (IRI-keyed pools).

Complements row_filters: join two cached row sets on a shared key, optional post-join filter.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Union

from mini_marie.row_filters import filter_rows

ResolveFn = Callable[[Any, Dict[str, Any]], Any]


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


def _norm_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _composite_key(row: Dict[str, Any], fields: Sequence[str]) -> Optional[str]:
    parts: List[str] = []
    for field in fields:
        v = _norm_key(_field_value(row, field))
        if v is None:
            return None
        parts.append(v)
    return "\x1f".join(parts) if parts else None


def _resolve_join_key_fields(
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    resolve_fn: ResolveFn,
) -> tuple[List[str], List[str]]:
    """Return (left_fields, right_fields) for join key matching."""
    keys = spec.get("keys")
    if isinstance(keys, list) and keys:
        left_f: List[str] = []
        right_f: List[str] = []
        for pair in keys:
            if isinstance(pair, dict):
                left_f.append(str(resolve_fn(pair.get("left") or pair.get("on"), variables)))
                right_f.append(str(resolve_fn(pair.get("right") or pair.get("left"), variables)))
        return left_f, right_f

    left_key = str(
        resolve_fn(
            spec.get("left_key") or spec.get("on") or spec.get("key"),
            variables,
        )
        or ""
    )
    right_key = str(resolve_fn(spec.get("right_key") or left_key, variables) or left_key)
    return [left_key], [right_key]


def _merge_row_pair(
    left: Dict[str, Any],
    right: Optional[Dict[str, Any]],
    *,
    right_prefix: str,
) -> Dict[str, Any]:
    out = dict(left)
    if right is None:
        return out
    for key, val in right.items():
        out_key = f"{right_prefix}{key}" if right_prefix else key
        if out_key in out and out_key != key:
            out_key = f"{right_prefix}{key}"
        out[out_key] = val
    return out


def join_rows(
    left_rows: Sequence[Dict[str, Any]],
    right_rows: Union[Sequence[Dict[str, Any]], Sequence[Any]],
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    *,
    resolve: Optional[ResolveFn] = None,
) -> List[Dict[str, Any]]:
    """
    Join two row pools (or left rows with a list of key values on the right).

    spec keys:
      - left_key / on: key field on left rows
      - right_key: key on right rows (defaults to left_key)
      - how: inner | left | anti (anti = left rows with no right match)
      - keys: [{left, right}, ...] for composite join keys
      - right_is_list: if true, right_rows is a list of key values (semi-join filter)
      - right_prefix: prefix for merged right columns (default "r_")
      - filters: optional post-join filter_rows clauses
      - logic: and | or for post-join filters
    """
    resolve_fn = resolve or (lambda v, _vars: v)
    left_fields, right_fields = _resolve_join_key_fields(spec, variables, resolve_fn)
    how = str(resolve_fn(spec.get("how", "inner"), variables)).strip().lower()
    if how not in ("inner", "left", "anti"):
        raise ValueError(f"join_rows how must be 'inner', 'left', or 'anti', got {how!r}")

    right_prefix = str(spec.get("right_prefix", "r_"))
    right_is_list = bool(spec.get("right_is_list"))
    left_key = left_fields[0] if left_fields else ""

    left: List[Dict[str, Any]] = [dict(r) for r in left_rows if isinstance(r, dict)]

    if right_is_list:
        right_keys: Set[str] = set()
        for item in right_rows:
            k = _norm_key(resolve_fn(item, variables) if callable(resolve_fn) else item)
            if k:
                right_keys.add(k)
        out: List[Dict[str, Any]] = []
        for row in left:
            lk = _composite_key(row, left_fields) if len(left_fields) > 1 else _norm_key(
                _field_value(row, left_key)
            )
            in_right = lk in right_keys if lk else False
            if how == "anti" and not in_right:
                out.append(dict(row))
            elif how != "anti" and in_right:
                out.append(dict(row))
        return _apply_post_filters(out, spec, variables, resolve_fn)

    right: List[Dict[str, Any]] = [dict(r) for r in right_rows if isinstance(r, dict)]
    index: Dict[str, List[Dict[str, Any]]] = {}
    for row in right:
        rk = _composite_key(row, right_fields) if len(right_fields) > 1 else _norm_key(
            _field_value(row, right_fields[0])
        )
        if rk:
            index.setdefault(rk, []).append(row)

    merged: List[Dict[str, Any]] = []
    for lrow in left:
        lk = _composite_key(lrow, left_fields) if len(left_fields) > 1 else _norm_key(
            _field_value(lrow, left_fields[0])
        )
        matches = index.get(lk, []) if lk else []
        if how == "anti":
            if not matches:
                merged.append(dict(lrow))
        elif matches:
            for rrow in matches:
                merged.append(_merge_row_pair(lrow, rrow, right_prefix=right_prefix))
        elif how == "left":
            merged.append(_merge_row_pair(lrow, None, right_prefix=right_prefix))

    return _apply_post_filters(merged, spec, variables, resolve_fn)


def _apply_post_filters(
    rows: List[Dict[str, Any]],
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    resolve_fn: ResolveFn,
) -> List[Dict[str, Any]]:
    if not spec.get("filters") and not spec.get("filter"):
        return rows
    filter_spec = {
        k: spec[k]
        for k in ("filters", "filter", "logic")
        if k in spec
    }
    return filter_rows(rows, filter_spec, variables, resolve=resolve_fn)


def run_multi_join_rows_transform(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    *,
    resolve: ResolveFn,
    format_tsv: Callable[[List[Dict[str, Any]]], str],
) -> Dict[str, Any]:
    """Chain multiple join_rows steps: left pool → join₁ → join₂ → … → output."""
    import time

    started = time.perf_counter()
    left_var = spec["left_variable"]
    current = variables.get(left_var) or []
    if not isinstance(current, list):
        raise ValueError(f"multi_join_rows left {left_var!r} must be a list")

    joins = spec.get("joins") or []
    if not joins:
        raise ValueError("multi_join_rows requires non-empty joins list")

    chain_summaries: List[str] = []
    for idx, join_spec in enumerate(joins):
        right_var = join_spec.get("right_variable")
        if not right_var:
            raise ValueError(f"multi_join_rows joins[{idx}] missing right_variable")
        right_rows = variables.get(right_var) or []
        if not isinstance(right_rows, list):
            raise ValueError(f"multi_join_rows right {right_var!r} must be a list")

        how = str(join_spec.get("how", "inner")).lower()
        current = join_rows(current, right_rows, join_spec, variables, resolve=resolve)
        chain_summaries.append(f"x{right_var}({how})->{len(current)}")

    out_var = spec["output_variable"]
    variables[out_var] = current
    if spec.get("also_store_rows"):
        variables[spec["also_store_rows"]] = current

    summary = (
        f"multi_join_rows {left_var} -> {out_var}: "
        f"{len(current)} rows ({left_var}{''.join(chain_summaries)})"
    )

    return {
        "step": step_index,
        "step_type": "transform",
        "name": spec.get("name", "multi_join_rows"),
        "transform": "multi_join_rows",
        "status": "pass" if current or not variables.get(left_var) else "empty",
        "rows": current,
        "row_count": len(current),
        "tsv": format_tsv(current[:100]) if current else "",
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "input": spec,
        "error": None,
        "join_chain": chain_summaries,
    }


def run_join_rows_transform(
    step_index: int,
    spec: Dict[str, Any],
    variables: Dict[str, Any],
    *,
    resolve: ResolveFn,
    format_tsv: Callable[[List[Dict[str, Any]]], str],
) -> Dict[str, Any]:
    """Workflow transform: join_rows on two variable-held pools."""
    import time

    started = time.perf_counter()
    left_var = spec["left_variable"]
    right_var = spec["right_variable"]
    left_rows = variables.get(left_var) or []
    right_rows = variables.get(right_var) or []
    if not isinstance(left_rows, list):
        raise ValueError(f"join_rows left {left_var!r} must be a list")
    if not isinstance(right_rows, list):
        raise ValueError(f"join_rows right {right_var!r} must be a list")

    joined = join_rows(left_rows, right_rows, spec, variables, resolve=resolve)
    out_var = spec["output_variable"]
    variables[out_var] = joined
    if spec.get("also_store_rows"):
        variables[spec["also_store_rows"]] = joined

    how = spec.get("how", "inner")
    summary = (
        f"join_rows {left_var} x {right_var} ({how}) -> {out_var}: "
        f"{len(joined)} rows from {len(left_rows)} x {len(right_rows)}"
    )

    return {
        "step": step_index,
        "step_type": "transform",
        "name": spec.get("name", "join_rows"),
        "transform": "join_rows",
        "status": "pass" if joined or not left_rows else "empty",
        "rows": joined,
        "row_count": len(joined),
        "tsv": format_tsv(joined[:100]) if joined else "",
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "input": spec,
        "error": None,
    }
