"""Factor Profile — Fama-French 5-Factor Regression (per-sleeve decomposition)."""
import logging
import streamlit as st
from datetime import date

import pandas as pd

st.set_page_config(page_title="Factor Profile", layout="wide")

from src.asof import as_of_banner
from src.config import get_demo_banner_text, IS_DEMO
from src.positioning import build_style_box_figure, get_non_us_equity_data, get_style_box_data
from src.style_box import STYLE_BOX_CAPTION
from src.ui_helpers import render_footer, render_page_header
render_page_header()

from src.factors import (
    _FI_WEIGHTS,
    EM_DISCLOSURE,
    alpha_ci_str,
    build_factor_methodology_notes,
    build_factor_prose,
    build_intl_tilt_disclosure,
    intl_residual_reading_order,
    regress_fi_sleeve,
    run_intl_tilt_regressions,
    run_sleeve_regressions,
    run_sleeve_regressions_mom,
    sig_marker,
)
from src.holdings import get_inception_date, get_portfolio_account_id

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

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "Each regressed sleeve uses its **region-appropriate Fama-French factor "
            "set**: US factors (Ken French Data Library) for the US equity sleeves (VOO, SPHQ, "
            "VTV, AVUV), and Developed ex-US factors for every developed-international sleeve — "
            "the cap-weighted core (VEA) and, in the tilted book, the quality / value / "
            "small-value tilt sleeves and their passive controls. "
            "Per-sleeve regressions avoid the model misspecification that arises when non-US "
            "returns flow into a single US-factor model.\n\n"
            "**Residual (labelled α in the academic model):** the return left unexplained by the "
            "factor loadings. It is **not** the amount by which the holding beat a portfolio you "
            "could have bought. The Fama-French factors are long-short, gross-of-cost, "
            "costlessly-rebalanced academic constructions — **no investable passive portfolio "
            "matches them** — so a non-zero residual is expected before skill is ever in "
            "question. Read it in this order, skill last and least: **sampling noise** (the 95% "
            "CI shown with each residual spans hundreds of bps at ~1 year of data), then "
            "**universe / classification mismatch** (the fund's index and the factor universe "
            "treat Korea and Canada differently), then **construction differences** between a "
            "real fund and the factors (net-of-withholding NAV vs gross factors, long-only vs "
            "long-short, a microcap tail, stale cross-timezone pricing), and only then — last, "
            "and weighted least — **skill**. Each tilt fund is shown beside a passive, "
            "**Canada-matched control**: when the control carries a large residual too, the "
            "residual is measuring construction, not selection.\n\n"
            "**Factor loadings (β):** Mkt-RF captures market beta (>1 = more market risk than "
            "benchmark); positive HML = value tilt; positive SMB = small-cap tilt; "
            "positive RMW = profitability tilt; positive CMA = conservative-investment tilt. "
            "At ~1 year of history the β loadings are more stable and more interpretable than "
            "the residual.\n\n"
            "**Significance:** \\* p<0.10 · \\*\\* p<0.05 · \\*\\*\\* p<0.01. "
            "All standard errors are Newey-West HAC-corrected for heteroskedasticity and "
            "serial correlation (lag = ⌊4 × (T/100)^(2/9)⌋)."
        )

    end_date  = date.today().isoformat()
    inception = get_inception_date(account_id=get_portfolio_account_id())

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
    def _get_tilt_results(inception_date: str, end: str) -> dict:
        try:
            return run_intl_tilt_regressions(inception_date, end)
        except Exception:
            return {}

    try:
        results      = _get_factor_results(inception, end_date)
        fi_result    = _get_fi_result(inception, end_date)
        results_mom  = _get_mom_results(inception, end_date)
        tilt_results = _get_tilt_results(inception, end_date)
    except Exception:
        logging.exception("Factor regression failed")
        st.error("Factor regression unavailable — please try again later.")
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
            "Factor":       "Residual",
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
        st.caption(f"Sample window: {win}")

    st.caption(
        "Regression windows end at the most recent date with published Fama-French "
        "factor data — a publication lag; live prices through today are shown in "
        "the Performance page KPI strip."
    )

    for key in _SLEEVE_ORDER:
        res = results.get(key)
        if res is None:
            continue

        st.subheader(res["sleeve_label"])
        st.caption(f"Tickers: {', '.join(res['tickers'])}")

        if key == "developed_exus":
            _render_factor_table(res, _FACTORS, label="FF5 — Developed ex-US")
            _render_fit_metrics(res)
            st.write(build_factor_prose({"us": None, "developed_exus": res})[0])
            res_mom = results_mom.get(key)
            if res_mom is not None:
                with st.expander("Carhart Momentum Supplement (FF5 + UMD)", expanded=False):
                    _render_factor_table(res_mom, _FACTORS + ["Mom"], label="FF5 + Momentum")
                    _mom_b = res_mom["betas"]["Mom"]
                    _mom_t = res_mom["t_stats"]["Mom"]
                    if abs(_mom_t) >= 1.96:
                        st.caption(
                            f"Mom loading of {_mom_b:+.3f} (t = {_mom_t:.2f}) is statistically "
                            f"significant but economically small — some incidental momentum exposure "
                            f"leaks through the SPHQ quality factor, but the loading magnitude confirms "
                            f"momentum is not a deliberate tilt."
                        )
                    else:
                        st.caption(
                            f"Mom loading of {_mom_b:+.3f} (t = {_mom_t:.2f}) confirms the portfolio "
                            f"does not carry a systematic momentum tilt — consistent with the tax-aware "
                            f"construction (high momentum turnover → short-term capital gains)."
                        )
        else:
            _render_factor_table(res, _FACTORS)
            _render_fit_metrics(res)
            st.write(build_factor_prose({"us": res})[0])
            res_mom = results_mom.get(key)
            if res_mom is not None:
                with st.expander("Carhart Momentum Supplement (FF5 + UMD)", expanded=False):
                    _render_factor_table(res_mom, _FACTORS + ["Mom"], label="FF5 + Momentum")
                    _mom_b = res_mom["betas"]["Mom"]
                    _mom_t = res_mom["t_stats"]["Mom"]
                    if abs(_mom_t) >= 1.96:
                        st.caption(
                            f"Mom loading of {_mom_b:+.3f} (t = {_mom_t:.2f}) is statistically "
                            f"significant but economically small — some incidental momentum exposure "
                            f"leaks through the SPHQ quality factor, but the loading magnitude confirms "
                            f"momentum is not a deliberate tilt."
                        )
                    else:
                        st.caption(
                            f"Mom loading of {_mom_b:+.3f} (t = {_mom_t:.2f}) confirms the portfolio "
                            f"does not carry a systematic momentum tilt — consistent with the tax-aware "
                            f"construction (high momentum turnover → short-term capital gains)."
                        )

        st.divider()

    # ── International tilt sleeves — fund vs Canada-matched control ───────────
    # Tilted (demo) book only: run_intl_tilt_regressions returns {} on the
    # untilted personal book, so this whole section is absent there.
    _tilt_renderable = {
        k: v for k, v in (tilt_results or {}).items()
        if v.get("fund_result") is not None
    }
    if _tilt_renderable:
        st.subheader("International Tilt Sleeves — fund vs Canada-matched control")
        st.caption(
            "Each developed-international tilt sleeve is regressed against the same "
            "Developed ex-US FF5 factors as VEA, and shown beside a passive control "
            "fund. The controls are Canada-matched (IQLT, IVLU, ISVL) — deliberately "
            "NOT the SAA attribution benchmarks (EFV, SCZ), which track MSCI EAFE and "
            "exclude Canada."
        )
        st.info(
            "**Why not EFV / SCZ as the controls?** The Ken French Developed ex-US "
            "factor universe includes Canada; MSCI EAFE (EFV, SCZ) excludes it, and "
            "the tilt funds hold it at ~11–13%. Sizing this over the regression "
            "window: Canada (EWC) returned about +37.8%/yr versus about +27.7%/yr for "
            "developed-ex-US — a ~10-point gap. Regressed against these same factors, "
            "the Canada-holed EFV shows only a ~+20 bps residual while the "
            "Canada-matched IVLU (shown below) shows ~+270 bps over the same "
            "large-value window — the missing Canada is most of the difference, a "
            "~90–250 bps understatement depending on weight. A Canada-holed control "
            "would therefore read as a clean gauge while hiding the missing-Canada "
            "return, so IQLT / IVLU / ISVL are used instead — each matches the factor "
            "universe on both counts (Canada in, Korea out)."
        )
        st.markdown(intl_residual_reading_order())
        st.divider()

        for _ft, _entry in _tilt_renderable.items():
            _fr = _entry["fund_result"]
            _cr = _entry["control_result"]
            st.markdown(
                f"#### {_entry['sleeve']} — {_entry['fund']} "
                f"<span style='font-weight:normal'>({_entry['fund_name']})</span>",
                unsafe_allow_html=True,
            )
            _render_factor_table(
                _fr, _FACTORS, label=f"FF5 — {_entry['fund']} (fund)"
            )
            _render_fit_metrics(_fr)

            if _cr is not None:
                _render_factor_table(
                    _cr, _FACTORS,
                    label=(
                        f"FF5 — {_entry['control']} control "
                        f"({_entry['control_index']}, Canada-matched)"
                    ),
                )
                _render_fit_metrics(_cr)
                _gap = _fr["alpha_annual_bps"] - _cr["alpha_annual_bps"]
                st.caption(
                    f"**Fund-minus-control residual: {_gap:+.0f} bps/yr** "
                    f"({_entry['fund']} {_fr['alpha_annual_bps']:+.0f} − "
                    f"{_entry['control']} {_cr['alpha_annual_bps']:+.0f}). Both residuals "
                    "carry wide confidence intervals at this sample length; the control's "
                    "own non-zero residual is the point — it is what construction against "
                    "the academic factors costs a passive, Canada-matched fund."
                )
            else:
                st.caption(
                    f"Control {_entry['control']} regression unavailable "
                    "(insufficient aligned observations)."
                )

            for _para in build_intl_tilt_disclosure(_entry):
                st.write(_para)
            st.divider()

    # ── FI sleeve — TERM / CREDIT regression ─────────────────────────────────
    st.subheader("Fixed Income Sleeve — TERM / CREDIT")
    st.caption(
        "FI sleeve uses TERM and CREDIT factors; "
        "Carhart momentum supplement not applicable to a duration-driven sleeve."
    )
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
            "Factor":       "Residual",
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
            f"Standard errors: Newey-West HAC &nbsp;|&nbsp; R² = {fi_result['r_squared']:.3f} &nbsp; "
            f"T = {fi_result['T']} obs"
        )

        d_s = date.fromisoformat(fi_result["sample_start"])
        d_e = date.fromisoformat(fi_result["sample_end"])
        fi_win = (
            f"{d_s.strftime('%B')} {d_s.day}, {d_s.year} — "
            f"{d_e.strftime('%B')} {d_e.day}, {d_e.year}"
        )
        st.caption(f"Sample window: {fi_win}")
        st.write(build_factor_prose({}, fi_result=fi_result)[0])
        st.divider()

    # ── Emerging Markets disclosure ───────────────────────────────────────────
    st.info(f"**Emerging Markets (IEMG):** {EM_DISCLOSURE}")

    st.divider()

    # ── Equity Style Profile ──────────────────────────────────────────────────
    st.subheader("Equity Style Profile")
    style_data = get_style_box_data(end_date)
    non_us     = get_non_us_equity_data(end_date)

    if style_data:
        fig = build_style_box_figure(style_data)
        box_col, _ = st.columns([3, 2])
        with box_col:
            st.plotly_chart(fig, width='stretch')
        st.caption(STYLE_BOX_CAPTION)
        st.caption(
            "Empty cells reflect deliberate construction: the SAA does not include mid-cap or "
            "pure-growth tilts. Quality (SPHQ) and value (VTV, AVUV) are the chosen factor exposures."
        )

        if non_us:
            st.markdown("**Non-US Equity Sleeves**")
            for item in non_us:
                st.markdown(
                    f"- **{item['ticker']}** ({item['region_label']}) — "
                    f"{item['weight_pct']:.1f}% of portfolio"
                )
            st.caption(
                "Non-US holdings are not directly comparable to US value/growth and "
                "market-cap distributions. See Morningstar regional style boxes for "
                "international placement methodology."
            )
            if len(non_us) > 2:
                # Tilted (12-sleeve) book only — on the personal book the two
                # rows are exactly the regressed sleeve plus EM, so the
                # coverage note would state the obvious.
                st.caption(
                    "Regression coverage: the cap-weighted core sleeve (VEA) and "
                    "the three quality/value/small-value tilt sleeves (IDHQ, AVIV, "
                    "AVDV) each carry a per-sleeve factor regression above, against "
                    "the developed ex-US FF5 series — the tilt sleeves paired with a "
                    "Canada-matched control fund. Emerging Markets has no regression "
                    "by design (see disclosure above)."
                )
    else:
        st.info("No equity holdings found.")

    st.divider()

    # ── Methodology disclosure ────────────────────────────────────────────────
    with st.expander("Methodology & Disclosure", expanded=False):
        for note in build_factor_methodology_notes(results, fi_result=fi_result):
            st.markdown(f"- {note}")
        st.markdown(
            "- **Residual (α) confidence intervals**: 95% CI = residual_bps ± 1.96 × SE_bps, "
            "where SE_bps = HAC standard error of the intercept × 252 × 10,000. "
            "CIs apply to the residual (regression intercept) only. Wide CIs at ≤2 years "
            "of history correctly communicate that the residual estimate is not yet "
            "stable — this is a feature of honest reporting, not a methodological weakness."
        )
        st.caption(
            "Data: Ken French Data Library, Dartmouth (mba.tuck.dartmouth.edu). "
            "US factors cached at data/ff_factors_us.csv; Developed ex-US at "
            "data/ff_factors_developed_exus.csv; Global at data/ff_factors_global.csv; "
            "Momentum (UMD) at data/ff_umd_us.csv. "
            "Each refreshed when the cache is older than 7 days or the most recent "
            "factor date exceeds 35 days lag."
        )
        st.caption(
            "The equity style profile uses a 3×3 size-by-style grid format. "
            "This implementation is independent of Morningstar, Inc. The 3×3 "
            "size-by-style grid is a generic equity portfolio analysis convention "
            "and is not produced by, affiliated with, or endorsed by Morningstar."
        )
    render_footer()
