"""
KGQA ReAct Agent GUI — competency catalog + natural-language Q&A.

Run:
  pip install -r requirements-docker.txt -r requirements-gui.txt -r requirements-kgqa.txt
  streamlit run mini_marie/kgqa/webapp/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from mini_marie.cache_paths import repo_root as REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mini_marie.cache_paths import data_dir, mini_marie_cache_root
from mini_marie.kgqa.orchestrator import run_offline_phase, run_online_phase
from mini_marie.kgqa.question_catalog import filter_catalog, list_domains
from mini_marie.kgqa.webapp.offline_view import render_offline_results


def _render_timing_row(result: dict) -> None:
    timing = result.get("timing") or {}
    cols = st.columns(5)
    cols[0].metric("Route ms", timing.get("route_ms", 0))
    cols[1].metric("Online ms", timing.get("online_ms", 0))
    cols[2].metric("Offline ms", timing.get("offline_ms", 0))
    cols[3].metric("Total ms", timing.get("total_ms", result.get("elapsed_ms", 0)))
    usage = (result.get("metadata") or {}).get("aggregated_usage") or {}
    cols[4].metric("LLM tokens", usage.get("total_tokens", 0))


def _render_online_block(result: dict) -> None:
    route = result.get("route") or {}
    meta = result.get("metadata") or {}
    usage = meta.get("aggregated_usage") or {}
    timing = result.get("timing") or {}

    cols = st.columns(4)
    cols[0].metric("Online ms", timing.get("online_ms", 0))
    cols[1].metric("Tokens", usage.get("total_tokens", 0))
    cols[2].metric("MCP servers", len(route.get("mcp_servers") or []))
    cols[3].metric(
        "Tools",
        len(meta.get("tool_activity", {}).get("executed_tool_name_set") or []),
    )

    st.caption(
        f"Route: {route.get('reason')} → `{', '.join(route.get('mcp_servers') or [])}` "
        f"(domain: {route.get('domain')})"
    )
    if result.get("recording_path"):
        st.info(f"Online recording: `{result['recording_path']}`")

    st.markdown(result.get("online_answer") or "_No answer text_")

    with st.expander("Online tool activity"):
        st.json(meta.get("tool_activity") or {})


st.set_page_config(
    page_title="KGQA ReAct Agent",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.title("KGQA settings")
domain_filter = st.sidebar.selectbox("Domain filter", list_domains(), index=0)
model_name = st.sidebar.text_input("Model", value="gpt-4o-mini")
recursion_limit = st.sidebar.number_input("Recursion limit", min_value=20, max_value=600, value=200)
auto_offline = st.sidebar.checkbox("Auto offline replay after online (no LLM)", value=True)
offline_cap = st.sidebar.number_input("Offline cap", min_value=1000, max_value=2_000_000, value=500_000, step=1000)

st.sidebar.markdown("---")
st.sidebar.caption(f"Data: `{data_dir()}`")
st.sidebar.caption(f"Cache: `{mini_marie_cache_root()}`")

st.title("KGQA ReAct Agent")
st.caption("Phase 1: online ReAct (LLM + MCP probe) → Phase 2: offline full cache replay.")

tab_competency, tab_examples, tab_ask = st.tabs(
    ["Competency questions", "Example questions", "Ask"]
)

if "selected_question" not in st.session_state:
    st.session_state.selected_question = ""
if "last_result" not in st.session_state:
    st.session_state.last_result = None

run_clicked = False

with tab_competency:
    search = st.text_input("Search competency questions", key="comp_search")
    items = filter_catalog(domain=domain_filter, kind="competency", search=search or None)
    if items:
        df = pd.DataFrame(
            [
                {
                    "id": e.id,
                    "domain": e.domain,
                    "question": e.question[:120],
                    "workflow": e.workflow_id or "",
                    "mcps": ", ".join(e.mcp_servers),
                }
                for e in items
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
        ids = [e.id for e in items]
        pick = st.selectbox(
            "Select question",
            ids,
            format_func=lambda i: next(e.question for e in items if e.id == i)[:80],
        )
        if st.button("Use this question", key="use_comp"):
            entry = next(e for e in items if e.id == pick)
            st.session_state.selected_question = entry.question
            st.success(f"Loaded: {entry.id}")
    else:
        st.info("No competency questions match the filter.")

with tab_examples:
    examples = filter_catalog(domain=domain_filter, kind="example")
    for ex in examples:
        if st.button(f"{ex.id}: {ex.question[:70]}…", key=f"ex_{ex.id}"):
            st.session_state.selected_question = ex.question

with tab_ask:
    question = st.text_area(
        "Question",
        value=st.session_state.selected_question,
        height=120,
        placeholder="Ask a natural-language question over MOF, city, chemistry, MOPs, or SG KGs…",
    )
    run_clicked = st.button("Run KGQA", type="primary", use_container_width=True)

    if run_clicked and question.strip():
        q = question.strip()
        try:
            st.markdown("### Phase 1 — Online (ReAct + MCP)")
            with st.spinner("Running online ReAct agent…"):
                partial = run_online_phase(
                    q,
                    model_name=model_name,
                    recursion_limit=int(recursion_limit),
                )
            _render_online_block(partial)

            if auto_offline and partial.get("recording_path"):
                st.markdown("### Phase 2 — Offline (full cache replay, no LLM)")
                with st.spinner("Replaying offline from recording…"):
                    result = run_offline_phase(
                        partial,
                        offline_cap=int(offline_cap),
                    )
                render_offline_results(
                    result["offline_recording_path"],
                    offline_summary=result.get("offline"),
                )
                _render_timing_row(result)
                st.session_state.last_result = result
            elif auto_offline:
                st.warning(
                    "Online phase did not produce a recording_path — offline replay skipped."
                )
                st.session_state.last_result = partial
            else:
                st.session_state.last_result = partial

        except Exception as exc:
            st.error(f"KGQA failed: {exc}")
            st.session_state.last_result = None

if st.session_state.last_result and not run_clicked:
    result = st.session_state.last_result
    st.markdown("---")
    st.markdown("### Last run")
    _render_online_block(result)
    if result.get("offline_recording_path"):
        st.markdown("### Offline full answer")
        render_offline_results(
            result["offline_recording_path"],
            offline_summary=result.get("offline"),
        )
        _render_timing_row(result)
