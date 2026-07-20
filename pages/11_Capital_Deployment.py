"""Capital deployment page — contribution allocation and band-breach rebalancing."""
import streamlit as st

st.set_page_config(page_title="Capital Deployment", layout="wide")

import pandas as pd
from datetime import date, timedelta

from src.config import is_write_enabled
from src.db import get_connection
from src.holdings import get_sleeve_weights_on_date, get_holdings_on_date
from src.prices import get_prices
from src.rebalance import (
    compute_drift,
    interpret_rebalance_status,
    rebalance_action_text,
    suggest_buys,
    suggest_contributions,
    SUM_INVARIANT_TOLERANCE,
)
from src.trade_writer import build_thesis_lookup, write_trades_batch
from src.ui_helpers import render_footer, render_page_header, write_guard_toast
render_page_header()



@st.cache_data(ttl=3600, show_spinner=False)
def _load_data(as_of: str) -> dict:
    sleeve_df = get_sleeve_weights_on_date(as_of)

    with get_connection() as conn:
        band_rows = conn.execute(
            "SELECT name, tolerance_band FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
        saa_bands = {r["name"]: float(r["tolerance_band"]) for r in band_rows}

        target_rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
        saa_targets_full = {r["name"]: float(r["target_weight"]) for r in target_rows}

        sec_rows = conn.execute(
            """SELECT s.ticker, ac.name AS sleeve
               FROM securities s
               JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id"""
        ).fetchall()
        ticker_to_sleeve = {r["ticker"]: r["sleeve"] for r in sec_rows}

        pos_theses = [dict(r) for r in conn.execute("""
            SELECT pt.thesis_id, pt.title, pt.conviction, pt.status,
                   it.title AS parent_title
            FROM theses pt
            LEFT JOIN theses it ON pt.parent_thesis_id = it.thesis_id
            WHERE pt.level = 'position'
            ORDER BY pt.title
        """).fetchall()]

    if sleeve_df.empty:
        return {
            "sleeve_df": sleeve_df,
            "saa_bands": saa_bands,
            "saa_targets_full": saa_targets_full,
            "ticker_to_sleeve": ticker_to_sleeve,
            "pos_theses": pos_theses,
            "prices": {},
            "portfolio_value": 0.0,
            "invested_value": 0.0,
            "spaxx_value": 0.0,
        }

    holdings = get_holdings_on_date(as_of)
    look_back = (date.fromisoformat(as_of) - timedelta(days=7)).isoformat()

    prices: dict[str, float] = {}
    for ticker in holdings.index:
        if ticker == "SPAXX":
            prices[ticker] = 1.0
            continue
        try:
            p = get_prices(ticker, look_back, as_of)
            if not p.empty:
                prices[ticker] = float(p["close"].ffill().iloc[-1])
        except Exception:
            pass

    # Phase 38a — strategic weights are ex-cash; the operational-cash figures
    # live on sleeve_df.attrs. invested_value (total − cash) is the base for all
    # weight×value and deployment-dollar math; spaxx_value is the funding float.
    portfolio_value = float(sleeve_df.attrs.get("total_value", sleeve_df["Market Value"].sum()))
    invested_value  = float(sleeve_df.attrs.get("invested_value", portfolio_value))
    spaxx_value     = float(sleeve_df.attrs.get("cash_mv", 0.0))

    return {
        "sleeve_df": sleeve_df,
        "saa_bands": saa_bands,
        "saa_targets_full": saa_targets_full,
        "ticker_to_sleeve": ticker_to_sleeve,
        "pos_theses": pos_theses,
        "prices": prices,
        "portfolio_value": portfolio_value,
        "invested_value": invested_value,
        "spaxx_value": spaxx_value,
    }


# ── Page ─────────────────────────────────────────────────────────────────────

st.title("Capital Deployment")
st.caption(
    "Deploy new contributions to maintain SAA policy · "
    "Correct band-breach drift · Execute via your broker"
)

with st.expander("How to read this page", expanded=False):
    st.markdown(
        "- **Deploy New Cash** — enter a contribution amount and see how it should be "
        "allocated across sleeves to close drift first, then distribute the remainder "
        "by SAA target weight. The Suggested \\$ column is editable — adjust amounts to "
        "match actual broker fills before clicking Execute and Log. Suggested Shares "
        "recompute automatically from the edited dollar amounts.\n"
        "- **Rebalancing Check** — detects when any sleeve has drifted outside its "
        "tolerance band (±3% for major equity sleeves, ±2% for smaller sleeves) and "
        "surfaces corrective buy suggestions. The table is read-only; execute the "
        "suggested trades directly via your broker.\n"
        "- **Rationale tags** — 'close drift' means the sleeve is below target and "
        "gets priority allocation; 'above target' means the sleeve has drifted high "
        "and receives no new cash; 'mixed' means the sleeve is below target and also "
        "receives a proportional residual share; 'maintain target' means no drift and "
        "the sleeve receives proportional contribution only.\n"
        "- **Execute and Log workflow** — edit the Suggested \\$ column to match actual "
        "broker fills, then click Execute and Log. A confirmation modal shows the final "
        "trade preview before writing to the Trade Log. In demo mode, Execute and Log "
        "is illustrative only and no writes occur."
    )

today = date.today().isoformat()
data = _load_data(today)
sleeve_df = data["sleeve_df"]

if sleeve_df.empty:
    st.info("No holdings found. Seed the database first.")
    render_footer()
    st.stop()

sleeve_weights   = dict(zip(sleeve_df.index, sleeve_df["Actual Weight"].values))
saa_targets      = data["saa_targets_full"]
saa_bands        = data["saa_bands"]
ticker_to_sleeve = data["ticker_to_sleeve"]
portfolio_value  = data["portfolio_value"]
invested_value   = data["invested_value"]
spaxx_value      = data["spaxx_value"]
prices           = data["prices"]
pos_theses       = data["pos_theses"]

# Ticker → thesis_id (for writing) and ticker → (pos_title, inv_title) (for display)
_thesis_id_lookup: dict[str, int | None] = build_thesis_lookup(pos_theses)
_thesis_title_lookup: dict[str, tuple[str, str]] = {}
for _pt in pos_theses:
    if _pt.get("status") != "active":
        continue
    _parts = (_pt.get("title") or "").rsplit(" — ", 1)
    if len(_parts) == 2:
        _sym = _parts[-1].strip()
        _thesis_title_lookup[_sym] = (_pt["title"], _pt.get("parent_title") or "—")


# ── Confirmation modal ────────────────────────────────────────────────────────

@st.dialog("Confirm trade log entries")
def _execute_confirm_dialog(
    trades_for_confirm: list[dict],
    expected_total: float,
    today_str: str,
) -> None:
    st.caption(
        f"These trades will be written to your Trade Log dated **{today_str}**. "
        "Edit prices or shares above before confirming if your actual fills differ "
        "from suggestions."
    )

    confirm_rows = []
    for t in trades_for_confirm:
        pos_title, inv_title = _thesis_title_lookup.get(
            t["ticker"], ("—", "—")
        )
        confirm_rows.append({
            "Ticker":            t["ticker"],
            "Sleeve":            ticker_to_sleeve.get(t["ticker"], "—"),
            "Shares":            round(t["shares"], 4),
            "Price":             round(t["price"], 2),
            "Total":             round(t["total_value"], 2),
            "Position Thesis":   pos_title,
            "Investment Thesis": inv_title,
        })
    confirm_df = pd.DataFrame(confirm_rows)
    total_row = pd.DataFrame([{
        "Ticker": "TOTAL", "Sleeve": "", "Shares": None,
        "Price": None,
        "Total": round(sum(t["total_value"] for t in trades_for_confirm), 2),
        "Position Thesis": "", "Investment Thesis": "",
    }])
    st.dataframe(
        pd.concat([confirm_df, total_row], ignore_index=True),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn(format="$%.2f"),
            "Total": st.column_config.NumberColumn(format="$%.2f"),
            "Shares": st.column_config.NumberColumn(format="%.4f"),
        },
    )

    col_ok, col_cancel = st.columns(2)
    with col_ok:
        if st.button("Confirm and log", type="primary", use_container_width=True):
            ok = write_trades_batch(trades_for_confirm, expected_total)
            if ok:
                n = len(trades_for_confirm)
                st.session_state["exec_success"] = (
                    f"{n} trade{'s' if n != 1 else ''} logged to Trade Log dated {today_str}"
                )
                st.session_state["contrib_cash"] = 0.0
            st.rerun()
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Deploy New Cash
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("Deploy New Cash")
st.caption(
    "Allocate new contributions across sleeves to maintain SAA policy. "
    "Suggestions close any drift first, then distribute the remainder by target weight."
)

