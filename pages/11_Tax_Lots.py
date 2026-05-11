"""Tax Lot Inventory — per-lot cost basis, holding period, and unrealized G/L."""
from datetime import date

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Tax Lot Inventory", layout="wide")

from src.asof import as_of_live_line, as_of_report_line
from src.tax_lots import (
    HARVEST_MATERIALITY_THRESHOLD,
    apply_sleeve_filter,
    compute_harvest_pool,
    get_lot_inventory,
    get_sleeve_rollup,
    lot_count_label,
    summary_metrics,
)
from src.ui_helpers import render_footer

TODAY = date.today().isoformat()


# ── Cached data loader ────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _load_lots(as_of: str) -> pd.DataFrame:
    return get_lot_inventory(as_of)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_dollar(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _fmt_signed_dollar(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _fmt_signed_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.1f}%"


# ── Page ──────────────────────────────────────────────────────────────────────

st.title("Tax Lot Inventory")
st.caption(as_of_live_line())
st.caption(as_of_report_line())

lots = _load_lots(TODAY)

if lots.empty:
    st.info(
        "No lot data available. Seed trades via `src/seed_paper_trades.py` "
        "or record a trade on the Trade Log page."
    )
    render_footer()
    st.stop()

# ── Summary metric cards ──────────────────────────────────────────────────────

metrics = summary_metrics(lots)
harvest_pool, harvest_n = compute_harvest_pool(lots)

c1, c2, c3 = st.columns(3)
c4, c5, c6 = st.columns(3)

with c1:
    st.metric("Total Cost Basis", _fmt_dollar(metrics["cost_basis_total"]))
with c2:
    st.metric("Total Market Value", _fmt_dollar(metrics["market_value_total"]))
with c3:
    gl = metrics["unrealized_gl_total"]
    gl_pct = metrics["unrealized_gl_pct"]
    st.metric(
        "Total Unrealized G/L",
        _fmt_signed_dollar(gl),
        delta=_fmt_signed_pct(gl_pct),
        delta_color="normal",
    )
with c4:
    st.metric("ST Unrealized Gain", _fmt_dollar(metrics["unrealized_st_gain"]))
with c5:
    st.metric("LT Unrealized Gain", _fmt_dollar(metrics["unrealized_lt_gain"]))
with c6:
    if harvest_n == 0:
        st.metric("Harvest Candidate Pool", "$0.00")
        st.caption("No material harvest candidates")
    else:
        st.metric("Harvest Candidate Pool", _fmt_dollar(abs(harvest_pool)))
        st.caption(
            f"n={harvest_n} material {lot_count_label(harvest_n).split()[1]} "
            f"(threshold: ${HARVEST_MATERIALITY_THRESHOLD:.0f}/lot)"
        )

st.divider()

# ── Filter controls ───────────────────────────────────────────────────────────

st.subheader("Filters")
fc1, fc2, fc3, fc4 = st.columns(4)

all_sleeves = sorted(lots["sleeve"].unique().tolist())
st_lot_count = int((lots["tax_status"] == "ST").sum())

with fc1:
    selected_sleeves = st.multiselect(
        "Sleeve",
        options=all_sleeves,
        default=[],
        placeholder="All sleeves",
        label_visibility="visible",
    )

with fc2:
    tax_status_filter = st.selectbox(
        "Tax Status",
        options=["All", "ST only", "LT only"],
        index=0,
    )

with fc3:
    gl_filter = st.selectbox(
        "G/L Sign",
        options=["All", "Gains only", "Losses only"],
        index=0,
    )

with fc4:
    if st_lot_count == 0:
        st.number_input(
            "Days to LT ≤ (ST lots)",
            min_value=0,
            max_value=366,
            value=366,
            step=5,
            disabled=True,
            help="No short-term lots in inventory — filter activates when lots within 1 year of purchase exist.",
        )
        days_to_lt_max = 366
    else:
        days_to_lt_max = st.number_input(
            "Days to LT ≤ (ST lots)",
            min_value=0,
            max_value=366,
            value=366,
            step=5,
            help="Show ST lots within this many days of long-term qualification. Set to 366 to show all.",
        )

# Apply filters
filtered = apply_sleeve_filter(lots, selected_sleeves)

if tax_status_filter == "ST only":
    filtered = filtered[filtered["tax_status"] == "ST"]
elif tax_status_filter == "LT only":
    filtered = filtered[filtered["tax_status"] == "LT"]

if gl_filter == "Gains only":
    filtered = filtered[filtered["unrealized_gl"] > 0]
elif gl_filter == "Losses only":
    filtered = filtered[filtered["unrealized_gl"] < 0]

if days_to_lt_max < 366:
    # Only apply this filter to ST lots; LT lots pass through regardless
    st_mask = filtered["tax_status"] == "ST"
    filtered = filtered[~st_mask | (filtered["days_to_lt"] <= days_to_lt_max)]

st.divider()

# ── Lot detail table ──────────────────────────────────────────────────────────

n_filtered = len(filtered)
st.subheader(f"Lot Detail — {lot_count_label(n_filtered)}")

if filtered.empty:
    st.info("No lots match the current filter combination.")
else:
    # Build display DataFrame sorted by ticker → purchase date
    display = filtered.sort_values(["ticker", "trade_date"]).copy()

    # Format Days to LT: blank for LT lots
    display["days_to_lt_display"] = display.apply(
        lambda r: "" if r["tax_status"] == "LT" else str(int(r["days_to_lt"])),
        axis=1,
    )

    # Lot source: title-case for display
    display["lot_source_display"] = display["lot_source"].str.title()

    display_cols = {
        "ticker":                "Ticker",
        "sleeve":                "Sleeve",
        "trade_date":            "Purchase Date",
        "days_held":             "Days Held",
        "shares":                "Shares",
        "cost_basis_per_share":  "Cost Basis/Share",
        "cost_basis_total":      "Cost Basis",
        "current_price":         "Current Price",
        "market_value":          "Market Value",
        "unrealized_gl":         "Unrealized G/L",
        "unrealized_gl_pct":     "Unrealized G/L %",
        "tax_status":            "Tax Status",
        "days_to_lt_display":    "Days to LT",
        "lot_source_display":    "Lot Source",
    }

    table = display[list(display_cols.keys())].rename(columns=display_cols)

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Purchase Date":    st.column_config.DateColumn("Purchase Date"),
            "Days Held":        st.column_config.NumberColumn("Days Held", format="%d"),
            "Shares":           st.column_config.NumberColumn("Shares", format="%.6f"),
            "Cost Basis/Share": st.column_config.NumberColumn(
                "Cost Basis/Share", format="$%.2f"
            ),
            "Cost Basis":       st.column_config.NumberColumn("Cost Basis", format="$%.2f"),
            "Current Price":    st.column_config.NumberColumn("Current Price", format="$%.2f"),
            "Market Value":     st.column_config.NumberColumn("Market Value", format="$%.2f"),
            "Unrealized G/L":   st.column_config.NumberColumn(
                "Unrealized G/L", format="$%.2f"
            ),
            "Unrealized G/L %": st.column_config.NumberColumn(
                "Unrealized G/L %", format="%.1f%%"
            ),
            "Days to LT":       st.column_config.TextColumn("Days to LT"),
            "Tax Status":       st.column_config.TextColumn("Tax Status"),
            "Lot Source":       st.column_config.TextColumn("Lot Source"),
        },
    )

