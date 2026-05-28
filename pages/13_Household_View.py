"""Household View — personal-mode only.

Aggregates all household accounts against the SAA framework with
look-through decomposition of target-date and allocation funds.
"""
import streamlit as st

st.set_page_config(page_title="Household View", layout="wide")

from pathlib import Path
import pandas as pd
import plotly.graph_objects as go

from src.config import IS_DEMO
from src.ui_helpers import render_page_header, render_sidebar_footer

render_page_header()

# ── Demo-mode gate ─────────────────────────────────────────────────────────────
# Streamlit always lists files in pages/ in the sidebar; true sidebar suppression
# requires additional nav machinery not yet wired up. The stub + st.stop() ensures
# no household data is loaded or rendered in demo mode.
if IS_DEMO:
    st.title("Household View")
    st.info("Household aggregation is available in personal mode only.")
    st.stop()

# ── Personal-mode imports ──────────────────────────────────────────────────────
from src.db import get_connection
from src.ingestion.fidelity import parse_fidelity_csv
from src.household import (
    compute_household_allocation,
    household_summary,
    build_drift_table,
    build_account_breakdown,
    build_location_flags,
    build_strategic_comparison,
)

# ── Load data ──────────────────────────────────────────────────────────────────
_CSV = Path(__file__).resolve().parent.parent / "data" / "uploads" / "Portfolio_Positions_May-27-2026.csv"

try:
    positions_df = parse_fidelity_csv(_CSV)
except FileNotFoundError:
    st.warning(
        f"Holdings file not found at `data/uploads/{_CSV.name}`. "
        "Upload the Fidelity positions CSV to `data/uploads/` to use this page."
    )
    st.stop()
except Exception as exc:
    st.error(f"Error loading holdings file: {exc}")
    st.stop()

with get_connection() as conn:
    accounts_df     = pd.read_sql_query("SELECT * FROM accounts", conn)
    securities_df   = pd.read_sql_query("SELECT * FROM securities", conn)
    compositions_df = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    saa_targets_df  = pd.read_sql_query(
        "SELECT asset_class_id, name, target_weight "
        "FROM asset_classes WHERE parent_id IS NOT NULL AND target_weight > 0",
        conn,
    )

summary = household_summary(positions_df, accounts_df, as_of_date="2026-05-27")

_BLUE     = "#3D5A80"
_BLUE_LT  = "rgba(61, 90, 128, 0.30)"
_GRAY     = "#AEAEAE"
_GRAY_DK  = "#6B6B6B"
_GREEN    = "#2d6a4f"
_RED      = "#c0392b"

# ── Title ──────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Household View")
    st.caption("All accounts aggregated against the Strategic Asset Allocation")

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "**Scope.** This page aggregates every household account — self-directed "
            "and externally-managed — against the SAA framework. "
            "Only the self-directed account (Z52…, ~\\$1k) is directly actionable; "
            "externally-managed accounts are shown as observed context. "
            "Changes to externally-managed accounts require coordination with the manager.\n\n"
            "**Look Through vs As Held.** Look Through decomposes target-date and "
            "allocation funds (RFUTX, GAOSX, 31564E540) into their underlying sleeve "
            "exposures. As Held treats every fund by its own sleeve label (target\\_date, "
            "multi\\_asset).\n\n"
            "**Off-SAA Exposure.** Sleeves that do not map to any SAA target are reported "
            "honestly as off-SAA — they are held, real exposures, but are not counted as "
            "drift against a sleeve. This is by design: the external manager's holdings "
            "include broad-market and alternative sleeves that do not align to the "
            "factor-tilted SAA.\n\n"
            "**Drift** is meaningful only for SAA sleeves. Off-SAA entries show no drift "
            "and no target."
        )
    st.divider()

# ── KPI header ─────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total AUM",        f"${summary['total_aum']:,.0f}")
    k2.metric("Accounts",          summary["account_count"])
    k3.metric("As of",             summary["as_of_date"])
    k4.metric("Self-Directed",    f"${summary['self_aum']:,.0f}")
    k5.metric("Ext. Managed",     f"${summary['external_aum']:,.0f}")
    st.divider()

