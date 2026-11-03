"""Liquidity — 'If you need cash' — personal-mode only.

Ranks every household holding by how cheaply it converts to spendable cash, cheapest
first — the near-inverse of the Asset Location priority — and turns that ranking into a
sell plan for an amount the owner enters: taxable accounts only, cash first, then losses,
then the lowest gain as a share of cost (src/sell_plan.py). It shows the household's
allocation before and after the plan. It PLANS ONLY: nothing is traded or written.
Does not touch holdings.py, rebalance.py, or page 11.
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
from src.liquidity import (
    EARLY_WITHDRAWAL_PENALTY, TIER_LABEL, build_liquidity_ladder, gain_tax_note,
    residence_names, residence_tax_profile,
)
from src.location_config import ordinary_rate
from src.personal_profile import (
    QUALIFIED_WITHDRAWAL_AGE, load_roth_profile, load_tax_residence,
)
from src.sell_plan import (
    GROUP_LABEL, WASH_SALE_WINDOW_DAYS, allocation_table, build_sell_plan,
    policy_targets,
)

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

# Reads only. The plan below is computed from these frames and written nowhere.
with get_connection() as conn:
    accounts_df = pd.read_sql_query(
        "SELECT account_id, name, type, custodian, is_active, created_at, "
        "tax_treatment, pseudonym, display_name, managed_by, included_in_household "
        "FROM accounts",
        conn,
    )
    securities_df = pd.read_sql_query(
        "SELECT ticker, sleeve_category, is_in_saa, asset_class_id FROM securities", conn)
    compositions_df = pd.read_sql_query(
        "SELECT fund_symbol, underlying_sleeve, weight FROM fund_compositions", conn)
    asset_classes_df = pd.read_sql_query(
        "SELECT asset_class_id, name, parent_id, target_weight FROM asset_classes", conn)

# The wash-sale check reads every account in the file, the household's or not: a
# purchase in any of them can wash a loss.
everywhere_df = positions_df

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

# Tax residence — the state and local jurisdictions, their rates and the published
# tables they come from, read from the gitignored private profile (synthetic in demo).
# The committed tree names no home state for this page.
_residence = load_tax_residence(IS_DEMO)
_tax = residence_tax_profile(_residence)
_where = residence_names(_residence)
_gain_note = gain_tax_note(_tax, _where)
_penord = EARLY_WITHDRAWAL_PENALTY + ordinary_rate(_tax)

ladder = build_liquidity_ladder(
    positions_df, accounts_df, _tax,
    roth_contribution_basis=_roth_basis, roth_is_qualified_age=_roth_qualified,
    tax_location=_where,
)


def _d(x):
    """Dollars, escaped for markdown so a leading $ isn't read as LaTeX."""
    return "\\$" + f"{x:,.0f}"


def _d2(x):
    """Dollars and cents, signed when negative, escaped for markdown."""
    return ("-" if x < 0 else "") + "\\$" + f"{abs(x):,.2f}"


def _pct(x):
    """A rate as a table prints it: 0.05 -> 5%, 0.0425 -> 4.25%, 0.01234 -> 1.234%."""
    return f"{x * 100:.3f}".rstrip("0").rstrip(".") + "%"


def _md(text):
    """Text from the private profile, with every $ escaped so markdown does not read
    '$10,000 to $20,000' as LaTeX."""
    return str(text).replace("$", "\\$")


_AGE = "59½" if QUALIFIED_WITHDRAWAL_AGE == 59.5 else f"{QUALIFIED_WITHDRAWAL_AGE:g}"

