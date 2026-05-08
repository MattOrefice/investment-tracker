"""Macro Dashboard — regime indicator panels."""
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Macro Dashboard", layout="wide")

from src import macro, shiller
from src.asof import as_of_banner
from src.prices import get_prices
from src.prose_helpers import percentile_label
from src.ui_helpers import render_footer

TODAY      = date.today().isoformat()
ONE_YR_AGO = (date.fromisoformat(TODAY) - timedelta(days=365)).isoformat()

_C = {
    "primary":   "#2E4057",
    "ref":       "#9E9E9E",
    "recession": "#9E9E9E",
    "current":   "#C0392B",
}

_CHART_H      = 280
_CHART_H_CAPE = 320


# ── helpers ───────────────────────────────────────────────────────────────────

def _window_start(window: str) -> str:
    t = date.fromisoformat(TODAY)
    try:
        if window == "5Y":
            return t.replace(year=t.year - 5).isoformat()
        if window == "10Y":
            return t.replace(year=t.year - 10).isoformat()
        if window == "20Y":
            return t.replace(year=t.year - 20).isoformat()
    except ValueError:
        pass
    return "1800-01-01"  # Max


def _ordinal(n: float) -> str:
    n = int(round(n))
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th'][min(n % 10, 4)]}"


def _window_pctile(series: pd.Series, current_val: float, w_start: str) -> float:
    return macro.window_pctile(series, current_val, w_start)


def _apply_style(fig: go.Figure, height: int = _CHART_H) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=24, b=0),
        paper_bgcolor="white",
        plot_bgcolor="#FAFAFA",
        font=dict(color="#333333", size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font_size=11),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#EBEBEB", showgrid=True, zeroline=False, tickfont_size=11, automargin=True)
    fig.update_yaxes(gridcolor="#EBEBEB", showgrid=True, zeroline=False, tickfont_size=11, automargin=True)
    return fig


def _tight_yrange(series: pd.Series, extra_ys: list | None = None, pad: float = 0.05) -> list | None:
    """Compute tight y-axis range: data extent ± 5% padding, including any annotation y-values."""
    clean = series.dropna()
    if clean.empty:
        return None
    lo = float(clean.min())
    hi = float(clean.max())
    if extra_ys:
        for v in extra_ys:
            if v is not None:
                lo = min(lo, float(v))
                hi = max(hi, float(v))
    rng = hi - lo
    if rng == 0:
        rng = abs(hi) or 1.0
    buf = rng * pad
    return [lo - buf, hi + buf]


def _add_recession_shading(fig: go.Figure, periods: list, start_filter: str) -> None:
    t_start = pd.Timestamp(start_filter)
    t_end   = pd.Timestamp(TODAY)
    for s, e in periods:
        s_ts, e_ts = pd.Timestamp(s), pd.Timestamp(e)
        if e_ts < t_start or s_ts > t_end:
            continue
        fig.add_vrect(
            x0=max(s_ts, t_start).isoformat()[:10],
            x1=min(e_ts, t_end).isoformat()[:10],
            fillcolor=_C["recession"],
            opacity=0.12,
            layer="below",
            line_width=0,
        )


def _add_current_annotation(fig: go.Figure, val: float, label: str) -> None:
    """Add a horizontal line at current value with a window-percentile annotation."""
    fig.add_hline(
        y=val,
        line_dash="solid",
        line_color=_C["current"],
        line_width=1.5,
        annotation_text=label,
        annotation_position="top left",
        annotation_font_size=9,
        annotation_font_color=_C["current"],
    )


def _yield_curve_state(series: pd.Series) -> str:
    clean = series.dropna()
    if clean.empty:
        return "Data unavailable"
    current = float(clean.iloc[-1])
    is_inv  = current < 0
    changes = (clean < 0).astype(int).diff().dropna()
    changes = changes[changes != 0]
    if changes.empty:
        return "Inverted" if is_inv else "Normal yield curve"
    last_lbl = changes.index[-1].strftime("%b %Y")
    return f"Inverted since {last_lbl}" if is_inv else f"Normalized since {last_lbl}"


def _ff_interpretation(current: float, change_bps: float) -> str:
    if current >= 5.0:
        stance = "firmly restrictive territory"
    elif current >= 4.0:
        stance = "moderately restrictive territory"
    elif current >= 2.5:
        stance = "near neutral"
    else:
        stance = "accommodative territory"

    if change_bps <= -100:
        traj = (f"down {abs(change_bps):.0f} bps over the past year — "
                "a meaningful easing cycle is underway")
    elif change_bps <= -25:
        traj = f"easing gradually, down {abs(change_bps):.0f} bps over 12 months"
    elif change_bps >= 100:
        traj = f"up {change_bps:.0f} bps over the past year in an active tightening cycle"
    elif change_bps >= 25:
        traj = f"tightening modestly, up {change_bps:.0f} bps over 12 months"
    else:
        traj = f"broadly stable over the past 12 months ({change_bps:+.0f} bps)"

    return (
        f"Fed Funds at {current:.2f}% places monetary policy in {stance}, "
        f"with rates {traj}. "
        "Duration in the Core Fixed Income and TIPS sleeves is most sensitive "
        "to the direction and pace of rate changes from here."
    )


# ── data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def _load_cape_series() -> pd.Series:
    return shiller.get_cape_series()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_fred(series_id: str, start_date: str) -> pd.Series:
    return macro.get_series(series_id, start_date)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_recession_periods() -> list:
    return macro.get_recession_periods("1945-01-01", TODAY)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_price_series(ticker: str, start_date: str) -> pd.Series:
    df = get_prices(ticker, start_date, TODAY)
    return df["adj_close"].ffill()


