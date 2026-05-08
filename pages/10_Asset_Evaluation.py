"""Asset Evaluation Framework — structured analysis for SAA candidate assets."""
from datetime import date

import streamlit as st

st.set_page_config(page_title="Asset Evaluation", layout="wide")

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src import asset_evaluation as ae
from src.asof import as_of_banner
from src.macro import get_recession_periods
from src.ui_helpers import render_footer

TODAY = date.today().isoformat()


# ── Cached data loaders ───────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _load_btc_returns() -> pd.Series:
    try:
        return ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_sleeve_returns() -> pd.DataFrame:
    try:
        return ae.get_sleeve_returns(ae.SAMPLE_START)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_spy_returns() -> pd.Series:
    try:
        return ae.get_candidate_returns("SPY", ae.SAMPLE_START)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_univariate_table() -> pd.DataFrame:
    try:
        return ae.build_univariate_table()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_full_sample_correlations(
    btc_key: str, sleeve_key: str
) -> pd.Series:
    """Cache key args are string digests passed by caller to bust cache on data refresh."""
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty:
            return pd.Series(dtype=float)
        return ae.compute_full_sample_correlations(btc, slv)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_rolling_correlation() -> pd.Series:
    try:
        btc = _load_btc_returns()
        spy = _load_spy_returns()
        if btc.empty or spy.empty:
            return pd.Series(dtype=float)
        return ae.compute_rolling_correlation(btc, spy, window=60)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_weekly_correlations() -> pd.Series:
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty:
            return pd.Series(dtype=float)
        return ae.compute_weekly_correlations(btc, slv)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_mv_analysis() -> dict:
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty:
            return {}
        return ae.compute_mv_analysis(btc, slv, ae.RF_ANNUAL)
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_marginal_sharpe_curve() -> pd.DataFrame:
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty:
            return pd.DataFrame()
        return ae.compute_marginal_sharpe_curve(btc, slv, ae.SLEEVE_WEIGHTS, ae.RF_ANNUAL)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_drawdown_sensitivity() -> pd.DataFrame:
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty:
            return pd.DataFrame()
        return ae.compute_drawdown_sensitivity(btc, slv, ae.SLEEVE_WEIGHTS, rf_annual=ae.RF_ANNUAL)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=7200, show_spinner=False)
def _load_regime_conditional_correlation() -> pd.DataFrame:
    try:
        btc = _load_btc_returns()
        spy = _load_spy_returns()
        if btc.empty or spy.empty:
            return pd.DataFrame()
        return ae.compute_regime_conditional_correlation(btc, spy, ae.SAMPLE_START)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=7200, show_spinner=False)
def _load_recession_periods() -> list:
    try:
        return get_recession_periods(ae.SAMPLE_START, TODAY)
    except Exception:
        return []


# ── Page ─────────────────────────────────────────────────────────────────────

# 1. Header
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Asset Evaluation Framework")
    st.caption("Structured analysis for evaluating candidate assets for SAA inclusion")
    st.caption(as_of_banner())
    st.divider()

