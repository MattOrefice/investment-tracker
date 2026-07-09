"""Household View — personal-mode only.

Aggregates all household accounts against the SAA framework with
look-through decomposition of target-date and allocation funds.
"""
import logging
import streamlit as st

st.set_page_config(page_title="Household View", layout="wide")

from pathlib import Path
import pandas as pd
import plotly.graph_objects as go

from src.config import IS_DEMO
from src.ui_helpers import render_page_header

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
from src.household_data import load_latest_positions
from src.household import (
    compute_household_allocation,
    household_summary,
    build_drift_table,
    build_account_breakdown,
    build_strategic_comparison,
    sleeve_display_name,
    build_tax_drag_ranking,
    build_top_holdings,
    build_issuer_concentration,
    build_single_stock_summary,
    methodology_note_markdown,
    load_household_performance,
    build_performance_table,
    load_household_benchmarks,
    build_benchmark_table,
)

# ── Load data ──────────────────────────────────────────────────────────────────
# Newest Portfolio_Positions_<Mon>-DD-YYYY>.csv in data/uploads/, chosen by the
# date in the filename (see src/household_data). The dated filename is no longer
# hardcoded here.
_PERF_CSV  = Path(__file__).resolve().parent.parent / "data" / "seed" / "household_performance.csv"
_BENCH_CSV = Path(__file__).resolve().parent.parent / "data" / "seed" / "household_benchmarks.csv"

try:
    positions_df, _csv_path, _as_of_date = load_latest_positions()
except FileNotFoundError:
    st.warning(
        "No Fidelity positions file found in `data/uploads/`. "
        "Upload a `Portfolio_Positions_<Mon>-DD-YYYY>.csv` export to "
        "`data/uploads/` to use this page."
    )
    st.stop()
except Exception:
    logging.exception("Error loading holdings file")
    st.error("Error loading holdings file — check the logs for details.")
    st.stop()

with get_connection() as conn:
    # Explicit column list — account_number is intentionally never selected
    # (raw account numbers must not reach this page or its DataFrames).
    accounts_df     = pd.read_sql_query(
        "SELECT account_id, name, type, custodian, is_active, created_at, "
        "tax_treatment, pseudonym, display_name, managed_by FROM accounts",
        conn,
    )
    securities_df   = pd.read_sql_query("SELECT * FROM securities", conn)
    compositions_df = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    saa_targets_df  = pd.read_sql_query(
        "SELECT asset_class_id, name, target_weight "
        "FROM asset_classes WHERE parent_id IS NOT NULL AND target_weight > 0",
        conn,
    )

summary = household_summary(positions_df, accounts_df, as_of_date=_as_of_date.isoformat())

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
            "and no target.\n\n"
            "**Basis.** Household weights are a share of the *entire* household — cash and "
            "off-SAA holdings included — by design, because this view reports observed "
            "whole-household allocation, where drift is informational rather than actionable. "
            "The demo SAA page, by contrast, measures the invested portfolio *ex-cash* "
            "(operational SPAXX float excluded), where cash is operational liquidity rather "
            "than a strategic holding. Here, household cash is real and material, so it is "
            "shown as an off-SAA line at its actual dollar value rather than stripped out."
        )
    st.divider()

# ── KPI header ─────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total AUM",     f"${summary['total_aum']:,.0f}")
    k2.metric("Accounts",       summary["account_count"])
    k3.metric("Self-Directed", f"${summary['self_aum']:,.0f}")
    k4.metric("Ext. Managed",  f"${summary['external_aum']:,.0f}")
    st.caption(f"As of {summary['as_of_date']} · source: {_csv_path.name}")
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

    st.divider()

# ── Drift Table ────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Drift Detail")

    # Actionable vs observed framing (Item D)
    if scope == "total":
        pct_self = summary["self_aum"] / summary["total_aum"] * 100 if summary["total_aum"] else 0
        pct_ext  = summary["external_aum"] / summary["total_aum"] * 100 if summary["total_aum"] else 0
        st.info(
            f"Drift is computed against the full household aggregate. "
            f"Of the ${summary['total_aum']:,.0f} household, "
            f"${summary['self_aum']:,.0f} ({pct_self:.1f}%) is self-directed and actionable; "
            f"${summary['external_aum']:,.0f} ({pct_ext:.1f}%) is externally-managed and observed. "
            "Aligning to SAA targets primarily requires manager coordination, not unilateral trades."
        )
    elif scope == "self_only":
        st.info(
            "Showing self-directed account only. Drift here is directly actionable "
            "via the Capital Deployment page."
        )
    else:
        st.info(
            "Showing externally-managed accounts only. Drift is observed context, "
            "not actionable without manager coordination."
        )

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

