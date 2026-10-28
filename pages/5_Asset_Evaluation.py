"""Asset Evaluation Framework — structured analysis for SAA candidate assets."""
from datetime import date

import streamlit as st

st.set_page_config(page_title="Asset Evaluation", layout="wide")

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src import asset_evaluation as ae
from src.asof import as_of_banner
from src import macro
from src.prices import is_valid_ticker
from src.prose_helpers import a_or_an
from src.ui_helpers import render_footer, render_page_header

_c = ae.fmt_corr
render_page_header()


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
def _load_price_gap_note(screened: str) -> "str | None":
    return ae.price_gap_note(screened=screened)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_spy_returns() -> pd.Series:
    try:
        return ae.get_candidate_returns("SPY", ae.SAMPLE_START)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_candidate_returns(ticker: str) -> pd.Series:
    """Daily returns for an arbitrary candidate ticker (Candidate Screen)."""
    try:
        return ae.get_candidate_returns(ticker, ae.SAMPLE_START)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_univariate_table() -> pd.DataFrame:
    try:
        return ae.build_univariate_table(rf_annual=_rf_annual())
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
def _load_risk_free():
    """(rate, reason): the sample's average 3-month Treasury bill rate (audit item
    15a), or None and why there is none."""
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty:
            return None, "the return series are empty"
        return ae.sample_risk_free(btc, slv), None
    except Exception as exc:
        return None, str(exc)


def _rf_annual() -> float:
    _r = _load_risk_free()[0]
    return _r.annual if _r is not None else float("nan")


@st.cache_data(ttl=3600, show_spinner=False)
def _load_mv_analysis() -> dict:
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty or _load_risk_free()[0] is None:
            return {}
        return ae.compute_mv_analysis(btc, slv, _rf_annual())
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_marginal_sharpe_curve() -> pd.DataFrame:
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty or _load_risk_free()[0] is None:
            return pd.DataFrame()
        return ae.compute_marginal_sharpe_curve(btc, slv, ae.sleeve_weights(), _rf_annual())
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_drawdown_sensitivity() -> pd.DataFrame:
    try:
        btc = _load_btc_returns()
        slv = _load_sleeve_returns()
        if btc.empty or slv.empty or _load_risk_free()[0] is None:
            return pd.DataFrame()
        return ae.compute_drawdown_sensitivity(btc, slv, ae.sleeve_weights(), rf_annual=_rf_annual())
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
def _load_recessions() -> "tuple[list, str | None]":
    """(periods, stored_on). Past FRED's wait (#435) the shading comes from the stored
    USREC copy, and stored_on is the date it was fetched; None for today's series."""
    try:
        usrec, stored_on = macro.series_or_stored("USREC", "1945-01-01")
        return macro.recession_periods(usrec, ae.SAMPLE_START, TODAY), stored_on
    except Exception:
        return [], None


def _load_recession_periods() -> list:
    return _load_recessions()[0]


# ── Page ─────────────────────────────────────────────────────────────────────

# 1. Header
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Asset Evaluation Framework")
    st.caption("Worked five-step framework applied to Bitcoin as a candidate for SAA inclusion.")
    st.caption(as_of_banner())
    st.divider()