# 2. Framework methodology preamble
_, col, _ = st.columns([1, 8, 1])
with col:
    st.markdown(
        "**Univariate statistics** characterize a candidate asset in isolation: "
        "annualized return, annualized volatility, Sharpe ratio (excess return per unit of risk), "
        "maximum drawdown (peak-to-trough capital loss), skewness (tail asymmetry), "
        "and excess kurtosis (fat-tail risk relative to a normal distribution). "
        "These metrics establish a baseline before asking how the asset interacts with the existing portfolio."
    )
    st.markdown(
        "**Correlation behavior** is evaluated at two frequencies. Full-sample Pearson correlation "
        "against each SAA sleeve measures the average co-movement since 2018. Rolling 60-day "
        "correlation against SPY surfaces regime changes — correlations that look low on average "
        "can spike toward 1 during market stress, precisely when diversification is most needed. "
        "All correlation estimates are restricted to equity market trading days; "
        "BTC trades continuously, so non-trading days are excluded to avoid spurious near-zero "
        "returns inflating diversification metrics."
    )
    st.markdown(
        "**Mean-variance impact** is assessed by computing the tangency (maximum-Sharpe) portfolio "
        "with and without the candidate asset. Both unconstrained (closed-form) and constrained "
        "(max 10% per asset, SLSQP) solutions are presented. The unconstrained result is shown "
        "for completeness only — unconstrained MV optimization is highly sensitive to estimation "
        "error and routinely produces extreme short positions that are not implementable. "
        "The constrained result better reflects a realistic institutional allocation."
    )
    st.markdown(
        "**Decision framework:** this page surfaces tradeoffs, not a single recommendation. "
        "Mean-variance analysis captures return and risk but ignores liquidity, tax treatment, "
        "operational complexity, and mandate fit — all of which are material considerations "
        "for a taxable individual investor. A candidate asset that improves constrained portfolio "
        "Sharpe is a necessary but not sufficient condition for inclusion."
    )
    st.divider()

# 3. Case Study header
_, col, _ = st.columns([1, 8, 1])
with col:
    st.header("Case Study: Bitcoin")
    st.caption(
        f"BTC-USD, {ae.SAMPLE_START} to present · daily returns · 252-day annualization"
    )

# ── Load all data up front ────────────────────────────────────────────────────

btc_ret  = _load_btc_returns()
slv_ret  = _load_sleeve_returns()
spy_ret  = _load_spy_returns()
uni_tbl  = _load_univariate_table()
corr     = _load_full_sample_correlations(
    btc_key=TODAY,   # daily cache bust key
    sleeve_key=TODAY,
)
rolling_corr = _load_rolling_correlation()
weekly_corr  = _load_weekly_correlations()
mv           = _load_mv_analysis()
msc          = _load_marginal_sharpe_curve()
dd_sens      = _load_drawdown_sensitivity()
recession_periods = _load_recession_periods()

data_ok = not btc_ret.empty and not slv_ret.empty

if not data_ok:
    _, col, _ = st.columns([1, 8, 1])
    with col:
        st.warning(
            "Price data unavailable — check internet connection or API rate limits. "
            "All sections below require BTC-USD and sleeve benchmark prices."
        )
    render_footer()
    st.stop()

# ── 5a. Univariate statistics ─────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5a — Univariate Statistics")

    if uni_tbl.empty:
        st.info("Univariate table unavailable.")
    else:
        display_tbl = uni_tbl.copy()
        # Rename columns
        display_tbl = display_tbl.rename(columns={
            "ann_return":   "Ann. Return",
            "ann_vol":      "Ann. Vol",
            "sharpe":       "Sharpe",
            "max_drawdown": "Max Drawdown",
            "skewness":     "Skewness",
            "kurtosis":     "Kurtosis",
        })
        fmt = {
            "Ann. Return":   "{:.1%}",
            "Ann. Vol":      "{:.1%}",
            "Sharpe":        "{:.2f}",
            "Max Drawdown":  "{:.1%}",
            "Skewness":      "{:.2f}",
            "Kurtosis":      "{:.2f}",
        }
        st.dataframe(
            display_tbl.style.format(fmt),
            use_container_width=True,
        )

    st.divider()

