"""SAA page — Strategic Asset Allocation."""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.db import get_connection

st.set_page_config(page_title="SAA", layout="wide")

PARENT_COLORS = {
    "Growth":      "#3D5A80",
    "Income":      "#4A7C59",
    "Real Assets": "#8B7355",
    "Cash":        "#AEAEAE",
}


@st.cache_data
def load_saa_data():
    with get_connection() as conn:
        parents = [
            dict(r) for r in conn.execute("""
                SELECT name, target_weight, rationale, benchmark_ticker
                FROM asset_classes
                WHERE parent_id IS NULL
                ORDER BY target_weight DESC
            """).fetchall()
        ]
        sub_classes = [
            dict(r) for r in conn.execute("""
                SELECT ac.name, ac.target_weight, ac.tolerance_band,
                       ac.rationale, ac.benchmark_ticker, p.name AS parent_name
                FROM asset_classes ac
                JOIN asset_classes p ON ac.parent_id = p.asset_class_id
                ORDER BY p.target_weight DESC, ac.target_weight DESC
            """).fetchall()
        ]
    return parents, sub_classes


parents, sub_classes = load_saa_data()

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Strategic Asset Allocation")
    st.caption("Target weights, tolerance bands, and rationale")
    parts = [f"{round(p['target_weight'] * 100)}% {p['name']}" for p in parents]
    st.markdown("&nbsp;&nbsp;·&nbsp;&nbsp;".join(f"**{p}**" for p in parts))
    st.divider()

# Top-level allocation chart
_, col, _ = st.columns([1, 8, 1])
with col:
    fig = go.Figure()
    for p in parents:
        pct = round(p["target_weight"] * 100, 1)
        color = PARENT_COLORS.get(p["name"], "#888888")
        fig.add_trace(go.Bar(
            name=p["name"],
            x=[pct],
            y=[""],
            orientation="h",
            marker_color=color,
            marker_line_width=0,
            text=f"<b>{p['name']}</b>  {pct}%",
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(color="white", size=13, family="Arial"),
        ))
    fig.update_layout(
        barmode="stack",
        height=90,
        paper_bgcolor="white",
        plot_bgcolor="white",
        showlegend=False,
        margin=dict(l=0, r=0, t=4, b=4),
        xaxis=dict(range=[0, 100], showticklabels=False, showgrid=False,
                   zeroline=False, fixedrange=True),
        yaxis=dict(showticklabels=False, showgrid=False,
                   zeroline=False, fixedrange=True),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.divider()

# Sub-class table
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Sleeve Allocation")
    rows = [
        {
            "Sleeve": sc["name"],
            "Category": sc["parent_name"],
            "Target (%)": round(sc["target_weight"] * 100, 1),
            "Band (±%)": round(sc["tolerance_band"] * 100, 1),
            "Benchmark": sc["benchmark_ticker"] or "—",
        }
        for sc in sub_classes
    ]
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Target (%)": st.column_config.NumberColumn(format="%.1f"),
            "Band (±%)":  st.column_config.NumberColumn(format="%.1f"),
        },
    )
    total = df["Target (%)"].sum()
    st.caption(
        f"Total: **{total:.1f}%** {'✓ sums to 100%' if abs(total - 100) < 0.1 else '⚠ does not sum to 100%'}"
    )
    st.divider()

# Rationale expanders
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Rationale")
    for sc in sub_classes:
        pct = round(sc["target_weight"] * 100, 1)
        bm = sc["benchmark_ticker"] or "—"
        with st.expander(f"{sc['name']} — {pct}% (benchmark: {bm})"):
            st.markdown(sc["rationale"])
    st.divider()

# Phase 4 placeholder
_, col, _ = st.columns([1, 8, 1])
with col:
    st.info("Drift analysis (actual vs. target) coming in Phase 4 once trades are logged.")
