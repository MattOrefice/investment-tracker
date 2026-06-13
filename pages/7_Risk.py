"""Risk — Portfolio Factor-Risk Decomposition (Phase 1).

First section of the Risk page: regresses the portfolio's daily excess return on
five systematic factors simultaneously (market, size, value, rates, credit) to
show what drives its risk. Scenario stress-testing (Phase 2) and risk
contribution (Phase 3) follow as later sections.
"""
import streamlit as st
from datetime import date

import pandas as pd

st.set_page_config(page_title="Risk", layout="wide")

from src.asof import as_of_banner
from src.config import get_demo_banner_text, IS_DEMO
from src.factors import sig_marker
from src.holdings import get_inception_date
from src.risk import (
    CREDIT_PROXY_DISCLOSURE,
    FACTORS,
    RATES_PROXY_DISCLOSURE,
    insufficient_history_message,
    low_confidence_caveat,
    methodology_notes,
    run_portfolio_factor_regression,
)
from src.ui_helpers import render_footer, render_page_header

render_page_header()


# Human-readable labels for the five factors (table display only).
_FACTOR_LABEL = {
    "Mkt-RF": "Market (Mkt-RF)",
    "SMB":    "Size (SMB)",
    "HML":    "Value (HML)",
    "RATES":  "Rates (IEF excess)",
    "CREDIT": "Credit (HYG − IEF)",
}


if IS_DEMO:
    st.info(get_demo_banner_text())

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Risk")
    st.caption(
        "Portfolio factor-risk decomposition · five-factor simultaneous "
        "regression, daily excess returns since inception."
    )
    st.caption(as_of_banner())
    st.divider()

    with st.expander("How to read this section", expanded=False):
        st.markdown(
            "This decomposes the portfolio's **systematic risk** into five factor "
            "exposures. The portfolio's daily excess return is regressed on all "
            "five factors **at once** — a single multiple regression, not five "
            "separate ones:\n\n"
            "> **R_portfolio − RF = α + β₁·Mkt-RF + β₂·SMB + β₃·HML + "
            "β₄·RATES + β₅·CREDIT + ε**\n\n"
            "Because the factors enter simultaneously, each **beta is a marginal "
            "exposure** — the loading on that factor *controlling for the other "
            "four*. (Five univariate regressions would double-count exposure "
            "shared across correlated factors.)\n\n"
            "- **Market / Size / Value** are the Fama-French US factors.\n"
            f"- **Rates** — {RATES_PROXY_DISCLOSURE} *(ETF-based proxy.)*\n"
            f"- **Credit** — {CREDIT_PROXY_DISCLOSURE} *(ETF-based proxy.)*\n\n"
            "**R²** is the share of return variance the factors jointly explain; "
            "the **residual (idiosyncratic) share = 1 − R²** is the portion they "
            "do *not* explain. Sample size (n) and the date window are shown so "
            "the estimates can be judged in context."
        )

    st.subheader("Factor decomposition")

    inception = get_inception_date()
    end_date  = date.today().isoformat()

    @st.cache_data(ttl=3600, show_spinner=False)
    def _get_decomposition(inception_date: str, end: str) -> dict:
        return run_portfolio_factor_regression(inception_date, end)

    try:
        result = _get_decomposition(inception, end_date)
    except Exception as exc:  # pragma: no cover - defensive
        st.error(f"Factor decomposition unavailable: {exc}")
        st.stop()

    # ── Insufficient-history empty state (the #38-analog) ─────────────────────
    if result["status"] == "insufficient_history":
        st.info(insufficient_history_message(result["n"], result["min_obs"]))
        st.caption(
            "Factor betas are deliberately suppressed on thin samples — an "
            "explicit empty state rather than unstable coefficients, consistent "
            "with the rest of the app's treatment of short histories."
        )
        render_footer()
        st.stop()

    # ── Low-confidence caveat on the [30, 60) observation band ────────────────
    if result.get("low_confidence"):
        st.warning(low_confidence_caveat(result["n"]))

    # ── Beta table ────────────────────────────────────────────────────────────
    table_rows = [{
        "Factor":       "Alpha (annualized)",
        "Beta (β)":     f"{result['alpha_annual_bps']:+.0f} bps",
        "t-stat":       f"{result['t_alpha']:.2f}",
        "p-value":      f"{result['p_alpha']:.3f}",
        "Significance": sig_marker(result["p_alpha"]),
    }]
    for f in FACTORS:
        p = result["p_values"][f]
        table_rows.append({
            "Factor":       _FACTOR_LABEL[f],
            "Beta (β)":     f"{result['betas'][f]:+.3f}",
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
    c1.metric("R² (explained)",      f"{result['r_squared'] * 100:.1f}%")
    c2.metric("Residual (idiosyncratic)", f"{result['residual_share'] * 100:.1f}%")
    c3.metric("Observations (n)",    str(result["n"]))
    c4.metric("NW Lags (L)",         str(result["nw_lags"]))
    st.caption(f"Sample window: {window_str}")
    st.caption(
        f"R² = {result['r_squared']:.3f}: the five factors jointly explain "
        f"{result['r_squared'] * 100:.1f}% of the portfolio's return variance; "
        f"the remaining {result['residual_share'] * 100:.1f}% is idiosyncratic — "
        "return the systematic factors do not account for."
    )

    # ── Proxy disclosure ──────────────────────────────────────────────────────
    st.caption(
        f"**Proxy disclosure** — {RATES_PROXY_DISCLOSURE} {CREDIT_PROXY_DISCLOSURE} "
        "Both are tradeable ETF stand-ins for the academic term and credit premia, "
        "disclosed as proxies."
    )

    st.divider()

    # ── Methodology ───────────────────────────────────────────────────────────
    with st.expander("Methodology & Disclosure", expanded=False):
        for note in methodology_notes():
            st.markdown(f"- {note}")

    render_footer()