if msg := st.session_state.pop("exec_success", None):
    st.success(msg)

contrib_cash = st.number_input(
    "New cash to deploy ($)",
    min_value=0.0,
    value=0.0,
    step=100.0,
    key="contrib_cash",
    help="New money being added to the portfolio. Does not include existing SPAXX balance.",
)

if contrib_cash > 0:
    orig_suggestions = suggest_contributions(
        portfolio_value=invested_value,
        cash_to_deploy=float(contrib_cash),
        sleeve_weights=sleeve_weights,
        saa_targets=saa_targets,
        ticker_to_sleeve=ticker_to_sleeve,
        prices=prices,
    )

    if orig_suggestions.empty:
        st.info("No investable sleeves with known prices — check the securities table.")
    else:
        # Editable table: user adjusts Suggested $ per ticker
        editor_cols = ["Ticker", "Sleeve", "Rationale", "Suggested $"]
        edited_df = st.data_editor(
            orig_suggestions[editor_cols],
            column_config={
                "Ticker":    st.column_config.TextColumn(disabled=True),
                "Sleeve":    st.column_config.TextColumn(disabled=True),
                "Rationale": st.column_config.TextColumn(disabled=True),
                "Suggested $": st.column_config.NumberColumn(
                    "Suggested $",
                    format="$%.2f",
                    min_value=0.0,
                    step=0.01,
                ),
            },
            use_container_width=True,
            hide_index=True,
            key="contrib_editor",
        )

        # Recompute shares from edited $ using original prices
        ticker_prices = dict(zip(orig_suggestions["Ticker"], orig_suggestions["Price"]))
        edited_df = edited_df.copy()
        edited_df["Price"] = edited_df["Ticker"].map(ticker_prices)
        edited_df["Suggested Shares"] = (
            edited_df["Suggested $"] / edited_df["Price"].replace(0, float("nan"))
        ).round(4)

        # ── Reconciliation panel ─────────────────────────────────────────────
        total_alloc = float(edited_df["Suggested $"].sum())
        diff = total_alloc - float(contrib_cash)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total allocated",  f"${total_alloc:,.2f}")
        c2.metric("Cash to deploy",   f"${contrib_cash:,.2f}")

        if abs(diff) < 0.01:
            c3.metric("Difference", "$0.00")
        elif diff > 0:
            c3.metric("Difference", f"${diff:,.2f} overallocated",
                      delta=f"+${diff:,.2f}", delta_color="inverse")
        else:
            c3.metric("Difference", f"${abs(diff):,.2f} unallocated",
                      delta=f"-${abs(diff):,.2f}", delta_color="off")

        # ── Execute and Log ──────────────────────────────────────────────────
        if not is_write_enabled():
            st.info(
                "Public demo: Execute and Log is illustrative only; "
                "trades are not written to the store."
            )

        shares_valid = bool((edited_df["Suggested Shares"].fillna(0) > 0).all())
        btn_disabled = (abs(diff) > SUM_INVARIANT_TOLERANCE) or not shares_valid
        if btn_disabled:
            if abs(diff) > SUM_INVARIANT_TOLERANCE:
                _btn_help = (
                    f"{'Overallocated' if diff > 0 else 'Underallocated'} by "
                    f"${abs(diff):,.2f} — reconciliation difference exceeds ${SUM_INVARIANT_TOLERANCE:.2f}; "
                    "adjust amounts above to match cash to deploy."
                )
            else:
                _btn_help = "One or more rows have zero or negative shares."
        else:
            _btn_help = (
                "Writes these trades to the Trade Log dated today. "
                "Edit amounts above to match your actual broker fills first."
            )

        st.caption(
            "Edit the Suggested \\$ column to match actual broker fills before "
            "executing. Suggested Shares recompute automatically from the edited amounts."
        )

        if st.button(
            "Execute and Log",
            disabled=btn_disabled,
            help=_btn_help,
            type="primary",
        ):
            _today = date.today()
            _trades_for_confirm = []
            for _, _row in edited_df.iterrows():
                _ticker = _row["Ticker"]
                _dollars = float(_row["Suggested $"])
                _shares  = float(_row["Suggested Shares"]) if float(_row["Suggested Shares"]) > 0 else 0.0
                _price   = (_dollars / _shares) if _shares > 0 else 0.0
                _trades_for_confirm.append({
                    "ticker":      _ticker,
                    "trade_date":  _today.isoformat(),
                    "action":      "Buy",
                    "shares":      _shares,
                    "price":       round(_price, 4),
                    "total_value": _dollars,
                    "thesis_id":   _thesis_id_lookup.get(_ticker),
                    "lot_source":  "Manual",
                })
            _execute_confirm_dialog(
                _trades_for_confirm,
                float(contrib_cash),
                _today.strftime("%B %d, %Y"),
            )

        st.caption(
            "Trades are dated today and written to the Trade Log. "
            "Edit the Suggested $ or Suggested Shares columns above to match "
            "your actual broker fills before confirming."
        )

        # ── Updated shares display ───────────────────────────────────────────
        with st.expander("Suggested Shares (recomputed from edited amounts)", expanded=True):
            shares_display = edited_df[["Ticker", "Sleeve", "Suggested $", "Suggested Shares", "Price"]].copy()
            st.dataframe(
                shares_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Suggested $":      st.column_config.NumberColumn("Suggested $",      format="$%.2f"),
                    "Price":            st.column_config.NumberColumn("Price",            format="$%.2f"),
                    "Suggested Shares": st.column_config.NumberColumn("Suggested Shares", format="%.4f"),
                },
            )

        # ── Post-deployment drift preview ────────────────────────────────────
        with st.expander("Post-deployment drift preview", expanded=False):
            # Map edited ticker allocations back to sleeve level
            sleeve_alloc_edited: dict[str, float] = {}
            for _, row in edited_df.iterrows():
                sleeve = ticker_to_sleeve.get(row["Ticker"], "")
                sleeve_alloc_edited[sleeve] = sleeve_alloc_edited.get(sleeve, 0.0) + float(row["Suggested $"])

            new_total = invested_value + total_alloc
            proj_rows = []
            for sleeve in sorted(saa_targets):
                if saa_targets.get(sleeve, 0.0) == 0:
                    continue
                current_mv = invested_value * sleeve_weights.get(sleeve, 0.0)
                added = sleeve_alloc_edited.get(sleeve, 0.0)
                projected_weight = (current_mv + added) / new_total if new_total > 0 else 0.0
                target = saa_targets[sleeve]
                proj_rows.append({
                    "Sleeve":    sleeve,
                    "Current":   sleeve_weights.get(sleeve, 0.0) * 100,
                    "Projected": projected_weight * 100,
                    "Target":    target * 100,
                    "Change":    (projected_weight - sleeve_weights.get(sleeve, 0.0)) * 100,
                })
            st.dataframe(
                pd.DataFrame(proj_rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Current":   st.column_config.NumberColumn("Current",   format="%.1f%%"),
                    "Projected": st.column_config.NumberColumn("Projected", format="%.1f%%"),
                    "Target":    st.column_config.NumberColumn("Target",    format="%.1f%%"),
                    "Change":    st.column_config.NumberColumn("Change",    format="%+.1f%%"),
                },
            )

        st.caption(
            "To overweight a sleeve persistently, revise SAA targets on the SAA page. "
            "Per-contribution overrides are appropriate for one-off tactical views; "
            "document rationale in the Trade Log."
        )
