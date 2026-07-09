"""Asset Location — personal-mode only.

Two questions: where new tax-advantaged cash should go (by an ordinal sleeve
priority), and which cleanup actions exist ranked by benefit / cost / urgency.
Rides the household CSV path; does not touch holdings.py or the trades ledger.
"""
import logging
import streamlit as st

st.set_page_config(page_title="Asset Location", layout="wide")

from pathlib import Path
import pandas as pd

from src.config import IS_DEMO
from src.ui_helpers import render_page_header

render_page_header()

# ── Demo-mode gate ─────────────────────────────────────────────────────────────
# Same guard as the Household View page: no personal-mode data is loaded or
# rendered in demo mode.
if IS_DEMO:
    st.title("Asset Location")
    st.info("Asset location analysis is available in personal mode only.")
    st.stop()

# ── Personal-mode imports ──────────────────────────────────────────────────────
from src.db import get_connection
from src.household_data import load_latest_positions
from src.household import (
    compute_sleeve_by_account,
    compute_embedded_gain,
    build_location_register,
    build_deploy_view,
    sleeve_display_name,
)
from src.location_config import (
    TAX_PROFILE,
    SLEEVE_LOCATION_PRIORITY,
    ACCOUNT_SHELTER_PRIORITY,
)

# ── Load data ──────────────────────────────────────────────────────────────────
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
    # account_number is intentionally never selected (raw numbers must not reach
    # this page or its DataFrames).
    accounts_df   = pd.read_sql_query(
        "SELECT account_id, name, type, custodian, is_active, created_at, "
        "tax_treatment, pseudonym, display_name, managed_by FROM accounts",
        conn,
    )
    securities_df = pd.read_sql_query("SELECT * FROM securities", conn)

# ── Derived frames ──────────────────────────────────────────────────────────────
sba = compute_sleeve_by_account(positions_df, accounts_df, securities_df)
_eg_df, _n_excluded_gain = compute_embedded_gain(positions_df)
register = build_location_register(
    positions_df, accounts_df, securities_df,
    TAX_PROFILE, SLEEVE_LOCATION_PRIORITY, ACCOUNT_SHELTER_PRIORITY,
)

# Idle cash per account (cash sleeve), for the deploy default + a KPI.
_cash_by_acct = (
    sba[sba["sleeve_category"] == "cash"]
    .set_index("pseudonym")["current_value"].to_dict()
)
_total_idle_cash = float(sum(_cash_by_acct.values()))
_total_annual_drag = float(register["annual_benefit"].sum()) if not register.empty else 0.0
_free_action_count = int(register["is_free"].sum()) if not register.empty else 0

_CASE_LABEL = {
    "A": "A — low-efficiency in taxable",
    "B": "B — medium-efficiency in taxable",
    "C": "C — premium-space waste in Roth",
    "D": "D — high-priority sleeve stuck in taxable",
}

# ── Title ──────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Asset Location")
    st.caption("Where to deploy new tax-advantaged cash, and which mislocations to clean up")
    st.caption(f"As of {_as_of_date.isoformat()} · source: {_csv_path.name}")

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "**Scope.** This page rides the same household positions export as the "
            "Household View. It answers two questions the drift view does not: "
            "*where should new tax-advantaged cash go*, and *which existing holdings "
            "sit in the wrong account type*.\n\n"
            "**Deploy ranking is ordinal.** Sleeves are ranked 1–N by how deserving "
            "they are of scarce tax-free (Roth) space — highest-expected-return "
            "sleeves first. This is a **ranking, not a return forecast**: no number on "
            "this page is a predicted return. A sleeve with no rank is simply *not a "
            "deploy target*, which is different from being ranked last.\n\n"
            "**Action register — four cases.** (A) tax-inefficient income asset in a "
            "taxable account; (B) medium-efficiency asset in taxable; (C) a "
            "low/medium-efficiency asset sitting in the **Roth**, which is correctly "
            "sheltered but wastes premium tax-free space that a higher-return asset "
            "should occupy; (D) a high-priority sleeve stuck in taxable while a "
            "lower-priority sleeve occupies Roth space. Case C is invisible to the "
            "Household View's tax-drag table, which only sees case A.\n\n"
            "**Free vs paid.** A move *inside or between shelters* (e.g. Roth → "
            "Traditional) triggers **no taxable sale — it is free**. A move out of a "
            "taxable account realizes the embedded gain; its cost is that gain times "
            "the combined long-term capital-gains rate, and `payback (months)` is how "
            "long the annual benefit takes to earn that cost back. Free actions are "
            "listed first.\n\n"
            "**Actionability.** Six of seven accounts are externally managed; most "
            "register rows are *observed* mislocations that would need manager "
            "coordination, not unilateral trades — the same caveat as the Household View."
        )
    st.divider()

# ── KPI header ─────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    k1, k2, k3 = st.columns(3)
    k1.metric("Est. total annual drag", f"${_total_annual_drag:,.0f}")
    k2.metric("Idle cash across accounts", f"${_total_idle_cash:,.0f}")
    k3.metric("Free actions available", _free_action_count)
    st.caption(
        "Annual drag is the summed income-shelter value at stake across all "
        "register rows (a ranking magnitude, not a precise forecast)."
    )
    st.divider()

