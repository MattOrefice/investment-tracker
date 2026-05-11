"""Capital deployment page — contribution allocation and band-breach rebalancing."""
import streamlit as st

st.set_page_config(page_title="Capital Deployment", layout="wide")

import pandas as pd
from datetime import date, timedelta

from src.db import get_connection
from src.holdings import get_sleeve_weights_on_date, get_holdings_on_date
from src.prices import get_prices
from src.rebalance import compute_drift, suggest_buys, suggest_contributions
from src.ui_helpers import render_footer


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

    if sleeve_df.empty:
        return {
            "sleeve_df": sleeve_df,
            "saa_bands": saa_bands,
            "saa_targets_full": saa_targets_full,
            "ticker_to_sleeve": ticker_to_sleeve,
            "prices": {},
            "portfolio_value": 0.0,
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

    portfolio_value = float(sleeve_df["Market Value"].sum())
    spaxx_value = (
        float(sleeve_df.loc["Cash / SPAXX", "Market Value"])
        if "Cash / SPAXX" in sleeve_df.index
        else 0.0
    )

    return {
        "sleeve_df": sleeve_df,
        "saa_bands": saa_bands,
        "saa_targets_full": saa_targets_full,
        "ticker_to_sleeve": ticker_to_sleeve,
        "prices": prices,
        "portfolio_value": portfolio_value,
        "spaxx_value": spaxx_value,
    }


# ── Page ─────────────────────────────────────────────────────────────────────

st.title("Capital Deployment")
st.caption(
    "Deploy new contributions to maintain SAA policy · "
    "Correct band-breach drift · Execute via your broker"
)

today = date.today().isoformat()
data = _load_data(today)
sleeve_df = data["sleeve_df"]

if sleeve_df.empty:
    st.info("No holdings found. Seed the database first.")
    render_footer()
    st.stop()

sleeve_weights  = dict(zip(sleeve_df.index, sleeve_df["Actual Weight"].values))
saa_targets     = data["saa_targets_full"]
saa_bands       = data["saa_bands"]
ticker_to_sleeve = data["ticker_to_sleeve"]
portfolio_value = data["portfolio_value"]
spaxx_value     = data["spaxx_value"]
prices          = data["prices"]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Deploy New Cash
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("Deploy New Cash")
st.caption(
    "Allocate new contributions across sleeves to maintain SAA policy. "
    "Suggestions close any drift first, then distribute the remainder by target weight."
)

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
        portfolio_value=portfolio_value,
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
                    step=1.0,
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

        # ── Updated shares display ───────────────────────────────────────────
        with st.expander("Suggested Shares (recomputed from edited amounts)", expanded=True):
            shares_display = edited_df[["Ticker", "Sleeve", "Suggested $", "Suggested Shares", "Price"]].copy()
            shares_display["Suggested $"] = shares_display["Suggested $"].map("${:,.2f}".format)
            shares_display["Price"]       = shares_display["Price"].map("${:,.2f}".format)
            shares_display["Suggested Shares"] = shares_display["Suggested Shares"].map("{:.4f}".format)
            st.dataframe(shares_display, use_container_width=True, hide_index=True)

        # ── Post-deployment drift preview ────────────────────────────────────
        with st.expander("Post-deployment drift preview", expanded=False):
            # Map edited ticker allocations back to sleeve level
            sleeve_alloc_edited: dict[str, float] = {}
            for _, row in edited_df.iterrows():
                sleeve = ticker_to_sleeve.get(row["Ticker"], "")
                sleeve_alloc_edited[sleeve] = sleeve_alloc_edited.get(sleeve, 0.0) + float(row["Suggested $"])

            new_total = portfolio_value + total_alloc
            proj_rows = []
            for sleeve in sorted(saa_targets):
                if saa_targets.get(sleeve, 0.0) == 0:
                    continue
                current_mv = portfolio_value * sleeve_weights.get(sleeve, 0.0)
                added = sleeve_alloc_edited.get(sleeve, 0.0)
                projected_weight = (current_mv + added) / new_total if new_total > 0 else 0.0
                target = saa_targets[sleeve]
                proj_rows.append({
                    "Sleeve":    sleeve,
                    "Current":   f"{sleeve_weights.get(sleeve, 0.0):.1%}",
                    "Projected": f"{projected_weight:.1%}",
                    "Target":    f"{target:.1%}",
                    "Change":    f"{projected_weight - sleeve_weights.get(sleeve, 0.0):+.1%}",
                })
            st.dataframe(pd.DataFrame(proj_rows), use_container_width=True, hide_index=True)

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
    "Identify and correct band-breach drift. "
    "Routine contributions go in the section above."
)

drift_df = compute_drift(sleeve_weights, saa_targets, saa_bands)

# ── Drift table ──────────────────────────────────────────────────────────────

def _status(row: pd.Series) -> str:
    if row["In Band"]:
        return "✓ In band"
    return "▼ Under" if row["Drift"] < 0 else "▲ Over"

display = drift_df.reset_index().copy()
display["Status"]        = drift_df.reset_index().apply(_status, axis=1)
display["Actual Weight"] = display["Actual Weight"].map("{:.1%}".format)
display["Target Weight"] = display["Target Weight"].map("{:.1%}".format)
display["Band"]          = display["Band"].map("±{:.0%}".format)
display["Drift"]         = display["Drift"].map("{:+.1%}".format)
display = display[["Sleeve", "Actual Weight", "Target Weight", "Band", "Drift", "Status"]]

st.dataframe(display, use_container_width=True, hide_index=True)

n_outside = int((~drift_df["In Band"]).sum())
n_under   = int((drift_df["Drift"] < 0).sum())

if n_outside == 0:
    st.success("All sleeves within tolerance bands.")
else:
    msg = f"{n_outside} sleeve{'s' if n_outside != 1 else ''} outside tolerance band"
    if n_under:
        msg += f" · {n_under} underweight"
    st.warning(msg)

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
        portfolio_value=portfolio_value,
        cash_to_deploy=float(rebal_cash),
        ticker_to_sleeve=ticker_to_sleeve,
        prices=prices,
    )

    if buy_df.empty:
        st.info("All sleeves within tolerance bands. No rebalancing required.")
    else:
        total_suggested = float(buy_df["Suggested $"].sum())
        leftover        = float(rebal_cash) - total_suggested

        fmt = buy_df.copy()
        fmt["Price"]            = fmt["Price"].map("${:,.2f}".format)
        fmt["Suggested $"]      = fmt["Suggested $"].map("${:,.2f}".format)
        fmt["Suggested Shares"] = fmt["Suggested Shares"].map("{:.4f}".format)

        st.dataframe(fmt, use_container_width=True, hide_index=True)

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

render_footer()
