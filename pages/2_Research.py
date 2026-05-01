"""Research page — Security Research."""
import streamlit as st
import pandas as pd
from collections import defaultdict
from src.db import get_connection

st.set_page_config(page_title="Research", layout="wide")

SPAXX_RATIONALE = (
    "SPAXX is Fidelity's default money market fund and the natural cash vehicle — no transaction "
    "cost, immediate liquidity, currently ~4-5% yield. BIL exists only for attribution comparison. "
    "The 3% cash weight is operational liquidity: it funds rebalancing trades, covers small drawdowns "
    "without forced selling, and buffers position friction. Would reduce toward 1% if short rates fall "
    "materially below 2%."
)


@st.cache_data
def load_research_data():
    with get_connection() as conn:
        sleeves = [dict(r) for r in conn.execute("""
            SELECT ac.asset_class_id, ac.name, ac.target_weight, ac.benchmark_ticker,
                   p.name AS parent_name, p.target_weight AS parent_weight
            FROM asset_classes ac
            JOIN asset_classes p ON ac.parent_id = p.asset_class_id
            ORDER BY p.target_weight DESC, ac.target_weight DESC
        """).fetchall()]
        secs = [dict(r) for r in conn.execute("""
            SELECT ticker, name, expense_ratio, security_type, holding_rationale, asset_class_id
            FROM securities
        """).fetchall()]
    return sleeves, secs


def _er_str(er):
    return f"{er * 100:.2f}%" if er is not None else "—"


def _pair_benchmarks_holdings(holdings, benchmarks, bm_ticker_str):
    """
    Pair each holding with its benchmark.
    If a holding's ticker appears in the sleeve's benchmark_ticker string but has no
    separate benchmark row (e.g. VNQ is both holding and benchmark), mark as self-benchmarked.
    """
    sleeve_bm_tickers = set((bm_ticker_str or "").split("+"))
    bm_by_ticker = {b["ticker"]: b for b in benchmarks}
    used = set()
    pairs = []

    for h in holdings:
        if h["ticker"] in sleeve_bm_tickers and h["ticker"] not in bm_by_ticker:
            pairs.append({"benchmark": h, "holding": h, "self_bm": True})
        else:
            explicit = next(
                (b for b in benchmarks if b["ticker"] not in used),
                None,
            )
            if explicit:
                used.add(explicit["ticker"])
            pairs.append({"benchmark": explicit, "holding": h, "self_bm": False})

    return pairs


def _comparison_df(bm, h, self_bm=False):
    bm_ticker = f"{bm['ticker']} (= holding)" if self_bm else (bm["ticker"] if bm else "—")
    bm_name   = bm["name"] if bm else "—"
    bm_er     = _er_str(bm.get("expense_ratio") if bm else None)

    return pd.DataFrame(
        {
            "Benchmark":       [bm_ticker, bm_name, bm_er],
            "Selected Holding": [h["ticker"], h["name"], _er_str(h.get("expense_ratio"))],
        },
        index=["Ticker", "Name", "Expense Ratio"],
    )


sleeves, secs = load_research_data()

holdings_by_class   = defaultdict(list)
benchmarks_by_class = defaultdict(list)
for s in secs:
    if s["security_type"] == "ETF":
        holdings_by_class[s["asset_class_id"]].append(s)
    else:
        benchmarks_by_class[s["asset_class_id"]].append(s)

# Weighted avg ER — Cash/SPAXX has no ETF entry so contributes 0 naturally
weighted_er = sum(
    sleeve["target_weight"] / len(holdings_by_class[sleeve["asset_class_id"]]) * h["expense_ratio"]
    for sleeve in sleeves
    for h in holdings_by_class[sleeve["asset_class_id"]]
    if h.get("expense_ratio") is not None
)

# ── Header ─────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Security Research")
    st.caption("Candidate ETFs by asset class — holdings vs. benchmarks")
    er_pct = weighted_er * 100
    er_bps = weighted_er * 10_000
    st.markdown(f"Blended weighted average ER: **{er_pct:.2f}%** ({er_bps:.1f} bps)")
    st.divider()

# ── Sleeve sections ─────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    current_parent = None

    for sleeve in sleeves:
        sid      = sleeve["asset_class_id"]
        holdings  = holdings_by_class[sid]
        benchmarks = benchmarks_by_class[sid]
        is_cash  = sleeve["name"] == "Cash / SPAXX"
        pct      = round(sleeve["target_weight"] * 100, 1)
        bm_str   = sleeve["benchmark_ticker"] or "—"

        if sleeve["parent_name"] != current_parent:
            if current_parent is not None:
                st.divider()
            st.subheader(sleeve["parent_name"])
            current_parent = sleeve["parent_name"]

        st.markdown(
            f"##### {sleeve['name']} &nbsp;"
            f"<span style='font-weight:400; font-size:0.875rem; color:#666'>"
            f"{pct}% target &nbsp;·&nbsp; benchmark: {bm_str}</span>",
            unsafe_allow_html=True,
        )

        if is_cash:
            bm = benchmarks[0] if benchmarks else None
            left, right = st.columns(2)
            with left:
                st.markdown("**Benchmark**")
                if bm:
                    st.markdown(f"**{bm['ticker']}** — {bm['name']}")
                    st.caption(f"ER: {_er_str(bm.get('expense_ratio'))}")
            with right:
                st.markdown("**Selected Holding**")
                st.markdown("**SPAXX** — Fidelity Government Money Market Fund")
                st.caption("ER: ~0.00%  ·  yield: ~4–5%  ·  type: money market")
            with st.expander("SPAXX — rationale"):
                st.markdown(SPAXX_RATIONALE)

        else:
            pairs = _pair_benchmarks_holdings(holdings, benchmarks, bm_str)
            for i, p in enumerate(pairs):
                bm, h = p["benchmark"], p["holding"]
                if len(pairs) > 1:
                    st.caption(f"{'50% REITs' if i == 0 else '50% Commodities'}")
                df = _comparison_df(bm, h, self_bm=p["self_bm"])
                st.dataframe(df, use_container_width=True)
                if h.get("holding_rationale"):
                    with st.expander(f"{h['ticker']} — rationale"):
                        st.markdown(h["holding_rationale"])

        st.markdown("")

    st.divider()
    st.info("Prices and live performance data coming in Phase 4.")