# ── Deploy new cash ─────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Deploy new cash")
    st.caption(
        "Pick an account and an amount; sleeves are ranked by deploy priority "
        "(1 = most deserving of tax-free space). Sleeve level only — no ticker is picked."
    )

    _present = accounts_df[accounts_df["pseudonym"].isin(positions_df["pseudonym"])].copy()
    _pseudo_by_label = dict(zip(_present["display_name"], _present["pseudonym"]))
    _labels = list(_pseudo_by_label.keys())

    # Default to the account holding the most idle cash (typically the Roth).
    if _cash_by_acct:
        _default_pseudo = max(_cash_by_acct, key=_cash_by_acct.get)
        _default_label = _present[_present["pseudonym"] == _default_pseudo]["display_name"]
        _default_idx = _labels.index(_default_label.iloc[0]) if not _default_label.empty else 0
    else:
        _default_idx = 0

    sel_label = st.selectbox("Account", _labels, index=_default_idx)
    sel_pseudo = _pseudo_by_label[sel_label]
    _default_amt = float(_cash_by_acct.get(sel_pseudo, 0.0))

    amount = st.number_input(
        "Cash to deploy ($)", min_value=0.0, value=round(_default_amt, 2), step=100.0,
        help="Defaults to this account's current idle cash.",
    )

    deploy_df = build_deploy_view(
        positions_df, accounts_df, securities_df,
        SLEEVE_LOCATION_PRIORITY, sel_pseudo, amount,
    )
    st.dataframe(
        deploy_df.rename(columns={
            "sleeve": "Sleeve", "priority": "Priority",
            "current_value_in_account": "In This Account ($)", "rationale": "Rationale",
        }),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Priority": st.column_config.NumberColumn(format="%d"),
            "In This Account ($)": st.column_config.NumberColumn(format="$%.0f"),
        },
    )
    st.divider()

# ── Action register ─────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Action register")
    st.caption(
        "Ranked: free actions first (by annual benefit), then paid actions by "
        "payback period. `is_free` and `cost_to_realize` are always shown — an "
        "in-shelter move costs $0."
    )

    if register.empty:
        st.success("No asset-location mislocations detected.")
    else:
        display = register.copy()
        display["case"] = display["case"].map(_CASE_LABEL).fillna(display["case"])
        display = display.rename(columns={
            "holding": "Holding", "symbol": "Symbol", "account": "Account",
            "sleeve": "Sleeve", "case": "Case", "annual_benefit": "Annual Benefit ($)",
            "embedded_gain": "Embedded Gain ($)", "cost_to_realize": "Cost to Realize ($)",
            "is_free": "Free?", "payback_months": "Payback (months)",
        })
        display["Sleeve"] = display["Sleeve"].map(sleeve_display_name)
        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Annual Benefit ($)":  st.column_config.NumberColumn(format="$%.2f"),
                "Embedded Gain ($)":   st.column_config.NumberColumn(format="$%.0f"),
                "Cost to Realize ($)": st.column_config.NumberColumn(format="$%.2f"),
                "Free?":               st.column_config.CheckboxColumn(),
                "Payback (months)":    st.column_config.NumberColumn(format="%.1f"),
            },
        )
        st.caption(
            f"{_free_action_count} of {len(register)} actions are free "
            "(in-shelter moves or loss-harvest opportunities)."
        )
    st.divider()

# ── Assumptions ─────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    with st.expander("Assumptions", expanded=False):
        _ord = TAX_PROFILE["federal_marginal"] + TAX_PROFILE["state_marginal"]
        _ltcg = TAX_PROFILE["federal_ltcg"] + TAX_PROFILE["state_ltcg"]
        st.markdown(
            "**Tax profile** (user-editable in `src/location_config.py`):\n\n"
            f"- Federal ordinary marginal: **{TAX_PROFILE['federal_marginal']:.2%}**\n"
            f"- Federal long-term capital gains: **{TAX_PROFILE['federal_ltcg']:.2%}**\n"
            f"- State ordinary (PA flat): **{TAX_PROFILE['state_marginal']:.2%}**\n"
            f"- State LTCG (PA — no preferential rate): **{TAX_PROFILE['state_ltcg']:.2%}**\n"
            f"- Combined ordinary rate used for annual benefit: **{_ord:.2%}**\n"
            f"- Combined LTCG rate used for realization cost: **{_ltcg:.2%}**\n\n"
            "**Annual benefit** = holding value × an assumed per-sleeve distribution "
            "yield × the combined ordinary rate. It is the income-shelter value at "
            "stake — a **ranking magnitude, not a dollar forecast**.\n\n"
            "**Cost to realize** = embedded gain × combined LTCG rate, and is **$0 for "
            "any sale inside a tax-advantaged account** (the crux: in-shelter moves are "
            "free). A negative embedded gain makes the move free too (a loss to harvest).\n\n"
            "**Sleeve deploy priority is an ordinal ranking**, not a return forecast. "
            "1 = most deserving of scarce tax-free space. A sleeve absent from the "
            "ranking is *not a deploy target* — it is not ranked last.\n\n"
            f"Cash / money-market rows carry no cost basis; **{_n_excluded_gain}** such "
            "rows were excluded from the embedded-gain computation (not zero-filled)."
        )
