"""Asset Location — personal-mode only.

Reads as six decisions: where to deploy idle Roth cash, and five ranked cleanup
actions. Scores and prose are authored (src/location_actions.py); dollar figures
are templated from the live CSV. Rides the household CSV path; does not touch
holdings.py, rebalance.py, page 11, or build_tax_drag_ranking.
"""
import logging
import streamlit as st

st.set_page_config(page_title="Asset Location", layout="wide")

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
    build_location_register,
    sleeve_display_name,
)
from src.location_config import (
    TAX_PROFILE,
    SLEEVE_LOCATION_PRIORITY,
    ACCOUNT_SHELTER_PRIORITY,
    LTCG_HEADROOM_2026,
    is_directable,
)
from src.location_actions import (
    ACTION_GROUPS,
    STATUS_ORDER,
    INFORMATIONAL_KEYS,
    build_roth_deploy_answer,
    resolve_placeholders,
    resolve_caption,
    render_prose_md,
    escape_md,
    filter_register_for_group,
    capital_gains_headroom,
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
register = build_location_register(
    positions_df, accounts_df, securities_df,
    TAX_PROFILE, SLEEVE_LOCATION_PRIORITY, ACCOUNT_SHELTER_PRIORITY,
)
deploy = build_roth_deploy_answer(positions_df, accounts_df, securities_df, SLEEVE_LOCATION_PRIORITY)
_roth_idle_cash = deploy["idle_cash"]

# ── KPIs (three distinct units; the last two must never be summed) ─────────────
_kpi_idle_roth = _roth_idle_cash
_kpi_annual_drag = float(register[register["case"].isin(["A", "B", "D"])]["annual_benefit"].sum())
# Case C is repositionable inside shelters — a stock of dollars, NOT an annual flow.
_case_c = register[register["case"] == "C"]
_pos_disp = positions_df.merge(accounts_df[["pseudonym", "display_name"]], on="pseudonym", how="left")
_kpi_repositionable = 0.0
for _, _r in _case_c.iterrows():
    _m = _pos_disp[(_pos_disp["symbol"] == _r["symbol"]) & (_pos_disp["display_name"] == _r["account"])]
    _kpi_repositionable += float(_m["current_value"].sum())

# Directability — which accounts (present in the export) can be traded jointly today.
_present_accts = accounts_df[accounts_df["pseudonym"].isin(positions_df["pseudonym"])]
_directable_names = [n for p, n in zip(_present_accts["pseudonym"], _present_accts["display_name"]) if is_directable(p)]
_coordination_names = [n for p, n in zip(_present_accts["pseudonym"], _present_accts["display_name"]) if not is_directable(p)]

# 0% LTCG headroom (finite budget) — same source of truth as group 4's prose.
_hr = capital_gains_headroom(register)
_headroom_consumed = _hr["consumed"]
_headroom_remaining = _hr["remaining"]

_STATUS_LABEL = {"act_now": "Act now", "evaluate": "Evaluate", "blocked": "Blocked", "accepted": "Accepted"}

# Group render order: status bucket, then score descending within bucket.
_ordered_groups = sorted(
    ACTION_GROUPS, key=lambda g: (STATUS_ORDER.index(g["status"]), -g["score"])
)


def _summary_line(group: dict, resolved: dict, reg_rows: pd.DataFrame) -> str:
    """One computed line: what it is, dollar size, free or costly."""
    if group["key"] == "deploy_roth_cash":
        return f"{resolved['value']} idle Roth cash · free · zero tax, zero friction"
    if group["key"] == "rollover_401k":
        return "The household's largest lever · blocked — needs your next employer's plan"
    size = resolved.get("value") or "—"
    n = resolved.get("count")
    where = f" across {n} holdings" if n else ""
    cost = "free" if (not reg_rows.empty and bool(reg_rows["is_free"].all())) else "costly"
    return f"{size}{where} · {cost}"


# ── Title ──────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Asset Location")
    st.caption("Six decisions: deploy idle Roth cash, then five ranked cleanup actions")
    st.caption(f"As of {_as_of_date.isoformat()} · source: {_csv_path.name}")

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "**Six decisions, not a table.** Each card is a decision with a "
            "score (authored, 1–10), a one-line summary, a **For** and an "
            "**Against**, and — for the cleanup actions — an expander with the "
            "underlying positions. Cards are ordered *act now → evaluate → "
            "blocked → accepted*.\n\n"
            "**Dollar figures are live.** Every dollar amount in the prose is "
            "computed from the current positions export, not hardcoded. If a "
            "figure can't be computed, the page refuses to render rather than "
            "show a misleading \\$0.\n\n"
            "**Free vs costly.** A move inside or between shelters (e.g. Roth → "
            "Traditional) is a non-taxable event — *free*. A sale in the taxable "
            "account realizes the embedded gain and is *costly*.\n\n"
            "**Scores are judgement, not output.** The 1–10 scores are the "
            "owner's authored priority, deliberately not derived from a formula. "
            "Sleeve deploy targets are an ordinal ranking, never a return forecast.\n\n"
            f"**Actionability.** {len(_directable_names)} of the household's accounts "
            f"are directable jointly today — {', '.join(_directable_names)} — and can be "
            f"traded now. The others ({', '.join(_coordination_names)}) are externally "
            "managed and need coordination before anything moves."
        )
    st.divider()