# ── Strategic Comparison ───────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Strategic Comparison — SAA vs Advisor Book")
    st.caption(
        "Side-by-side framing of the SAA framework against the positioning of the "
        "advisor-managed accounts (six of seven accounts, ~$201k of household). "
        "The advisor book runs a coherent strategy; it is not random — it just differs "
        "from the SAA philosophy. This comparison is editorial context; computed drift "
        "and exposure data follows below."
    )

    with st.expander("Advisor portfolio — strategic synthesis", expanded=True):
        st.markdown(
            "The advisor-managed portion of the household reads as a risk-aware, "
            "income-focused, diversified portfolio with an explicit downside-protection "
            "philosophy and a credit-tilted fixed income book. The structure is "
            "recognizable: a broad-market equity core (ITOT, VONE, IXUS, VXUS, IEFA, "
            "IJH, IJR) overlaid with active hedged-equity strategies (JHEQX, JEPI, "
            "JEPQ, HELO) that participate in upside while explicitly pricing left-tail "
            "risk; a multi-sector fixed income book (FIWDX, HLIPX, GBOSX, FAGIX, "
            "BFRIX, GHYIX) built around yield generation through credit and "
            "floating-rate exposure rather than duration ballast; a series of small "
            "thematic and sector satellite positions; token-sized real-asset "
            "diversifiers; one actively-managed multi-asset allocation fund (GAOSX); "
            "and a workplace-default target-date fund (RFUTX) that drives a large "
            "share of the equity exposure on auto-pilot."
        )
        st.markdown(
            "This is a thoughtful diversifier — the kind of book a CFA managing a "
            "young family member's money at no fee would build when optimizing for "
            "'won't blow up, won't underperform by much, generates income, sleeps well "
            "at night.' The SAA framework is a different philosophy: factor tilts "
            "(Quality, Value, Small Value) rather than market-cap broad exposure; "
            "Treasury and TIPS for duration ballast rather than credit risk for yield; "
            "commodities and REITs at a strategic 10% rather than token positions; no "
            "active hedging overlay; no thematic satellites. Neither approach is wrong; "
            "they are different philosophies for different reasons. Understanding the "
            "gap is more useful than trying to eliminate it."
        )

    st.caption("Strategic positioning by dimension.")
    st.dataframe(build_strategic_comparison(), use_container_width=True, hide_index=True)
    st.divider()

# ── Toggles ────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    tc1, tc2 = st.columns(2)
    with tc1:
        scope_label = st.radio(
            "View scope",
            ["Total Household", "Self-Directed Only", "Externally Managed Only"],
            horizontal=True,
        )
    with tc2:
        mode_label = st.radio(
            "Fund treatment",
            ["Look Through", "As Held"],
            horizontal=True,
        )
    st.divider()

_SCOPE = {"Total Household": "total", "Self-Directed Only": "self_only", "Externally Managed Only": "external_only"}
_MODE  = {"Look Through": "look_through", "As Held": "as_held"}
scope  = _SCOPE[scope_label]
mode   = _MODE[mode_label]

# ── Compute allocation ─────────────────────────────────────────────────────────
alloc_df = compute_household_allocation(
    positions_df, accounts_df, securities_df, compositions_df, saa_targets_df,
    mode=mode, scope=scope,
)

