"""
Competency Test GUI — MOF + TWA city workflows with table / chart / map views.

Run:
  pip install -r requirements-gui.txt
  streamlit run mini_marie/competency_gui/app.py
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from mini_marie.cache_paths import data_dir, mini_marie_cache_root
from mini_marie.competency_gui import runner, viz
from mini_marie.mop_mof.mof.competency_workflow_engine import load_manifest as load_mof_manifest

st.set_page_config(
    page_title="Competency Test GUI",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Sidebar ---
st.sidebar.title("Competency tester")
domain = st.sidebar.radio("Domain", ["MOF (OntoMOFs)", "TWA City (buildings)"], index=0)

mode = st.sidebar.selectbox("Run mode", ["online", "offline"], index=0)
online_limit = st.sidebar.number_input("Online LIMIT", min_value=1, max_value=100, value=10)
offline_cap = st.sidebar.number_input("Offline cap", min_value=100, max_value=2_000_000, value=500_000, step=1000)
force_refresh = st.sidebar.checkbox("Force refresh (cold SPARQL)", value=False)

st.sidebar.markdown("---")
st.sidebar.caption(f"Data: `{data_dir()}`")
st.sidebar.caption(f"Cache: `{mini_marie_cache_root()}`")

# --- Question catalog ---
mof_manifest = load_mof_manifest()
mof_items = {w["id"]: w for w in mof_manifest.get("workflows", [])}
city_items = {w["name"]: w for w in runner.list_city_workflows()}

if domain.startswith("MOF"):
    st.title("MOF competency questions")
    st.caption(mof_manifest.get("description", ""))
    options = sorted(mof_items.keys())
    default_ix = 0
    selected_id = st.selectbox(
        "Question",
        options,
        index=default_ix,
        format_func=lambda x: f"{x} — {mof_items[x].get('question', '')[:70]}",
    )
    wf_meta = mof_items[selected_id]
    st.info(wf_meta.get("question", ""))
    city_for_map = ""
else:
    st.title("TWA city workflows")
    names = sorted(city_items.keys())
    selected_name = st.selectbox(
        "Workflow",
        names,
        format_func=lambda n: city_items[n].get("label", n),
    )
    wf_meta = city_items[selected_name]
    st.info(wf_meta.get("description", ""))
    city_for_map = wf_meta.get("city", "bremen")
    selected_id = wf_meta.get("id", selected_name)

run_clicked = st.button("Run query", type="primary", use_container_width=True)

if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "last_domain" not in st.session_state:
    st.session_state.last_domain = None

if run_clicked:
    with st.spinner(f"Running {mode} (network SPARQL)…"):
        try:
            if domain.startswith("MOF"):
                result = runner.run_mof(
                    selected_id,
                    mode=mode,
                    online_limit=int(online_limit),
                    offline_cap=int(offline_cap),
                    force_refresh=force_refresh,
                )
            else:
                result = runner.run_city(
                    selected_name,
                    mode=mode,
                    online_limit=int(online_limit),
                    offline_cap=int(offline_cap),
                    force_refresh=force_refresh,
                )
            st.session_state.last_result = result
            st.session_state.last_domain = domain
            st.session_state.last_city = city_for_map
        except Exception as exc:
            st.error(f"{type(exc).__name__}: {exc}")
            st.session_state.last_result = None

result: Optional[Dict[str, Any]] = st.session_state.last_result

if result is None:
    st.markdown("---")
    st.markdown("Select a question and click **Run query** to fetch results (cached on repeat).")
    st.stop()

# --- Summary ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Status", result.get("status", "?"))
col2.metric("Workflow ms", result.get("elapsed_ms", "?"))
col3.metric("Wall ms", result.get("wall_ms", "?"))
col4.metric("Mode", result.get("mode", "?"))

st.success(result.get("answer", ""))

digest = result.get("answer_digest") or {}
auth = digest.get("authoritative") or {}
if auth:
    with st.expander("Authoritative metrics", expanded=True):
        st.json(auth)

cache_stats = result.get("cache_stats") or {}
if cache_stats:
    with st.expander("Cache stats"):
        st.json(cache_stats)

with st.expander("Step trace"):
    for step in digest.get("steps", []):
        fc = " (cache)" if step.get("from_cache") else ""
        st.write(
            f"**{step.get('step')}.** `{step.get('name')}` — "
            f"{step.get('row_count')} rows, {step.get('elapsed_ms')} ms{fc}"
        )

# --- Visualizations ---
row_sets = runner.collect_row_sets(result)
label, primary_rows = viz.pick_primary_rows(row_sets)

if not primary_rows:
    st.warning("No tabular rows in this result.")
    st.stop()

st.markdown("---")
st.subheader("Visualization")

if len(row_sets) > 1:
    set_labels = [s[0] for s in row_sets]
    pick_label = st.selectbox("Row set", set_labels, index=set_labels.index(label) if label in set_labels else 0)
    primary_rows = next(rows for lbl, rows in row_sets if lbl == pick_label)
else:
    pick_label = label or "results"

modes = viz.suggest_viz_modes(primary_rows)
tab_table, tab_chart, tab_map = st.tabs(["Table", "Chart", "Map"])

with tab_table:
    df = viz.render_table(primary_rows)
    st.caption(f"Showing up to {len(df)} of {len(primary_rows)} rows — `{pick_label}`")
    st.dataframe(df, use_container_width=True, height=400)
    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"{selected_id}_{mode}.csv",
        mime="text/csv",
    )

with tab_chart:
    if "chart" not in modes:
        st.info("No chart view for this result shape (need numeric / count columns).")
    else:
        fig = viz.render_chart(primary_rows)
        if fig is None:
            st.info("Could not build a chart for these columns.")
        else:
            st.plotly_chart(fig, use_container_width=True)
        if len(primary_rows) == 1:
            st.json(primary_rows[0])

with tab_map:
    city = st.session_state.get("last_city", "bremen")
    if "map" not in modes:
        st.info("Map view requires rows with `wkt` footprints (city location workflows).")
    else:
        html = viz.render_map_html(primary_rows, city=city)
        if html is None:
            st.warning("WKT present but failed to parse geometries.")
        else:
            st.caption(f"Up to 50 building footprints — {city}")
            st.components.v1.html(html, height=520, scrolling=True)
