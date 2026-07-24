"""Render offline replay JSON for the KGQA GUI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


def load_offline_payload(path: str) -> Optional[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def collect_row_tables(payload: Dict[str, Any]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Named row sets from call_trace and answer list."""
    tables: List[Tuple[str, List[Dict[str, Any]]]] = []
    answer = payload.get("answer")
    if isinstance(answer, list) and answer and isinstance(answer[0], dict):
        tables.append(("answer", answer))

    for step in payload.get("call_trace") or []:
        rows = step.get("rows") or []
        if not rows or not isinstance(rows[0], dict):
            continue
        label = step.get("tool") or step.get("join") or step.get("name") or f"step_{step.get('step')}"
        tables.append((f"step: {label}", rows))

    variables = payload.get("variables") or {}
    for key, val in variables.items():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            tables.append((f"var: {key}", val))

    return tables


def render_offline_results(
    offline_path: str,
    *,
    offline_summary: Optional[Dict[str, Any]] = None,
) -> None:
    """Display offline full answer: metrics, tables, digest, download."""
    payload = load_offline_payload(offline_path)
    if payload is None:
        st.warning(f"Offline file not found: `{offline_path}`")
        return

    status = payload.get("status") or (offline_summary or {}).get("status")
    st.success(f"Offline replay **{status}** — `{Path(offline_path).name}`")

    cols = st.columns(4)
    cols[0].metric("Workflow", payload.get("workflow_id", "—"))
    cols[1].metric("Mode", payload.get("mode", "offline"))
    cols[2].metric(
        "Engine ms",
        payload.get("elapsed_ms") or (offline_summary or {}).get("engine_ms", "—"),
    )
    cols[3].metric(
        "Rows (summary)",
        (offline_summary or {}).get("row_count")
        or sum(s.get("row_count") or 0 for s in (payload.get("call_trace") or [])),
    )

    digest = payload.get("answer_digest") or {}
    authoritative = digest.get("authoritative") or {}
    if authoritative:
        st.markdown("**Authoritative metrics (from full cache replay)**")
        st.json(authoritative)

    summary_text = digest.get("summary_text") or payload.get("answer")
    if summary_text and not isinstance(summary_text, list):
        st.markdown("**Summary**")
        st.write(summary_text)

    tables = collect_row_tables(payload)
    if tables:
        st.markdown("**Full result tables**")
        for name, rows in tables:
            st.caption(name)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No tabular rows in offline file.")

    with st.expander("Step trace"):
        trace_rows = []
        for step in payload.get("call_trace") or []:
            trace_rows.append(
                {
                    "step": step.get("step"),
                    "type": step.get("step_type"),
                    "tool": step.get("tool") or step.get("join") or step.get("name"),
                    "status": step.get("status"),
                    "rows": step.get("row_count"),
                    "ms": step.get("elapsed_ms"),
                    "from_cache": (step.get("cache_meta") or step.get("meta") or {}).get("from_cache"),
                }
            )
        if trace_rows:
            st.dataframe(pd.DataFrame(trace_rows), use_container_width=True, hide_index=True)

    st.download_button(
        "Download offline JSON",
        data=json.dumps(payload, indent=2),
        file_name=Path(offline_path).name,
        mime="application/json",
        key=f"dl_{Path(offline_path).name}",
    )