# 2. How to read this page + framework methodology preamble
_, col, _ = st.columns([1, 8, 1])
with col:
    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "This page applies a five-step framework to Bitcoin as a worked case study for SAA "
            "inclusion. The framework is **descriptive, not prescriptive**: its numbers quantify "
            "observed behavior over the 2018–present sample period and are not a forecast.\n\n"
            "**Sections in order:** univariate statistics (5a) → correlation behavior: "
            "full-sample (5b), rolling 60-day (5c), and daily against weekly (5d) → "
            "mean-variance tangency, unconstrained (5e) and constrained (5f) → marginal Sharpe "
            "contribution (5g) → drawdown sensitivity (5h) → regime-conditional correlation by "
            "NBER cycle phase (5i) → decision framework (5j).\n\n"
            "The **decision framework** section is the conclusion. It separates sample-period "
            "arithmetic (which favors high-return assets mechanically) from forward-looking "
            "allocator considerations: liquidity, tax treatment, operational complexity, mandate "
            "fit, and expected behavior under different macro regimes. Those factors cannot be "
            "reduced to a Sharpe improvement number.\n\n"
            "**Sample start:** 2018-01-01. Earlier BTC history exists but covers only one "
            "bull market. Starting from 2018 captures the 2018 bear, the 2020 COVID crash, "
            "the 2021 peak, and the 2022 drawdown, which together give a more complete picture "
            "of the asset's behavior."
        )
    st.markdown(
        "**Univariate statistics** characterize a candidate asset in isolation. They are "
        "annualized return, annualized volatility, Sharpe ratio (excess return per unit of risk), "
        "maximum drawdown (peak-to-trough capital loss), skewness (tail asymmetry), "
        "and excess kurtosis (fat-tail risk relative to a normal distribution). "
        "These metrics set a baseline before the page turns to how the asset interacts with "
        "the existing portfolio."
    )
    st.markdown(
        "**Correlation behavior** is evaluated over two windows. Full-sample Pearson correlation "
        "against each SAA sleeve measures the average co-movement since 2018. Rolling 60-day "
        "correlation against SPY shows regime changes: correlations that look low on average "
        "can spike toward 1 during market stress, when diversification is most needed. "
        "All correlation estimates are restricted to equity market trading days. "
        "BTC trades continuously, so non-trading days are excluded to keep spurious near-zero "
        "returns from inflating the diversification metrics."
    )
    st.markdown(
        "**Mean-variance impact** is assessed by computing the tangency (maximum-Sharpe) portfolio "
        "with and without the candidate asset. Both unconstrained (closed-form) and constrained "
        "(max 25% per sleeve, SLSQP) solutions are presented. The unconstrained result is shown "
        "for completeness only: unconstrained MV optimization is highly sensitive to estimation "
        "error and routinely produces extreme short positions that cannot be implemented. "
        "The constrained result better reflects a realistic institutional allocation."
    )
    st.markdown(
        "Mean-variance optimization is presented as a directional sanity check, "
        "not a portfolio recommendation. The instability of sample-period mean estimates "
        "and the documented sensitivity of MVO weights to small perturbations (Michaud 1989) "
        "make raw optimizer output unsuitable as an allocation tool. The primary analytical "
        "frame is marginal contribution against the existing SAA target weights: what the "
        "candidate does to the actual portfolio."
    )
    st.markdown(
        "**Decision framework:** the page ends with its conclusion (5j). "
        "Mean-variance analysis captures return and risk but ignores liquidity, tax treatment, "
        "operational complexity, and mandate fit, all of them material considerations "
        "for a taxable individual investor. An improvement in constrained portfolio Sharpe "
        "is necessary for inclusion but does not justify it on its own."
    )
    st.divider()