# ── SAA Drift Chart ────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Actual vs. Target — SAA Sleeves")

    saa_chart = (
        alloc_df[~alloc_df["is_off_saa"] & (alloc_df["target_weight"] > 0)]
        .sort_values("percent_weight", ascending=True)
    )

    fig_saa = go.Figure()
    fig_saa.add_trace(go.Bar(
        name="Actual",
        y=saa_chart["sleeve"],
        x=saa_chart["percent_weight"].round(1),
        orientation="h",
        marker_color=_BLUE,
        text=[f"{v:.1f}%" for v in saa_chart["percent_weight"]],
        textposition="outside",
        textfont=dict(size=11),
    ))
    fig_saa.add_trace(go.Bar(
        name="Target",
        y=saa_chart["sleeve"],
        x=(saa_chart["target_weight"] * 100).round(1),
        orientation="h",
        marker_color=_BLUE_LT,
        marker_line=dict(color=_BLUE, width=1),
        text=[f"{v:.1f}%" for v in saa_chart["target_weight"] * 100],
        textposition="outside",
        textfont=dict(size=11, color=_GRAY_DK),
    ))
    fig_saa.update_layout(
        barmode="group",
        height=max(320, len(saa_chart) * 44),
        paper_bgcolor="white",
        plot_bgcolor="white",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=0, r=80, t=40, b=4),
        xaxis=dict(
            ticksuffix="%",
            gridcolor="#EBEBEB",
            showgrid=True,
            zeroline=False,
        ),
        yaxis=dict(showgrid=False),
    )
    st.plotly_chart(fig_saa, width="stretch", config={"displayModeBar": False})

    st.subheader("Off-SAA Exposure")
    st.caption(
        "Held exposure that maps to no SAA target — not counted as drift. "
        "Reported as observed context."
    )

    off_chart = (
        alloc_df[alloc_df["is_off_saa"] & (alloc_df["dollar_value"] > 0)]
        .sort_values("percent_weight", ascending=True)
    )

    if not off_chart.empty:
        fig_off = go.Figure()
        fig_off.add_trace(go.Bar(
            name="Off-SAA Actual",
            y=off_chart["sleeve"],
            x=off_chart["percent_weight"].round(1),
            orientation="h",
            marker_color=_GRAY,
            text=[f"{v:.1f}%" for v in off_chart["percent_weight"]],
            textposition="outside",
            textfont=dict(size=11),
        ))
        fig_off.update_layout(
            height=max(200, len(off_chart) * 30),
            paper_bgcolor="white",
            plot_bgcolor="white",
            showlegend=False,
            margin=dict(l=0, r=80, t=4, b=4),
            xaxis=dict(
                ticksuffix="%",
                gridcolor="#EBEBEB",
                showgrid=True,
                zeroline=False,
            ),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig_off, width="stretch", config={"displayModeBar": False})
    st.divider()

# ── Drift Table ────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Drift Detail")
    saa_tbl, off_tbl = build_drift_table(alloc_df)

    def _drift_color(val: float) -> str:
        if val >= 1.0:
            return f"color: {_RED}"
        if val <= -1.0:
            return f"color: {_RED}"
        return f"color: {_GREEN}"

    st.markdown("**SAA Sleeves** — drift vs. target")
    st.dataframe(
        saa_tbl,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Actual (%)": st.column_config.NumberColumn(format="%.1f"),
            "Actual ($)": st.column_config.NumberColumn(format="$%d"),
            "Target (%)": st.column_config.NumberColumn(format="%.1f"),
            "Drift (pp)": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    st.markdown("**Off-SAA Exposure** — held, no target")
    st.caption(
        "Held exposure that maps to no SAA target — not counted as drift."
    )
    st.dataframe(
        off_tbl,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Actual (%)": st.column_config.NumberColumn(format="%.1f"),
            "Actual ($)": st.column_config.NumberColumn(format="$%d"),
        },
    )
    st.divider()

# ── By-Account Breakdown ───────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("By-Account Breakdown")
    st.caption(
        "Account names are pseudonymized. Six of seven accounts are externally managed."
    )

    breakdown_df = build_account_breakdown(positions_df, accounts_df, securities_df)

    st.dataframe(
        breakdown_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Total AUM ($)": st.column_config.NumberColumn(format="$%.0f"),
        },
    )
    st.divider()

# ── Tax-Location Flags ─────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Asset-Location Observations")
    st.caption(
        "Flags holdings where tax location appears suboptimal. "
        "This is observational only — no trade recommendations. "
        "Six of seven accounts are externally managed; changes require manager coordination."
    )

    flags_df = build_location_flags(positions_df, accounts_df, securities_df)

    if flags_df.empty:
        st.success("No suboptimal tax-location patterns detected.")
    else:
        st.dataframe(
            flags_df,
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Low-efficiency assets (FI, REITs, commodities) ideally held in tax-advantaged accounts. "
            "High-efficiency assets (equity ETFs) ideally held in taxable accounts to preserve "
            "tax-advantaged space for less-efficient assets."
        )
    st.divider()

render_sidebar_footer()
