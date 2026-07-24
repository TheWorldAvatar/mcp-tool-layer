"""Table, chart, and map visualizations for workflow results."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from mini_marie.zaha.twa_city.gis_visualization import CITY_VIEW, rows_to_geojson, _leaflet_html

MAX_TABLE_ROWS = 500
MAX_CHART_ROWS = 2000


def _has_wkt(rows: List[Dict[str, Any]]) -> bool:
    return any(r.get("wkt") for r in rows[:50])


def _numeric_columns(df: pd.DataFrame) -> List[str]:
    out = []
    for col in df.columns:
        if col.lower() in ("wkt", "mofid", "building", "doi", "method", "linker", "node"):
            continue
        try:
            pd.to_numeric(df[col].dropna().head(20), errors="raise")
            out.append(col)
        except (TypeError, ValueError):
            pass
    return out


def pick_primary_rows(
    row_sets: List[Tuple[str, List[Dict[str, Any]]]],
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    if not row_sets:
        return None, []
    # Prefer locations, then largest set
    for label, rows in row_sets:
        if "location" in label.lower() and _has_wkt(rows):
            return label, rows
    best = max(row_sets, key=lambda x: len(x[1]))
    return best[0], best[1]


def to_dataframe(rows: List[Dict[str, Any]], limit: int = MAX_TABLE_ROWS) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if len(df) > limit:
        return df.head(limit)
    return df


def suggest_viz_modes(rows: List[Dict[str, Any]]) -> List[str]:
    modes = ["table"]
    if not rows:
        return modes
    if _has_wkt(rows):
        modes.append("map")
    df = pd.DataFrame(rows)
    if len(df) > 1:
        num_cols = _numeric_columns(df)
        label_cols = [c for c in df.columns if c not in num_cols and c != "wkt"]
        if num_cols and (len(df) <= MAX_CHART_ROWS or "count" in df.columns):
            modes.append("chart")
        if "count" in df.columns and label_cols:
            modes.append("chart")
    elif len(df) == 1:
        modes.append("chart")
    return modes


def render_table(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    return to_dataframe(rows)


def _chart_bar(df: pd.DataFrame) -> Optional[Any]:
    import plotly.express as px

    if df.empty:
        return None
    if len(df) == 1:
        row = df.iloc[0]
        num_cols = _numeric_columns(df)
        if not num_cols:
            return None
        melt = pd.DataFrame(
            {"metric": num_cols, "value": [float(row[c]) for c in num_cols]}
        )
        return px.bar(melt, x="metric", y="value", title="Scalar metrics")

    if "count" in df.columns:
        label = None
        for c in ("sourcedb", "solvent", "source", "topology", "metal_lc", "name"):
            if c in df.columns:
                label = c
                break
        if label:
            plot_df = df.copy()
            plot_df["count"] = pd.to_numeric(plot_df["count"], errors="coerce")
            plot_df = plot_df.dropna(subset=["count"]).sort_values("count", ascending=False).head(30)
            return px.bar(plot_df, x=label, y="count", title=f"Count by {label}")

    if "height" in df.columns and "building" in df.columns:
        plot_df = df.copy()
        plot_df["height"] = pd.to_numeric(plot_df["height"], errors="coerce")
        plot_df = plot_df.dropna(subset=["height"]).sort_values("height", ascending=False).head(25)
        plot_df["label"] = plot_df.get("label", plot_df["building"]).astype(str).str.slice(0, 24)
        return px.bar(plot_df, x="label", y="height", title="Building height (top 25)")

    num_cols = _numeric_columns(df)
    if num_cols:
        col = num_cols[0]
        plot_df = df.head(30).copy()
        plot_df[col] = pd.to_numeric(plot_df[col], errors="coerce")
        x_col = df.columns[0] if df.columns[0] != col else df.columns[min(1, len(df.columns) - 1)]
        return px.bar(plot_df, x=str(x_col), y=col, title=f"{col} distribution")

    return None


def render_chart(rows: List[Dict[str, Any]]) -> Optional[Any]:
    df = to_dataframe(rows, limit=MAX_CHART_ROWS)
    return _chart_bar(df)


def render_map_html(rows: List[Dict[str, Any]], city: str = "bremen") -> Optional[str]:
    wkt_rows = [r for r in rows if r.get("wkt")]
    if not wkt_rows:
        return None
    city_key = city.strip().lower()
    if city_key == "kl":
        city_key = "kaiserslautern"
    view = CITY_VIEW.get(city_key, CITY_VIEW["bremen"])
    clat, clon = view["center"]
    geojson = rows_to_geojson(wkt_rows[:50])
    if not geojson.get("features"):
        return None
    title = f"Buildings — {city_key}"
    return _leaflet_html(title, clat, clon, geojson)


def metrics_cards(digest: Dict[str, Any]) -> Dict[str, Any]:
    return dict(digest.get("authoritative") or {})