# ── KPIs ────────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    k1, k2, k3 = st.columns(3)
    k1.metric("Idle Roth cash", f"${_kpi_idle_roth:,.0f}")
    k2.metric("Annual tax drag (A/B/D)", f"${_kpi_annual_drag:,.0f}")
    k3.metric("Repositionable in shelters (C)", f"${_kpi_repositionable:,.0f}")
    st.caption(
        "Annual tax drag is a yearly flow (cases A/B/D; case C is excluded — it "
        "carries no drag). Repositionable is a one-time stock of dollars that can "
        "move between shelters for free (case C). Different units — never summed."
    )
    st.divider()

# ── Six decisions ───────────────────────────────────────────────────────────────
_CASE_LABEL = {
    "A": "A — low-eff in taxable", "B": "B — medium-eff in taxable",
    "C": "C — premium-space waste in Roth", "D": "D — high-priority stuck in taxable",
}

for group in _ordered_groups:
    reg_rows = (
        filter_register_for_group(register, group)
        if group["key"] not in INFORMATIONAL_KEYS else register.iloc[0:0]
    )
    resolved = resolve_placeholders(group, positions_df, accounts_df, register,
                                    roth_idle_cash=_roth_idle_cash)

    _, col, _ = st.columns([1, 8, 1])
    with col:
        st.subheader(f"{group['title']}  ·  {group['score']}/10")
        st.caption(escape_md(f"**{_STATUS_LABEL[group['status']]}** — {_summary_line(group, resolved, reg_rows)}"))

        st.markdown(f"**For.** {render_prose_md(group['pros'], resolved)}")
        st.markdown(f"**Against.** {render_prose_md(group['cons'], resolved)}")

        if group["key"] == "deploy_roth_cash":
            tbl = deploy["table"].copy()
            tbl["sleeve"] = tbl["sleeve"].map(sleeve_display_name)
            total = pd.DataFrame([{"ticker": "Total", "sleeve": "", "dollar": deploy["idle_cash"]}])
            disp = pd.concat([tbl, total], ignore_index=True).rename(
                columns={"ticker": "Ticker", "sleeve": "Sleeve", "dollar": "Amount ($)"}
            )
            st.dataframe(
                disp, use_container_width=True, hide_index=True,
                column_config={"Amount ($)": st.column_config.NumberColumn(format="$%.0f")},
            )
            st.caption(
                "One is_in_saa ticker per sleeve; split 50/50 across the top two "
                "eligible sleeves. The 50/50 split is a policy choice, not a "
                "computed optimum. No ticker beyond these is auto-selected."
            )
        elif group["key"] not in INFORMATIONAL_KEYS:
            _cap = resolve_caption(group, positions_df, accounts_df, register)
            if _cap:
                st.caption(_cap)
            with st.expander(f"Underlying positions ({len(reg_rows)})", expanded=False):
                show = reg_rows.copy()
                show["case"] = show["case"].map(_CASE_LABEL).fillna(show["case"])
                show["sleeve"] = show["sleeve"].map(sleeve_display_name)
                show = show.rename(columns={
                    "holding": "Holding", "symbol": "Symbol", "account": "Account",
                    "sleeve": "Sleeve", "case": "Case", "annual_benefit": "Annual Benefit ($)",
                    "embedded_gain": "Embedded Gain ($)", "cost_to_realize": "Cost to Realize ($)",
                    "is_free": "Free?", "payback_months": "Payback (months)",
                })
                st.dataframe(
                    show, use_container_width=True, hide_index=True,
                    column_config={
                        "Annual Benefit ($)":  st.column_config.NumberColumn(format="$%.2f"),
                        "Embedded Gain ($)":   st.column_config.NumberColumn(format="$%.0f"),
                        "Cost to Realize ($)": st.column_config.NumberColumn(format="$%.2f"),
                        "Free?":               st.column_config.CheckboxColumn(),
                        "Payback (months)":    st.column_config.NumberColumn(format="%.1f"),
                    },
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
            f"- Federal long-term capital gains: **0% up to \\${LTCG_HEADROOM_2026:,.0f} "
            "of remaining 2026 headroom, then 15%**\n"
            f"- State ordinary (PA flat): **{TAX_PROFILE['state_marginal']:.2%}**\n"
            f"- State LTCG (PA — no preferential rate): **{TAX_PROFILE['state_ltcg']:.2%}**\n"
            f"- Combined ordinary rate: **{_ord:.2%}** · combined LTCG rate on "
            f"realizations: **{_ltcg:.2%}** (federal 0% within headroom + PA)\n\n"
            f"**0% capital-gains headroom (2026).** A finite budget of "
            f"**\\${LTCG_HEADROOM_2026:,.0f}**, not a rate. The recommended gain-side "
            f"relocations would consume **\\${_headroom_consumed:,.0f}**, leaving "
            f"**\\${_headroom_remaining:,.0f}**; realizations beyond that are taxed at "
            "15% federal + 3.07% PA.\n\n"
            "**Scores are authored**, not computed — the owner's priority judgement, "
            "deliberately without a scoring formula. **Sleeve deploy targets are an "
            "ordinal ranking**, not a return forecast. **Dollar figures in every card "
            "are templated from the live positions CSV**; an unresolvable figure "
            "raises rather than rendering \\$0."
        )