else:
    st.info("Enter a cash amount above to see contribution suggestions.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Rebalancing Check (band-breach correction)
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("Rebalancing Check")
st.caption(
    "Identify band-breach drift and correct it with new contributions. "
    "Routine contributions go in the section above."
)

with st.expander("Why this rebalancer is buy-only (tax-aware)", expanded=False):
    st.markdown(
        "In a taxable account this tool rebalances with **new contributions** — buying "
        "underweight sleeves — and deliberately does **not** suggest selling overweight "
        "sleeves. Selling to correct a tolerance-band drift realizes capital gains, and "
        "the tax cost of those gains typically exceeds the tracking error the trade would "
        "fix. Overweight drift is instead closed over time by directing new cash and "
        "dividends toward underweight sleeves; any active trimming is best done inside "
        "tax-advantaged accounts (IRA/401k), where it triggers no current tax. The "
        "buy-only design is a deliberate tax-aware choice, not a limitation — consistent "
        "with the same tax-drag and realized-gains discipline applied elsewhere in this "
        "tracker."
    )

drift_df = compute_drift(sleeve_weights, saa_targets, saa_bands)

# ── Drift table ──────────────────────────────────────────────────────────────

def _status(row: pd.Series) -> str:
    if row["In Band"]:
        return "✓ In band"
    return "▼ Under" if row["Drift"] < 0 else "▲ Over"

display = drift_df.reset_index().copy()
display["Status"]        = drift_df.reset_index().apply(_status, axis=1)
display["Band"]          = display["Band"].map("±{:.0%}".format)
display["Actual Weight"] = display["Actual Weight"] * 100
display["Target Weight"] = display["Target Weight"] * 100
display["Drift"]         = display["Drift"] * 100
display = display[["Sleeve", "Actual Weight", "Target Weight", "Band", "Drift", "Status"]]

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Actual Weight": st.column_config.NumberColumn("Actual Weight", format="%.1f%%"),
        "Target Weight": st.column_config.NumberColumn("Target Weight", format="%.1f%%"),
        "Drift":         st.column_config.NumberColumn("Drift",         format="%+.1f%%"),
    },
)

