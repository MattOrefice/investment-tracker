"""SAA page — Strategic Asset Allocation."""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.db import get_connection

st.set_page_config(page_title="SAA", layout="wide")

PARENT_COLORS = {
    "Equity":      "#3D5A80",
    "Income":      "#4A7C59",
    "Real Assets": "#8B7355",
    "Cash":        "#AEAEAE",
}


def _safe_md(text):
    """Escape $ before passing to st.markdown so Streamlit doesn't treat them as LaTeX."""
    if text:
        st.markdown(text.replace("$", r"\$"))


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
                ORDER BY p.target_weight DESC, COALESCE(ac.sort_order, 999) ASC
            """).fetchall()
        ]
    return parents, sub_classes


parents, sub_classes = load_saa_data()

n_sleeves    = len(sub_classes)
n_parents    = len(parents)
total_alloc  = round(sum(sc["target_weight"] for sc in sub_classes) * 100, 1)

# ── Header ─────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Strategic Asset Allocation")
    st.caption("Target weights, tolerance bands, and rationale")
    st.caption(f"{n_sleeves} sleeves  ·  {total_alloc:.1f}% allocated  ·  {n_parents} parent categories")
    parts = [f"{round(p['target_weight'] * 100)}% {p['name']}" for p in parents]
    st.markdown("&nbsp;&nbsp;·&nbsp;&nbsp;".join(f"**{p}**" for p in parts))
    st.divider()

# ── Top-level allocation chart ──────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    fig = go.Figure()
    for p in parents:
        pct   = round(p["target_weight"] * 100, 1)
        color = PARENT_COLORS.get(p["name"], "#888888")
        # Abbreviated labels for segments too narrow to hold full text
        if pct >= 14:
            label = f"<b>{p['name']}</b>  {pct}%"
        elif pct >= 7:
            label = f"<b>{p['name']}</b>"
        else:
            label = f"{pct:.0f}%"
        fig.add_trace(go.Bar(
            name=p["name"],
            x=[pct],
            y=[""],
            orientation="h",
            marker_color=color,
            marker_line_width=0,
            text=label,
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
    # Legend row anchors small-segment labels that can't fit inside the bar
    legend_parts = []
    for p in parents:
        c   = PARENT_COLORS.get(p["name"], "#888")
        pct = round(p["target_weight"] * 100)
        legend_parts.append(
            f'<span style="color:{c}">■</span>'
            f'&nbsp;<span style="color:#555; font-size:0.85rem">{p["name"]} {pct}%</span>'
        )
    st.markdown("&emsp;".join(legend_parts), unsafe_allow_html=True)
    st.divider()

# ── Sub-class table ────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Sleeve Allocation")
    rows = [
        {
            "Sleeve":      sc["name"],
            "Category":    sc["parent_name"],
            "Target (%)":  round(sc["target_weight"] * 100, 1),
            "Band (±%)":   round(sc["tolerance_band"] * 100, 1),
            "Benchmark":   sc["benchmark_ticker"] or "—",
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
    if abs(total - 100) < 0.1:
        st.markdown(
            f'<span style="color:#2d6a4f; font-size:0.875rem">✓ Allocated: <b>{total:.1f}%</b></span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<span style="color:#c0392b; font-size:0.875rem">'
            f'⚠ Allocated: <b>{total:.1f}%</b> — does not sum to 100%</span>',
            unsafe_allow_html=True,
        )
    st.divider()

# ── Rationale expanders ────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Rationale")
    for sc in sub_classes:
        pct = round(sc["target_weight"] * 100, 1)
        bm  = sc["benchmark_ticker"] or "—"
        with st.expander(f"{sc['name']} — {pct}% (benchmark: {bm})"):
            _safe_md(sc["rationale"])
    st.divider()