# 2.5 Candidate Correlation Screen (generic — any ticker)
_, col, _ = st.columns([1, 8, 1])
with col:
    st.header("Candidate Correlation Screen")
    st.caption(
        "Screen any candidate ticker for its correlation to the existing SAA sleeves: a "
        "quantitative check of whether it diversifies the book or doubles down on it. This is "
        "a lightweight, generic screen; the five-step framework below is the worked "
        "Bitcoin case study."
    )

    cand_ticker = st.text_input("Candidate ticker", value="QQQ", key="cand_ticker").strip().upper()

    _scr_sleeves = _load_sleeve_returns()
    _scr_recessions = _load_recession_periods()

    if not cand_ticker:
        st.info("Enter a ticker to screen its correlation to the SAA sleeves.")
    elif not is_valid_ticker(cand_ticker):
        # Rejected on FORMAT, before any request. Distinct from the
        # "fetched but found nothing" case below: this input could not be a
        # symbol at all, so say that rather than blaming the connection.
        # The echo is truncated and code-quoted: st.warning escapes HTML, but
        # this box is on the public demo, so reflecting an arbitrary-length
        # string back at the page earns nothing.
        _shown = cand_ticker[:20] + ("…" if len(cand_ticker) > 20 else "")
        st.warning(
            f"`{_shown}` isn't a valid ticker format. Use an exchange-listed "
            "symbol of up to 15 characters (e.g. QQQ, TLT, BRK-B): letters, "
            "digits, and `.` `-` `=` `^` only."
        )
    elif _scr_sleeves.empty:
        st.warning("Sleeve benchmark prices unavailable. Check the connection or API limits.")
    else:
        with st.spinner(f"Screening {cand_ticker}…"):
            cand_ret = _load_candidate_returns(cand_ticker)

        if cand_ret.empty:
            st.warning(
                f"Couldn't fetch data for **{cand_ticker}**. Check the symbol "
                "(e.g. QQQ, TLT, GLD, VNQ) and use a valid exchange-listed ticker."
            )
        else:
            per_sleeve = ae.compute_full_sample_correlations(cand_ret, _scr_sleeves)
            per_sleeve = per_sleeve.reindex(ae.sleeve_names()).dropna()

            if per_sleeve.empty:
                st.warning(f"Not enough overlapping history to correlate {cand_ticker}.")
            else:
                avg_corr = float(per_sleeve.mean())

                # (a) Per-sleeve correlation — the headline (sorted, RdYlGn like 5b)
                _ps_sorted = per_sleeve.sort_values()  # ascending → highest at top of h-bar
                fig_scr = go.Figure(go.Bar(
                    x=_ps_sorted.values,
                    y=list(_ps_sorted.index),
                    orientation="h",
                    marker=dict(
                        color=_ps_sorted.values, colorscale="RdYlGn",
                        cmid=0, cmin=-0.5, cmax=1.0,
                        colorbar=dict(title="ρ", thickness=12),
                    ),
                    text=[f"{v:+.2f}" for v in _ps_sorted.values],
                    textposition="auto",
                    hovertemplate="%{y}: ρ = %{x:.2f}<extra></extra>",
                ))
                fig_scr.add_vline(x=0, line_dash="dash", line_color="#9E9E9E", line_width=1)
                fig_scr.update_layout(
                    height=320,
                    margin=dict(l=0, r=0, t=8, b=0),
                    paper_bgcolor="white",
                    plot_bgcolor="#FAFAFA",
                    xaxis=dict(range=[-1.05, 1.05], gridcolor="#EBEBEB",
                               title="Full-sample correlation (ρ)"),
                    yaxis=dict(tickfont_size=11),
                )
                st.plotly_chart(fig_scr, width="stretch", config={"displayModeBar": False})

                # (b) Average + verdict
                st.metric("Avg correlation to SAA sleeves", f"{avg_corr:+.2f}")
                st.markdown(
                    ae.interpret_candidate_diversification(avg_corr, per_sleeve, cand_ticker)
                )

                # (c) Rolling candidate-to-sleeves average over time
                roll_set = ae.rolling_correlation_to_set(cand_ret, _scr_sleeves, window=60)
                if not roll_set.empty:
                    fig_rs = go.Figure()
                    for _rs, _re in _scr_recessions:
                        fig_rs.add_vrect(
                            x0=str(_rs), x1=str(_re),
                            fillcolor="lightgray", opacity=0.35,
                            line_width=0, layer="below",
                        )
                    fig_rs.add_hline(y=0, line_dash="dash", line_color="#9E9E9E", line_width=1)
                    fig_rs.add_hline(
                        y=avg_corr, line_dash="dot", line_color="#9E9E9E", line_width=1,
                        annotation_text=f"Full-sample avg {avg_corr:+.2f}",
                        annotation_position="top left", annotation_font_size=9,
                        annotation_font_color="#888",
                    )
                    fig_rs.add_trace(go.Scatter(
                        x=roll_set.index, y=roll_set.values, mode="lines",
                        name=f"{cand_ticker} 60d avg corr to sleeves",
                        line=dict(color="#2E4057", width=1.5),
                    ))
                    fig_rs.update_layout(
                        height=280,
                        margin=dict(l=0, r=0, t=8, b=0),
                        paper_bgcolor="white",
                        plot_bgcolor="#FAFAFA",
                        showlegend=False,
                        hovermode="x unified",
                        yaxis=dict(range=[-1.05, 1.05], gridcolor="#EBEBEB",
                                   title="Avg rolling corr to sleeves (ρ)"),
                        xaxis=dict(gridcolor="#EBEBEB"),
                    )
                    st.plotly_chart(fig_rs, width="stretch", config={"displayModeBar": False})
                    _rs_cur = float(roll_set.iloc[-1])
                    st.caption(
                        f"{cand_ticker}'s average rolling 60-day correlation to the "
                        f"{len(_scr_sleeves.columns)} SAA "
                        f"sleeves (recession-shaded). Current ρ ≈ {_rs_cur:+.2f} vs the "
                        f"full-sample average of {avg_corr:+.2f}. A line that rises in the "
                        "shaded stress windows means the candidate's diversification weakens "
                        "when it is most needed."
                    )

                # (d) Stress-vs-normal: regime-conditional correlation vs SPY (reuses 5i helper)
                _spy = _load_spy_returns()
                if not _spy.empty:
                    try:
                        _reg = ae.compute_regime_conditional_correlation(
                            cand_ret, _spy, ae.SAMPLE_START
                        )
                        if not _reg.empty and _reg["Correlation"].notna().any():
                            with st.expander("Stress-vs-normal: correlation vs SPY by regime", expanded=False):
                                st.dataframe(
                                    _reg.style.format({
                                        "Correlation": lambda v: f"{v:.2f}" if not np.isnan(v) else "—",
                                        "N Obs": "{:.0f}",
                                    }),
                                    hide_index=True, use_container_width=True,
                                )
                                st.caption(
                                    f"{cand_ticker} vs SPY Pearson correlation conditioned on the "
                                    "NBER cycle phase (FRED USREC/T10Y2Y/UNRATE). A recession "
                                    "or late-cycle reading above the mid-cycle one means the "
                                    "candidate couples to equities in stress."
                                )
                    except Exception:
                        pass

                st.caption(
                    "Full-sample and rolling correlations are backward-looking and restricted "
                    "to equity trading days since 2018. This screen measures *diversification "
                    "overlap* with the existing SAA; it does not assess expected return, "
                    "valuation, or fit. A low correlation is necessary for inclusion but does "
                    "not justify it on its own."
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
def _rf_line() -> str:
    """The rate this page's Sharpe figures use, and where it came from."""
    from src import risk_free
    _r, _why = _load_risk_free()
    if _r is None:
        return (f"not available ({_why}), so the Sharpe figures and the mean-variance "
                "analysis are not shown.")
    return risk_free.describe(_r, name_series=True, window="the sample's own window") + "."


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


def _stored_fred_note() -> "str | None":
    """Which FRED series this page shows as stored copies, and the date each was
    fetched (#435): past the wait, the page renders what is stored rather than hold on
    FRED. The risk-free rate says so in its own line."""
    stored = {}
    if _load_recessions()[1]:
        stored["USREC (recession shading)"] = _load_recessions()[1]
    for sid, on in _load_regime_conditional_correlation().attrs.get("fred_stored", {}).items():
        stored[f"{sid} (regime table)"] = on
    if not stored:
        return None
    from src.asof import format_long_date
    return (f"Today's FRED data did not arrive within {macro.PAGE_WAIT_SECONDS:.0f} "
            "seconds, so these are shown as stored, with the date each was fetched: "
            + "; ".join(f"{k}, {format_long_date(v)}" for k, v in stored.items())
            + ". The fetch continues in the background; reload the page to read it.")


_fred_note = _stored_fred_note()
if _fred_note:
    _, col, _ = st.columns([1, 8, 1])
    with col:
        st.info(_fred_note)

# A close the book lacks inside the history of a ticker read above, named with its
# date. Cached as the series are, so the note describes the data the figures came from.
_gap_note = _load_price_gap_note(cand_ticker)
if _gap_note:
    _, col, _ = st.columns([1, 8, 1])
    with col:
        st.info(_gap_note)

data_ok = not btc_ret.empty and not slv_ret.empty

if not data_ok:
    _, col, _ = st.columns([1, 8, 1])
    with col:
        st.warning(
            "Price data unavailable. Check the internet connection or API rate limits. "
            "All sections below require BTC-USD and sleeve benchmark prices."
        )
    render_footer()
    st.stop()

# ── 5a. Univariate statistics ─────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5a. Univariate Statistics")

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
            display_tbl.style.format(fmt, na_rep="—"),
            use_container_width=True,
        )
        # Why a row's Sharpe ratio, skewness and kurtosis are "—" when its returns never
        # move (#678, #690).
        _never_moves_note = ae.never_moves_note(uni_tbl)
        if _never_moves_note:
            st.caption(_never_moves_note)

    st.divider()