n_outside    = int((~drift_df["In Band"]).sum())
_status_line = interpret_rebalance_status(drift_df)

if n_outside == 0:
    # Calm state — interpret_rebalance_status names the closest-to-breach sleeve
    # and its remaining headroom, so "all in band" is still informative.
    st.success(_status_line)
else:
    st.warning(_status_line)
    # Per-sleeve tax-aware corrective action for each out-of-band sleeve.
    for _sleeve, _row in drift_df[~drift_df["In Band"]].iterrows():
        st.caption(f"**{_sleeve}** · {rebalance_action_text(_row)}")

# ── Band-breach buy suggestions ───────────────────────────────────────────────

rebal_cash = st.number_input(
    "Cash for corrective rebalancing ($)",
    min_value=0.0,
    value=0.0,
    step=100.0,
    key="rebal_cash",
    help=(
        f"Cash for band-breach correction only. "
        f"Current SPAXX balance: ${spaxx_value:,.2f}. "
        "Routine contributions go in the section above."
    ),
)

if rebal_cash > 0:
    buy_df = suggest_buys(
        drift_df=drift_df,
        portfolio_value=invested_value,
        cash_to_deploy=float(rebal_cash),
        ticker_to_sleeve=ticker_to_sleeve,
        prices=prices,
    )

    if buy_df.empty:
        st.info("All sleeves within tolerance bands. No rebalancing required.")
    else:
        total_suggested = float(buy_df["Suggested $"].sum())
        leftover        = float(rebal_cash) - total_suggested

        st.dataframe(
            buy_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Price":            st.column_config.NumberColumn("Price",            format="$%.2f"),
                "Suggested $":      st.column_config.NumberColumn("Suggested $",      format="$%.2f"),
                "Suggested Shares": st.column_config.NumberColumn("Suggested Shares", format="%.4f"),
            },
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Total suggested", f"${total_suggested:,.2f}")
        c2.metric("Cash input",       f"${rebal_cash:,.2f}")
        c3.metric("Undeployed",       f"${leftover:,.2f}")

        if leftover > 0.01:
            st.caption(
                f"${leftover:,.2f} undeployed — all band-breach shortfalls fully filled. "
                "Hold remaining cash or add it to the most underweight sleeve manually."
            )

        st.caption(
            "Prices based on most recent close. Execute via your broker. "
            "Shares may need to be rounded to whole numbers depending on your broker."
        )
