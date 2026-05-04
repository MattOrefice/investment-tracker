"""Benchmark Attribution — Portfolio vs Custom Blended SAA Regression."""
import streamlit as st
from datetime import date

import pandas as pd

from src.config import IS_DEMO
from src.factors import (
    build_benchmark_methodology,
    build_benchmark_prose,
    run_benchmark_attribution_regression,
    sig_marker,
)

st.set_page_config(page_title="Benchmark Attribution", layout="wide")

if IS_DEMO:
    st.info(
        "**Demo mode** — regression computed on the demo portfolio's paper-trade return series. "
        "Factor loadings reflect the SAA construction, not live trading decisions."
    )

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Benchmark Attribution")
    st.caption(
        "Portfolio vs Custom Blended SAA Benchmark · "
        "R_p − RF ~ (R_b − RF) + HML + SMB + RMW · "
        "Daily excess returns since inception · Newey-West HAC standard errors"
    )
    st.divider()

    end_date  = date.today().isoformat()
    inception = "2025-05-01"

    @st.cache_data(ttl=3600)
    def _get_benchmark_result(inception_date: str, end: str) -> dict | None:
        return run_benchmark_attribution_regression(inception_date, end)

    try:
        result = _get_benchmark_result(inception, end_date)
    except Exception as exc:
        st.error(f"Benchmark attribution regression unavailable: {exc}")
        st.stop()

    if result is None:
        st.info(
            "Insufficient data for regression — requires at least 30 aligned trading days. "
            "Section will populate as portfolio history grows."
        )
        st.stop()

    _BENCH_FACTORS = ["Bench-RF", "HML", "SMB", "RMW"]

    # ── Regression table ──────────────────────────────────────────────────────
    table_rows = []
    p_a = result["p_alpha"]
    table_rows.append({
        "Factor":       "Alpha (annualized)",
        "Loading (β)":  f"{result['alpha_annual_bps']:+.0f} bps/yr",
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
        width="stretch",
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

    st.divider()

    # ── Interpretation ────────────────────────────────────────────────────────
    st.subheader("Interpretation")
    for sentence in build_benchmark_prose(result):
        st.write(sentence)

    st.divider()

    # ── Methodology ───────────────────────────────────────────────────────────
    with st.expander("Methodology & Disclosure", expanded=False):
        for note in build_benchmark_methodology(result):
            st.markdown(f"- {note}")
        st.caption(
            "Portfolio returns: get_portfolio_value_series (adj_close basis). "
            "Benchmark returns: get_custom_blended_series (SAA target-weight basket). "
            "RF, HML, SMB, RMW: Ken French US daily factors (mba.tuck.dartmouth.edu). "
            "Cached at data/ff_factors_us.csv; refreshed when older than 7 days or "
            "most recent factor date exceeds 35-day publication lag."
        )