# ── 5b. Full-sample correlation heatmap ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5b. Full-Sample Correlation vs SAA Sleeves")

    if corr.empty:
        st.info("Correlation data unavailable.")
    else:
        _hm_names = ae.sleeve_names()
        fig_heat = go.Figure(go.Heatmap(
            z=[[corr[s] for s in _hm_names]],
            x=_hm_names,
            y=["BTC"],
            colorscale="RdYlGn",
            zmid=0,
            zmin=-0.5,
            zmax=0.5,
            text=[[_c(corr[s]) for s in _hm_names]],
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
                "The full-sample correlation analysis contradicts the uncorrelated-alternative "
                "narrative that dominated pre-2020 institutional thinking on Bitcoin. "
                f"BTC's correlation with US Large Core since 2018 is {btc_spy_corr:+.2f}, "
                "meaningfully above zero. "
                f"The highest sleeve correlation is {highest_sleeve} ({highest_val:+.2f}); "
                f"the lowest is {lowest_sleeve} ({_c(lowest_val, '+')}). "
            )
            hi_lo_note = ""
        else:
            spy_note = (
                f"BTC's full-sample correlation with US Large Core is {btc_spy_corr:.2f}. "
            )
            hi_lo_note = (
                f"The highest sleeve correlation is {highest_sleeve} ({highest_val:.2f}) "
                f"and the lowest is {lowest_sleeve} ({_c(lowest_val)}). "
            )

        fi_note = ""
        if not np.isnan(fi_corr):
            # The figure, with no reading of it: "is near zero, which is consistent with
            # Bitcoin offering some diversification relative to duration" printed whatever
            # the correlation was (#425).
            fi_note = (
                f"The Core Fixed Income correlation is {_c(fi_corr)}. As a full-sample "
                "figure, it says nothing about behavior during equity stress. "
            )

        ra_note = ""
        if not np.isnan(ra_corr):
            ra_note = (
                f"The Real Assets correlation ({_c(ra_corr)}) is relevant to the 'digital gold' "
                "thesis: if Bitcoin were an inflation hedge or commodity substitute, "
                "one would expect higher co-movement with this sleeve. "
            )

        st.markdown(spy_note + hi_lo_note + fi_note + ra_note)

    st.divider()

# ── 5c. Rolling 60-day BTC-SPY correlation ────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5c. Rolling 60-Day Correlation: BTC vs SPY")

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

        # The two averages, as the PDF states them since #600, and no reading of them
        # (#601): rolling_averages_sentence says what the paragraph said before.
        _averages = ae.rolling_averages_sentence(*ae.pre_post_2020(rolling_corr)[:2])
        if _averages:
            st.markdown(_averages)

    st.divider()

# ── 5d. Robustness: weekly correlations ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5d. Robustness: Daily vs Weekly Correlations")

    if corr.empty or weekly_corr.empty:
        st.info("Correlation data unavailable.")
    else:
        _rb_names = ae.sleeve_names()
        daily_corr  = corr.reindex(_rb_names)
        weekly_corr_aligned = weekly_corr.reindex(_rb_names)
        diff_corr   = weekly_corr_aligned - daily_corr

        robust_tbl = pd.DataFrame({
            "Sleeve":           _rb_names,
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
            "the effect of asynchronous trading across markets: BTC trades continuously, "
            "while equity benchmarks observe exchange hours and holiday schedules. "
            "Small differences between daily and weekly estimates indicate the results "
            "are not driven by microstructure noise; large differences would suggest "
            "settlement lags or thin-market days materially distort the daily figure."
        )

        # Which sleeves' differences are positive, read from the table (#425). This said
        # "TIPS shows the only positive daily-to-weekly difference" whenever TIPS's was
        # positive, whatever the others were, and followed it with an explanation and a
        # verdict ("the overall robustness conclusion stands") that read no figure.
        _only_positive = ae.only_positive_difference(diff_corr)
        if _only_positive:
            st.markdown(_only_positive)

    st.divider()

# ── 5e. MV optimization — unconstrained ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5e. Mean-Variance: Unconstrained Tangency")

    if not mv:
        st.info("Mean-variance analysis unavailable.")
    else:
        sleeves_list = mv["sleeves"]
        w_unc_no   = mv["w_unc_no"]
        w_unc_with = mv["w_unc_with"]   # length = n_sleeves + 1 (BTC last)
        sharpe_unc_no   = mv["sharpe_unc_no"]
        sharpe_unc_with = mv["sharpe_unc_with"]

        st.markdown(
            "The unconstrained tangency portfolio below assigns extreme long/short positions "
            "because small estimation errors in expected returns are magnified through matrix "
            "inversion. The table shows which assets dominate the tangency frontier in "
            "mean-variance space; its weights are not actionable allocations."
        )

        left_col, right_col = st.columns([1, 1])

        with left_col:
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
            st.metric(
                "Sharpe without BTC (unconstrained)",
                f"{sharpe_unc_no:.3f}",
            )
            st.metric(
                "Sharpe with BTC (unconstrained)",
                f"{sharpe_unc_with:.3f}",
                delta=f"{sharpe_unc_with - sharpe_unc_no:+.3f}",
            )
            st.caption(
                "Unconstrained Sharpe = √[(μ−rf)'Σ⁻¹(μ−rf)] × √252, "
                "the theoretical maximum of the unconstrained efficient frontier. "
                "The normalized weights indicate direction only."
            )

        st.caption(
            "Unconstrained optimization is presented for completeness. "
            "The constrained result in the next section is the operationally relevant case."
        )

    st.divider()

# ── 5f. MV optimization — constrained ────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5f. Mean-Variance: Constrained Tangency (max 25%)")

    if not mv:
        st.info("Mean-variance analysis unavailable.")
    else:
        sleeves_list = mv["sleeves"]
        w_con_no    = mv["w_con_no"]
        w_con_with  = mv["w_con_with"]
        sharpe_con_no   = mv["sharpe_con_no"]
        sharpe_con_with = mv["sharpe_con_with"]
        delta_sharpe_con = sharpe_con_with - sharpe_con_no
        _years = (btc_ret.index[-1] - btc_ret.index[0]).days / 365.25
        _years_s = f"{_years:.1f}"

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
            "With a 25% per-sleeve cap, the constraint Σw=1 is feasible for any universe of "
            "four or more assets (4×25%=100%), so the optimizer can concentrate weights by "
            "Sharpe contribution rather than fill them uniformly."
        )

        btc_wt_con = float(mv["w_con_with"][-1])
        if btc_wt_con >= 0.249:
            btc_wt_note = (
                "the mean-variance optimizer fills Bitcoin to its 25% cap, "
                "driven by BTC's exceptional sample-period realized return, which is "
                "not a forward-looking expected return"
            )
        else:
            btc_wt_note = (
                f"the mean-variance optimizer assigns Bitcoin a {btc_wt_con:.1%} weight"
            )

        st.markdown(
            f"Under realistic institutional constraints (max 25% per sleeve), "
            f"{btc_wt_note}. "
            f"The constrained Sharpe is {sharpe_con_no:.3f} (without BTC) "
            f"and {sharpe_con_with:.3f} (with BTC), a change of {delta_sharpe_con:+.3f}. "
            "This result is driven by BTC's high sample return. "
            "It does not account for estimation error in the mean, which is extremely large "
            f"for {a_or_an(_years_s)} {_years_s}-year history of a volatile, regime-shifting asset. "
            "Section 5g's SAA-anchored marginal contribution analysis is the primary "
            "analytical frame."
        )
        st.caption(
            f"The {btc_wt_con:.1%} allocation is the sample-period mean-variance optimum. "
            "It does not account for tax treatment, operational complexity, liquidity "
            "constraints, or out-of-sample expected-return shrinkage. The decision framework "
            "in Section 5j weighs the first three; it does not model shrinkage."
        )

    st.divider()

# ── 5g. Marginal Sharpe curve ─────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5g. Marginal Sharpe Contribution")

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
        st.markdown(
            "This curve is mechanical, not predictive. Any asset with exceptional realized "
            "returns over a sample period will produce a monotonically increasing Sharpe "
            "contribution curve at low allocations, so the shape itself is not evidence of "
            "an attractive forward-looking opportunity."
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
                f"rising from {sharpe_0:.3f} at 0.0% BTC to {sharpe_max:.3f} at {btc_at_max:.1f}% BTC. "
                "Each incremental unit of BTC improves the risk-adjusted portfolio "
                "return within this allocation range, driven primarily by BTC's high realized "
                "return over the sample period."
            )
        else:
            curve_desc = (
                f"The Sharpe curve peaks at {btc_at_max:.0f}% BTC allocation ({sharpe_max:.3f}), "
                f"against {sharpe_0:.3f} at 0%. "
                "Beyond the peak, BTC's volatility begins to dominate and erodes risk-adjusted returns."
            )

        st.markdown(
            curve_desc
            + " The curve reflects sample-period arithmetic: BTC's realized return since 2018 "
            "is exceptional and is not an expected return for forward-looking "
            "allocation decisions."
        )

    st.divider()

# ── 5h. Drawdown sensitivity ──────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5h. Drawdown Sensitivity")

    if dd_sens.empty:
        st.info("Drawdown sensitivity data unavailable.")
    else:
        def _tint_mdd_col(df: pd.DataFrame) -> pd.DataFrame:
            style = pd.DataFrame("", index=df.index, columns=df.columns)
            if "2022 MDD" in style.columns:
                style["2022 MDD"] = "background-color: #fff3cd"
            return style

        st.dataframe(
            dd_sens.style.format({
                "CAGR":     "{:.1%}",
                "Max DD":   "{:.1%}",
                "Sharpe":   "{:.2f}",
                "2022 MDD": "{:.1%}",
            }).apply(_tint_mdd_col, axis=None),
            hide_index=True,
            use_container_width=True,
        )

        # Compute incremental max DD at 10% BTC vs 0%
        dd_0_row  = dd_sens[dd_sens["BTC Alloc"] == "0.0%"]
        dd_10_row = dd_sens[dd_sens["BTC Alloc"] == "10.0%"]
        dd_note = ""
        # 2022 from the book's own prices, stated once: each series' calendar-year return
        # (#425, decided by the owner on October 15, 2026). Bitcoin's 2022 fall printed
        # twice here as a peak-to-trough decline within the year, and again in 5j's
        # bullet as its 2022 drawdown; 5j now leaves 2022 to this paragraph. Equities and
        # bonds are the naive benchmark's two legs (#406 item 6).
        from src.benchmarks import NAIVE_60_40
        (_eq_t, _), (_bd_t, _) = NAIVE_60_40
        _r22 = {_eq_t: ae.return_in_year(_load_candidate_returns(_eq_t)),
                _bd_t: ae.return_in_year(_load_candidate_returns(_bd_t)),
                "BTC": ae.return_in_year(btc_ret)}
        if not dd_0_row.empty and not dd_10_row.empty:
            dd_0   = float(dd_0_row["Max DD"].iloc[0])
            dd_10  = float(dd_10_row["Max DD"].iloc[0])
            mdd22_0  = float(dd_0_row["2022 MDD"].iloc[0])
            mdd22_10 = float(dd_10_row["2022 MDD"].iloc[0])
            _dd_verb = ("deepens" if dd_10 < dd_0 else "narrows" if dd_10 > dd_0
                        else "leaves unchanged")
            dd_note = (
                f"Adding a 10% BTC allocation {_dd_verb} portfolio maximum drawdown "
                f"from {dd_0:.1%} to {dd_10:.1%} over the full sample. "
                f"Within calendar 2022, the portfolio's maximum drawdown shifts from "
                f"{mdd22_0:.1%} (0% BTC) to {mdd22_10:.1%} (10% BTC). "
            )

        # "All at the same time ... That eliminated any diversification benefit and
        # amplified drawdown" printed whatever the figures were. The joint-stress
        # sentence now renders only when all three fell over the year.
        st.markdown(
            dd_note
            + "2022 is the most important stress-test period for evaluating Bitcoin's "
            "portfolio impact. "
            + ae.stress_2022_sentences(_r22)
        )

    st.divider()

# ── 5i. Regime-conditional correlation ───────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5i. Regime-Conditional Correlation")

    try:
        regime_corr = _load_regime_conditional_correlation()
        if regime_corr.empty:
            st.info("Regime data unavailable: it requires a FRED API key.")
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
                    f"The late-cycle correlation ({_c(late_corr)}) against mid-cycle ({_c(mid_corr)}) "
                    "is the key comparison: if Bitcoin behaves as a risk asset "
                    "when the portfolio most needs a hedge, its diversification value "
                    "is fundamentally compromised."
                )

            st.markdown(
                rec_note + late_note
                + " Regime-conditional analysis uses FRED USREC, T10Y2Y, and UNRATE data; "
                "limited recession observations since 2018 may make the recession estimate noisy."
            )

    except Exception:
        st.info("Regime data unavailable: it requires a FRED API key.")

    st.divider()