else:
    st.info("Enter a cash amount above to see band-breach buy suggestions.")

with st.expander("Methodology", expanded=False):
    # Bands are RENDERED from asset_classes rather than described in prose. A
    # hand-written rule ("±3% at or above 10%") was false for Real Assets and
    # Income, which sit at 10.2% with a deliberate ±2% band, and any restatement
    # re-stales the moment a sleeve crosses the threshold. This table reads the
    # same column compute_drift applies, so the two cannot disagree.
    st.markdown(
        "**Tiered SAA bands** — larger sleeves carry wider tolerance bands. Each band "
        "below is read from the database alongside its target, so this table cannot "
        "drift from the bands the rebalancing check actually applies. Tighter bands on "
        "smaller sleeves prevent small absolute drift from compounding into meaningful "
        "tracking error against the SAA."
    )
    _band_tbl = pd.DataFrame(
        [
            {
                "Sleeve":         _s,
                "SAA target":     saa_targets[_s] * 100,
                "Tolerance band": saa_bands[_s] * 100,
            }
            for _s in sorted(saa_targets, key=lambda n: -saa_targets[n])
            if saa_targets.get(_s, 0.0) > 0 and _s in saa_bands
        ]
    )
    st.dataframe(
        _band_tbl,
        hide_index=True,
        use_container_width=True,
        column_config={
            "SAA target":     st.column_config.NumberColumn(format="%.2f%%"),
            "Tolerance band": st.column_config.NumberColumn(format="±%.0f%%"),
        },
    )
    st.markdown(
        "**Contribution priority** — cash flows to below-band sleeves first (close drift), "
        "then to in-band sleeves by SAA target weight (maintain allocation). Above-band "
        "sleeves receive no new cash until they return to band naturally or are "
        "rebalanced.\n\n"
        "**Band-breach detection** — a sleeve is in breach if |actual weight − target "
        "weight| > tolerance band. Rebalancing buy suggestions in the Rebalancing Check "
        "section are sized to return the sleeve to its target weight, not to its band "
        "edge. The Rebalancing Check table is read-only; execute suggested trades via "
        "your broker.\n\n"
        "**Editable suggestions** — the Suggested \\$ column in Deploy New Cash is "
        "editable. After editing, the Total allocated / Difference reconciliation panel "
        "updates immediately to flag any mismatch against the entered cash amount. "
        "Suggested Shares recompute from the edited dollar amounts using current prices.\n\n"
        "**Execute and Log mechanics** — clicking Execute and Log writes the "
        "suggested (or edited) trades to the Trade Log via the same lot-tracking system "
        "used for direct trade entry, with lot_source='Manual' to distinguish from DRIP "
        "reinvestments (lot_source='drip') and inception buys (lot_source='inception')."
    )

render_footer()