# ── Account Performance ────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Account Performance")

    _return_type_label = st.radio(
        "Return type",
        ["Time-Weighted (TWR)", "Money-Weighted (MWR)"],
        horizontal=True,
    )
    _return_type = "TWR" if "TWR" in _return_type_label else "MWR"
    st.caption(
        "Time-weighted strips out the effect of deposit/withdrawal timing "
        "(the standard for comparing managers); money-weighted reflects the "
        "actual dollar experience including cash-flow timing."
    )

    _perf_df = load_household_performance(_PERF_CSV)
    # Filter accounts_df to the 7 active accounts (those with positions in the CSV)
    _active_accounts = accounts_df[
        accounts_df["pseudonym"].isin(positions_df["pseudonym"])
    ]
    _perf_tbl = build_performance_table(_perf_df, _active_accounts, return_type=_return_type)
    st.dataframe(_perf_tbl, use_container_width=True, hide_index=True)
    st.caption(
        "Returns as reported by Fidelity, not computed by this tool. "
        "Three accounts (Traditional IRA, HSA, secondary workplace plan) are not "
        "included in Fidelity's performance export and show as not reported."
    )

    if _return_type == "MWR":
        st.caption(
            "Fidelity does not report money-weighted returns for the workplace plan "
            "or the household total; those show as not reported under MWR."
        )

    st.markdown("**Household vs Benchmarks — 1Y (Jun 2025 – May 2026)**")
    _bench_df = load_household_benchmarks(_BENCH_CSV)
    _bench_tbl = build_benchmark_table(_bench_df)
    if not _bench_tbl.empty:
        st.dataframe(_bench_tbl, use_container_width=True, hide_index=True)
    st.markdown(
        "The household's +24.52% one-year return trailed US equity benchmarks "
        "(S&P 500 +29.78%, Dow US Total Market +29.84%) and the international index "
        "(MSCI ACWI ex USA +32.99%), while exceeding both bond benchmarks "
        "(US Aggregate +5.13%, Municipal +6.67%). The shortfall versus pure equity "
        "indices reflects the book's diversification — international, fixed income, "
        "hedged equity, and real assets — not underperformance against its own mandate. "
        "A diversified multi-asset household is expected to land between equity and bond "
        "benchmarks in a strong equity year, and it does."
    )
    st.divider()

# ── Concentration ──────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Concentration")

    st.markdown("**Top 5 Holdings by Value**")
    top5_df = build_top_holdings(positions_df, accounts_df, securities_df, n=5)
    st.dataframe(
        top5_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "$ Value":         st.column_config.NumberColumn(format="$%d"),
            "% of Household":  st.column_config.NumberColumn(format="%.1f"),
        },
    )

    st.markdown("**Top 3 Issuers**")
    issuers_df = build_issuer_concentration(positions_df, n=3)
    st.dataframe(
        issuers_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "$ Total":         st.column_config.NumberColumn(format="$%d"),
            "% of Household":  st.column_config.NumberColumn(format="%.1f"),
        },
    )

    st.markdown("**Single-Stock Exposure**")
    st.markdown(build_single_stock_summary(positions_df, securities_df))
    st.divider()

# ── Asset-Location Observations (tax drag ranking) ─────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Asset-Location Observations")
    st.caption(
        "Ranked by estimated annual tax drag. "
        "Six of seven accounts are externally managed; addressing this would require "
        "coordination with the account manager."
    )

    with st.expander("Assumptions (annual tax drag is a rough estimate)", expanded=False):
        st.markdown(
            "- **Marginal rate:** 32% federal + 9% DC = 41% combined\n"
            "- **Alt rate (low-efficiency in taxable):** 20% (LTCG + state) if moved "
            "to tax-advantaged\n"
            "- **Yields:** per-sleeve assumed annual distribution yields (not per-ticker)\n"
            "- **Formula:** `drag = value × assumed_yield × (marginal_rate − alt_rate)`\n"
            "- Holdings with zero estimated drag (e.g. equity ETFs in Roth) are excluded."
        )

    top_n = st.slider(
        "Showing top N suboptimal holdings", min_value=5, max_value=25, value=10, step=5
    )
    drag_df = build_tax_drag_ranking(positions_df, accounts_df, securities_df, top_n=top_n)

    if drag_df.empty:
        st.success("No suboptimal tax-location patterns with estimated drag detected.")
    else:
        st.dataframe(
            drag_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Est. Annual Drag ($)": st.column_config.NumberColumn(format="$%.0f"),
            },
        )
        total_drag = drag_df["Est. Annual Drag ($)"].sum()
        st.caption(
            f"Estimated total annual tax drag from top {len(drag_df)} flagged holdings: "
            f"**${total_drag:,.0f}**. "
            "Six of seven accounts are externally managed; addressing this would require "
            "coordination with the account manager."
        )
    st.divider()

# ── Methodology Note ───────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    with st.expander("Methodology note — household asset location", expanded=False):
        st.markdown(methodology_note_markdown())
