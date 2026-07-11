"""Liquidity — 'If you need cash' — personal-mode only.

Ranks every household holding by how cheaply it converts to spendable cash, cheapest
first — the near-inverse of the Asset Location priority. Taxable brokerage is liquid
(taxed only on the gain); tax-advantaged accounts are locked before 59½ (10% penalty
+ ordinary income). Answers 'what do I sell to raise cash for a house/car in 5–10
years'. Does not touch holdings.py, rebalance.py, or page 11.
"""
import logging
import streamlit as st

st.set_page_config(page_title="Liquidity", layout="wide")

import pandas as pd

from src.config import IS_DEMO
from src.ui_helpers import render_page_header

render_page_header()

if IS_DEMO:
    st.title("Liquidity")
    st.info("The liquidity hierarchy is available in personal mode only.")
    st.stop()

from src.db import get_connection
from src.household_data import load_latest_positions
from src.location_config import TAX_PROFILE
from src.liquidity import build_liquidity_ladder, TIER_LABEL

# ── Load data ──────────────────────────────────────────────────────────────────
try:
    positions_df, _csv_path, _as_of_date = load_latest_positions()
except FileNotFoundError:
    st.warning(
        "No Fidelity positions file found in `data/uploads/`. Upload a "
        "`Portfolio_Positions_<Mon>-DD-YYYY>.csv` export to use this page."
    )
    st.stop()
except Exception:
    logging.exception("Error loading holdings file")
    st.error("Error loading holdings file — check the logs for details.")
    st.stop()

with get_connection() as conn:
    accounts_df = pd.read_sql_query(
        "SELECT account_id, name, type, custodian, is_active, created_at, "
        "tax_treatment, pseudonym, display_name, managed_by FROM accounts",
        conn,
    )

ladder = build_liquidity_ladder(positions_df, accounts_df, TAX_PROFILE)

# ── Title ──────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 10, 1])
with col:
    st.title("Liquidity — if you need cash")
    st.caption(
        "Every holding ranked by how cheaply it converts to spendable cash — cheapest "
        "first. This is nearly the inverse of Asset Location: what belongs in a shelter "
        "is exactly what you can't spend without a penalty."
    )
    st.caption(f"As of {_as_of_date.isoformat()} · source: {_csv_path.name}")

# ── KPIs ────────────────────────────────────────────────────────────────────────
_liquid = float(ladder[ladder["tier"] < 3]["net_cash"].sum())
_tier1 = float(ladder[ladder["tier"] == 1]["net_cash"].sum())
_locked = float(ladder[ladder["tier"] == 3]["value"].sum())
with col:
    k1, k2, k3 = st.columns(3)
    k1.metric("Spendable from taxable (net)", f"${_liquid:,.0f}")
    k2.metric("Tier-1 free-to-sell (net)", f"${_tier1:,.0f}")
    k3.metric("Locked in retirement accounts", f"${_locked:,.0f}")
    st.caption(
        "Spendable-from-taxable is net of the 15% capital-gains tax on gains. "
        "Locked-in-retirement is the gross balance you'd raid only as a last resort — "
        "a withdrawal before 59½ costs a 10% penalty plus ordinary income tax."
    )
    st.divider()

# ── The ladder ───────────────────────────────────────────────────────────────────
with col:
    st.subheader("If you need cash, sell from the top")
    st.caption(
        "Rows are cheapest-to-convert first. **Cumulative net cash** is the running "
        "total of spendable dollars raised as you sell down the list — to raise \\$X, "
        "sell from the top until it reaches \\$X."
    )

    show = ladder.copy()
    show["tier"] = show["tier"].map(TIER_LABEL)
    show["cost_pct"] = (show["cost_to_cash"] / show["value"].where(show["value"] != 0)).fillna(0.0)
    show = show[[
        "symbol", "account", "tier", "value", "embedded_gain",
        "cost_to_cash", "cost_pct", "net_cash", "cumulative_net_cash", "note",
    ]].rename(columns={
        "symbol": "Symbol", "account": "Account", "tier": "Tier", "value": "Value ($)",
        "embedded_gain": "Embedded Gain ($)", "cost_to_cash": "Cost to cash ($)",
        "cost_pct": "Cost %", "net_cash": "Net cash ($)",
        "cumulative_net_cash": "Cumulative net cash ($)", "note": "Why",
    })
    st.dataframe(
        show, use_container_width=True, hide_index=True,
        column_config={
            "Value ($)":                st.column_config.NumberColumn(format="$%.0f"),
            "Embedded Gain ($)":        st.column_config.NumberColumn(format="$%.0f"),
            "Cost to cash ($)":         st.column_config.NumberColumn(format="$%.0f"),
            "Cost %":                   st.column_config.NumberColumn(format="%.0f%%"),
            "Net cash ($)":             st.column_config.NumberColumn(format="$%.0f"),
            "Cumulative net cash ($)":  st.column_config.NumberColumn(format="$%.0f"),
        },
    )
    st.divider()

# ── How to read ──────────────────────────────────────────────────────────────────
with col:
    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "**Liquid vs locked is the whole story.** Money in a taxable brokerage "
            "account is fully liquid — sell any lot and the only cost is tax on its "
            "embedded gain (15% federal + 3.07% PA), and a lot at a loss costs nothing "
            "and even harvests a deduction. Money in a Roth, either IRA, or the 401(k) "
            "is **locked**: a withdrawal before age 59½ triggers a **10% early-withdrawal "
            "penalty plus ordinary income tax** on the amount taken.\n\n"
            "**The three tiers.**\n"
            "- **Tier 1 — free / cheap.** Taxable holdings at a loss or a small gain, "
            "and taxable cash. Sell first: little or no tax.\n"
            "- **Tier 2 — moderate.** Taxable holdings with a meaningful embedded gain. "
            "Selling realizes 15% long-term capital-gains tax (plus PA) on the gain.\n"
            "- **Tier 3 — locked.** Roth first (your *contributions* come out penalty- "
            "and tax-free, but you forfeit the tax-free growth), then Traditional "
            "IRA/401(k) (10% penalty + ordinary income on the whole withdrawal) — the "
            "genuine last resort.\n\n"
            "**Why this matters for the IRA.** This is the mirror image of the Asset "
            "Location page, and the reason **not to over-fund the IRA if a house or car "
            "purchase is likely in the next 5–10 years.** Every dollar you shelter for "
            "the tax break is a dollar you can't spend before 59½ without giving ~35% of "
            "it back. Keep the near-term spending goal funded in taxable."
        )