# ── Title ──────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 10, 1])
with col:
    st.title("Liquidity — if you need cash")
    st.caption(
        "Every holding ranked by how cheaply it converts to spendable cash — cheapest "
        "first — and a sell plan for the amount you need. This is nearly the inverse of "
        "Asset Location: what belongs in a shelter is exactly what you can't spend "
        "without a penalty."
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

# ── Sell plan ────────────────────────────────────────────────────────────────────
# What to sell to raise the amount entered, from taxable accounts only (src/sell_plan).
with col:
    st.subheader("Sell plan — raise cash from taxable accounts")
    _in, _why = st.columns([1, 2])
    with _in:
        _need = st.number_input(
            "Amount to raise ($)", min_value=0, value=0, step=1000, format="%d",
        )
    with _why:
        st.caption(
            "Only **taxable** accounts are sold. A traditional IRA or 401(k) withdrawal "
            f"is taxed as income and, before {_AGE}, generally owes a "
            f"{EARLY_WITHDRAWAL_PENALTY:.0%} additional tax (IRS Publications 590-B and "
            "575, 2025 editions). A Roth withdrawal of contributions is tax-free "
            "(Publication 590-B, 2025), but the tax-free room it gives up is gone for "
            "good once 60 days pass: money put back after that is a new contribution, "
            "held to that year's limit (Publication 590-A, 2025)."
        )
    st.caption(
        "Order: cash and money market first, then positions at a loss, then the lowest "
        "embedded gain as a share of cost. Whole positions first, then the last one in "
        "part. This plans only: nothing is traded or written."
    )

    plan = build_sell_plan(float(_need or 0), positions_df, accounts_df, securities_df,
                           everywhere_df=everywhere_df)
    if plan.amount <= 0:
        st.caption("Enter an amount to see which holdings the plan sells.")
    else:
        if plan.picks.empty:
            st.warning(
                f"No taxable holding can be sold under this plan, so it raises nothing "
                f"toward {_d2(plan.amount)}. It never sells from tax-advantaged accounts "
                "or the positions skipped below."
            )
        else:
            _pk = plan.picks.copy()
            _pk["why"] = [GROUP_LABEL[g] + (" (part)" if p else "")
                          for g, p in zip(_pk["group"], _pk["partial"])]
            _pk = _pk[["account", "symbol", "why", "shares", "value", "cost_basis",
                       "realized_gain"]].rename(columns={
                "account": "Account", "symbol": "Ticker", "why": "Why",
                "shares": "Shares", "value": "Value ($)", "cost_basis": "Cost basis ($)",
                "realized_gain": "Est. realized gain/loss ($)"})
            st.dataframe(
                _pk.style.format({"Shares": "{:,.3f}", "Value ($)": "${:,.2f}",
                                  "Cost basis ($)": "${:,.2f}",
                                  "Est. realized gain/loss ($)": "${:,.2f}"}, na_rep="—"),
                use_container_width=True, hide_index=True,
            )
            _m1, _m2 = st.columns(2)
            _m1.metric("Total raised", f"${plan.raised:,.2f}")
            _m2.metric("Est. realized gain/loss", ("-" if plan.realized_gain < 0 else "")
                       + f"${abs(plan.realized_gain):,.2f}")
            st.caption(
                f"Positions as of {_as_of_date.isoformat()} ({_csv_path.name}). Cash and "
                "money market realize no gain and show no cost basis. A partial sale's "
                "cost basis and gain are pro rata to the position's totals in that file; "
                "the lots actually sold decide the real figure."
            )
            if plan.shortfall > 0:
                st.warning(
                    f"The holdings this plan may sell raise {_d2(plan.raised)}, "
                    f"{_d2(plan.shortfall)} short of {_d2(plan.amount)}. It never sells "
                    "from tax-advantaged accounts or the positions skipped below."
                )
    if not plan.wash_sale_skips.empty:
        st.markdown("**Skipped rather than sold at a loss** — the same ticker is held in "
                    "another account:")
        _ws = plan.wash_sale_skips[["account", "symbol", "value", "loss", "also_held_in"]]
        st.dataframe(
            _ws.rename(columns={"account": "Account", "symbol": "Ticker",
                                "value": "Value ($)", "loss": "Loss ($)",
                                "also_held_in": "Also held in"})
            .style.format({"Value ($)": "${:,.2f}", "Loss ($)": "${:,.2f}"}),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            "A wash sale occurs when you sell at a loss and, within "
            f"{WASH_SALE_WINDOW_DAYS} days before or after the sale, buy substantially "
            "identical stock or securities or acquire substantially identical stock for "
            "your IRA or Roth IRA (IRS Publication 550 (2025), Wash Sales). A purchase "
            "in that other account in that window would disallow the loss, and that "
            "includes a reinvested dividend, which uses your dividends “to buy … more "
            "shares” (Publication 550 (2025), Dividends Used To Buy More Stock). Only "
            "the same ticker is checked, not another fund that may be substantially "
            "identical."
        )
    if not plan.no_basis_skips.empty:
        st.caption(
            "Skipped: no cost basis in the positions file, so the gain a sale realizes "
            "is unknown — " + ", ".join(
                f"{s} ({a})" for s, a in zip(plan.no_basis_skips["symbol"],
                                             plan.no_basis_skips["account"])) + "."
        )

    # ── Allocation before and after ─────────────────────────────────────────────
    st.markdown("**Allocation before and after the plan**")
    try:
        _targets = policy_targets(asset_classes_df, securities_df)
        _alloc = allocation_table(positions_df, securities_df, compositions_df, _targets,
                                  plan.picks[["pseudonym", "symbol", "value"]])
    except ValueError as exc:
        st.error(f"The allocation could not be built: {exc}")
    else:
        _al = _alloc.rename(columns={
            "broad_class": "Class", "target_pct": "Policy target",
            "before_pct": "Before", "after_pct": "After the plan"})
        _al["Change (pts)"] = _al["After the plan"] - _al["Before"]
        st.dataframe(
            _al[["Class", "Policy target", "Before", "After the plan", "Change (pts)"]]
            .style.format({"Policy target": "{:.1f}%", "Before": "{:.1f}%",
                           "After the plan": "{:.1f}%", "Change (pts)": "{:+.1f}"},
                          na_rep="—"),
            use_container_width=True, hide_index=True,
        )
        _held = set(positions_df["symbol"].astype(str))
        _blends = sorted(set(compositions_df["fund_symbol"].astype(str)) & _held)
        st.caption(
            "The household's accounts in the positions file. "
            + (("Blended funds are looked through with their fund_compositions rows: "
                + ", ".join(_blends) + ". ") if _blends else
               "No blended fund is held, so nothing is looked through. ")
            + "Bank accounts outside the positions file are left out. The sale proceeds "
            "leave the household: the after column weighs what remains. A class the "
            "policy sets no target for shows —."
        )
    st.divider()

# ── The ladder ───────────────────────────────────────────────────────────────────
with col:
    st.subheader("Every holding, cheapest to convert first")
    st.caption(
        "Rows are cheapest-to-convert first. **Cumulative net cash** is the running "
        "total of net dollars raised as you sell down the list. The same symbol in a "
        "different **Account** is a distinct lot, not a duplicate."
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

    _sty = (
        _disp.style
        .format(_fmt, na_rep="—")
        .set_properties(subset=["Account"], **{"font-weight": "700"})   # Feature B — bold account
    )
    st.dataframe(_sty, use_container_width=True, hide_index=True)

    # The rates the costs above are priced at, each with the table it comes from.
    _fed = (f"Federal {_tax['federal_marginal']:.0%} ordinary and "
            f"{_tax['federal_ltcg']:.0%} on long-term gains (TAX_PROFILE in "
            "src/location_config.py)")
    if _residence["jurisdictions"]:
        _parts = []
        for _j in _residence["jurisdictions"]:
            _r = (_pct(_j["ordinary_rate"]) if _j["ordinary_rate"] == _j["gains_rate"] else
                  f"{_pct(_j['ordinary_rate'])} ordinary and {_pct(_j['gains_rate'])} on gains")
            _parts.append(f"{_md(_j['name'])} {_r} ({_md(_j['source'])}, "
                          f"{_md(_j['source_date'])})")
        st.caption(f"**Rates.** {_fed}; " + "; ".join(_parts) + ".")
    else:
        st.caption(
            f"**Rates.** {_fed}. No state or local tax is included: "
            f"{_md(_residence['problem'])}. Add a `tax_residence` to "
            "`private/personal_profile.json` (see `personal_profile.example.json`)."
        )
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
        _pct_t, _end = float(_mc.loc[_t, "pct"]), float(_cum.loc[_t])
        _pts.append({"Cumulative gross raised ($)": _prev, "Marginal cost %": _pct_t})
        _pts.append({"Cumulative gross raised ($)": _end, "Marginal cost %": _pct_t})
        _prev = _end
    st.line_chart(pd.DataFrame(_pts), x="Cumulative gross raised ($)", y="Marginal cost %", height=240)
    st.caption(
        f"Cost ÷ gross of each block, cheapest first: **Tier 1 ≈ {_p(1):.1f}%** → "
        f"**Tier 2 ≈ {_p(2):.1f}%** ({_gain_note}, on the embedded gain only) → "
        f"**Tier 3 ≈ {_p(3):.1f}%** ({EARLY_WITHDRAWAL_PENALTY:.0%} penalty + ordinary on "
        f"the whole withdrawal). You can raise ~{_d(_b1)} before touching a taxable gain, "
        f"and ~{_d(_b2)} before touching retirement."
    )
    st.divider()

# ── How to read ──────────────────────────────────────────────────────────────────
with col:
    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "**Liquid vs locked is the whole story.** Money in a taxable brokerage "
            "account is fully liquid — sell any lot and the only cost is tax on its "
            f"embedded gain ({_gain_note}), and a lot at a loss costs nothing "
            "and even harvests a deduction. Money in a Roth, either IRA, or the 401(k) "
            "is **locked**: a withdrawal before age 59½ triggers a **10% early-withdrawal "
            "penalty plus ordinary income tax** on the amount taken.\n\n"
            "**The three tiers.**\n"
            "- **Tier 1 — free / cheap.** Taxable holdings at a loss or a small gain, "
            "and taxable cash. Sell first: little or no tax.\n"
            "- **Tier 2 — moderate.** Taxable holdings with a meaningful embedded gain. "
            f"Selling realizes long-term capital-gains tax on the gain ({_gain_note}).\n"
            "- **Tier 3 — locked.** Roth, both IRAs, and the 401(k). This tool costs the "
            f"**whole withdrawal** at a 10% penalty + ordinary income tax ({_penord:.0%} "
            "together at the rates below the ladder), *if taken before 59½* — "
            "deliberately conservative. Your real cost is lower if you're over 59½, or "
            "(for a Roth) if you withdraw contributions first, which come out penalty- "
            "and tax-free. Never treat the shown cost as exact — confirm with Fidelity.\n\n"
            "**Why this matters for the IRA.** This is the mirror image of the Asset "
            "Location page, and the reason **not to over-fund the IRA if a house or car "
            "purchase is likely in the next 5–10 years.** Every dollar you shelter for "
            "the tax break is a dollar you can't spend before 59½ without giving about "
            f"{_penord:.0%} of it back. Keep the near-term spending goal funded in taxable."
        )
