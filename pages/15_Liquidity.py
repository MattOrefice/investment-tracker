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

# ── Tier-capital cards — pure aggregation of the ladder's own columns ────────────
# gross = Σ Value for the tier's rows; net = Σ Net cash for the tier's rows. No new
# tax computation — these reuse the per-row figures the table below already shows,
# so any tier-total is guaranteed to equal the tier section beneath it.
_by = ladder.groupby("tier").agg(gross=("value", "sum"), net=("net_cash", "sum"))


def _tier(t, c):
    return float(_by.loc[t, c]) if t in _by.index else 0.0


_t1g, _t1n = _tier(1, "gross"), _tier(1, "net")
_t2g, _t2n = _tier(2, "gross"), _tier(2, "net")
_t3g, _t3n = _tier(3, "gross"), _tier(3, "net")
_total_gross = float(ladder["value"].sum())
_total_net = float(ladder["net_cash"].sum())


def _d(x):
    """Dollars, escaped for markdown so a leading $ isn't read as LaTeX."""
    return "\\$" + f"{x:,.0f}"


with col:
    _c1, _c2, _c3 = st.columns(3)
    _c1.metric("Tier 1 · taxable, minimal gains", f"${_t1g:,.0f}", f"net ${_t1n:,.0f} if sold", delta_color="off")
    _c2.metric("Tier 2 · taxable with gains", f"${_t2g:,.0f}", f"net ${_t2n:,.0f} if sold", delta_color="off")
    _c3.metric("Tier 3 · locked (retirement)", f"${_t3g:,.0f}", f"net ${_t3n:,.0f} if sold", delta_color="off")
    _tc1, _tc2 = st.columns(2)
    _tc1.metric("Total available (gross)", f"${_total_gross:,.0f}")
    _tc2.metric("Total net if fully liquidated", f"${_total_net:,.0f}")
    st.caption(
        "Gross is market value; **net** is after the tax/penalty to convert to cash. Tier 1 + "
        "Tier 2 are the liquid-in-taxable total — they nest, and do not add to Tier 3. "
        "**Settlement:** ETFs, equities, and most mutual funds settle **T+1**; core / "
        "money-market cash is **same-day**."
    )
    st.info(
        "Roth and pre-tax costs assume withdrawal **before age 59½** and **no accessible "
        "Roth contribution basis**. Your actual cost is lower if you're over 59½ or "
        "withdraw Roth contributions first — this tool can't see your basis or age. "
        "Confirm with Fidelity."
    )
    st.divider()

# ── The ladder ───────────────────────────────────────────────────────────────────
with col:
    st.subheader("If you need cash, sell from the top")
    st.caption(
        "Rows are cheapest-to-convert first. **Cumulative net cash** is the running "
        "total of net dollars raised as you sell down the list. The same symbol in a "
        "different **Account** is a distinct lot, not a duplicate."
    )

    # Feature A — answer the actual question: how far down do I sell to raise $X?
    _need = st.number_input(
        "How much cash do you need?  (house, car, … — enter 0 to hide)",
        min_value=0, value=0, step=1000, format="%d",
    )
    _sell_n = 0
    if _need and _need > 0:
        _mask = ladder["cumulative_net_cash"] >= float(_need)
        if not _mask.any():
            _sell_n = len(ladder)
            st.warning(
                f"Even selling **everything** raises only {_d(float(ladder['cumulative_net_cash'].iloc[-1]))} "
                f"net — short of {_d(float(_need))}."
            )
        else:
            _idx = int(_mask.idxmax())
            _sell_n = _idx + 1
            _sold = ladder.iloc[:_sell_n]
            _tax = float(_sold["cost_to_cash"].sum())
            _gross = float(_sold["value"].sum())
            _blend = (_tax / _gross * 100) if _gross else 0.0
            st.success(
                f"To raise {_d(float(_need))}, sell the top **{_sell_n}** holdings (down to "
                f"**{ladder.iloc[_idx]['symbol']}** in **{ladder.iloc[_idx]['account']}**) — "
                f"realizing ~{_d(_tax)} in tax/penalty on {_d(_gross)} sold, a blended cost of "
                f"**{_blend:.1f}%**. The highlighted rows below are what you'd sell."
            )

    _disp = ladder.copy()
    _disp["tier"] = _disp["tier"].map(TIER_LABEL)
    _disp["cost_pct"] = (_disp["cost_to_cash"] / _disp["value"].where(_disp["value"] != 0) * 100).fillna(0.0)
    _disp["embedded_gain"] = pd.to_numeric(_disp["embedded_gain"], errors="coerce")
    _cols = {
        "symbol": "Symbol", "account": "Account", "tier": "Tier", "value": "Value ($)",
        "embedded_gain": "Embedded Gain ($)", "cost_to_cash": "Cost to cash ($)",
        "cost_pct": "Cost %", "net_cash": "Net cash ($)",
        "cumulative_net_cash": "Cumulative net cash ($)", "note": "Why",
    }
    _disp = _disp[list(_cols)].rename(columns=_cols)
    _fmt = {c: "${:,.0f}" for c in ["Value ($)", "Embedded Gain ($)", "Cost to cash ($)",
                                    "Net cash ($)", "Cumulative net cash ($)"]}
    _fmt["Cost %"] = "{:.1f}%"          # Feature C — one decimal, no more 0% on small lots

    def _row_style(row):               # Feature A — shade the rows you'd sell to raise $X
        hl = "background-color: rgba(46,160,67,0.20)" if row.name < _sell_n else ""
        return [hl] * len(row)

    _sty = (
        _disp.style
        .format(_fmt, na_rep="—")
        .apply(_row_style, axis=1)
        .set_properties(subset=["Account"], **{"font-weight": "700"})   # Feature B — bold account
    )
    st.dataframe(_sty, use_container_width=True, hide_index=True)
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
            "- **Tier 3 — locked.** Roth, both IRAs, and the 401(k). This tool costs the "
            "**whole withdrawal** at a 10% penalty + ordinary income tax (~35%), *if taken "
            "before 59½* — deliberately conservative, because it can't see your age or your "
            "Roth contribution basis. Your real cost is lower if you're over 59½, or (for a "
            "Roth) if you withdraw contributions first, which come out penalty- and tax-free. "
            "Never treat the shown cost as exact — confirm with Fidelity.\n\n"
            "**Why this matters for the IRA.** This is the mirror image of the Asset "
            "Location page, and the reason **not to over-fund the IRA if a house or car "
            "purchase is likely in the next 5–10 years.** Every dollar you shelter for "
            "the tax break is a dollar you can't spend before 59½ without giving ~35% of "
            "it back. Keep the near-term spending goal funded in taxable."
        )
