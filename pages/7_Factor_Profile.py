"""Factor Profile — Fama-French 5-Factor Regression."""
import streamlit as st
from datetime import date

import pandas as pd

from src.config import IS_DEMO
from src.factors import (
    build_factor_methodology_notes,
    build_factor_prose,
    run_ff5_regression,
    sig_marker,
)

st.set_page_config(page_title="Factor Profile", layout="wide")

if IS_DEMO:
    st.info(
        "**Demo mode** — regression computed on the demo portfolio's paper-trade return series. "
        "Factor loadings reflect the SAA construction, not live trading decisions."
    )

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Factor Profile")
    st.caption(
        "Fama-French 5-Factor Regression · Daily portfolio excess returns since inception · "
        "Newey-West HAC standard errors"
    )
    st.divider()

    end_date  = date.today().isoformat()
    inception = "2025-05-01"

    @st.cache_data(ttl=3600)
    def _get_factor_result(inception_date: str, end: str):
        return run_ff5_regression(inception_date, end)

    try:
        res = _get_factor_result(inception, end_date)
    except Exception as exc:
        st.error(f"Factor regression unavailable: {exc}")
        st.stop()

    if res is None:
        st.info(
            "Insufficient data for regression — requires at least 30 aligned trading days. "
            "Section will populate as portfolio history grows."
        )
        st.stop()

    # ── Factor loadings table ────────────────────────────────────────────────
    st.subheader("Factor Loadings")

    _FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]

    table_rows = []
    # Alpha row at top
    p_a = res["p_alpha"]
    table_rows.append({
        "Factor":         "Alpha (annualized)",
        "Loading (β)":    f"{res['alpha_annual_bps']:+.0f} bps/yr",
        "t-stat":         f"{res['t_alpha']:.2f}",
        "p-value":        f"{p_a:.3f}",
        "Significance":   sig_marker(p_a),
    })
    for f in _FACTORS:
        p = res["p_values"][f]
        table_rows.append({
            "Factor":         f,
            "Loading (β)":    f"{res['betas'][f]:.3f}",
            "t-stat":         f"{res['t_stats'][f]:.2f}",
            "p-value":        f"{p:.3f}",
            "Significance":   sig_marker(p),
        })

    st.dataframe(
        pd.DataFrame(table_rows).set_index("Factor"),
        width="stretch",
    )

    st.caption(
        "* p < 0.10 &nbsp; ** p < 0.05 &nbsp; *** p < 0.01 &nbsp;|&nbsp; "
        "Standard errors: Newey-West HAC"
    )

    # ── Fit statistics ───────────────────────────────────────────────────────
    st.subheader("Fit Statistics")

    d_start = date.fromisoformat(res["sample_start"])
    d_end   = date.fromisoformat(res["sample_end"])
    window_str = (
        f"{d_start.strftime('%B')} {d_start.day}, {d_start.year} — "
        f"{d_end.strftime('%B')} {d_end.day}, {d_end.year}"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("R²",            f"{res['r_squared']:.3f}")
    c2.metric("Adj. R²",       f"{res['adj_r_squared']:.3f}")
    c3.metric("Observations",  str(res["T"]))
    c4.metric("NW Lags (L)",   str(res["nw_lags"]))

    st.caption(f"Sample window: {window_str}")

    st.divider()

    # ── Interpretation ───────────────────────────────────────────────────────
    st.subheader("Interpretation")
    for sentence in build_factor_prose(res):
        st.write(sentence)

    st.divider()

    # ── Methodology disclosure ───────────────────────────────────────────────
    with st.expander("Methodology & Disclosure", expanded=False):
        for note in build_factor_methodology_notes(res):
            st.markdown(f"- {note}")
        st.caption(
            "Data: Ken French Data Library, Dartmouth (mba.tuck.dartmouth.edu). "
            "Cached locally at data/ff_factors.csv; refreshed when the cache is "
            "older than 7 days or the most recent factor date exceeds 35 days lag."
        )
