"""Research page — Security Research."""
import streamlit as st

st.set_page_config(page_title="Research", layout="wide")

import pandas as pd
from collections import defaultdict
from src.asof import as_of_banner
from src.config import get_demo_banner_text, IS_DEMO
from src.db import get_connection
from src.ui_helpers import render_footer, render_sidebar_header, render_sidebar_footer

render_sidebar_header()

SPAXX_RATIONALE = (
    "SPAXX is Fidelity's default money market fund and the natural cash vehicle — no transaction "
    "cost, immediate liquidity, currently ~4-5% yield. BIL exists only for attribution comparison. "
    "The 3% cash weight is operational liquidity: it funds rebalancing trades, covers small drawdowns "
    "without forced selling, and buffers position friction. Would reduce toward 1% if short rates fall "
    "materially below 2%."
)

SPAXX_HOLDING = {
    "ticker": "SPAXX",
    "name": "Fidelity Government Money Market Fund",
    "expense_ratio": None,
    "_er_display": "n/a (money market)",
    "security_type": "Money Market Fund",
    "holding_rationale": SPAXX_RATIONALE,
}


def load_research_data():
    with get_connection() as conn:
        sleeves = [dict(r) for r in conn.execute("""
            SELECT ac.asset_class_id, ac.name, ac.target_weight, ac.benchmark_ticker,
                   p.name AS parent_name, p.target_weight AS parent_weight
            FROM asset_classes ac
            JOIN asset_classes p ON ac.parent_id = p.asset_class_id
            ORDER BY p.target_weight DESC, COALESCE(ac.sort_order, 999) ASC
        """).fetchall()]
        secs = [dict(r) for r in conn.execute("""
            SELECT ticker, name, expense_ratio, security_type, holding_rationale, asset_class_id
            FROM securities
        """).fetchall()]
    return sleeves, secs


def _safe_md(text):
    if text:
        st.markdown(text.replace("$", r"\$"))


def _er_str(er, display_override=None):
    if display_override is not None:
        return display_override
    return f"{er * 100:.2f}%" if er is not None else "—"


def _savings_caption(bm_er, h_er):
    if bm_er is None:
        return None
    h_val = h_er if h_er is not None else 0.0
    bps = round((bm_er - h_val) * 10_000)
    if bps == 0:
        return "Holding matches benchmark"
    elif bps > 0:
        return f"Holding saves {bps} bps annually vs. benchmark"
    else:
        return f"Holding costs {abs(bps)} bps more than benchmark"


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


def _comparison_df(bm, h, self_bm=False, show_type=False):
    bm_ticker = bm["ticker"] if bm else "—"
    bm_name   = bm["name"] if bm else "—"
    bm_er     = _er_str(bm.get("expense_ratio") if bm else None)

    h_er_display = h.get("_er_display")
    cols = {
        "Benchmark":        [bm_ticker, bm_name, bm_er],
        "Selected Holding": [h["ticker"], h["name"], _er_str(h.get("expense_ratio"), h_er_display)],
    }
    idx = ["Ticker", "Name", "Expense Ratio"]

    if show_type:
        cols["Benchmark"].append((bm.get("security_type") or "—") if bm else "—")
        cols["Selected Holding"].append(h.get("security_type") or "—")
        idx.append("Type")

    return pd.DataFrame(cols, index=idx)


sleeves, secs = load_research_data()

holdings_by_class   = defaultdict(list)
benchmarks_by_class = defaultdict(list)
for s in secs:
    if s["security_type"] == "ETF":
        holdings_by_class[s["asset_class_id"]].append(s)
    else:
        benchmarks_by_class[s["asset_class_id"]].append(s)

# Pre-compute all sleeve/pair data (Cash uses synthetic SPAXX_HOLDING)
sleeve_data = []
for sleeve in sleeves:
    sid = sleeve["asset_class_id"]
    if sleeve["name"] == "Cash / SPAXX":
        bm = benchmarks_by_class[sid][0] if benchmarks_by_class[sid] else None
        pairs = [{"benchmark": bm, "holding": SPAXX_HOLDING, "self_bm": False}]
    else:
        pairs = _pair_benchmarks_holdings(
            holdings_by_class[sid],
            benchmarks_by_class[sid],
            sleeve["benchmark_ticker"] or "",
        )
    sleeve_data.append({"sleeve": sleeve, "pairs": pairs})

