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
from src.household import exclude_non_household_positions
from src.location_config import TAX_PROFILE
from src.liquidity import build_liquidity_ladder, TIER_LABEL
from src.personal_profile import load_roth_profile

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
        "tax_treatment, pseudonym, display_name, managed_by, included_in_household "
        "FROM accounts",
        conn,
    )

# Single source of truth: accounts holding money that isn't a household asset
# (e.g. unvested/forfeitable employer contributions) are dropped here, once —
# such money can't be sold for cash at any price, so it must not appear in the
# ladder at all, not even as a locked/penalized row.
positions_df = exclude_non_household_positions(positions_df, accounts_df)

# Roth basis/age profile — synthetic in demo, gitignored private/ file in personal
# (absent → zero basis, whole Roth stays locked). Drives the Roth contribution-basis
# tranche (Tier 1) vs earnings tranche (Tier 3 until 59½). No real data is committed.
_profile = load_roth_profile(IS_DEMO, _as_of_date)
_roth_basis = float(_profile["roth_contribution_basis"])
_roth_qualified = bool(_profile["is_qualified_age"])

ladder = build_liquidity_ladder(
    positions_df, accounts_df, TAX_PROFILE,
    roth_contribution_basis=_roth_basis, roth_is_qualified_age=_roth_qualified,
)

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
    # "Accessible now" is the genuinely-spendable number on a page titled "if you need
    # cash": Tier 1 + Tier 2, reached without the 10% early-withdrawal penalty. It
    # REUSES the tier totals above (_tier), so it can never disagree with them. Tier 3
    # (locked retirement) is deliberately excluded — the portfolio total is separate.
    _acc_gross, _acc_net = _t1g + _t2g, _t1n + _t2n
    _tc1, _tc2, _tc3 = st.columns(3)
    _tc1.metric("Accessible now — no penalty (Tier 1 + Tier 2)", f"${_acc_gross:,.0f}",
                f"net ${_acc_net:,.0f} after tax", delta_color="off")
    _tc2.metric("Total portfolio (gross)", f"${_total_gross:,.0f}")
    _tc3.metric("Total net if fully liquidated", f"${_total_net:,.0f}")
    st.caption(
        "Gross is market value; **net** is after the tax/penalty to convert to cash. "
        "**Accessible now** is Tier 1 + Tier 2 — what you can reach *without* the 10% "
        "early-withdrawal penalty: taxable holdings plus your penalty-free Roth "
        "contribution basis. They nest and do **not** add to Tier 3 (locked retirement "
        "— the 401(k) and IRAs, and Roth earnings before 59½). **Settlement:** ETFs, "
        "equities, and most mutual funds settle **T+1**; core / money-market cash is "
        "**same-day**."
    )
    if _roth_basis > 0:
        _earn = (
            "Since you are 59½ or older, the Roth **earnings are also free** (Tier 1), "
            "assuming the account is at least 5 years old. "
            if _roth_qualified else
            "The remaining Roth **earnings stay locked (Tier 3) until 59½**. "
        )
        st.info(
            f"Your Roth **contribution basis of {_d(_roth_basis)}** (as entered locally) is "
            f"treated as **Tier-1 free** — contributions come out first, tax- and "
            f"penalty-free at any age. {_earn}"
            "Verify the basis against **Form 8606**, **Form 5498 box 10**, or Fidelity. "
            "This assumes **direct contributions**: converted amounts carry their own "
            "5-year clocks, and the earnings 5-year-account rule also applies at 59½ — "
            "neither is modeled. Pre-tax accounts remain locked (10% penalty + ordinary "
            "income) before 59½."
        )
    else:
        st.info(
            "No Roth contribution basis is configured, so the **whole Roth is shown as "
            "locked (Tier 3)** — the conservative default. To split out the penalty-free "
            "contribution basis (it comes out first, tax- and penalty-free at any age), "
            "add your date of birth and direct-contribution basis to "
            "`private/personal_profile.json` (gitignored — see `personal_profile.example.json`). "
            "Verify the basis against Form 8606, Form 5498 box 10, or Fidelity. Pre-tax "
            "accounts remain locked (10% penalty + ordinary income) before 59½."
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

# ── Marginal cost-to-cash exhibit (Part 3) — derived from the same per-row columns ─
# Marginal cost of each successive block (tier), cheapest-first: cost ÷ gross. Reuses
# the ladder's Value + Cost-to-cash columns — no tax recomputation.
with col:
    st.subheader("Marginal cost of the next dollar raised")
    _mc = ladder.groupby("tier").agg(gross=("value", "sum"), cost=("cost_to_cash", "sum")).sort_index()
    _mc["pct"] = (_mc["cost"] / _mc["gross"].where(_mc["gross"] != 0) * 100).fillna(0.0)
    _cum = _mc["gross"].cumsum()

    def _p(t):
        return float(_mc.loc[t, "pct"]) if t in _mc.index else 0.0

    _b1 = float(_cum.loc[1]) if 1 in _cum.index else 0.0          # end of Tier 1 → first taxable gain
    _b2 = float(_cum.loc[2]) if 2 in _cum.index else _b1          # end of Tier 2 → first retirement dollar

    _pts, _prev = [], 0.0                                         # step-shaped points (holds each tier's %)
    for _t in _mc.index:
        _pct, _end = float(_mc.loc[_t, "pct"]), float(_cum.loc[_t])
        _pts.append({"Cumulative gross raised ($)": _prev, "Marginal cost %": _pct})
        _pts.append({"Cumulative gross raised ($)": _end, "Marginal cost %": _pct})
        _prev = _end
    st.line_chart(pd.DataFrame(_pts), x="Cumulative gross raised ($)", y="Marginal cost %", height=240)
    st.caption(
        f"Cost ÷ gross of each block, cheapest first: **Tier 1 ≈ {_p(1):.1f}%** → "
        f"**Tier 2 ≈ {_p(2):.1f}%** (15% LTCG + PA, on the embedded gain only) → "
        f"**Tier 3 ≈ {_p(3):.1f}%** (10% penalty + ordinary on the whole withdrawal). You can raise "
        f"~{_d(_b1)} before touching a taxable gain, and ~{_d(_b2)} before touching retirement."
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
