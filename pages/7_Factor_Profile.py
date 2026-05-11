"""Factor Profile — Fama-French 5-Factor Regression (per-sleeve decomposition)."""
import streamlit as st
from datetime import date

import pandas as pd

st.set_page_config(page_title="Factor Profile", layout="wide")

from src.asof import as_of_banner
from src.config import get_demo_banner_text, IS_DEMO
from src.ui_helpers import render_footer
from src.factors import (
    _FI_WEIGHTS,
    EM_DISCLOSURE,
    GLOBAL_DAILY_FACTORS_CUTOFF,
    alpha_ci_str,
    build_factor_methodology_notes,
    build_factor_prose,
    interpret_sleeve_regression,
    regress_fi_sleeve,
    run_intl_global_regression,
    run_sleeve_regressions,
    run_sleeve_regressions_mom,
    sig_marker,
)
from src.holdings import get_inception_date

if IS_DEMO:
    st.info(get_demo_banner_text())

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Factor Profile")
    st.caption(
        "Fama-French 5-Factor Regression · Per-sleeve decomposition · "
        "Daily excess returns since inception · Newey-West HAC standard errors"
    )
    st.caption(as_of_banner())
    st.divider()

    end_date  = date.today().isoformat()
    inception = get_inception_date()

    @st.cache_data(ttl=3600)
    def _get_factor_results(inception_date: str, end: str) -> dict:
        return run_sleeve_regressions(inception_date, end)

    @st.cache_data(ttl=3600)
    def _get_fi_result(inception_date: str, end: str):
        try:
            return regress_fi_sleeve(inception_date, end)
        except Exception:
            return None

    @st.cache_data(ttl=3600)
    def _get_mom_results(inception_date: str, end: str) -> dict:
        try:
            return run_sleeve_regressions_mom(inception_date, end)
        except Exception:
            return {"us": None, "developed_exus": None}

    @st.cache_data(ttl=3600)
    def _get_global_result(inception_date: str, end: str):
        try:
            return run_intl_global_regression(inception_date, end)
        except Exception:
            return None

    try:
        results       = _get_factor_results(inception, end_date)
        fi_result     = _get_fi_result(inception, end_date)
        results_mom   = _get_mom_results(inception, end_date)
        global_result = _get_global_result(inception, end_date)
    except Exception as exc:
        st.error(f"Factor regression unavailable: {exc}")
        st.stop()

    # ── Factor definitions panel ─────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**Fama-French Factor Definitions**")
        st.markdown(
            "| Factor | Definition |\n"
            "|--------|------------|\n"
            "| **Mkt-RF** | Market excess return — broad market return minus risk-free rate |\n"
            "| **SMB** | Small Minus Big — small-cap minus large-cap returns |\n"
            "| **HML** | High Minus Low — value minus growth (high book-to-market minus low) |\n"
            "| **RMW** | Robust Minus Weak — high-profitability minus low-profitability firms |\n"
            "| **CMA** | Conservative Minus Aggressive — low-investment minus high-investment firms |"
        )
    st.divider()

    _FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]

    _SLEEVE_ORDER = ["us", "developed_exus"]
    any_result = any(results.get(k) is not None for k in _SLEEVE_ORDER)

    if not any_result:
        st.info(
            "Insufficient data for regression — requires at least 30 aligned trading days. "
            "Section will populate as portfolio history grows."
        )
        st.stop()

    def _render_factor_table(res: dict, factor_list: list[str], label: str = "") -> None:
        """Render a regression result as a styled dataframe with fit metrics."""
        if label:
            st.markdown(f"**{label}**")
        rows = []
        p_a = res["p_alpha"]
        rows.append({
            "Factor":       "Alpha (annualized)",
            "Loading (β)":  alpha_ci_str(res),
            "t-stat":       f"{res['t_alpha']:.2f}",
            "p-value":      f"{p_a:.3f}",
            "Significance": sig_marker(p_a),
        })
        for f in factor_list:
            p = res["p_values"][f]
            rows.append({
                "Factor":       f,
                "Loading (β)":  f"{res['betas'][f]:.3f}",
                "t-stat":       f"{res['t_stats'][f]:.2f}",
                "p-value":      f"{p:.3f}",
                "Significance": sig_marker(p),
            })
        st.dataframe(pd.DataFrame(rows).set_index("Factor"), width='stretch')
        st.caption(
            "* p < 0.10 &nbsp; ** p < 0.05 &nbsp; *** p < 0.01 &nbsp;|&nbsp; "
            f"Standard errors: Newey-West HAC &nbsp;|&nbsp; R² = {res['r_squared']:.3f} &nbsp; "
            f"T = {res['T']} obs"
        )

    # ── Per-sleeve regression tables ─────────────────────────────────────────
    def _render_fit_metrics(r: dict) -> None:
        d_s = date.fromisoformat(r["sample_start"])
        d_e = date.fromisoformat(r["sample_end"])
        win = (
            f"{d_s.strftime('%B')} {d_s.day}, {d_s.year} — "
            f"{d_e.strftime('%B')} {d_e.day}, {d_e.year}"
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("R²",           f"{r['r_squared']:.3f}")
        c2.metric("Adj. R²",      f"{r['adj_r_squared']:.3f}")
        c3.metric("Observations", str(r["T"]))
        c4.metric("NW Lags (L)",  str(r["nw_lags"]))
        st.caption(f"Sample window: {win}")

    for key in _SLEEVE_ORDER:
        res = results.get(key)
        if res is None:
            continue

        st.subheader(res["sleeve_label"])
        st.caption(f"Tickers: {', '.join(res['tickers'])}")

        if key == "developed_exus":
            tab_dev, tab_glob = st.tabs(["Developed ex-US Factors", "Global Factors"])
            with tab_dev:
                _render_factor_table(res, _FACTORS, label="FF5 — Developed ex-US")
                _render_fit_metrics(res)
                st.caption(interpret_sleeve_regression(res, _FACTORS))
                res_mom = results_mom.get(key)
                if res_mom is not None:
                    with st.expander("Carhart Momentum Supplement (FF5 + UMD)", expanded=False):
                        _render_factor_table(res_mom, _FACTORS + ["Mom"], label="FF5 + Momentum")
                        st.caption(
                            "Supplementary regression including the Ken French daily UMD (Mom) factor. "
                            "A near-zero Mom loading is expected given the portfolio's tax-aware construction "
                            "(momentum strategies carry high turnover, creating short-term capital gains). "
                            "Alpha change vs. FF5 above reflects covariance between sleeve returns and "
                            "the momentum factor."
                        )
            with tab_glob:
                if global_result is not None:
                    _render_factor_table(global_result, _FACTORS, label="FF5 — Global")
                    _render_fit_metrics(global_result)
                    st.caption(interpret_sleeve_regression(global_result, _FACTORS))
                else:
                    st.info(
                        "Global daily factor data discontinued — Ken French ceased "
                        "publication of the daily Global 5-factor file in June 2019. "
                        "This portfolio started in May 2025; with no data overlap, "
                        "a regression cannot be produced at daily frequency. "
                        "The Developed ex-US Factors tab provides the primary factor "
                        "decomposition for the International Developed sleeve."
                    )
        else:
            _render_factor_table(res, _FACTORS)
            _render_fit_metrics(res)
            st.caption(interpret_sleeve_regression(res, _FACTORS))
            res_mom = results_mom.get(key)
            if res_mom is not None:
                with st.expander("Carhart Momentum Supplement (FF5 + UMD)", expanded=False):
                    _render_factor_table(res_mom, _FACTORS + ["Mom"], label="FF5 + Momentum")
                    st.caption(
                        "Supplementary regression including the Ken French daily UMD (Mom) factor. "
                        "A near-zero Mom loading is expected given the portfolio's tax-aware construction "
                        "(momentum strategies carry high turnover, creating short-term capital gains). "
                        "Alpha change vs. FF5 above reflects covariance between sleeve returns and "
                        "the momentum factor."
                    )

        st.divider()

    # ── FI sleeve — TERM / CREDIT regression ─────────────────────────────────
    st.subheader("Fixed Income Sleeve — TERM / CREDIT")
    if fi_result is None:
        st.warning(
            "FI factor data temporarily unavailable — requires IEF, BIL, and HYG daily prices "
            "plus Ken French US risk-free rate. Panel populates once the price series are cached."
        )
        if st.button("Retry", key="retry_fi"):
            _get_fi_result.clear()
            st.rerun()
    else:
        _fi_pcts = " / ".join(
            f"{round(_FI_WEIGHTS.get(t, 0) * 100):.0f}%"
            for t in fi_result["tickers"]
        )
        st.caption(
            f"Tickers: {', '.join(fi_result['tickers'])} ({_fi_pcts}, proportional to SAA) · "
            "TERM = IEF − BIL · CREDIT = HYG − IEF"
        )
        fi_rows = []
        p_a_fi = fi_result["p_alpha"]
        fi_rows.append({
            "Factor":       "Alpha (annualized)",
            "Loading (β)":  alpha_ci_str(fi_result),
            "t-stat":       f"{fi_result['t_alpha']:.2f}",
            "p-value":      f"{p_a_fi:.3f}",
            "Significance": sig_marker(p_a_fi),
        })
        for f in ["TERM", "CREDIT"]:
            p = fi_result["p_values"][f]
            fi_rows.append({
                "Factor":       f,
                "Loading (β)":  f"{fi_result['betas'][f]:.3f}",
                "t-stat":       f"{fi_result['t_stats'][f]:.2f}",
                "p-value":      f"{p:.3f}",
                "Significance": sig_marker(p),
            })

        st.dataframe(
            pd.DataFrame(fi_rows).set_index("Factor"),
            width='stretch',
        )
        st.caption(
            "* p < 0.10 &nbsp; ** p < 0.05 &nbsp; *** p < 0.01 &nbsp;|&nbsp; "
            "Standard errors: Newey-West HAC"
        )

        d_s = date.fromisoformat(fi_result["sample_start"])
        d_e = date.fromisoformat(fi_result["sample_end"])
        fi_win = (
            f"{d_s.strftime('%B')} {d_s.day}, {d_s.year} — "
            f"{d_e.strftime('%B')} {d_e.day}, {d_e.year}"
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("R²",           f"{fi_result['r_squared']:.3f}")
        c2.metric("Adj. R²",      f"{fi_result['adj_r_squared']:.3f}")
        c3.metric("Observations", str(fi_result["T"]))
        c4.metric("NW Lags (L)",  str(fi_result["nw_lags"]))
        st.caption(f"Sample window: {fi_win}")
        st.caption(interpret_sleeve_regression(fi_result, ["TERM", "CREDIT"]))
        st.divider()

    # ── Emerging Markets disclosure ───────────────────────────────────────────
    st.info(f"**Emerging Markets (IEMG):** {EM_DISCLOSURE}")

    st.divider()

    # ── Scope notes ──────────────────────────────────────────────────────────
    st.subheader("Scope Notes")
    # Full narrative retained for PDF export; inline per-sleeve interpretation above
    _full_prose = build_factor_prose(results, fi_result=fi_result, global_result=global_result)
    # Show only the exclusion / scope notes (last sentence covers EM + Real Assets)
    if _full_prose:
        st.write(_full_prose[-1])

    st.divider()

    # ── Methodology disclosure ────────────────────────────────────────────────
    with st.expander("Methodology & Disclosure", expanded=False):
        for note in build_factor_methodology_notes(results, fi_result=fi_result):
            st.markdown(f"- {note}")
        st.markdown(
            "- **Alpha confidence intervals**: 95% CI = alpha_bps ± 1.96 × SE_bps, "
            "where SE_bps = HAC standard error of the intercept × 252 × 10,000. "
            "CIs apply to alpha only. Wide CIs at ≤2 years of history correctly "
            "communicate that alpha estimates are not yet stable — this is a "
            "feature of honest reporting, not a methodological weakness."
        )
        st.caption(
            "Data: Ken French Data Library, Dartmouth (mba.tuck.dartmouth.edu). "
            "US factors cached at data/ff_factors_us.csv; Developed ex-US at "
            "data/ff_factors_developed_exus.csv; Global at data/ff_factors_global.csv; "
            "Momentum (UMD) at data/ff_umd_us.csv. "
            "Each refreshed when the cache is older than 7 days or the most recent "
            "factor date exceeds 35 days lag."
        )
    render_footer()
