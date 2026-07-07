"""Benchmark Attribution — Portfolio vs Custom Blended SAA Regression."""
import logging
import streamlit as st
from datetime import date

import pandas as pd

st.set_page_config(page_title="Benchmark Attribution", layout="wide")

from src.asof import as_of_banner
from src.attribution import benchmark_gap_notice, brinson_fachler_period, price_gap_notice
from src.config import get_demo_banner_text, IS_DEMO
from src.holdings import get_inception_date
from src.factors import (
    alpha_ci_str,
    build_benchmark_methodology,
    build_benchmark_prose,
    interpret_benchmark_attribution,
    run_benchmark_attribution_regression,
    sig_marker,
)
from src.reports import build_bf_cross_reference
from src.ui_helpers import render_footer, render_page_header
render_page_header()


if IS_DEMO:
    st.info(get_demo_banner_text())

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Benchmark Attribution")
    st.caption(
        "Portfolio vs Custom Blended SAA Benchmark · "
        "Newey-West HAC standard errors, daily excess returns since inception."
    )
    st.caption(as_of_banner())
    st.divider()

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "This page is **distinct from the Brinson-Fachler attribution** on the Performance "
            "page. BF decomposes active return into allocation and selection effects per sleeve — "
            "*what weights did we hold, and did our ETF choices beat their sleeve benchmarks?*\n\n"
            "This page asks a different question: *what factor tilts explain portfolio performance "
            "relative to the SAA policy benchmark?* The model is:\n\n"
            "> **R_portfolio − RF = α + β₁·(R_benchmark − RF) + β₂·HML + β₃·SMB + β₄·RMW + ε**\n\n"
            "**R_benchmark − RF** is the custom SAA-target-weighted blended benchmark excess "
            "return (not the S&P 500). **HML, SMB, RMW** are Fama-French style factors. "
            "**Alpha (α)** is the active return unexplained by benchmark beta or factor tilts — "
            "the institutional alpha definition used in endowment and IDD contexts.\n\n"
            "CMA is excluded for this passive/semi-passive implementation — see Methodology for details."
        )

    end_date  = date.today().isoformat()
    inception = get_inception_date()

    @st.cache_data(ttl=3600)
    def _get_benchmark_result(inception_date: str, end: str) -> dict | None:
        return run_benchmark_attribution_regression(inception_date, end)

    try:
        result = _get_benchmark_result(inception, end_date)
    except Exception:
        logging.exception("Benchmark attribution regression failed")
        st.error("Benchmark attribution regression unavailable — please try again later.")
        st.stop()

    if result is None:
        st.info(
            "Insufficient data for regression — requires at least 30 aligned trading days. "
            "Section will populate as portfolio history grows."
        )
        st.stop()

    # ── Factor definitions panel ─────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**Factor Definitions**")
        st.markdown(
            "| Factor | Definition |\n"
            "|--------|------------|\n"
            "| **Bench-RF** | Custom SAA-target-weighted blended benchmark excess return (vs risk-free) |\n"
            "| **HML** | High Minus Low — value minus growth (Fama-French) |\n"
            "| **SMB** | Small Minus Big — small-cap minus large-cap returns |\n"
            "| **RMW** | Robust Minus Weak — high-profitability minus low-profitability |"
        )
        st.caption(
            "This regression isolates *residual style tilts beyond the SAA policy benchmark*. "
            "It is a selection-and-intra-sleeve-tilt model, not a Brinson-Fachler "
            "allocation/selection decomposition — that lives on the Performance page."
        )
    st.divider()

    _BENCH_FACTORS = ["Bench-RF", "HML", "SMB", "RMW"]

    # ── Regression table ──────────────────────────────────────────────────────
    table_rows = []
    p_a = result["p_alpha"]
    table_rows.append({
        "Factor":       "Alpha (annualized)",
        "Loading (β)":  alpha_ci_str(result),
        "t-stat":       f"{result['t_alpha']:.2f}",
        "p-value":      f"{p_a:.3f}",
        "Significance": sig_marker(p_a),
    })
    for f in _BENCH_FACTORS:
        p = result["p_values"][f]
        table_rows.append({
            "Factor":       f,
            "Loading (β)":  f"{result['betas'][f]:.3f}",
            "t-stat":       f"{result['t_stats'][f]:.2f}",
            "p-value":      f"{p:.3f}",
            "Significance": sig_marker(p),
        })

    st.dataframe(
        pd.DataFrame(table_rows).set_index("Factor"),
        width='stretch',
    )
    st.caption(
        "* p < 0.10 &nbsp; ** p < 0.05 &nbsp; *** p < 0.01 &nbsp;|&nbsp; "
        "Standard errors: Newey-West HAC"
    )

    # ── Fit statistics ────────────────────────────────────────────────────────
    d_start = date.fromisoformat(result["sample_start"])
    d_end   = date.fromisoformat(result["sample_end"])
    window_str = (
        f"{d_start.strftime('%B')} {d_start.day}, {d_start.year} — "
        f"{d_end.strftime('%B')} {d_end.day}, {d_end.year}"
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("R²",           f"{result['r_squared']:.3f}")
    c2.metric("Adj. R²",      f"{result['adj_r_squared']:.3f}")
    c3.metric("Observations", str(result["T"]))
    c4.metric("NW Lags (L)",  str(result["nw_lags"]))
    st.caption(f"Sample window: {window_str}")
    _lag_days = (date.today() - d_end).days
    st.caption(
        f"Regression window ends at the most recent date with published "
        f"Fama-French factor data ({d_end.strftime('%B')} {d_end.day}, "
        f"{d_end.year}) — a {_lag_days}-calendar-day publication lag; "
        f"live prices through {date.today().strftime('%B')} "
        f"{date.today().day}, {date.today().year} are shown on the "
        f"Performance page."
    )
    st.caption(interpret_benchmark_attribution(result))

    st.divider()

    # ── Interpretation ────────────────────────────────────────────────────────
    # Top-3 Brinson-Fachler selection effects since inception for prose cross-reference.
    # build_bf_cross_reference is the single source shared with src/reports.py's
    # PDF path — it reports raw_diff (r_p - r_b), not the weight-scaled selection
    # effect, so prose reads "AVUV outperformed IWM by 768 bps" correctly.
    _bhb_top = None
    _bf_price_gaps = None
    _bf_benchmark_gaps = None
    try:
        _bf_df = brinson_fachler_period(inception, end_date)
        _bhb_top = build_bf_cross_reference(_bf_df) or None
        _bf_price_gaps = _bf_df.attrs.get("price_gaps")
        _bf_benchmark_gaps = _bf_df.attrs.get("benchmark_gaps")
    except Exception:
        pass

    if _bf_price_gaps:
        st.warning(price_gap_notice(_bf_price_gaps))
    if _bf_benchmark_gaps:
        st.warning(benchmark_gap_notice(_bf_benchmark_gaps))

    st.subheader("Interpretation")
    for sentence in build_benchmark_prose(result, bhb_top_selection=_bhb_top):
        st.write(sentence)

    st.divider()

    # ── Methodology ───────────────────────────────────────────────────────────
    with st.expander("Methodology & Disclosure", expanded=False):
        for note in build_benchmark_methodology(result):
            st.markdown(f"- {note}")
        st.markdown(
            "- **Alpha confidence intervals**: 95% CI = alpha_bps ± 1.96 × SE_bps, "
            "where SE_bps = HAC standard error of the intercept × 252 × 10,000. "
            "HAC SEs are authoritative (correct for daily return autocorrelation); "
            "CI applies to alpha only, not to factor betas. "
            "Wide CIs at this sample length reflect parameter uncertainty, "
            f"not a methodological failure — they correctly communicate sample-size limitations "
            f"rather than model failure: the point estimate is unbiased; the confidence interval "
            f"is wide because {result['T']} observations is a short window for risk-adjusted "
            "return inference."
        )
        st.caption(
            "Portfolio returns: get_portfolio_value_series (adj_close basis). "
            "Benchmark returns: get_custom_blended_series (SAA target-weight basket). "
            "RF, HML, SMB, RMW: Ken French US daily factors (mba.tuck.dartmouth.edu). "
            "Cached at data/ff_factors_us.csv; refreshed when older than 7 days or "
            "most recent factor date exceeds 35-day publication lag."
        )
    render_footer()