# ── page ──────────────────────────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:

    st.markdown("## Macro Dashboard")
    st.caption("Regime indicators relevant to portfolio positioning.")
    st.caption(as_of_banner())

    hdr_l, hdr_r = st.columns([3, 1])
    with hdr_l:
        st.caption(
            f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
            "Data: FRED & Shiller. Percentiles are window-relative (toggle selector to recompute)."
        )
    with hdr_r:
        if st.button("Force refresh", type="secondary",
                     help="Bypass the disk cache and re-fetch macro data from FRED and Shiller."):
            macro.clear_macro_cache()
            shiller.clear_shiller_cache()
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # ── Load all FRED data upfront ────────────────────────────────────────────

    def _try_fred(series_id: str, start_date: str):
        """Return (series, None) on success or (None, exc) on failure."""
        try:
            return _load_fred(series_id, start_date), None
        except Exception as exc:
            return None, exc

    def _try_rec():
        try:
            return _load_recession_periods(), None
        except Exception as exc:
            return None, exc

    def _panel_error(panel_title: str, exc: Exception, retry_key: str) -> None:
        with st.container(border=True):
            st.markdown(f"**{panel_title}** — data temporarily unavailable")
            st.caption(f"{type(exc).__name__}: {exc}")
            if st.button("Retry", key=retry_key):
                _load_fred.clear()
                st.rerun()

    with st.spinner("Loading FRED data…"):
        rec_periods, _rec_err    = _try_rec()
        usrec,       _usrec_err  = _try_fred("USREC",             "1945-01-01")
        t10y2y,      _t10y2y_err = _try_fred("T10Y2Y",           "1976-06-01")
        dff,         _dff_err    = _try_fred("DFF",               "1954-07-01")
        hy_oas,      _hy_oas_err = _try_fred("BAMLH0A0HYM2",     "1996-12-31")
        dgs10,       _dgs10_err  = _try_fred("DGS10",             "2003-01-01")
        t10yie,      _t10yie_err = _try_fred("T10YIE",            "2003-01-01")
        unrate,      _unrate_err = _try_fred("UNRATE",            "1948-01-01")
        gdp_gr,      _gdp_err    = _try_fred("A191RL1Q225SBEA",   "1947-01-01")
        core_cpi,    _cpi_err    = _try_fred("CPILFESL",          "1957-01-01")

    # ═══════════════════════════════════════════════════════════════════════════
    # REGIME CLASSIFIER (prominent header)
    # ═══════════════════════════════════════════════════════════════════════════

    _REGIME_COLORS = {
        "Recession":   ("#7B2D2D", "#FDE8E8"),
        "Early-cycle": ("#1A5C2E", "#E8F5EC"),
        "Mid-cycle":   ("#1A3A5C", "#E8EFF7"),
        "Late-cycle":  ("#7B5C00", "#FDF5E0"),
    }
    _REGIME_PROSE = {
        "Recession":   (
            "Output is contracting and the NBER has declared a recession. "
            "Quality equities and intermediate duration have historically held up best. "
            "Avoid adding cyclical risk; focus on rebalancing into weakness."
        ),
        "Early-cycle": (
            "The economy is recovering from a downturn: unemployment remains elevated "
            "but the yield curve is no longer inverted. Historically the strongest phase "
            "for small-cap and value factor returns. The SAA's 7% small-cap and 8% value "
            "sleeves are positioned for this environment."
        ),
        "Mid-cycle":   (
            "Growth is moderate, the yield curve is positively sloped, and labor markets "
            "are neither too tight nor too loose. SAA weights are calibrated for this baseline "
            "environment. No tactical tilt is warranted by current signals."
        ),
        "Late-cycle":  (
            "The yield curve is inverted or labor markets are historically tight — both "
            "signal late-expansion risk. Quality and inflation-linked assets (TIPS, Real Assets) "
            "have historically held up better in this phase. Duration should be watched carefully."
        ),
    }

    _cur_usrec  = float(usrec.dropna().iloc[-1])  if usrec  is not None and not usrec.empty  else None
    _cur_t10y2y = float(t10y2y.dropna().iloc[-1]) if t10y2y is not None and not t10y2y.empty else None
    _cur_unrate = float(unrate.dropna().iloc[-1]) if unrate is not None and not unrate.empty else None

    _regime_label = macro.classify_regime(_cur_usrec, _cur_t10y2y, _cur_unrate)
    _r_fg, _r_bg  = _REGIME_COLORS[_regime_label]

    with st.container(border=True):
        st.markdown(
            f"<div style='background:{_r_bg};border-left:5px solid {_r_fg};"
            f"padding:12px 16px;border-radius:4px;margin-bottom:8px'>"
            f"<span style='font-size:1.4rem;font-weight:700;color:{_r_fg}'>"
            f"Current Regime: {_regime_label}</span></div>",
            unsafe_allow_html=True,
        )
        st.caption(_REGIME_PROSE[_regime_label])

        sig_l, sig_m, sig_r = st.columns(3)
        with sig_l:
            st.metric("NBER Recession (USREC)",
                      "Active" if (_cur_usrec or 0) >= 0.5 else "None",
                      help="1 = NBER-declared recession; 0 = expansion")
        with sig_m:
            st.metric("Yield Curve (10Y–2Y)",
                      f"{_cur_t10y2y:+.2f}%" if _cur_t10y2y is not None else "—",
                      help="Negative = inverted; < -0.25% triggers Late-cycle")
        with sig_r:
            st.metric("Unemployment Rate",
                      f"{_cur_unrate:.1f}%" if _cur_unrate is not None else "—",
                      help="> 5.5% = Early-cycle; < 4.2% = Late-cycle trigger")

        # Backtest: apply classifier across common history of all three signals
        try:
            if usrec is not None and t10y2y is not None and unrate is not None:
                _bt_usrec  = usrec.resample("MS").last().ffill()
                _bt_curve  = t10y2y.resample("MS").last().ffill()
                _bt_ur     = unrate.resample("MS").last().ffill()
                _bt_common = _bt_usrec.index \
                    .intersection(_bt_curve.index) \
                    .intersection(_bt_ur.index)
                _bt_common = _bt_common[_bt_common >= "1976-06-01"]

                _regime_map = {"Recession": 0, "Early-cycle": 1, "Mid-cycle": 2, "Late-cycle": 3}
                _regime_num = [
                    _regime_map[macro.classify_regime(
                        float(_bt_usrec.loc[d]),
                        float(_bt_curve.loc[d]),
                        float(_bt_ur.loc[d]),
                    )]
                    for d in _bt_common
                ]
                _bt_labels = [
                    macro.classify_regime(
                        float(_bt_usrec.loc[d]),
                        float(_bt_curve.loc[d]),
                        float(_bt_ur.loc[d]),
                    )
                    for d in _bt_common
                ]
                _bt_series = pd.Series(_regime_num, index=_bt_common)

                _color_map = {
                    0: _REGIME_COLORS["Recession"][0],
                    1: _REGIME_COLORS["Early-cycle"][0],
                    2: _REGIME_COLORS["Mid-cycle"][0],
                    3: _REGIME_COLORS["Late-cycle"][0],
                }

                with st.expander("Historical regime backtest (since 1976)", expanded=False):
                    fig_bt = go.Figure()
                    for _num, _lbl in [(0, "Recession"), (1, "Early-cycle"),
                                       (2, "Mid-cycle"), (3, "Late-cycle")]:
                        _mask = _bt_series == _num
                        if not _mask.any():
                            continue
                        _dates = _bt_series[_mask].index
                        fig_bt.add_trace(go.Scatter(
                            x=_dates, y=[_num] * len(_dates),
                            mode="markers",
                            marker=dict(color=_REGIME_COLORS[_lbl][0], size=5, symbol="square"),
                            name=_lbl,
                        ))
                    fig_bt.update_layout(
                        height=200,
                        margin=dict(l=0, r=0, t=16, b=0),
                        paper_bgcolor="white",
                        plot_bgcolor="#FAFAFA",
                        yaxis=dict(
                            tickvals=[0, 1, 2, 3],
                            ticktext=["Recession", "Early-cycle", "Mid-cycle", "Late-cycle"],
                            showgrid=True, gridcolor="#EBEBEB",
                        ),
                        xaxis=dict(showgrid=True, gridcolor="#EBEBEB"),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_bt, width="stretch")
                    st.caption(
                        "Regime labels are applied retroactively using USREC, T10Y2Y, and UNRATE. "
                        "USREC is declared by NBER and can lag real recession starts by 6–18 months. "
                        "This backtest uses hindsight-available data — it is not a real-time signal."
                    )
        except Exception:
            pass

        st.caption(
            "⚠️ **Disclaimer:** This classifier is a rules-based heuristic, not a forecast or "
            "trading signal. USREC is declared retroactively by the NBER and may lag actual "
            "recession onset by 6–18 months. See docs/regime_classifier.md for full methodology."
        )

    st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # VALUATION
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Valuation")

    # ── Panel 1: CAPE ────────────────────────────────────────────────────────

    st.markdown("#### CAPE / Shiller P/E")

    try:
        with st.spinner("Loading CAPE data…"):
            cape_series = _load_cape_series()
        cape_val     = float(cape_series.dropna().iloc[-1])
        cape_pctile  = macro.percentile(cape_series, cape_val)  # full history (used in summary)
        cape_implied = macro.compute_cape_implied_return(cape_val)
        cape_median  = float(cape_series.median())
        cape_std     = float(cape_series.std())
        cape_ok = True
    except Exception as exc:
        cape_ok = False
        st.error(f"CAPE data unavailable: {exc}")

    if cape_ok:
        cape_last_date = cape_series.dropna().index[-1]
        staleness_days = (pd.Timestamp.today() - cape_last_date).days
        if staleness_days > 95:
            st.warning(
                f"Shiller data is {staleness_days} days stale "
                f"(last observation: {cape_last_date.strftime('%Y-%m')}). "
                "Force refresh below to pull the latest data."
            )

        cape_window = st.radio(
            "Window", ["20Y", "50Y", "Max"],
            index=2, key="cape_window", horizontal=True,
        )
        cape_as_of    = cape_last_date.strftime("%b %Y")
        w_start_cape  = _window_start(cape_window)
        cape_filtered = cape_series[cape_series.index >= w_start_cape].dropna()
        cape_pctile_w = _window_pctile(cape_series, cape_val, w_start_cape)

        col_l, col_r = st.columns([1, 2])
        with col_l:
            st.metric("Shiller CAPE", f"{cape_val:.1f}×")
            st.caption(
                f"{_ordinal(cape_pctile_w)} percentile of {cape_window} window "
                f"· full history: {_ordinal(cape_pctile)} pct since 1881 "
                f"· data as of {cape_as_of}"
            )
            st.markdown(
                f"**Implied forward 10Y real return:** {cape_implied:+.2%}  \n"
                "*Historical relationship, not a forecast.*"
            )

        with col_r:
            fig_cape = go.Figure()
            fig_cape.add_trace(go.Scatter(
                x=cape_filtered.index, y=cape_filtered.values,
                mode="lines", name="CAPE",
                line=dict(color=_C["primary"], width=2),
            ))
            fig_cape.add_hline(
                y=cape_median,
                line_dash="dash", line_color=_C["ref"], line_width=1,
                annotation_text=f"Median {cape_median:.0f}×",
                annotation_position="right", annotation_font_size=10,
            )
            fig_cape.add_hline(
                y=cape_median + cape_std,
                line_dash="dot", line_color=_C["ref"], line_width=1,
                annotation_text=f"+1σ  {cape_median+cape_std:.0f}×",
                annotation_position="right", annotation_font_size=10,
            )
            fig_cape.add_hline(
                y=max(1.0, cape_median - cape_std),
                line_dash="dot", line_color=_C["ref"], line_width=1,
                annotation_text=f"−1σ  {cape_median-cape_std:.0f}×",
                annotation_position="right", annotation_font_size=10,
            )
            _add_current_annotation(
                fig_cape, cape_val,
                f"Current {cape_val:.1f}× ({_ordinal(cape_pctile_w)} pct, {cape_window})",
            )
            _apply_style(fig_cape, height=_CHART_H_CAPE)
            fig_cape.update_yaxes(title_text="CAPE (×)")
            _yr = _tight_yrange(cape_filtered, [cape_val, cape_median,
                                                cape_median + cape_std, max(1.0, cape_median - cape_std)])
            if _yr:
                fig_cape.update_yaxes(range=_yr)
            st.plotly_chart(fig_cape, width='stretch')

        pctile_label_cape = percentile_label(cape_pctile)
        st.caption(
            f"CAPE in the {_ordinal(cape_pctile)} percentile historically — {pctile_label_cape}. "
            "Only the dot-com bubble peak (1999–2001) has sustained CAPE above 40 in the "
            "full 145-year Shiller record. "
            "Periods of extreme valuation have preceded materially below-average decade-ahead "
            "returns. Most directly relevant to the International Developed and US Large Value "
            "sleeves, where the discount-to-US-CAPE thesis depends on US valuations remaining "
            "above historical norms."
        )

    st.divider()

    # ── Panel 2: Excess CAPE Yield ──────────────────────────────────────────

    if cape_ok and (dgs10 is not None) and (t10yie is not None):
        st.markdown("#### Excess CAPE Yield (ECY)")

        dgs10_clean  = dgs10.dropna()
        t10yie_clean = t10yie.dropna()
        current_dgs10  = float(dgs10_clean.iloc[-1])
        current_t10yie = float(t10yie_clean.iloc[-1])
        current_ecy    = macro.compute_ecy(cape_val, current_dgs10, current_t10yie)

        _ecy_start = "2003-01-01"
        _dgs10_m   = dgs10_clean.loc[_ecy_start:].resample("MS").mean()
        _t10yie_m  = t10yie_clean.loc[_ecy_start:].resample("MS").mean()
        _cape_m    = cape_series.dropna().loc[_ecy_start:]
        _ecy_df    = pd.concat([_cape_m, _dgs10_m, _t10yie_m], axis=1).dropna()
        _ecy_df.columns = ["cape", "dgs10", "t10yie"]
        _ecy_hist  = (100.0 / _ecy_df["cape"]) - (_ecy_df["dgs10"] - _ecy_df["t10yie"])

        ecy_since   = _ecy_hist.index[0].strftime("%b %Y")
        _ecy_median = float(_ecy_hist.median())
        _real_rate  = current_dgs10 - current_t10yie

        ecy_window    = st.radio(
            "Window", ["5Y", "10Y", "Max"],
            index=2, key="ecy_window", horizontal=True,
        )
        w_start_ecy   = _window_start(ecy_window)
        ecy_pctile_w  = _window_pctile(_ecy_hist, current_ecy, w_start_ecy)
        ecy_pctile    = macro.percentile(_ecy_hist, current_ecy)

        col_l, col_r = st.columns([1, 2])
        with col_l:
            st.metric("ECY", f"{current_ecy:.2f}%")
            st.caption(
                f"{_ordinal(ecy_pctile_w)} percentile of {ecy_window} window "
                f"(full history since {ecy_since}: {_ordinal(ecy_pctile)} pct)  \n"
                f"CAPE yield {100/cape_val:.2f}% vs real rate {_real_rate:.2f}% "
                f"({current_dgs10:.2f}% − {current_t10yie:.2f}%)"
            )

        with col_r:
            _ecy_w_start = _window_start(ecy_window)
            _ecy_w       = _ecy_hist[_ecy_hist.index >= pd.Timestamp(_ecy_w_start)]
            fig_ecy = go.Figure()
            fig_ecy.add_trace(go.Scatter(
                x=_ecy_w.index, y=_ecy_w.values,
                mode="lines", name="ECY (%)",
                line=dict(color=_C["primary"], width=2),
            ))
            fig_ecy.add_hline(
                y=_ecy_median,
                line_dash="dash", line_color=_C["ref"], line_width=1,
                annotation_text=f"Median {_ecy_median:.1f}%",
                annotation_position="right", annotation_font_size=10,
            )
            fig_ecy.add_hline(
                y=0,
                line_dash="dot", line_color="#CC4444", line_width=1,
                annotation_text="0 = bonds match equities",
                annotation_position="top left", annotation_font_size=9,
                annotation_font_color="#888",
            )
            _add_current_annotation(
                fig_ecy, current_ecy,
                f"Current {current_ecy:.2f}% ({_ordinal(ecy_pctile_w)} pct, {ecy_window})",
            )
            _apply_style(fig_ecy, height=_CHART_H_CAPE)
            fig_ecy.update_yaxes(title_text="ECY (%)")
            _yr = _tight_yrange(_ecy_w, [current_ecy, _ecy_median, 0.0])
            if _yr:
                fig_ecy.update_yaxes(range=_yr)
            st.plotly_chart(fig_ecy, width='stretch')

        if current_ecy >= 3.0:
            _ecy_interp = (
                f"ECY of {current_ecy:.2f}% signals equities offer a substantial earnings yield "
                "above real bond yields — historically associated with attractive equity forward "
                "returns relative to fixed income."
            )
        elif current_ecy >= 1.0:
            _ecy_interp = (
                f"ECY of {current_ecy:.2f}% reflects a modest equity premium above real bond yields — "
                "historically consistent with reasonable forward returns, though the margin of safety "
                "is thinner than the post-2008 zero-real-rate era."
            )
        elif current_ecy >= 0.0:
            _ecy_interp = (
                f"ECY of {current_ecy:.2f}% places equities near parity with real bond yields — "
                "limited yield premium above what intermediate Treasuries offer in real terms."
            )
        else:
            _ecy_interp = (
                f"ECY of {current_ecy:.2f}% indicates real bonds yield more than equities — "
                "a historically unusual regime where fixed income competes directly with equity returns."
            )

        st.caption(
            _ecy_interp + " "
            f"Current reading at the {_ordinal(ecy_pctile_w)} percentile of its "
            f"{ecy_window} window ({ecy_since}–present for full history; "
            "T10YIE breakeven data starts Jan 2003)."
        )
        st.divider()

    elif cape_ok:
        _panel_error("Excess CAPE Yield (ECY)", _dgs10_err or _t10yie_err, "retry_ecy")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # CURVE & POLICY
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Curve & Policy")

    # ── Panel 3: Yield Curve ─────────────────────────────────────────────────

    st.markdown("#### 2/10 Yield Curve Spread")
    if t10y2y is not None:

        t10y2y_clean       = t10y2y.dropna()
        current_spread_bps = float(t10y2y_clean.iloc[-1]) * 100
        curve_state        = _yield_curve_state(t10y2y_clean)
        t10y2y_bps         = t10y2y_clean * 100

        col_l, col_r = st.columns([1, 2])
        with col_l:
            yc_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="yc_window",
            )
            yc_start    = _window_start(yc_window)
            yc_pctile_w = _window_pctile(t10y2y_bps, current_spread_bps, yc_start)
            yc_data     = t10y2y_bps.loc[yc_start:]
            sign = "+" if current_spread_bps >= 0 else ""
            st.metric("10Y − 2Y Spread", f"{sign}{current_spread_bps:.0f} bps")
            st.caption(
                f"{curve_state} · "
                f"{_ordinal(yc_pctile_w)} percentile of {yc_window} window"
            )
        with col_r:
            fig_yc = go.Figure()
            _add_recession_shading(fig_yc, rec_periods or [], yc_start)
            fig_yc.add_trace(go.Scatter(
                x=yc_data.index, y=yc_data.values,
                mode="lines", name="10Y−2Y (bps)",
                line=dict(color=_C["primary"], width=2),
            ))
            fig_yc.add_hline(
                y=0, line_dash="dash", line_color=_C["ref"], line_width=1,
                annotation_text="0 = flat  |  above: normal  |  below: inverted",
                annotation_position="top left", annotation_font_size=9,
                annotation_font_color="#888",
            )
            _add_current_annotation(
                fig_yc, current_spread_bps,
                f"Current {current_spread_bps:+.0f} bps ({_ordinal(yc_pctile_w)} pct, {yc_window})",
            )
            _apply_style(fig_yc)
            fig_yc.update_yaxes(title_text="Spread (bps)")
            _yr = _tight_yrange(yc_data, [current_spread_bps, 0.0])
            if _yr:
                fig_yc.update_yaxes(range=_yr)
            st.plotly_chart(fig_yc, width='stretch')

        st.caption(
            "Yield curve inversions (spread < 0) have preceded each of the last seven "
            "recessions with a 12–18 month lead time. Gray shading marks NBER-dated recessions."
        )
        st.divider()
    else:
        _panel_error("2/10 Yield Curve Spread", _t10y2y_err, "retry_yc")
        st.divider()

    # ── Panel 4: Fed Funds ───────────────────────────────────────────────────

    st.markdown("#### Effective Federal Funds Rate")
    if dff is not None:

        dff_clean  = dff.dropna()
        current_ff = float(dff_clean.iloc[-1])

        dff_1y_data = dff_clean[dff_clean.index <= ONE_YR_AGO]
        ff_1y_ago   = float(dff_1y_data.iloc[-1]) if not dff_1y_data.empty else current_ff
        ff_1y_date  = dff_1y_data.index[-1].strftime("%b %Y") if not dff_1y_data.empty else ""
        ff_chg_bps  = (current_ff - ff_1y_ago) * 100

        col_l, col_r = st.columns([1, 2])
        with col_l:
            ff_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="ff_window",
            )
            ff_start    = _window_start(ff_window)
            ff_pctile_w = _window_pctile(dff_clean, current_ff, ff_start)
            ff_data     = dff_clean.loc[ff_start:]
            chg_sign = "+" if ff_chg_bps >= 0 else ""
            st.metric("Fed Funds Rate", f"{current_ff:.2f}%")
            st.caption(
                f"{chg_sign}{ff_chg_bps:.0f} bps from {ff_1y_date} · "
                f"{_ordinal(ff_pctile_w)} percentile of {ff_window} window"
            )
        with col_r:
            fig_ff = go.Figure()
            _add_recession_shading(fig_ff, rec_periods or [], ff_start)
            fig_ff.add_trace(go.Scatter(
                x=ff_data.index, y=ff_data.values,
                mode="lines", name="Fed Funds (%)",
                line=dict(color=_C["primary"], width=2),
            ))
            _add_current_annotation(
                fig_ff, current_ff,
                f"Current {current_ff:.2f}% ({_ordinal(ff_pctile_w)} pct, {ff_window})",
            )
            _apply_style(fig_ff)
            fig_ff.update_yaxes(title_text="Rate (%)")
            _yr = _tight_yrange(ff_data, [current_ff])
            if _yr:
                _yr[0] = max(0.0, _yr[0])
                fig_ff.update_yaxes(range=_yr)
            st.plotly_chart(fig_ff, width='stretch')

        st.caption(_ff_interpretation(current_ff, ff_chg_bps))
        st.divider()
    else:
        _panel_error("Effective Federal Funds Rate", _dff_err, "retry_ff")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # CREDIT
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Credit")

    # ── Panel 5: HY Credit Spreads ───────────────────────────────────────────

    st.markdown("#### HY Credit Spreads (OAS)")
    if hy_oas is not None:

        hy_clean       = hy_oas.dropna()
        hy_bps         = hy_clean * 100
        current_hy     = float(hy_bps.iloc[-1])
        hy_median_bps  = float(hy_bps.median())
        hy_since       = hy_bps.index[0].strftime("%b %Y")
        hy_data_start  = hy_bps.index[0]

        col_l, col_r = st.columns([1, 2])
        with col_l:
            hy_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="hy_window",
            )
            hy_start    = _window_start(hy_window)
            hy_pctile_w = _window_pctile(hy_bps, current_hy, hy_start)
            hy_data     = hy_bps.loc[hy_start:]
            # F.1 fix: warn when selected window predates available data
            _hy_avail_start = hy_data_start.date()
            _hy_window_start_date = date.fromisoformat(hy_start) if hy_start != "1800-01-01" else date(1800, 1, 1)
            if _hy_window_start_date < _hy_avail_start:
                st.info(
                    f"HY OAS data starts **{hy_since}** (FRED restricts BAMLH0A0HYM2 to this date). "
                    f"Selecting a {hy_window} window shows only ~{len(hy_clean)} trading days of data. "
                    "The percentile is computed over the available window, not the full requested window."
                )
            st.metric("HY OAS", f"{current_hy:.0f} bps")
            st.caption(
                f"{_ordinal(hy_pctile_w)} percentile of available history "
                f"(data from {hy_since})"
            )
        with col_r:
            fig_hy = go.Figure()
            _add_recession_shading(fig_hy, rec_periods or [], hy_start)
            fig_hy.add_trace(go.Scatter(
                x=hy_data.index, y=hy_data.values,
                mode="lines", name="HY OAS (bps)",
                line=dict(color=_C["primary"], width=2),
            ))
            fig_hy.add_hline(
                y=hy_median_bps,
                line_dash="dash", line_color=_C["ref"], line_width=1,
                annotation_text=f"Median {hy_median_bps:.0f} bps",
                annotation_position="right", annotation_font_size=10,
            )
            _add_current_annotation(
                fig_hy, current_hy,
                f"Current {current_hy:.0f} bps ({_ordinal(hy_pctile_w)} pct, {hy_since}+)",
            )
            _apply_style(fig_hy)
            fig_hy.update_yaxes(title_text="OAS (bps)")
            _yr = _tight_yrange(hy_data, [current_hy, hy_median_bps])
            if _yr:
                _yr[0] = max(0.0, _yr[0])
                fig_hy.update_yaxes(range=_yr)
            st.plotly_chart(fig_hy, width='stretch')

        hy_framing = (
            "suggests late-cycle complacency — limited cushion for additional compression"
            if hy_pctile_w < 40 else
            "is near the median for the available window, consistent with a neutral credit environment"
            if hy_pctile_w < 60 else
            "reflects elevated stress or risk aversion, pricing in meaningful default risk"
        )
        st.caption(
            f"HY spreads at the {_ordinal(hy_pctile_w)} percentile of the available history "
            f"({hy_since}+) — {hy_framing}. "
            "Low spreads historically correspond to late-cycle dynamics where credit risk "
            "is under-priced. Gray shading marks NBER-dated recessions. "
            f"*FRED restricts this ICE BofA series to {hy_since}+; full pre-2023 history "
            "is unavailable from FRED's API.*"
        )
        st.divider()
    else:
        _panel_error("HY Credit Spreads (OAS)", _hy_oas_err, "retry_hy")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # LABOR
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Labor")

    # ── Panel 6: Unemployment Rate ───────────────────────────────────────────

    st.markdown("#### Unemployment Rate (UNRATE)")
    if unrate is not None:

        ur_clean      = unrate.dropna()
        current_ur    = float(ur_clean.iloc[-1])
        ur_since      = ur_clean.index[0].strftime("%b %Y")
        ur_1y_data    = ur_clean[ur_clean.index <= ONE_YR_AGO]
        ur_1y_ago     = float(ur_1y_data.iloc[-1]) if not ur_1y_data.empty else current_ur
        ur_chg        = (current_ur - ur_1y_ago) * 100  # in basis points of percentage points

        col_l, col_r = st.columns([1, 2])
        with col_l:
            ur_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="ur_window", horizontal=True,
            )
            ur_yaxis = st.radio(
                "Y-axis", ["Full series", "Excl. 2020 spike"],
                index=0, key="ur_yaxis", horizontal=True,
            )
            ur_start    = _window_start(ur_window)
            ur_pctile_w = _window_pctile(ur_clean, current_ur, ur_start)
            ur_data     = ur_clean.loc[ur_start:]
            chg_sign = "+" if ur_chg >= 0 else ""
            st.metric("Unemployment Rate", f"{current_ur:.1f}%")
            st.caption(
                f"{chg_sign}{ur_chg:.0f} bps from one year ago · "
                f"{_ordinal(ur_pctile_w)} percentile of {ur_window} window"
            )
        with col_r:
            fig_ur = go.Figure()
            _add_recession_shading(fig_ur, rec_periods or [], ur_start)
            fig_ur.add_trace(go.Scatter(
                x=ur_data.index, y=ur_data.values,
                mode="lines", name="Unemployment (%)",
                line=dict(color=_C["primary"], width=2),
            ))
            _add_current_annotation(
                fig_ur, current_ur,
                f"Current {current_ur:.1f}% ({_ordinal(ur_pctile_w)} pct, {ur_window})",
            )
            _apply_style(fig_ur)
            fig_ur.update_yaxes(title_text="Rate (%)")
            _ur_base = ur_data[ur_data.index.year != 2020] if ur_yaxis == "Excl. 2020 spike" else ur_data
            _yr = _tight_yrange(_ur_base, [current_ur])
            if _yr:
                _yr[0] = max(0.0, _yr[0])
                fig_ur.update_yaxes(range=_yr)
            st.plotly_chart(fig_ur, width='stretch')

        if ur_pctile_w < 30:
            _ur_interp = (
                f"Unemployment at {current_ur:.1f}% is in the {_ordinal(ur_pctile_w)} percentile "
                f"of the {ur_window} window — historically low, consistent with a tight labor market "
                "and late-cycle conditions. Low unemployment has historically preceded cyclical peaks."
            )
        elif ur_pctile_w < 60:
            _ur_interp = (
                f"Unemployment at {current_ur:.1f}% is in the {_ordinal(ur_pctile_w)} percentile "
                f"of the {ur_window} window — near the historical median for this window, "
                "consistent with a mid-cycle labor market."
            )
        else:
            _ur_interp = (
                f"Unemployment at {current_ur:.1f}% is in the {_ordinal(ur_pctile_w)} percentile "
                f"of the {ur_window} window — elevated relative to recent history, "
                "potentially consistent with recessionary or early-recovery conditions."
            )
        st.caption(
            _ur_interp + " "
            "Rising unemployment from a cyclical low is a key recession coincident indicator. "
            "Gray shading marks NBER-dated recessions."
        )
        st.divider()
    else:
        _panel_error("Unemployment Rate", _unrate_err, "retry_ur")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # GROWTH
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Growth")

    # ── Panel 7: Real GDP Growth ──────────────────────────────────────────────

    st.markdown("#### Real GDP Growth (QoQ Annualized)")
    if gdp_gr is not None:

        gdp_clean   = gdp_gr.dropna()
        current_gdp = float(gdp_clean.iloc[-1])
        gdp_as_of   = gdp_clean.index[-1].strftime("%b %Y")

        col_l, col_r = st.columns([1, 2])
        with col_l:
            gdp_window = st.radio(
                "Window", ["10Y", "20Y", "Max"],
                index=1, key="gdp_window", horizontal=True,
            )
            gdp_yaxis = st.radio(
                "Y-axis", ["Full series", "Excl. 2020 outliers"],
                index=1, key="gdp_yaxis", horizontal=True,
            )
            gdp_start    = _window_start(gdp_window)
            gdp_pctile_w = _window_pctile(gdp_clean, current_gdp, gdp_start)
            gdp_data     = gdp_clean.loc[gdp_start:]
            st.metric("Real GDP Growth (QoQ ann.)", f"{current_gdp:.1f}%")
            st.caption(
                f"As of {gdp_as_of} (quarterly release, 1-2 quarter lag) · "
                f"{_ordinal(gdp_pctile_w)} percentile of {gdp_window} window"
            )
        with col_r:
            fig_gdp = go.Figure()
            _add_recession_shading(fig_gdp, rec_periods or [], gdp_start)
            fig_gdp.add_trace(go.Bar(
                x=gdp_data.index, y=gdp_data.values,
                name="Real GDP QoQ ann. (%)",
                marker_color=[_C["primary"] if v >= 0 else "#C0392B" for v in gdp_data.values],
            ))
            fig_gdp.add_hline(
                y=0, line_color=_C["ref"], line_width=1, line_dash="dash",
            )
            _apply_style(fig_gdp)
            fig_gdp.update_yaxes(title_text="Growth (%)")
            _gdp_base = gdp_data[gdp_data.index.year != 2020] if gdp_yaxis == "Excl. 2020 outliers" else gdp_data
            _yr = _tight_yrange(_gdp_base, [current_gdp, 0.0])
            if _yr:
                fig_gdp.update_yaxes(range=_yr)
            st.plotly_chart(fig_gdp, width='stretch')

        if current_gdp >= 3.0:
            _gdp_interp = f"GDP growth of {current_gdp:.1f}% is above trend — consistent with expansion."
        elif current_gdp >= 0.0:
            _gdp_interp = f"GDP growth of {current_gdp:.1f}% is positive but below the long-run trend of ~2%."
        else:
            _gdp_interp = (
                f"GDP growth of {current_gdp:.1f}% is negative. Two consecutive negative quarters "
                "satisfies the informal (not NBER) recession definition."
            )
        st.caption(
            _gdp_interp + " "
            "FRED A191RL1Q225SBEA: real GDP percent change from preceding period, "
            "seasonally adjusted annual rate. Gray shading = NBER recessions."
        )
        st.divider()
    else:
        _panel_error("Real GDP Growth", _gdp_err, "retry_gdp")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # INFLATION
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Inflation")

    # ── Panel 8: Core CPI YoY ────────────────────────────────────────────────

    st.markdown("#### Core CPI (ex Food & Energy) — Year-over-Year")
    if core_cpi is not None:

        cpi_clean    = core_cpi.dropna()
        # Compute YoY % change
        cpi_yoy      = cpi_clean.pct_change(12) * 100
        cpi_yoy      = cpi_yoy.dropna()
        current_cpi  = float(cpi_yoy.iloc[-1])
        cpi_as_of    = cpi_yoy.index[-1].strftime("%b %Y")

        col_l, col_r = st.columns([1, 2])
        with col_l:
            cpi_window   = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="cpi_window",
            )
            cpi_start    = _window_start(cpi_window)
            cpi_pctile_w = _window_pctile(cpi_yoy, current_cpi, cpi_start)
            cpi_data     = cpi_yoy.loc[cpi_start:]
            st.metric("Core CPI YoY", f"{current_cpi:.1f}%")
            st.caption(
                f"As of {cpi_as_of} · "
                f"{_ordinal(cpi_pctile_w)} percentile of {cpi_window} window"
            )
        with col_r:
            fig_cpi = go.Figure()
            _add_recession_shading(fig_cpi, rec_periods or [], cpi_start)
            fig_cpi.add_trace(go.Scatter(
                x=cpi_data.index, y=cpi_data.values,
                mode="lines", name="Core CPI YoY (%)",
                line=dict(color=_C["primary"], width=2),
            ))
            fig_cpi.add_hline(
                y=2.0, line_dash="dash", line_color=_C["ref"], line_width=1,
                annotation_text="Fed target 2%",
                annotation_position="right", annotation_font_size=10,
            )
            _add_current_annotation(
                fig_cpi, current_cpi,
                f"Current {current_cpi:.1f}% ({_ordinal(cpi_pctile_w)} pct, {cpi_window})",
            )
            _apply_style(fig_cpi)
            fig_cpi.update_yaxes(title_text="YoY Change (%)")
            _yr = _tight_yrange(cpi_data, [current_cpi, 2.0])
            if _yr:
                fig_cpi.update_yaxes(range=_yr)
            st.plotly_chart(fig_cpi, width='stretch')

        if current_cpi > 4.0:
            _cpi_interp = (
                f"Core CPI at {current_cpi:.1f}% is well above the Fed's 2% target — "
                "an inflationary regime where TIPS and Real Assets provide direct portfolio hedging."
            )
        elif current_cpi > 2.5:
            _cpi_interp = (
                f"Core CPI at {current_cpi:.1f}% remains above target — "
                "disinflation in progress but not yet complete. "
                "TIPS sleeve remains a relevant inflation hedge."
            )
        elif current_cpi > 1.5:
            _cpi_interp = (
                f"Core CPI at {current_cpi:.1f}% is near or slightly above the Fed's 2% target — "
                "a regime where nominal duration (Core Fixed Income) is reasonably priced."
            )
        else:
            _cpi_interp = (
                f"Core CPI at {current_cpi:.1f}% is below target — "
                "disinflationary conditions that historically support nominal bond returns "
                "and may reduce the immediate urgency of the TIPS inflation hedge."
            )
        st.caption(
            _cpi_interp + " "
            "FRED CPILFESL: CPI for All Urban Consumers, All Items Less Food and Energy, "
            "12-month percent change. Gray shading = NBER recessions."
        )
        st.divider()
    else:
        _panel_error("Core CPI YoY", _cpi_err, "retry_cpi")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # RELATIVE PERFORMANCE
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Relative Performance")

    # ── Panel 9: US vs. International ────────────────────────────────────────

    st.markdown("#### US vs. International Equity")

    try:
        with st.spinner("Loading SPY / EFA price data…"):
            spy_raw = _load_price_series("SPY", "2004-01-01")
            efa_raw = _load_price_series("EFA", "2004-01-01")

        aligned = pd.concat([spy_raw, efa_raw], axis=1).dropna()
        aligned.columns = ["SPY", "EFA"]
        aligned.index = pd.to_datetime(aligned.index)
        ratio = (aligned["SPY"] / aligned["EFA"]).dropna()

        twenty_start  = _window_start("20Y")
        ratio_20y     = ratio[ratio.index >= pd.Timestamp(twenty_start)]
        current_ratio = float(ratio.iloc[-1])

        ratio_1y_base = ratio[ratio.index <= ONE_YR_AGO]
        ratio_1y_ago  = float(ratio_1y_base.iloc[-1]) if not ratio_1y_base.empty else current_ratio
        rel_perf_bps  = (current_ratio / ratio_1y_ago - 1) * 10000

        col_l, col_r = st.columns([1, 2])
        with col_l:
            us_window  = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="us_window",
            )
            us_start       = _window_start(us_window)
            ratio_w        = ratio[ratio.index >= pd.Timestamp(us_start)]
            ratio_pctile_w = _window_pctile(ratio, current_ratio, us_start)
            rel_dir = "outperformed" if rel_perf_bps >= 0 else "underperformed"
            st.metric(f"US/Intl Ratio ({us_window} percentile)", _ordinal(ratio_pctile_w))
            st.caption(
                f"US {rel_dir} international by {abs(rel_perf_bps):.0f} bps over last 12 months"
            )
        with col_r:
            ratio_norm = ratio_w / float(ratio_w.iloc[0])
            ratio_20y_median_norm = (
                float(ratio_20y.median()) / float(ratio_w.iloc[0])
                if not ratio_20y.empty and ratio_w.iloc[0] != 0 else 1.0
            )
            fig_us = go.Figure()
            fig_us.add_trace(go.Scatter(
                x=ratio_norm.index, y=ratio_norm.values,
                mode="lines", name="SPY / EFA (normalized)",
                line=dict(color=_C["primary"], width=2),
            ))
            fig_us.add_hline(
                y=ratio_20y_median_norm,
                line_dash="dash", line_color=_C["ref"], line_width=1,
                annotation_text="20Y median",
                annotation_position="right", annotation_font_size=10,
            )
            _apply_style(fig_us)
            fig_us.update_yaxes(title_text="Ratio (normalized to 1.0 at window start)")
            _yr = _tight_yrange(ratio_norm, [ratio_20y_median_norm])
            if _yr:
                fig_us.update_yaxes(range=_yr)
            fig_us.add_annotation(
                xref="paper", yref="paper",
                x=0.01, y=0.98,
                text="Rising = US outperforming international",
                showarrow=False,
                font=dict(size=9, color="#888"),
                xanchor="left", yanchor="top",
            )
            st.plotly_chart(fig_us, width='stretch')

        us_label = percentile_label(ratio_pctile_w)
        st.caption(
            f"US outperformance vs. international is at the {_ordinal(ratio_pctile_w)} percentile "
            f"of the {us_window} window — {us_label} relative to that window. "
            "Extended US outperformance has historically mean-reverted via valuation "
            "convergence and dollar cycle turns, supporting the International Developed "
            "sleeve and its valuation-driven thesis."
        )

    except Exception as exc:
        st.error(f"US vs. International data unavailable: {exc}")

    st.divider()

    # ── Sources ───────────────────────────────────────────────────────────────

    with st.expander("Data sources & freshness"):
        _src_lines = []
        if cape_ok:
            _cape_last = cape_series.dropna().index[-1].strftime("%b %Y")
            _src_lines.append(
                f"**Shiller CAPE** (multpl.com, sourced from Robert Shiller's dataset): "
                f"last observation **{_cape_last}** · monthly cadence"
            )
        else:
            _src_lines.append("**Shiller CAPE**: unavailable")

        def _fred_src(label: str, series: "pd.Series | None") -> str:
            if series is not None:
                last = series.dropna().index[-1].strftime("%b %d, %Y")
                return f"**{label}**: last observation **{last}**"
            return f"**{label}**: unavailable"

        _src_lines += [
            _fred_src("FRED DGS10 (10-Year Treasury Rate)",              dgs10)  + " · daily",
            _fred_src("FRED T10YIE (10-Year Breakeven Inflation)",       t10yie) + " · daily · starts Jan 2003",
            _fred_src("FRED T10Y2Y (10Y−2Y Treasury spread)",            t10y2y) + " · daily",
            _fred_src("FRED DFF (Fed Funds Rate)",                       dff)    + " · daily",
            _fred_src("FRED BAMLH0A0HYM2 (ICE BofA HY OAS, May 2023+)", hy_oas) + " · daily",
            _fred_src("FRED UNRATE (Unemployment Rate)",                 unrate) + " · monthly",
            _fred_src("FRED A191RL1Q225SBEA (Real GDP QoQ ann.)",        gdp_gr) + " · quarterly (1-2 quarter lag)",
            _fred_src("FRED CPILFESL (Core CPI, level)",                 core_cpi) + " · monthly (YoY change computed)",
            "**FRED USREC** (NBER recession indicator): monthly, lags recession end by ~12 months"
            + ("" if rec_periods is not None else " — unavailable"),
        ]
        _src_lines.append(
            "**Yahoo Finance** (SPY, EFA): daily prices via local SQLite cache · "
            "used for the US vs. International relative performance panel"
        )
        _src_lines.append(
            "**ISM Manufacturing PMI**: not available via FRED API — see post-launch backlog for status."
        )
        st.caption("  \n".join(_src_lines))
    render_footer()
