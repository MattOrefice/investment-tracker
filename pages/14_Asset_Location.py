"""Asset Location — personal-mode only.

Reads as a set of decisions: where to deploy idle Roth cash, then a series of
ranked cleanup/coverage actions (count derived from ACTION_GROUPS, not hardcoded).
Scores and prose are authored (src/location_actions.py); dollar figures are
templated from the live CSV. Every register row must belong to some action group
(assert_full_coverage raises at render otherwise). Rides the household CSV path;
does not touch holdings.py, rebalance.py, or page 11.
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
    compute_household_allocation,
    exclude_non_household_positions,
    sleeve_display_name,
)
from src.location_config import (
    TAX_PROFILE,
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    ACCOUNT_SHELTER_PRIORITY,
    LTCG_0_BRACKET_CEILING_SINGLE_2026,
    is_directable,
    # IRS 2026 limits — for the ideal-location reference table (2c). Named constants
    # so no dollar literal is scattered in the page.
    IRA_CONTRIB_LIMIT_2026,
    IRA_CATCHUP_50_2026,
    WORKPLACE_ELECTIVE_DEFERRAL_2026,
    WORKPLACE_CATCHUP_50_2026,
    WORKPLACE_CATCHUP_60_63_2026,
    WORKPLACE_415C_TOTAL_2026,
    ROTH_MAGI_PHASEOUT_SINGLE_2026,
    ROTH_MAGI_PHASEOUT_MFJ_2026,
    TRAD_IRA_DEDUCTION_PHASEOUT_SINGLE_2026,
    HSA_CONTRIB_LIMIT_SELF_2026,
    HSA_CONTRIB_LIMIT_FAMILY_2026,
    HSA_CATCHUP_55_2026,
)
from src.location_actions import (
    ACTION_GROUPS,
    STATUS_ORDER,
    INFORMATIONAL_KEYS,
    build_roth_deploy_answer,
    household_deploy_gaps,
    deploy_targets_split,
    resolve_placeholders,
    resolve_caption,
    render_prose_md,
    escape_md,
    _fmt_dollars,
    filter_register_for_group,
    capital_gains_headroom,
    assert_full_coverage,
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
        "tax_treatment, pseudonym, display_name, managed_by, included_in_household "
        "FROM accounts",
        conn,
    )
    securities_df = pd.read_sql_query("SELECT * FROM securities", conn)
    # For gap-proportional Roth deploy sizing — the SAME household aggregation the
    # Household View uses (compute_household_allocation), so the deploy closes real
    # household sleeve gaps rather than splitting by a round number.
    compositions_df = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    saa_targets_df  = pd.read_sql_query(
        "SELECT asset_class_id, name, target_weight FROM asset_classes "
        "WHERE parent_id IS NOT NULL AND target_weight > 0",
        conn,
    )

# Single source of truth: accounts holding money that isn't a household asset
# (e.g. unvested/forfeitable employer contributions) are dropped here, once, so
# the register, deploy sizing, and every dollar figure below agree.
positions_df = exclude_non_household_positions(positions_df, accounts_df)

# ── Derived frames ──────────────────────────────────────────────────────────────
register = build_location_register(
    positions_df, accounts_df, securities_df,
    TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY,
)
# Coverage invariant: every register row must be claimed by some action group.
# Raise loudly at render if a new mislocation slips in with no group to narrate it.
assert_full_coverage(register)
deploy = build_roth_deploy_answer(
    positions_df, accounts_df, securities_df, compositions_df, saa_targets_df)
_roth_idle_cash = deploy["idle_cash"]
_deploy_residual = float(deploy.get("residual", 0.0))
_deploy_buys = float(deploy["table"]["dollar"].sum()) if not deploy["table"].empty else 0.0

# ── KPIs (three distinct units; the last two must never be summed) ─────────────
_kpi_idle_roth = _roth_idle_cash
_kpi_annual_drag = float(register[register["case"].isin(["A", "B", "D"])]["annual_benefit"].sum())
# The KPI counts ALL A/B/D drag. Partition it into actionable-now vs deferred, where
# deferred = accepted groups (logged, no action) + blocked groups (a good move you
# can't make yet — the gain side is blocked on pre-tax capacity). Every register row
# belongs to exactly one group, so this is a partition of the KPI, never a re-sum.
_deferred_idx = set()
for _g in ACTION_GROUPS:
    if _g["status"] in ("accepted", "blocked"):
        _deferred_idx |= set(filter_register_for_group(register, _g).index)
_def_rows = register.loc[sorted(_deferred_idx)]
_deferred_drag = float(_def_rows[_def_rows["case"].isin(["A", "B", "D"])]["annual_benefit"].sum()) if len(_def_rows) else 0.0
_actionable_drag = _kpi_annual_drag - _deferred_drag
# Case C is repositionable inside shelters — a stock of dollars, NOT an annual flow.
_case_c = register[register["case"] == "C"]
_pos_disp = positions_df.merge(accounts_df[["pseudonym", "display_name"]], on="pseudonym", how="left")
_kpi_repositionable = 0.0
for _, _r in _case_c.iterrows():
    _m = _pos_disp[(_pos_disp["symbol"] == _r["symbol"]) & (_pos_disp["display_name"] == _r["account"])]
    _kpi_repositionable += float(_m["current_value"].sum())

# Loss-side total value for the Act-now summary — the relocate_loss_side group's
# position value, computed from the register (not hardcoded). This is a VALUE total
# (parallel to _kpi_repositionable), not the net embedded loss.
_loss_group = next(g for g in ACTION_GROUPS if g["key"] == "relocate_loss_side")
_loss_side_total = float(filter_register_for_group(register, _loss_group)["current_value"].sum())

# Directability — which accounts (present in the export) can be traded jointly today.
_present_accts = accounts_df[accounts_df["pseudonym"].isin(positions_df["pseudonym"])]
_directable_names = [n for p, n in zip(_present_accts["pseudonym"], _present_accts["display_name"]) if is_directable(p)]
_coordination_names = [n for p, n in zip(_present_accts["pseudonym"], _present_accts["display_name"]) if not is_directable(p)]

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
        return f"{resolved['workplace_plan_value']} former-employer 401(k) · available now · destination is the choice"
    size = resolved.get("value") or "—"
    n = resolved.get("count")
    where = f" across {n} holdings" if n else ""
    cost = "free" if (not reg_rows.empty and bool(reg_rows["is_free"].all())) else "costly"
    return f"{size}{where} · {cost}"


# ── Title ──────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Asset Location")
    st.caption(
        f"{len(ACTION_GROUPS)} decisions: deploy idle Roth cash, then "
        f"{len(ACTION_GROUPS) - 1} ranked cleanup actions"
    )
    st.caption(f"As of {_as_of_date.isoformat()} · source: {_csv_path.name}")

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            f"**{len(ACTION_GROUPS)} decisions, not a table.** Each card is a decision with a "
            "score (authored, 1–10), a one-line summary, a **For** and an "
            "**Against**, and — for the cleanup actions — an expander with the "
            "underlying positions. Cards are ordered *act now → evaluate → "
            "blocked → accepted*.\n\n"
            "**Dollar figures are live.** Every dollar amount in the prose is "
            "computed from the current positions export, not hardcoded. If a "
            "figure can't be computed, the page refuses to render rather than "
            "show a misleading \\$0.\n\n"
            "**Free vs costly.** Trades INSIDE any tax-advantaged account are "
            "non-taxable — *free*. Assets cannot be transferred between accounts; "
            "relocation is synthesized as two independent trades whose net effect "
            "leaves household exposure unchanged. A sale in a taxable account "
            "realizes the embedded gain and is *costly*.\n\n"
            "**How the shelters are taxed.** Inside a Roth, IRA, or 401(k) nothing "
            "is taxed as it happens — income and gains compound untaxed, with no "
            "basis tracking and no wash-sale rule — which is *why* the in-shelter "
            "moves above are free: the account is taxed on what you withdraw, not "
            "on gains, so there is no gain to realize. The only taxable event is "
            "money leaving: a Traditional-IRA withdrawal is ordinary income no "
            "matter which lots were sold, while qualified Roth withdrawals are "
            "tax-free. Only taxable-account holdings carry an embedded-gain cost — "
            "which is why they alone show one here and on the Liquidity page.\n\n"
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
    st.caption(
        f"The drag counts A/B/D across all groups; about {escape_md(_fmt_dollars(_deferred_drag))} "
        "of it sits in accepted or capacity-blocked groups you can't act on now — most of it "
        "waiting on the 401(k) rollover to free pre-tax space — so the drag you'd actually "
        f"remove by acting today is about {escape_md(_fmt_dollars(_actionable_drag))}."
    )
    st.divider()

# ── Six decisions ───────────────────────────────────────────────────────────────
_CASE_LABEL = {
    "A": "A — low-eff in taxable", "B": "B — medium-eff in taxable",
    "C": "C — premium-space waste in Roth", "D": "D — high-priority stuck in taxable",
}

# Each status bucket gets a visible section header (H2, above the H3 card titles) so
# the act-now → evaluate → blocked → accepted grouping is obvious at a glance — not
# just implied by each card's caption. _ordered_groups is status-then-score sorted,
# so a bucket's cards are contiguous and the header fires on each status change.
_BUCKET_BLURB = {
    "act_now":  "Trade now — free, or clearly worth the cost.",
    "evaluate": "Optional — depends on this year's income; wait-and-see.",
    "blocked":  "Waiting on an external event before it can happen.",
    "accepted": "Logged as a deliberate decision — no action.",
}
_bucket_counts = {s: sum(1 for g in _ordered_groups if g["status"] == s) for s in STATUS_ORDER}
_prev_status = None

for group in _ordered_groups:
    reg_rows = (
        filter_register_for_group(register, group)
        if group["key"] not in INFORMATIONAL_KEYS else register.iloc[0:0]
    )
    resolved = resolve_placeholders(group, positions_df, accounts_df, securities_df, register,
                                    roth_idle_cash=_roth_idle_cash)

    _, col, _ = st.columns([1, 8, 1])
    with col:
        if group["status"] != _prev_status:
            _n = _bucket_counts[group["status"]]
            st.header(_STATUS_LABEL[group["status"]])
            st.caption(f"{_n} {'decision' if _n == 1 else 'decisions'} · {_BUCKET_BLURB[group['status']]}")
            if group["status"] == "act_now":
                # Actionable dollar weight in one glance — every figure computed from
                # the register above. loss-side is a VALUE total (relocated free); its
                # net embedded loss is tiny, so this is deliberately not "$X in losses".
                st.markdown(
                    f"Deploy **{escape_md(_fmt_dollars(_kpi_idle_roth))}** · "
                    f"reposition **{escape_md(_fmt_dollars(_kpi_repositionable))}** "
                    f"in-shelter (free) · harvest the loss side "
                    f"(**{escape_md(_fmt_dollars(_loss_side_total))}**, free)"
                )
            _prev_status = group["status"]
        st.subheader(f"{group['title']}  ·  {group['score']}/10")
        # One-line imperative decision, directly under the score — what to DO, in
        # bold, before the two-paragraph For/Against reasoning below.
        if group["key"] == "deploy_roth_cash":
            _dts = deploy_targets_split(deploy)
            if _dts["deploy_targets"]:
                st.markdown(f"**{render_prose_md(group['action'], {**resolved, **_dts})}**")
            else:
                st.markdown("**Deploy the idle Roth cash across your underweight Roth sleeves.**")
        else:
            st.markdown(f"**{render_prose_md(group['action'], resolved)}**")
        st.caption(escape_md(f"**{_STATUS_LABEL[group['status']]}** — {_summary_line(group, resolved, reg_rows)}"))

        st.markdown(f"**For.** {render_prose_md(group['pros'], resolved)}")
        st.markdown(f"**Against.** {render_prose_md(group['cons'], resolved)}")

        if group["key"] == "deploy_roth_cash":
            tbl = deploy["table"].copy()
            tbl["sleeve"] = tbl["sleeve"].map(sleeve_display_name)
            # Total row = Σ buys (= idle cash minus any residual), so the table
            # reconciles against its own rows. A residual (cash left after every gap
            # is filled) is reported separately below — never forced into a sleeve.
            total = pd.DataFrame([{"ticker": "Total", "sleeve": "", "dollar": _deploy_buys}])
            disp = pd.concat([tbl, total], ignore_index=True).rename(
                columns={"ticker": "Ticker", "sleeve": "Sleeve", "dollar": "Amount ($)"}
            )
            st.dataframe(
                disp, use_container_width=True, hide_index=True,
                column_config={"Amount ($)": st.column_config.NumberColumn(format="$%.0f")},
            )
            _sizing_note = (
                "One is_in_saa ticker per sleeve; the idle cash is sized to close each "
                "sleeve's household gap to target, filling the underweight equity "
                "sleeves in proportion to their dollar gaps (largest underweight gets "
                "the most), capped so none overshoots. The number of tickers follows "
                "from how many sleeves are underweight — no fixed count, no round-number "
                "split."
            )
            if _deploy_residual >= 1.0:
                _sizing_note += (
                    f" Every gap is already fully funded, so about "
                    f"{escape_md(_fmt_dollars(_deploy_residual))} of the idle cash stays "
                    "undeployed rather than being forced into a sleeve past its target."
                )
            st.caption(_sizing_note)

            with st.expander("How these weights were sized", expanded=False):
                _total_hh = float(positions_df["current_value"].sum())
                _gaps_df = household_deploy_gaps(
                    positions_df, accounts_df, securities_df, compositions_df, saa_targets_df)
                _buy_by_ticker = dict(zip(deploy["table"]["ticker"], deploy["table"]["dollar"]))
                _exhibit = _gaps_df.copy()
                _exhibit["Sleeve"] = _exhibit["sleeve"].map(sleeve_display_name)
                _exhibit["Target (%)"] = _exhibit["target"] / _total_hh * 100
                _exhibit["Current (%)"] = _exhibit["current"] / _total_hh * 100
                _exhibit["Buy ($)"] = _exhibit["ticker"].map(_buy_by_ticker)
                _exhibit = _exhibit.rename(columns={"gap": "Gap ($)"})[
                    ["Sleeve", "Target (%)", "Current (%)", "Gap ($)", "Buy ($)"]
                ]
                st.markdown(
                    "**Method.** Each buy is sized proportional to its sleeve's dollar "
                    "gap — target weight × household value, minus what look-through "
                    "already counts toward it — across the Roth-eligible sleeves still "
                    "underweight after that look-through, capped so none overshoots target."
                )
                st.caption(
                    "This table is a filtered subset, not the household breakdown: it "
                    "lists only the Roth-eligible sleeves still underweight after "
                    "look-through, so its Current (%) sums to a fraction of the book "
                    "rather than 100% — sleeves already at or over target (notably US "
                    "Large Core) do not appear because new cash never overshoots a "
                    "target. The full household sleeve breakdown — every sleeve with "
                    "current $, current %, target %, and drift — is on the Household "
                    "View page."
                )
                st.dataframe(
                    _exhibit, use_container_width=True, hide_index=True,
                    column_config={
                        "Target (%)":  st.column_config.NumberColumn(format="%.1f%%"),
                        "Current (%)": st.column_config.NumberColumn(format="%.1f%%"),
                        "Gap ($)":     st.column_config.NumberColumn(format="$%.0f"),
                        "Buy ($)":     st.column_config.NumberColumn(format="$%.0f"),
                    },
                )

                _lt_symbols = set(compositions_df["fund_symbol"].unique())
                _lt_value = float(
                    positions_df[positions_df["symbol"].isin(_lt_symbols)]["current_value"].sum()
                )
                _lt_pct = (_lt_value / _total_hh * 100) if _total_hh else 0.0
                _alloc = compute_household_allocation(
                    positions_df, accounts_df, securities_df, compositions_df, saa_targets_df,
                    mode="look_through", scope="total",
                )
                _ulc = _alloc[_alloc["sleeve"] == "US Large Core"]
                _ulc_current = float(_ulc.iloc[0]["dollar_value"])
                _ulc_target = float(_ulc.iloc[0]["target_weight"]) * _total_hh
                st.markdown(
                    f"**Why look-through matters.** About {_lt_pct:.0f}% of the household "
                    "sits inside multi-asset funds — chiefly the American Funds 2060 "
                    "target-date fund — that are decomposed into underlying sleeves rather "
                    "than counted as one lump. That is why US Large Core (VOO) is excluded "
                    f"above despite never being bought directly: look-through counts it at "
                    f"{escape_md(_fmt_dollars(_ulc_current))} against a "
                    f"{escape_md(_fmt_dollars(_ulc_target))} target — already overweight — "
                    "because broad US equity buried inside those funds counts toward it."
                )
                st.markdown(
                    "**Provenance.** The target-date fund's sleeve split is sourced from "
                    "its factsheet, but the split BY sleeve — large-core vs. value, "
                    "developed vs. emerging within its international exposure — is "
                    "approximated to fit the factsheet's published totals, not read off "
                    "exact underlying holdings: sourced and reasoned, not precise. The "
                    "blended large-core weight across the household's multi-asset funds "
                    "would have to be overstated by roughly 18 percentage points before "
                    "US Large Core flipped from overweight to underweight and changed "
                    "which sleeves get bought — the ranking is not fragile to this "
                    "approximation, even though the approximation itself is real."
                )
        elif group["key"] not in INFORMATIONAL_KEYS:
            _cap = resolve_caption(group, positions_df, accounts_df, register)
            if _cap:
                # Prominent (normal-weight markdown, not a muted st.caption): the
                # book-vs-mislocation gap is the whole reason the count exceeds the
                # rows below, so it must be unmissable, directly above the expander.
                st.markdown(_cap)
            # Show EVERY register row backing this group, including sub-threshold
            # (< MIN_ANNUAL_BENEFIT) drag. The Value column gives each row its position
            # size, so a small drag reads as small — not as a count-vs-empty-table
            # contradiction. This table now sums the same rows as the drag KPI and the
            # group totals above (count == row_count); the only legitimate count≠rows
            # gap is the matched_symbols groups, which carry a caption for it.
            with st.expander(f"Underlying positions ({len(reg_rows)})", expanded=False):
                show = reg_rows.drop(columns=["surfaced"], errors="ignore").copy()
                show["case"] = show["case"].map(_CASE_LABEL).fillna(show["case"])
                show["sleeve"] = show["sleeve"].map(sleeve_display_name)
                show = show.rename(columns={
                    "holding": "Holding", "symbol": "Symbol", "account": "Account",
                    "sleeve": "Sleeve", "case": "Case", "current_value": "Value ($)",
                    "annual_benefit": "Annual Benefit ($)",
                    "embedded_gain": "Embedded Gain ($)", "cost_to_realize": "Cost to Realize ($)",
                    "is_free": "Free?", "payback_months": "Payback (months)",
                })
                # Total row (Value summed) so the header count/value reconciles against
                # the rows below — same style as the Deploy card's Total row. Only Value
                # is totalled; the other money columns stay blank in the total row.
                _total = {}
                for _c in show.columns:
                    if _c == "Holding":
                        _total[_c] = "Total"
                    elif _c == "Value ($)":
                        _total[_c] = float(reg_rows["current_value"].sum())
                    elif pd.api.types.is_bool_dtype(show[_c]) or pd.api.types.is_numeric_dtype(show[_c]):
                        _total[_c] = float("nan")
                    else:
                        _total[_c] = ""
                show = pd.concat([show, pd.DataFrame([_total])], ignore_index=True)
                st.dataframe(
                    show, use_container_width=True, hide_index=True,
                    column_config={
                        "Value ($)":           st.column_config.NumberColumn(format="$%.0f"),
                        "Annual Benefit ($)":  st.column_config.NumberColumn(format="$%.2f"),
                        "Embedded Gain ($)":   st.column_config.NumberColumn(format="$%.0f"),
                        "Cost to Realize ($)": st.column_config.NumberColumn(format="$%.2f"),
                        "Free?":               st.column_config.CheckboxColumn(),
                        "Payback (months)":    st.column_config.NumberColumn(format="%.1f"),
                    },
                )
                # Case C "Annual Benefit" is repositioning value, not tax drag — it is
                # excluded from the drag KPI, so summing the tables won't reconcile to it.
                if (reg_rows["case"] == "C").any():
                    st.caption(
                        "Case C “Annual Benefit” is repositioning value, not tax drag — "
                        "excluded from the drag KPI above."
                    )
        st.divider()

# ── Assumptions ─────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    with st.expander("Assumptions", expanded=False):
        _ord = TAX_PROFILE["federal_marginal"] + TAX_PROFILE["state_marginal"]
        _ltcg = TAX_PROFILE["federal_ltcg"] + TAX_PROFILE["state_ltcg"]
        # Income comes from the runtime profile, so the headroom prose is stated
        # conditionally rather than asserting the exhausted-bracket conclusion
        # that only holds while income sits above the ceiling.
        _hr = capital_gains_headroom(register)
        _income = _hr["ordinary_income"]

        if not _hr["income_known"]:
            _income_line = (
                "- Federal ordinary marginal: "
                f"**{TAX_PROFILE['federal_marginal']:.0%}** (2026 single bracket; "
                "your income is not configured)\n"
            )
            _headroom_para = (
                "**0% capital-gains headroom (2026): not computed.** No "
                "`ordinary_income` is set in `private/personal_profile.json`, and "
                "the 0% bracket is sized as (ceiling − income) — so it is treated "
                "as exhausted rather than guessed at. That is the conservative "
                "reading: assuming room that isn't there would mean realizing "
                "gains you expected to be untaxed. Set your income to size it."
            )
        else:
            _income_line = (
                f"- Federal ordinary marginal: **{TAX_PROFILE['federal_marginal']:.0%}** "
                f"(2026 single bracket at ~\\${_income:,.0f} income)\n"
            )
            if _hr["total"] > 0:
                _headroom_para = (
                    f"**0% capital-gains headroom (2026): \\${_hr['total']:,.0f}.** "
                    f"With 2026 income of ~\\${_income:,.0f} below the "
                    f"~\\${LTCG_0_BRACKET_CEILING_SINGLE_2026:,.0f} single-filer "
                    "0%-rate ceiling, that much realized long-term gain is federally "
                    "untaxed; beyond it, gains are taxed at 15% federal + 3.07% PA."
                )
            else:
                _headroom_para = (
                    "**0% capital-gains headroom (2026): \\$0.** With 2026 income of "
                    f"~\\${_income:,.0f} above the "
                    f"~\\${LTCG_0_BRACKET_CEILING_SINGLE_2026:,.0f} single-filer "
                    "0%-rate ceiling, the 0% bracket is exhausted — every realized "
                    "long-term gain is taxed at 15% federal + 3.07% PA."
                )

        st.markdown(
            "**Tax profile** (rates user-editable in `src/location_config.py`; "
            "income in `private/personal_profile.json`):\n\n"
            + _income_line
            + f"- Federal long-term capital gains: **{TAX_PROFILE['federal_ltcg']:.0%}** "
            "— the rate that applies once the 0% bracket is used up\n"
            f"- State ordinary (PA flat): **{TAX_PROFILE['state_marginal']:.2%}**\n"
            f"- State LTCG (PA — no preferential rate): **{TAX_PROFILE['state_ltcg']:.2%}**\n"
            f"- Combined ordinary rate: **{_ord:.2%}** · combined rate on realized "
            f"gains: **{_ltcg:.2%}** (15% federal + PA)\n\n"
            + _headroom_para
            + "\n\n**Scores are authored**, not computed — the owner's priority judgement, "
            "deliberately without a scoring formula. **Sleeve deploy targets are an "
            "ordinal ranking**, not a return forecast. **Dollar figures in every card "
            "are templated from the live positions CSV**; an unresolvable figure "
            "raises rather than rendering \\$0."
        )

# ── Ideal account location — 2026 reference ─────────────────────────────────────
# A static reference: the TARGET location for each account type, with IRS-2026
# limits. Every dollar figure templates from the named constants in
# location_config.py (no scattered literals); ages/years are inherent content.
_, col, _ = st.columns([1, 8, 1])
with col:
    with st.expander("Where each account type ideally sits — 2026", expanded=False):
        def _d(x):   return escape_md(_fmt_dollars(x))     # full dollars: "\$7,500"
        def _k(x):   return "\\$" + f"{x / 1000:,.0f}k"    # abbreviated:  "\$153k"
        def _rng(t): return f"{_k(t[0])}–{_k(t[1])}"       # phase-out range: "\$153k–\$168k"

        _ira_contrib = f"{_d(IRA_CONTRIB_LIMIT_2026)} combined¹ (+{_d(IRA_CATCHUP_50_2026)} if 50+)"
        _rows = [
            ("Roth IRA",
             "Highest-return equity — broad-market, growth-tilted",
             _ira_contrib,
             f"Phases out MAGI {_rng(ROTH_MAGI_PHASEOUT_SINGLE_2026)} single, "
             f"{_rng(ROTH_MAGI_PHASEOUT_MFJ_2026)} joint",
             "Growth & withdrawals never taxed; contributions withdrawable anytime; "
             "no RMDs."),
            ("Traditional IRA",
             "Income-heavy / lower-return — bonds, REITs, hedged equity",
             _ira_contrib,
             f"Anyone can contribute; deduction phases out "
             f"{_rng(TRAD_IRA_DEDUCTION_PHASEOUT_SINGLE_2026)} single if covered by a workplace plan",
             "Withdrawn as ordinary income; RMDs from 73 (75 if born 1960+)."),
            ("401(k) / 403(b)",
             "Same as Traditional; capture the full employer match first",
             f"{_d(WORKPLACE_ELECTIVE_DEFERRAL_2026)} employee (+{_d(WORKPLACE_CATCHUP_50_2026)} if 50+, "
             f"+{_d(WORKPLACE_CATCHUP_60_63_2026)} if 60–63); {_k(WORKPLACE_415C_TOTAL_2026)} incl. employer",
             "No income limit",
             "Employer match beats any market return. Roth 401(k): after-tax in, "
             "tax-free growth out, no income limit — same ideal contents as a Roth "
             "IRA; pre-tax side holds bonds like a Traditional IRA."),
            ("Taxable brokerage",
             "Tax-efficient broad equity, munis, anything needed before 59½",
             "No limit",
             "No limit",
             "Low turnover + qualified dividends keep drag low; no early-withdrawal "
             "penalty; harvest losses; step-up at death."),
            ("HSA (needs HDHP)",
             "Long-horizon growth equity, if medical bills paid out of pocket",
             f"{_d(HSA_CONTRIB_LIMIT_SELF_2026)} self / {_d(HSA_CONTRIB_LIMIT_FAMILY_2026)} family "
             f"(+{_d(HSA_CATCHUP_55_2026)} if 55+)",
             "No income limit; requires an HSA-eligible HDHP",
             "Triple tax-free; after 65 works like a Traditional IRA for non-medical spending."),
        ]
        _hdr = ("| Account | Ideal holdings | 2026 contribution limit | "
                "2026 income / eligibility limit | Why here / key note |\n"
                "|---|---|---|---|---|\n")
        _body = "\n".join("| " + " | ".join(r) + " |" for r in _rows)
        st.markdown(_hdr + _body)
        st.markdown(
            f"¹ {_d(IRA_CONTRIB_LIMIT_2026)} is the *combined* ceiling across both IRAs, not each.\n\n"
            "**Why growth, not drag, picks the Roth.** Value scales with tax-free growth, "
            "not drag avoided — equities (highest return) go here; bonds (low return, "
            "tax-inefficient) cost nothing extra in the Traditional IRA regardless. The "
            "40-year horizon is beside the point: location only answers where the bonds "
            "you hold go, not whether to hold them — that is a whole-portfolio risk "
            "decision (the SAA's fixed-income target), not a location one.\n\n"
            "² Pre-tax 401(k), Roth 401(k), and Roth IRA can coexist — the 401(k) limit "
            "is shared across pre-tax + Roth; the Roth IRA limit is separate."
        )
