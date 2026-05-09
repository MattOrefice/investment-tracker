"""Benchmark Attribution — Portfolio vs Custom Blended SAA Regression."""
import streamlit as st
from datetime import date

import pandas as pd

st.set_page_config(page_title="Benchmark Attribution", layout="wide")

from src.asof import as_of_banner
from src.attribution import brinson_fachler_period
from src.config import get_demo_banner_text, IS_DEMO
from src.holdings import get_inception_date
from src.factors import (
    alpha_ci_str,
    build_benchmark_methodology,
    build_benchmark_prose,
    run_benchmark_attribution_regression,
    sig_marker,
)
from src.ui_helpers import render_footer

# Locked Phase 2 holdings and benchmarks per sleeve — used for BHB cross-reference
_SLEEVE_HOLDING = {
    "US Large Core":           "VOO",
    "US Large Quality":        "SPHQ",
    "US Large Value":          "VTV",
    "US Small Cap":            "AVUV",
    "International Developed": "VEA",
    "Emerging Markets":        "IEMG",
    "Core Fixed Income":       "VGIT",
    "TIPS":                    "SCHP",
    "Real Assets":             "VNQ / PDBC",
    "Cash / SPAXX":            "SPAXX",
}
_SLEEVE_BENCH = {
    "US Large Core":           "SPY",
    "US Large Quality":        "QUAL",
    "US Large Value":          "IWD",
    "US Small Cap":            "IWM",
    "International Developed": "EFA",
    "Emerging Markets":        "EEM",
    "Core Fixed Income":       "IEF",
    "TIPS":                    "TIP",
    "Real Assets":             "VNQ / DBC",
    "Cash / SPAXX":            "BIL",
}

if IS_DEMO:
    st.info(get_demo_banner_text())

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Benchmark Attribution")
    st.caption(
        "Portfolio vs Custom Blended SAA Benchmark · "
        "R_p − RF ~ (R_b − RF) + HML + SMB + RMW · "
        "Daily excess returns since inception · Newey-West HAC standard errors"
    )
    st.caption(as_of_banner())
    st.divider()

    end_date  = date.today().isoformat()
    inception = get_inception_date()

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

    st.divider()

    # ── Interpretation ────────────────────────────────────────────────────────
    # Compute top-3 BHB selection effects since inception for prose cross-reference
    _bhb_top = None
    try:
        _bf_df = brinson_fachler_period(inception, end_date)
        if not _bf_df.empty:
            _top3 = _bf_df.nlargest(3, "selection_effect")
            _bhb_top = []
            for _, _row in _top3.iterrows():
                _sleeve = _row["sleeve"]
                _bhb_top.append({
                    "holding": _SLEEVE_HOLDING.get(_sleeve, _sleeve),
                    "bench":   _SLEEVE_BENCH.get(_sleeve, _sleeve),
                    "sel_bps": _row["selection_effect"] * 10_000,
                })
    except Exception:
        pass

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
            "not a methodological failure — they correctly communicate that the "
            "alpha estimate is not yet stable."
        )
        st.caption(
            "Portfolio returns: get_portfolio_value_series (adj_close basis). "
            "Benchmark returns: get_custom_blended_series (SAA target-weight basket). "
            "RF, HML, SMB, RMW: Ken French US daily factors (mba.tuck.dartmouth.edu). "
            "Cached at data/ff_factors_us.csv; refreshed when older than 7 days or "
            "most recent factor date exceeds 35-day publication lag."
        )
    render_footer()