# ── 5j. Decision framework summary ───────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("5j. Decision Framework Summary")

    # Build argument lists dynamically
    args_for: list[str] = []
    args_against: list[str] = []

    # The case the marginal-Sharpe curve (section 5g) makes, read as the PDF reads it
    # (#629): the curve's best weight above 0% against its 0% point. It compared the 10%
    # point alone, which #426 had already moved the PDF off.
    _sharpe_case, _ = ae.sharpe_argument(msc, mv)
    if _sharpe_case:
        args_for.append(_sharpe_case)

    if not corr.empty:
        low_corr_sleeves = [s for s in ae.sleeve_names() if not np.isnan(corr.get(s, float("nan"))) and corr[s] < 0.1]
        for s in low_corr_sleeves:
            args_for.append(f"Low correlation ({_c(corr[s])}) with {s}")

        btc_spy_j = float(corr.get("US Large Core", float("nan")))
        _equity_like = ae.equity_like_argument(btc_spy_j)
        if _equity_like:
            args_against.append(_equity_like)

    # The drawdown bullet's inputs, built as the PDF builds them (src/reports.py).
    _max_drawdown, _drawdown_argument = ae.max_drawdown, ae.drawdown_argument
    _port: dict = {}
    if not dd_sens.empty:
        _b, _t = dd_sens.iloc[0], dd_sens.iloc[-1]
        if not np.isnan(_b["2022 MDD"]) and not np.isnan(_t["2022 MDD"]):
            _port = {"max_dd": float(_b["Max DD"]), "alloc_top": str(_t["BTC Alloc"]),
                     "mdd22_lo": float(_b["2022 MDD"]), "mdd22_hi": float(_t["2022 MDD"])}
    args_against.extend([
        _drawdown_argument(_max_drawdown(btc_ret), _port, ae.SAMPLE_START),
        ae.BITCOIN_TAX_LINE,
        "Operational complexity: self-custody introduces key management risk "
        "and exchange-held BTC introduces counterparty risk (FTX 2022). "
        # Three sentences went (#627, decided by the owner on October 16, 2026), none cited:
        # spot ETFs "materially reduce these operational frictions", bitcoin held "with
        # creation/redemption mechanics that mirror commodity ETF wrappers", and "the
        # expense drag ... and tax classification" as what remains.
        # Cited, as #604 decided on October 15, 2026. A typed "0.12–0.25%" fee range went,
        # as did "no qualified-dividend treatment" and "the wash-sale rule does not apply
        # under current IRS guidance", which cited nothing.
        "IBIT's sponsor treats the trust as a grantor trust for U.S. federal income tax "
        "purposes, so its shareholders are taxed as if they directly owned a pro rata "
        "share of its bitcoin (iShares Bitcoin Trust ETF, annual report on Form 10-K for "
        "2025, filed February 27, 2026)",
        "No intrinsic cash flow, earnings, or fundamental anchor for valuation: "
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
        # A "Tax: commodity classification" bullet went (#425): it contradicted the case
        # against's tax line two bullets up, the IRS treating bitcoin as property
        # (Notice 2014-21), which stays.
        # "ETF alternatives simplify ops at the cost of fee drag and tracking risk" went
        # (#627): it cited nothing.
        "- **Operational:** custodial complexity\n"
        "- **Mandate fit:** no explicit SAA mandate; would require a new sleeve "
        "or an overweight to Real Assets with a sub-allocation"
    )

    # The second trigger names the range this page's own curve was read over (#627).
    st.markdown(f"**Conclusion:**\n\n{ae.conclusion(rolling_corr, ae.curve_top_allocation(msc))}")
    st.caption("This page updates automatically as new data arrives.")

    st.divider()

# ── 6. Methodology expander ───────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    with st.expander("Methodology", expanded=False):
        st.markdown(
            "**Data source:** Daily adjusted-close prices from the app's price cache "
            "(SQLite; refreshed on a cache miss). Sleeve benchmark prices come from "
            "the same cache, for each sleeve's benchmark tickers.\n\n"
            f"**Date range:** {ae.SAMPLE_START} to present. BTC-USD data available "
            "from approximately 2014; 2018-01-01 is used as the start date because "
            "it precedes the first major post-ICO bear market (2018) and provides a "
            "full bull/bear cycle for statistical estimation.\n\n"
            "**Return computation:** The daily percentage change of the adjusted close. "
            "Bitcoin's adjusted close equals its close (no dividends or splits in the "
            "traditional sense).\n\n"
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
            "**MV optimization:** Unconstrained tangency direction w_raw = Σ⁻¹(μ − rf·1). "
            "The headline Sharpe is √[(μ−rf)'Σ⁻¹(μ−rf)] × √252, the theoretical "
            "maximum of the unconstrained efficient frontier, non-negative by construction. "
            "Normalized weights w* = w_raw / (1'w_raw) are shown for directional "
            "interpretation only. When the normalization denominator is negative, the "
            "normalized weights flip sign and their portfolio Sharpe is negative, which is "
            "why the headline metric uses the information-ratio formula instead. "
            "Constrained tangency uses scipy.optimize.minimize (SLSQP) with bounds "
            "0 ≤ w_i ≤ 25% and Σw = 1, maximizing annualized Sharpe.\n\n"
            f"**Risk-free rate:** {_rf_line()} The Performance page averages the same "
            "series over each of its own windows. "
            "For the MV optimization it is converted to a daily rate by geometric "
            "compounding, (1+rf)^(1/252) − 1, the convention the Performance page "
            "also uses, rather than by simple division by 252.\n\n"
            "**Regime classification:** FRED USREC (NBER monthly indicator), T10Y2Y "
            "(daily yield-curve spread), and UNRATE (monthly unemployment) are fetched "
            "from FRED and vectorized into four labels: "
            "Recession, Early-cycle, Mid-cycle, and Late-cycle. The labels follow the same "
            "priority ordering as the Macro page's regime classifier."
        )

render_footer()