st.divider()

# ── Sleeve summary roll-up ────────────────────────────────────────────────────

st.subheader("Sleeve Summary")

rollup = get_sleeve_rollup(filtered)

if rollup.empty:
    st.info("No sleeve data to aggregate.")
else:
    st.dataframe(
        rollup,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Sleeve":           st.column_config.TextColumn("Sleeve"),
            "Lot Count":        st.column_config.NumberColumn("Lots", format="%d"),
            "Cost Basis":       st.column_config.NumberColumn("Cost Basis", format="$%.2f"),
            "Market Value":     st.column_config.NumberColumn("Market Value", format="$%.2f"),
            "Unrealized G/L":   st.column_config.NumberColumn("Unrealized G/L", format="$%.2f"),
            "Unrealized G/L %": st.column_config.NumberColumn("Unrealized G/L %", format="%.1f%%"),
            "ST Gain":          st.column_config.NumberColumn("ST Gain", format="$%.2f"),
            "LT Gain":          st.column_config.NumberColumn("LT Gain", format="$%.2f"),
            "Unrealized Loss":  st.column_config.NumberColumn("Unrealized Loss", format="$%.2f"),
        },
    )

    # Reconciliation note
    total_mv_rollup = rollup["Market Value"].sum()
    total_mv_lots = filtered["market_value"].sum()
    if abs(total_mv_rollup - total_mv_lots) > 0.01:
        st.warning(
            f"Sleeve roll-up total (${total_mv_rollup:,.2f}) differs from "
            f"lot-level total (${total_mv_lots:,.2f}) by more than $0.01. "
            "Check for data integrity issues."
        )

st.caption(
    "Note: DRIP reinvestments are included in portfolio value calculations (TWR) "
    "but are not persisted as separate lot rows. The lot inventory reflects only "
    "explicit trade entries. Cost basis uses execution price (adj_close at purchase). "
    "Long-term qualification: held more than 365 calendar days (day 366+)."
)

render_footer()
