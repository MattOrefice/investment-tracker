"""Risk — Portfolio Factor-Risk Decomposition (Phase 1).

First section of the Risk page: regresses the portfolio's daily excess return on
five systematic factors simultaneously (market, size, value, rates, credit) to
show what drives its risk. Scenario stress-testing (Phase 2) and risk
contribution (Phase 3) follow as later sections.
"""
import logging
import streamlit as st
from datetime import date

import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Risk", layout="wide")

from src.asof import as_of_banner
from src.config import get_demo_banner_text, IS_DEMO
from src.factors import sig_marker
from src.holdings import get_current_market_value, get_inception_date, get_portfolio_account, get_portfolio_account_id
from src.risk import (
    CREDIT_PROXY_DISCLOSURE,
    FACTORS,
    RATES_PROXY_DISCLOSURE,
    insufficient_history_message,
    low_confidence_caveat,
    methodology_notes,
    risk_contribution_insufficient_history_message,
    risk_contribution_low_confidence_caveat,
    risk_contribution_methodology_notes,
    run_portfolio_factor_regression,
    run_risk_contribution,
    run_scenarios,
    scenario_insufficient_history_message,
    scenario_methodology_notes,
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
    st.caption(
        f"Scope: **{get_portfolio_account()['display_name']}** — the self-directed "
        "taxable book (traded ledger); retirement and externally-managed accounts "
        "are excluded (see Household View)."
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
            "the **residual (unexplained) share = 1 − R²** is the portion they "
            "do *not* explain — including any Emerging Markets and Real Assets "
            "return the five factors are not built to span, so this residual is "
            "not purely security-specific noise. Sample size (n) and the date "
            "window are shown so the estimates can be judged in context."
        )

    st.subheader("Factor decomposition")

    inception = get_inception_date(account_id=get_portfolio_account_id())
    end_date  = date.today().isoformat()

    @st.cache_data(ttl=3600, show_spinner=False)
    def _get_decomposition(inception_date: str, end: str) -> dict:
        return run_portfolio_factor_regression(inception_date, end)

    try:
        result = _get_decomposition(inception, end_date)
    except Exception:  # pragma: no cover - defensive
        logging.exception("Factor decomposition failed")
        st.error("Factor decomposition unavailable — please try again later.")
        st.stop()

    # ── Insufficient-history empty state (the #38-analog) ─────────────────────
    # Phase 2 (scenarios) is GATED on Phase 1: with no betas there is nothing to
    # shock, so BOTH sections show the empty state — never garbage P&L.
    if result["status"] == "insufficient_history":
        st.info(insufficient_history_message(result["n"], result["min_obs"]))
        st.caption(
            "Factor betas are deliberately suppressed on thin samples — an "
            "explicit empty state rather than unstable coefficients, consistent "
            "with the rest of the app's treatment of short histories."
        )
        st.divider()
        st.subheader("Scenario stress test")
        _scn_empty = run_scenarios(result)  # inherits the insufficient-history state
        st.info(scenario_insufficient_history_message(_scn_empty["n"], _scn_empty["min_obs"]))
        st.divider()
        st.subheader("Risk contribution")
        _rc_empty = run_risk_contribution()  # covariance needs even more history
        st.info(risk_contribution_insufficient_history_message(
            _rc_empty["n"], _rc_empty["min_obs"]))
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
    c2.metric("Residual (unexplained)", f"{result['residual_share'] * 100:.1f}%")
    c3.metric("Observations (n)",    str(result["n"]))
    c4.metric("NW Lags (L)",         str(result["nw_lags"]))
    st.caption(f"Sample window: {window_str}")
    st.caption(
        f"The five factors jointly explain {result['r_squared'] * 100:.1f}% of "
        f"the portfolio's return variance; the remaining "
        f"{result['residual_share'] * 100:.1f}% is unexplained — return the "
        "systematic factors do not account for (including any Emerging "
        "Markets and Real Assets return the five factors don't span)."
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

    # ══════════════════════════════════════════════════════════════════════════
    #  Section 2 — Scenario stress test (consumes the Phase 1 betas above)
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("Scenario stress test")
    st.caption(
        "Estimated instantaneous portfolio impact under five factor shocks, "
        "applying the betas above to duration-translated factor moves."
    )

    with st.expander("How to read this section", expanded=False):
        st.markdown(
            "Each scenario applies the **same factor betas** from the decomposition "
            "above to a set of factor shocks: **estimated P&L = Σ (β × factor "
            "move)**. Yield and spread shocks are **translated to factor returns "
            "through duration** before the beta is applied — a rate move is a "
            "duration-implied price return, not a raw basis-point number — and "
            "the translation chain is shown on every line so the units are "
            "transparent.\n\n"
            "These are **instantaneous sensitivities, not forecasts**: what the "
            "book would gain or lose if the move happened at once. They are "
            "**linear first-order** estimates and do not capture convexity or beta "
            "instability in large moves. Magnitudes are illustrative stress "
            "sizes, not predictions."
        )

    current_mv = get_current_market_value()
    scen = run_scenarios(result, current_mv if current_mv and current_mv > 0 else None)

    if scen.get("low_confidence"):
        st.warning(low_confidence_caveat(scen.get("n", result["n"])))

    # Per-scenario cards: factor moves, the translation chain, and total P&L.
    for s in scen["scenarios"]:
        with st.container(border=True):
            usd = f" &nbsp;·&nbsp; **${s['total_usd']:+,.0f}**" if s["total_usd"] is not None else ""
            st.markdown(f"**{s['name']}** — estimated impact **{s['total_pct'] * 100:+.2f}%**{usd}")
            st.caption(s["summary"])
            for leg in s["legs"]:
                st.markdown(f"- {leg['chain']}")

    # Summary comparison of the five scenarios.
    st.markdown("**Scenario comparison**")
    summary_rows = []
    for s in scen["scenarios"]:
        summary_rows.append({
            "Scenario":            s["name"],
            "Estimated impact":    f"{s['total_pct'] * 100:+.2f}%",
            "Estimated $ impact":  (f"${s['total_usd']:+,.0f}" if s["total_usd"] is not None else "—"),
        })
    st.dataframe(pd.DataFrame(summary_rows).set_index("Scenario"), width="stretch")
    st.caption(
        "Risk-off shows the diversification-works case — a flight-to-quality rate "
        "rally offsets part of the equity loss; the 2022-style regime shows the "
        "diversification-fails case — rates and equity fall together and the "
        "losses compound."
    )

    with st.expander("Scenario methodology & disclosure", expanded=False):
        for note in scenario_methodology_notes(scen.get("durations")):
            st.markdown(f"- {note}")

    # ══════════════════════════════════════════════════════════════════════════
    #  Section 3 — Risk contribution (Euler / marginal-contribution-to-risk)
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("Risk contribution")
    st.caption(
        "Total policy volatility decomposed into each sleeve's contribution — "
        "risk share beside capital share, because correlation makes them differ."
    )

    with st.expander("How to read this section", expanded=False):
        st.markdown(
            "Policy volatility is split into per-sleeve **risk contributions** "
            "via the **Euler decomposition** — each sleeve's marginal contribution "
            "to risk weighted by its allocation. The contributions **sum exactly "
            "to total policy volatility** (and the percentages to 100%); that "
            "summation is the correctness check.\n\n"
            "The point is the gap between **risk share and weight share**: a "
            "high-volatility or highly-correlated sleeve contributes **more** risk "
            "than its weight, while a diversifying (low/negative-correlation) "
            "sleeve contributes **less** — *a 10% allocation is not 10% of the "
            "risk*. This is a decomposition of **total volatility**, not VaR or "
            "downside risk, using realized **sample covariance** over the window."
        )

    rc = run_risk_contribution()

    if rc["status"] == "insufficient_history":
        st.info(risk_contribution_insufficient_history_message(rc["n"], rc["min_obs"]))
    else:
        # ── Low-confidence caveat on the [60, 120) observation band ───────────
        if rc.get("low_confidence"):
            st.warning(risk_contribution_low_confidence_caveat(rc["n"]))

        st.metric("Policy / SAA volatility (annualized)", f"{rc['portfolio_vol'] * 100:.1f}%")
        st.caption(
            "Computed from SAA target weights and sleeve-benchmark proxies — this "
            "is the policy portfolio's volatility, not the realized volatility of "
            "current holdings (which appears on the Performance page)."
        )

        # Grouped horizontal bars: each sleeve's risk share beside its capital
        # share, sorted by risk share (rc["sleeves"] is already risk_pct-desc).
        # Where the navy (risk) bar exceeds the grey (weight) bar the sleeve is a
        # risk concentrator; where it falls short it diversifies — the
        # "10% allocation ≠ 10% of risk" point at a glance. Display-only: consumes
        # the SAME computed values as the table below, no recomputation.
        _RC_RISK_COLOR   = "#2E4057"   # navy — risk share (emphasis)
        _RC_WEIGHT_COLOR = "#9E9E9E"   # slate — weight share (reference)
        _rc_names   = [s["sleeve"] for s in rc["sleeves"]]
        _rc_weights = [s["weight"] * 100 for s in rc["sleeves"]]
        _rc_risks   = [s["risk_pct"] * 100 for s in rc["sleeves"]]
        _fig_rc = go.Figure()
        _fig_rc.add_trace(go.Bar(
            y=_rc_names, x=_rc_weights, name="Weight % (capital share)",
            orientation="h", marker_color=_RC_WEIGHT_COLOR,
            hovertemplate="<b>%{y}</b><br>Weight: %{x:.1f}%<extra></extra>",
        ))
        _fig_rc.add_trace(go.Bar(
            y=_rc_names, x=_rc_risks, name="Risk % (volatility share)",
            orientation="h", marker_color=_RC_RISK_COLOR,
            hovertemplate="<b>%{y}</b><br>Risk: %{x:.1f}%<extra></extra>",
        ))
        _fig_rc.update_layout(
            barmode="group",
            height=440,
            margin=dict(l=0, r=10, t=10, b=0),
            paper_bgcolor="white", plot_bgcolor="white",
            font=dict(color="#333333", size=12),
            xaxis=dict(title="Share of total (%)", gridcolor="#EBEBEB", ticksuffix="%"),
            yaxis=dict(autorange="reversed"),   # highest risk share at top
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font_size=11),
            bargap=0.28, bargroupgap=0.12,
        )
        st.plotly_chart(_fig_rc, width="stretch")
        st.caption(
            "Each sleeve's share of risk (navy) beside its share of capital (grey), "
            "sorted by risk share. Where the navy bar exceeds the grey, the sleeve "
            "contributes more risk than its weight (a risk concentrator); where it "
            "falls short, the sleeve diversifies — a sleeve's share of capital is "
            "not its share of risk. The table below is the precise reference."
        )

        rc_rows = []
        for s in rc["sleeves"]:
            divergence = (s["risk_pct"] - s["weight"]) * 100
            rc_rows.append({
                "Sleeve":      s["sleeve"],
                "Weight %":    f"{s['weight'] * 100:.1f}%",
                "Risk %":      f"{s['risk_pct'] * 100:.1f}%",
                "Risk − Weight": f"{divergence:+.1f} pp",
                "Sleeve vol":  f"{s['vol'] * 100:.1f}%",
            })
        st.dataframe(pd.DataFrame(rc_rows).set_index("Sleeve"), width="stretch")

        # The Euler summation, surfaced as the correctness proof.
        _rc_sum_pct = sum(s["risk_pct"] for s in rc["sleeves"]) * 100
        st.caption(
            f"Euler check: the sleeve risk contributions sum to total policy "
            f"volatility — Σ RC = {rc['rc_sum_check'] * 100:.2f}% = "
            f"σ_p ({rc['portfolio_vol'] * 100:.2f}%); risk shares sum to "
            f"{_rc_sum_pct:.1f}%. The **Risk − Weight** column is the point: where "
            "it is positive the sleeve carries more risk than capital, where "
            "negative it diversifies."
        )
        st.caption(
            f"Sample covariance over {rc['n']} trading days since inception, "
            "annualized (√252). Sample (realized) covariance is an estimate — "
            "off-diagonal correlation terms especially — so read the shares in "
            "the context of the window length."
        )

    with st.expander("Risk-contribution methodology & disclosure", expanded=False):
        for note in risk_contribution_methodology_notes():
            st.markdown(f"- {note}")

    render_footer()
