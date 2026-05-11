"""Rebalancing page — buy-only, cash-deploy mode."""
import streamlit as st

st.set_page_config(page_title="Rebalance", layout="wide")

import pandas as pd
from datetime import date, timedelta

from src.db import get_connection
from src.holdings import get_sleeve_weights_on_date, get_holdings_on_date
from src.prices import get_prices
from src.rebalance import compute_drift, suggest_buys
from src.ui_helpers import render_footer


@st.cache_data(ttl=3600, show_spinner=False)
def _load_data(as_of: str) -> dict:
    sleeve_df = get_sleeve_weights_on_date(as_of)

    with get_connection() as conn:
        band_rows = conn.execute(
            "SELECT name, tolerance_band FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
        saa_bands = {r["name"]: float(r["tolerance_band"]) for r in band_rows}

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
        "ticker_to_sleeve": ticker_to_sleeve,
        "prices": prices,
        "portfolio_value": portfolio_value,
        "spaxx_value": spaxx_value,
    }


# ── Page ─────────────────────────────────────────────────────────────────────

st.title("Rebalancing Tool")
st.caption("Buy-only · Deploy cash to underweight sleeves · Execute via your broker")

today = date.today().isoformat()
data = _load_data(today)
sleeve_df = data["sleeve_df"]

if sleeve_df.empty:
    st.info("No holdings found. Seed the database first.")
    render_footer()
    st.stop()

sleeve_weights = dict(zip(sleeve_df.index, sleeve_df["Actual Weight"].values))
saa_targets    = dict(zip(sleeve_df.index, sleeve_df["Target Weight"].values))
saa_bands      = data["saa_bands"]
portfolio_value = data["portfolio_value"]
spaxx_value     = data["spaxx_value"]

drift_df = compute_drift(sleeve_weights, saa_targets, saa_bands)

# ── Drift table ──────────────────────────────────────────────────────────────

st.subheader("Current Drift")

def _status(row: pd.Series) -> str:
    if row["In Band"]:
        return "✓ In band"
    return "▼ Under" if row["Drift"] < 0 else "▲ Over"

display = drift_df.reset_index().copy()
display["Status"] = drift_df.reset_index().apply(_status, axis=1)
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

st.divider()

# ── Buy suggestions ───────────────────────────────────────────────────────────

st.subheader("Suggest Buys")

cash_input = st.number_input(
    "Cash to deploy ($)",
    min_value=0.0,
    max_value=float(spaxx_value),
    value=float(spaxx_value),
    step=100.0,
    help=f"Current SPAXX balance: ${spaxx_value:,.2f}",
)

if cash_input > 0:
    buy_df = suggest_buys(
        drift_df=drift_df,
        portfolio_value=portfolio_value,
        cash_to_deploy=cash_input,
        ticker_to_sleeve=data["ticker_to_sleeve"],
        prices=data["prices"],
    )

    if buy_df.empty:
        st.info("All sleeves within tolerance bands. No rebalancing required.")
    else:
        total_suggested = float(buy_df["Suggested $"].sum())
        leftover        = cash_input - total_suggested

        fmt = buy_df.copy()
        fmt["Price"]            = fmt["Price"].map("${:,.2f}".format)
        fmt["Suggested $"]      = fmt["Suggested $"].map("${:,.2f}".format)
        fmt["Suggested Shares"] = fmt["Suggested Shares"].map("{:.4f}".format)

        st.dataframe(fmt, use_container_width=True, hide_index=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total suggested",  f"${total_suggested:,.2f}")
        c2.metric("Cash input",        f"${cash_input:,.2f}")
        c3.metric("Undeployed",        f"${leftover:,.2f}")

        if leftover > 0.01:
            st.caption(
                f"${leftover:,.2f} undeployed — all underweight shortfalls are fully filled. "
                "Hold remaining cash or add it to the most underweight sleeve manually."
            )

        st.caption(
            "Prices based on most recent close. Execute via your broker. "
            "Shares may need to be rounded to whole numbers depending on your broker."
        )
else:
    st.info("Enter a cash amount above to see buy suggestions.")

render_footer()