# Summary stats
n_holdings = sum(len(d["pairs"]) for d in sleeve_data)
weighted_er = 0.0
portfolio_savings_bps = 0.0
for d in sleeve_data:
    n = len(d["pairs"])
    w_per = d["sleeve"]["target_weight"] / n
    for p in d["pairs"]:
        h_er  = p["holding"].get("expense_ratio")
        bm_er = p["benchmark"].get("expense_ratio") if p["benchmark"] else None
        if h_er is not None:
            weighted_er += w_per * h_er
        if bm_er is not None:
            portfolio_savings_bps += w_per * (bm_er - (h_er or 0.0)) * 10_000

if IS_DEMO:
    st.info(get_demo_banner_text())

# ── Header ─────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Security Research")
    st.caption("Candidate ETFs by asset class — holdings vs. benchmarks")
    st.caption(as_of_banner())
    er_pct = weighted_er * 100
    _savings_250k = round(portfolio_savings_bps / 10_000 * 250_000)
    st.caption(
        f"{len(sleeves)} sleeves  ·  {n_holdings} holdings  ·  "
        f"{er_pct:.2f}% blended ER  ·  {portfolio_savings_bps:.0f} bps savings vs. benchmarks  ·  "
        f"~\\${_savings_250k:,}/yr in ER savings at \\$250k"
    )
    st.divider()

    # ── Featured Selections ──────────────────────────────────────────────────────
    st.subheader("Featured Selections")
    st.caption(
        "Three holdings where the case for diverging from the benchmark is most compelling."
    )

    _featured_meta = {
        "SPHQ": {
            "bench":    "QUAL",
            "headline": "Accruals screen filters accounting manipulation that QUAL misses.",
        },
        "IEMG": {
            "bench":    "EEM",
            "headline": "Identical EM exposure at 0.09% vs EEM's 0.70% — 61 bps of pure fee drag eliminated.",
        },
        "PDBC": {
            "bench":    "DJP",
            "headline": "Only broad commodity ETF with no K-1 in a taxable account; C-corp structure avoids partnership filing complexity.",
        },
    }
    _sec_by_ticker = {s["ticker"]: s for s in secs}

    _fc1, _fc2, _fc3 = st.columns(3)
    for _col, _ticker in zip([_fc1, _fc2, _fc3], ["SPHQ", "IEMG", "PDBC"]):
        _meta  = _featured_meta[_ticker]
        _sec   = _sec_by_ticker.get(_ticker, {})
        _er    = _sec.get("expense_ratio")
        _er_s  = f"{_er * 100:.2f}%" if _er is not None else "—"
        with _col:
            st.markdown(
                f"**{_ticker}** <span style='color:#666;font-size:0.85rem'>vs {_meta['bench']}</span>",
                unsafe_allow_html=True,
            )
            if _ticker == "PDBC":
                st.caption("Most distinctive rationale")
            st.caption(f"ER: {_er_s}")
            st.caption(_meta["headline"])
            if _sec.get("holding_rationale"):
                with st.expander("Full rationale"):
                    _safe_md(_sec["holding_rationale"])

    st.divider()

# ── Sleeve sections ─────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    current_parent = None

    for d in sleeve_data:
        sleeve = d["sleeve"]
        pairs  = d["pairs"]
        pct    = round(sleeve["target_weight"] * 100, 1)
        bm_str = sleeve["benchmark_ticker"] or "—"

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

        is_cash = sleeve["name"] == "Cash / SPAXX"
        for i, p in enumerate(pairs):
            bm, h = p["benchmark"], p["holding"]
            if len(pairs) > 1:
                st.caption(f"{'REITs (50% of sleeve)' if i == 0 else 'Commodities (50% of sleeve)'}")
            df = _comparison_df(bm, h, self_bm=p["self_bm"], show_type=is_cash)
            st.dataframe(df, width='stretch')

            caption = _savings_caption(
                bm.get("expense_ratio") if bm else None,
                h.get("expense_ratio"),
            )
            if caption:
                st.caption(f"· {caption}")

            if h.get("holding_rationale"):
                with st.expander(f"{h['ticker']} — rationale"):
                    _safe_md(h["holding_rationale"])

        st.markdown("")

    st.divider()
    render_footer()
render_sidebar_footer()