# ── 5b. Full-sample correlation heatmap ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5b — Full-Sample Correlation vs SAA Sleeves")

    if corr.empty:
        st.info("Correlation data unavailable.")
    else:
        fig_heat = go.Figure(go.Heatmap(
            z=[[corr[s] for s in ae.SLEEVES]],
            x=ae.SLEEVES,
            y=["BTC"],
            colorscale="RdYlGn",
            zmid=0,
            zmin=-1,
            zmax=1,
            text=[[f"{corr[s]:.2f}" for s in ae.SLEEVES]],
            texttemplate="%{text}",
            showscale=True,
        ))
        fig_heat.update_layout(
            height=160,
            margin=dict(l=60, r=20, t=20, b=80),
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
        fig_heat.update_xaxes(tickangle=45)
        st.plotly_chart(fig_heat, width="stretch", config={"displayModeBar": False})

        # Prose paragraph
        btc_spy_corr  = float(corr.get("US Large Core", float("nan")))
        highest_sleeve = corr.idxmax()
        highest_val    = float(corr.max())
        lowest_sleeve  = corr.idxmin()
        lowest_val     = float(corr.min())
        fi_corr        = float(corr.get("Core Fixed Income", float("nan")))
        ra_corr        = float(corr.get("Real Assets", float("nan")))

        if not np.isnan(btc_spy_corr) and btc_spy_corr > 0.3:
            spy_note = (
                f"BTC's full-sample correlation with US Large Core is {btc_spy_corr:.2f}, "
                "suggesting equity-like co-movement since 2018 — meaningfully above zero and "
                "inconsistent with the uncorrelated-alternative characterization that dominated "
                "pre-2020 narratives. "
            )
        else:
            spy_note = (
                f"BTC's full-sample correlation with US Large Core is {btc_spy_corr:.2f}. "
            )

        fi_note = ""
        if not np.isnan(fi_corr):
            fi_note = (
                f"The Core Fixed Income correlation ({fi_corr:.2f}) is near zero, "
                "which is consistent with Bitcoin offering some diversification relative "
                "to duration — though this says nothing about behavior during equity stress. "
            )

        ra_note = ""
        if not np.isnan(ra_corr):
            ra_note = (
                f"The Real Assets correlation ({ra_corr:.2f}) is relevant to the 'digital gold' "
                "thesis: if Bitcoin were a genuine inflation hedge or commodity substitute, "
                "one would expect higher co-movement with this sleeve. "
            )

        st.markdown(
            spy_note
            + f"The highest sleeve correlation is {highest_sleeve} ({highest_val:.2f}) "
            f"and the lowest is {lowest_sleeve} ({lowest_val:.2f}). "
            + fi_note
            + ra_note
        )

    st.divider()

# ── 5c. Rolling 60-day BTC-SPY correlation ────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5c — Rolling 60-Day Correlation: BTC vs SPY")

    if rolling_corr.empty:
        st.info("Rolling correlation data unavailable.")
    else:
        fig_roll = go.Figure()

        # Recession shading
        for rec_start, rec_end in recession_periods:
            fig_roll.add_vrect(
                x0=str(rec_start),
                x1=str(rec_end),
                fillcolor="lightgray",
                opacity=0.35,
                line_width=0,
                layer="below",
            )

        # Zero reference line
        fig_roll.add_hline(y=0, line_dash="dash", line_color="#9E9E9E", line_width=1)

        # Rolling correlation
        fig_roll.add_trace(go.Scatter(
            x=rolling_corr.index,
            y=rolling_corr.values,
            mode="lines",
            name="BTC-SPY 60d corr",
            line=dict(color="#2E4057", width=1.5),
        ))

        fig_roll.update_layout(
            height=280,
            margin=dict(l=0, r=0, t=8, b=0),
            paper_bgcolor="white",
            plot_bgcolor="#FAFAFA",
            showlegend=False,
            hovermode="x unified",
            yaxis=dict(gridcolor="#EBEBEB", range=[-1.05, 1.05]),
            xaxis=dict(gridcolor="#EBEBEB"),
        )
        st.plotly_chart(fig_roll, width="stretch", config={"displayModeBar": False})

        # Prose
        post_2020 = rolling_corr.loc["2020-01-01":]
        post_2020_avg = float(post_2020.mean()) if len(post_2020) > 0 else float("nan")
        pre_2020  = rolling_corr.loc[:"2019-12-31"]
        pre_2020_avg  = float(pre_2020.mean())  if len(pre_2020) > 0 else float("nan")

        pre_note = (
            f"Prior to 2020, BTC's rolling 60-day correlation with SPY averaged "
            f"near {pre_2020_avg:.2f}, broadly consistent with the 'uncorrelated alternative' "
            "narrative that drove early institutional interest. "
        ) if not np.isnan(pre_2020_avg) else ""

        post_note = ""
        if not np.isnan(post_2020_avg):
            post_note = (
                f"Post-COVID, the correlation has remained persistently elevated at "
                f"{post_2020_avg:.2f} on average, undermining the diversification claim. "
                "The elevated post-2020 correlation likely reflects the institutionalization "
                "of crypto markets: as Bitcoin entered professional portfolios, it began trading "
                "with the risk-on/risk-off dynamics that characterize equity markets, "
                "reducing its value as a genuine diversifier precisely when correlations "
                "are most costly."
            )

        st.markdown(pre_note + post_note)

    st.divider()

# ── 5d. Robustness: weekly correlations ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5d — Robustness: Daily vs Weekly Correlations")

    if corr.empty or weekly_corr.empty:
        st.info("Correlation data unavailable.")
    else:
        daily_corr  = corr.reindex(ae.SLEEVES)
        weekly_corr_aligned = weekly_corr.reindex(ae.SLEEVES)
        diff_corr   = weekly_corr_aligned - daily_corr

        robust_tbl = pd.DataFrame({
            "Sleeve":           ae.SLEEVES,
            "Daily Corr":       daily_corr.values,
            "Weekly Corr":      weekly_corr_aligned.values,
            "Difference":       diff_corr.values,
        })
        st.dataframe(
            robust_tbl.style.format({
                "Daily Corr":  "{:.2f}",
                "Weekly Corr": "{:.2f}",
                "Difference":  "{:.2f}",
            }),
            hide_index=True,
            use_container_width=True,
        )

        st.markdown(
            "Weekly correlation uses Friday-to-Friday compounded returns to reduce "
            "the effect of asynchronous trading across markets — BTC trades continuously "
            "while equity benchmarks observe exchange hours and holiday schedules. "
            "Small differences between daily and weekly estimates indicate the results "
            "are not driven by microstructure noise; large differences would suggest "
            "the daily figure is materially distorted by settlement lags or thin-market days."
        )

    st.divider()

# ── 5e. MV optimization — unconstrained ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5e — Mean-Variance: Unconstrained Tangency")

    if not mv:
        st.info("Mean-variance analysis unavailable.")
    else:
        sleeves_list = mv["sleeves"]
        w_unc_no   = mv["w_unc_no"]
        w_unc_with = mv["w_unc_with"]   # length = n_sleeves + 1 (BTC last)
        sharpe_unc_no   = mv["sharpe_unc_no"]
        sharpe_unc_with = mv["sharpe_unc_with"]

        left_col, right_col = st.columns([1, 1])

        with left_col:
            st.caption(
                "Unconstrained optimization produces extreme / short positions — "
                "presented for completeness only."
            )
            unc_tbl = pd.DataFrame({
                "Sleeve":        sleeves_list + ["BTC"],
                "w/o BTC":       list(w_unc_no) + [float("nan")],
                "w/ BTC":        list(w_unc_with),
            })
            st.dataframe(
                unc_tbl.style.format({
                    "w/o BTC": lambda v: f"{v:.1%}" if not np.isnan(v) else "—",
                    "w/ BTC":  "{:.1%}",
                }),
                hide_index=True,
                use_container_width=True,
            )

        with right_col:
            delta_bps = (sharpe_unc_with - sharpe_unc_no) * 10_000
            st.metric(
                "Sharpe without BTC (unconstrained)",
                f"{sharpe_unc_no:.3f}",
            )
            st.metric(
                "Sharpe with BTC (unconstrained)",
                f"{sharpe_unc_with:.3f}",
                delta=f"{delta_bps:+.0f} bps",
            )

        st.markdown(
            "Unconstrained MV optimization is a mathematical exercise, not a portfolio "
            "construction tool. With 9–10 assets, the optimizer frequently assigns extreme "
            "long and short positions because small estimation errors in expected returns "
            "or covariances are magnified through matrix inversion. The unconstrained weights "
            "above should be read as showing which assets dominate the tangency frontier "
            "in mean-variance space, not as actionable allocations. "
            "The constrained result in the next section is the operationally relevant case."
        )

    st.divider()

# ── 5f. MV optimization — constrained ────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5f — Mean-Variance: Constrained Tangency (max 10%)")

    if not mv:
        st.info("Mean-variance analysis unavailable.")
    else:
        sleeves_list = mv["sleeves"]
        w_con_no    = mv["w_con_no"]
        w_con_with  = mv["w_con_with"]
        sharpe_con_no   = mv["sharpe_con_no"]
        sharpe_con_with = mv["sharpe_con_with"]
        delta_bps_con   = (sharpe_con_with - sharpe_con_no) * 10_000

        con_tbl = pd.DataFrame({
            "Sleeve":        sleeves_list + ["BTC"],
            "w/o BTC":       list(w_con_no) + [float("nan")],
            "w/ BTC":        list(w_con_with),
        })
        st.dataframe(
            con_tbl.style.format({
                "w/o BTC": lambda v: f"{v:.1%}" if not np.isnan(v) else "—",
                "w/ BTC":  "{:.1%}",
            }),
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "Note: with 9 sleeves and a 10% per-asset cap, the constraint Σw=1 is "
            "infeasible at 10% uniform (9×10%=90%<100%), so the optimizer returns "
            "approximately 11.1% per sleeve. With 10 assets including BTC, "
            "the cap is binding and all weights converge to 10%."
        )

        st.markdown(
            f"Under realistic institutional constraints (max 10% per sleeve), "
            "the mean-variance optimizer assigns Bitcoin to its maximum allowable "
            f"weight of 10%, suggesting marginal Sharpe improvement at each incremental "
            f"BTC allocation up to the constraint. "
            f"The constrained Sharpe rises from {sharpe_con_no:.3f} (without BTC) "
            f"to {sharpe_con_with:.3f} (with BTC), an improvement of {delta_bps_con:.0f} bps. "
            "This result is driven by BTC's high expected return over the sample period — "
            "it does not account for estimation error in the mean, which is extremely large "
            "for a 7-year history of a volatile, regime-shifting asset."
        )

    st.divider()

# ── 5g. Marginal Sharpe curve ─────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5g — Marginal Sharpe Contribution")

    if msc.empty:
        st.info("Marginal Sharpe curve unavailable.")
    else:
        fig_msc = go.Figure()
        fig_msc.add_trace(go.Scatter(
            x=msc["btc_alloc"] * 100,
            y=msc["sharpe"],
            mode="lines+markers",
            line=dict(color="#2E4057", width=2),
            marker=dict(size=7),
            name="Portfolio Sharpe",
        ))
        fig_msc.update_layout(
            height=280,
            margin=dict(l=0, r=0, t=8, b=0),
            paper_bgcolor="white",
            plot_bgcolor="#FAFAFA",
            xaxis=dict(
                title="BTC Allocation (%)",
                gridcolor="#EBEBEB",
            ),
            yaxis=dict(
                title="Portfolio Sharpe",
                gridcolor="#EBEBEB",
            ),
            hovermode="x unified",
        )
        st.plotly_chart(fig_msc, width="stretch", config={"displayModeBar": False})

        sharpe_0   = float(msc.loc[msc["btc_alloc"] == msc["btc_alloc"].min(), "sharpe"].iloc[0])
        sharpe_max = float(msc["sharpe"].max())
        btc_at_max = float(msc.loc[msc["sharpe"].idxmax(), "btc_alloc"]) * 100
        monotone   = all(
            msc["sharpe"].iloc[i] <= msc["sharpe"].iloc[i + 1]
            for i in range(len(msc) - 1)
        )

        if monotone:
            curve_desc = (
                f"The Sharpe curve is monotonically increasing across the BTC allocation range, "
                f"rising from {sharpe_0:.3f} at 0% BTC to {sharpe_max:.3f} at {btc_at_max:.0f}% BTC. "
                "This means each incremental unit of BTC improves the risk-adjusted portfolio "
                "return within this allocation range, driven primarily by BTC's high realized "
                "return over the sample period."
            )
        else:
            curve_desc = (
                f"The Sharpe curve peaks at {btc_at_max:.0f}% BTC allocation ({sharpe_max:.3f}), "
                f"rising from {sharpe_0:.3f} at 0% and declining thereafter. "
                "Beyond the peak, BTC's volatility begins to dominate and erodes risk-adjusted returns."
            )

        st.markdown(
            curve_desc
            + " The curve reflects sample-period arithmetic — BTC's realized return since 2018 "
            "is exceptional and should not be extrapolated as expected return for forward-looking "
            "allocation decisions."
        )

    st.divider()

# ── 5h. Drawdown sensitivity ──────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5h — Drawdown Sensitivity")

    if dd_sens.empty:
        st.info("Drawdown sensitivity data unavailable.")
    else:
        st.dataframe(
            dd_sens.style.format({
                "CAGR":     "{:.1%}",
                "Max DD":   "{:.1%}",
                "Sharpe":   "{:.2f}",
                "2022 MDD": "{:.1%}",
            }),
            hide_index=True,
            use_container_width=True,
        )

        # Compute incremental max DD at 10% BTC vs 0%
        dd_0_row  = dd_sens[dd_sens["BTC Alloc"] == "0%"]
        dd_10_row = dd_sens[dd_sens["BTC Alloc"] == "10%"]
        dd_note = ""
        if not dd_0_row.empty and not dd_10_row.empty:
            dd_0   = float(dd_0_row["Max DD"].iloc[0])
            dd_10  = float(dd_10_row["Max DD"].iloc[0])
            mdd22_0  = float(dd_0_row["2022 MDD"].iloc[0])
            mdd22_10 = float(dd_10_row["2022 MDD"].iloc[0])
            dd_note = (
                f"Adding a 10% BTC allocation widens portfolio maximum drawdown "
                f"from {dd_0:.1%} to {dd_10:.1%} over the full sample. "
                f"In 2022 specifically — when BTC fell approximately 65% and equities "
                f"sold off simultaneously — the portfolio max drawdown during that calendar year "
                f"shifts from {mdd22_0:.1%} (0% BTC) to {mdd22_10:.1%} (10% BTC). "
            )

        st.markdown(
            dd_note
            + "2022 is the most important stress-test period for evaluating Bitcoin's "
            "portfolio impact: equities fell roughly 20%, bonds fell 15%, and BTC fell "
            "over 60% — all simultaneously, eliminating any diversification benefit "
            "and amplifying drawdown. This joint stress scenario, not the full-sample "
            "average correlation, is the relevant risk scenario for a risk-aware allocator."
        )

    st.divider()

# ── 5i. Regime-conditional correlation ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5i — Regime-Conditional Correlation")

    try:
        regime_corr = _load_regime_conditional_correlation()
        if regime_corr.empty:
            st.info("Regime data unavailable — requires FRED API key.")
        else:
            st.dataframe(
                regime_corr.style.format({
                    "Correlation": lambda v: f"{v:.2f}" if not np.isnan(v) else "—",
                    "N Obs":       "{:.0f}",
                }),
                hide_index=True,
                use_container_width=True,
            )

            # Interpret regime table
            rec_row  = regime_corr[regime_corr["Regime"] == "Recession"]
            late_row = regime_corr[regime_corr["Regime"] == "Late-cycle"]
            mid_row  = regime_corr[regime_corr["Regime"] == "Mid-cycle"]

            rec_corr  = float(rec_row["Correlation"].iloc[0])  if not rec_row.empty  else float("nan")
            late_corr = float(late_row["Correlation"].iloc[0]) if not late_row.empty else float("nan")
            mid_corr  = float(mid_row["Correlation"].iloc[0])  if not mid_row.empty  else float("nan")

            rec_note = ""
            if not np.isnan(rec_corr):
                if rec_corr > 0.4:
                    rec_note = (
                        f"During recession periods, BTC's correlation with SPY rises to {rec_corr:.2f}, "
                        "consistent with the risk-off behavior observed during COVID-19 (2020) "
                        "and the 2022 bear market. "
                    )
                else:
                    rec_note = (
                        f"During recession periods, BTC's correlation with SPY is {rec_corr:.2f}. "
                    )

            late_note = ""
            if not np.isnan(late_corr) and not np.isnan(mid_corr):
                late_note = (
                    f"The late-cycle correlation ({late_corr:.2f}) versus mid-cycle ({mid_corr:.2f}) "
                    "comparison is particularly relevant: if Bitcoin behaves as a risk asset "
                    "precisely when the portfolio most needs a hedge, its diversification value "
                    "is fundamentally compromised."
                )

            st.markdown(
                rec_note + late_note
                + " Regime-conditional analysis uses FRED USREC, T10Y2Y, and UNRATE data; "
                "limited recession observations since 2018 may make the recession estimate noisy."
            )

    except Exception:
        st.info("Regime data unavailable — requires FRED API key.")

    st.divider()

# ── 5j. Decision framework summary ───────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5j — Decision Framework Summary")

    # Build argument lists dynamically
    args_for: list[str] = []
    args_against: list[str] = []

    if mv:
        sharpe_con_no   = mv["sharpe_con_no"]
        sharpe_con_with = mv["sharpe_con_with"]
        if sharpe_con_with > sharpe_con_no:
            delta_bps_j = (sharpe_con_with - sharpe_con_no) * 10_000
            args_for.append(
                f"Sharpe-improving under constrained MV optimization "
                f"({sharpe_con_no:.3f} → {sharpe_con_with:.3f}, +{delta_bps_j:.0f} bps)"
            )

    if not corr.empty:
        low_corr_sleeves = [s for s in ae.SLEEVES if not np.isnan(corr.get(s, float("nan"))) and corr[s] < 0.1]
        for s in low_corr_sleeves:
            args_for.append(f"Low correlation ({corr[s]:.2f}) with {s}")

        btc_spy_j = float(corr.get("US Large Core", float("nan")))
        if not np.isnan(btc_spy_j) and btc_spy_j > 0.3:
            args_against.append(
                f"Equity-like correlation with US Large Core ({btc_spy_j:.2f}) post-2020 — "
                "co-movement spikes during stress precisely when a hedge is most valuable"
            )

    args_against.extend([
        "Maximum historical drawdown exceeding 80% — three separate episodes since 2018; "
        "2022 coincided with equity and bond losses (no diversification benefit when needed)",
        "Capital gains tax treatment: BTC is a commodity under US tax law, "
        "generating short-term ordinary income on positions held under 12 months "
        "and long-term gains on positions held over 12 months — unfavorable vs. ETFs "
        "which qualify for in-kind creation/redemption",
        "Operational complexity: self-custody introduces key management risk; "
        "exchange-held BTC introduces counterparty risk (FTX 2022); "
        "ETF wrappers (IBIT, FBTC) add 0.12–0.25% in fees and may not be available "
        "in all account types",
        "No intrinsic cash flow, earnings, or fundamental anchor for valuation — "
        "expected return is purely sentiment-driven, making MV inputs highly unreliable",
    ])

    for_bullets  = "\n".join(f"- {a}" for a in args_for)  if args_for  else "- None identified from available data"
    against_bullets = "\n".join(f"- {a}" for a in args_against)

    st.markdown(f"**Arguments for inclusion:**\n{for_bullets}")
    st.markdown(f"**Arguments against inclusion:**\n{against_bullets}")

    st.markdown(
        "**Allocator-side considerations:**\n"
        "- **Liquidity:** BTC is highly liquid on a 24/7 basis, but institutional "
        "liquidity in regulated ETF wrappers is limited to equity market hours\n"
        "- **Tax:** commodity classification, no qualified-dividend treatment, "
        "wash-sale rules do not apply (potential harvesting benefit)\n"
        "- **Operational:** custodial complexity; ETF alternatives simplify ops "
        "at the cost of fee drag and tracking risk\n"
        "- **Mandate fit:** no explicit SAA mandate; would require a new sleeve "
        "or an overweight to Real Assets with a sub-allocation"
    )

    st.markdown(
        "**Conclusion:** Bitcoin's sample-period Sharpe improvement is real but almost "
        "entirely attributable to its exceptional 2020–2021 bull market return. "
        "The post-2020 correlation structure, the 2022 joint drawdown, and the "
        "fundamental absence of cash flows make a strong case against inclusion "
        "in a tax-aware taxable account with an institutional-style SAA. "
        "The framework does not foreclose future re-evaluation if: (1) the "
        "correlation regime reverts toward zero, (2) regulated custodial solutions "
        "reduce operational risk materially, or (3) the asset develops a cleaner "
        "valuation framework. This page will update automatically as new data arrives."
    )

    st.divider()

# ── 6. Methodology expander ───────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    with st.expander("Methodology", expanded=False):
        st.markdown(
            "**Data source:** Yahoo Finance BTC-USD daily prices retrieved via "
            "`src/prices.py` (SQLite-cached; refreshes on cache miss). "
            "Sleeve benchmark prices use the same fetcher and the tickers defined "
            "in `src/asset_evaluation.SLEEVE_BENCHMARKS`.\n\n"
            f"**Date range:** {ae.SAMPLE_START} to present. BTC-USD data available "
            "from approximately 2014; 2018-01-01 is used as the start date because "
            "it precedes the first major post-ICO bear market (2018) and provides a "
            "full bull/bear cycle for statistical estimation.\n\n"
            "**Return computation:** Daily pct_change on adj_close. BTC adj_close "
            "equals close (no dividends or splits in the traditional sense).\n\n"
            f"**Annualization:** {ae.TRADING_DAYS} trading days. Annualized return "
            "uses geometric compounding: (∏(1+r_t))^(252/n) − 1. "
            "Annualized volatility uses sample standard deviation × √252.\n\n"
            "**Trading-day alignment:** BTC trades continuously; all correlation and "
            "MV estimates are restricted to dates present in both the BTC index and "
            "the equity sleeve returns index. Rows where all equity sleeves show "
            "near-zero absolute returns (non-trading days, holidays) are excluded "
            "per the Phase 11 zero-return filtering convention.\n\n"
            "**Correlation:** Pearson correlation coefficient. Full-sample uses all "
            "aligned trading days since 2018-01-01. Rolling uses a 60-day trailing "
            "window. Weekly resamples to Friday-to-Friday cumulative returns before "
            "correlating.\n\n"
            "**MV optimization:** Unconstrained tangency computed via closed-form "
            "solution w* = Σ⁻¹(μ − rf·1) / [1'Σ⁻¹(μ − rf·1)], where μ and Σ are "
            "sample daily mean and covariance. Constrained tangency uses "
            "scipy.optimize.minimize (SLSQP) with bounds 0 ≤ w_i ≤ 10% and Σw = 1, "
            "maximizing annualized Sharpe.\n\n"
            f"**Risk-free rate:** {ae.RF_ANNUAL:.2%} annual ({ae.RF_ANNUAL * 100:.2f} bps), "
            "consistent with the Performance page Sharpe disclosure. "
            "Converted to daily as rf_annual / 252 for MV optimization internals.\n\n"
            "**Regime classification:** FRED USREC (NBER monthly indicator), T10Y2Y "
            "(daily yield-curve spread), and UNRATE (monthly unemployment) are fetched "
            "via `src/macro.get_series()` and vectorized into four labels: "
            "Recession, Early-cycle, Mid-cycle, Late-cycle — using the same priority "
            "ordering as `src/macro.classify_regime()`."
        )

render_footer()
