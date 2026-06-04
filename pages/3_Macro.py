"""Macro Dashboard — regime indicator panels."""
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Macro Dashboard", layout="wide")

from src import factor_regime, macro, shiller
from src.asof import as_of_banner
from src.config import IS_DEMO
from src.macro import (
    format_ur_delta,
    interpret_curve_spread,
    interpret_excess_cape,
    interpret_gdp_growth,
    interpret_hy_spread,
    interpret_us_vs_intl_spread,
)
from src.prices import get_prices
from src.prose_helpers import percentile_label
from src.ui_helpers import render_footer, render_page_header
render_page_header()


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
        margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="white",
        plot_bgcolor="#FAFAFA",
        font=dict(color="#333333", size=12),
        showlegend=False,
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#EBEBEB", showgrid=True, zeroline=False, tickfont_size=11, automargin=True)
    fig.update_yaxes(gridcolor="#EBEBEB", showgrid=True, zeroline=False, tickfont_size=11, automargin=True)
    return fig


def _tight_yrange(series: pd.Series, extra_ys: list | None = None, pad: float = 0.10) -> list | None:
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


@st.cache_data(ttl=86400, show_spinner=False)
def _load_size_factor_frame() -> pd.DataFrame:
    return factor_regime.build_size_factor_frame(end=TODAY)


@st.cache_data(ttl=86400, show_spinner=False)
def _load_style_factor_frame() -> pd.DataFrame:
    return factor_regime.build_style_factor_frame(end=TODAY)


# ── page ──────────────────────────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:

    st.markdown("## Macro Dashboard")
    st.caption(
        "Macro indicators across growth, inflation, monetary policy, credit, valuation, "
        "and cross-asset performance. Used as reference data — positioning remains "
        "policy-driven, not view-driven."
    )
    st.caption(as_of_banner())

    hdr_l, hdr_r = st.columns([3, 1])
    with hdr_l:
        st.caption(
            f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
            "Data: FRED & Shiller. Percentiles are window-relative (toggle selector to recompute)."
        )
    with hdr_r:
        if not IS_DEMO and st.button("Force refresh", type="secondary",
                     help="Bypass the disk cache and re-fetch macro data from FRED and Shiller."):
            macro.clear_macro_cache()
            shiller.clear_shiller_cache()
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # ── helpers defined inside col ────────────────────────────────────────────

    def _try_fred(series_id: str, start_date: str):
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

    # ── Load all FRED data upfront ────────────────────────────────────────────

    with st.spinner("Loading FRED data…"):
        rec_periods, _rec_err    = _try_rec()
        usrec,       _usrec_err  = _try_fred("USREC",             "1945-01-01")
        t10y2y,      _t10y2y_err = _try_fred("T10Y2Y",           "1976-06-01")
        dff,         _dff_err    = _try_fred("DFF",               "1954-07-01")
        hy_oas,      _hy_oas_err = _try_fred("BAMLH0A0HYM2",     "1996-12-31")
        ig_oas,      _ig_oas_err = _try_fred("BAMLC0A0CM",       "1996-01-01")
        ccc_oas,     _ccc_oas_err = _try_fred("BAMLH0A3HYC",     "1996-01-01")
        dgs10,       _dgs10_err  = _try_fred("DGS10",             "1990-01-01")
        dgs3mo,      _dgs3mo_err = _try_fred("DGS3MO",            "1990-01-01")
        dgs2,        _dgs2_err   = _try_fred("DGS2",              "1990-01-01")
        dgs1,        _dgs1_err   = _try_fred("DGS1",              "1990-01-01")
        dgs5,        _dgs5_err   = _try_fred("DGS5",              "1990-01-01")
        dgs7,        _dgs7_err   = _try_fred("DGS7",              "1990-01-01")
        dgs20,       _dgs20_err  = _try_fred("DGS20",             "1990-01-01")
        dgs30,       _dgs30_err  = _try_fred("DGS30",             "1990-01-01")
        t10yie,      _t10yie_err = _try_fred("T10YIE",            "2003-01-01")
        dfii10,      _dfii10_err = _try_fred("DFII10",            "2003-01-01")
        unrate,      _unrate_err = _try_fred("UNRATE",            "1948-01-01")
        gdp_gr,      _gdp_err    = _try_fred("A191RL1Q225SBEA",   "1947-01-01")
        core_cpi,    _cpi_err    = _try_fred("CPILFESL",          "1957-01-01")
        cfnai_diff,  _cfnai_err  = _try_fred("CFNAIDIFF",         "1967-01-01")
        dtwexbgs,    _dtwex_err  = _try_fred("DTWEXBGS",          "2006-01-01")

    # ── Compute rate volatility from DGS10 (VXTLT not in the price cache) ──
    rate_vol_21 = None
    if dgs10 is not None:
        _dgs10_ch  = dgs10.dropna().diff().dropna()
        rate_vol_21 = (_dgs10_ch.rolling(21).std() * (252 ** 0.5)).dropna()

    # ── Pre-compute cross-panel reference values ──────────────────────────────
    _shared_cpi_pct = None
    if core_cpi is not None:
        _cpi_yoy_shared = core_cpi.dropna().pct_change(12) * 100
        _cpi_yoy_shared = _cpi_yoy_shared.dropna()
        if not _cpi_yoy_shared.empty:
            _shared_cpi_pct = float(_cpi_yoy_shared.iloc[-1])

    _shared_t10yie_pct = None
    if t10yie is not None and not t10yie.dropna().empty:
        _shared_t10yie_pct = float(t10yie.dropna().iloc[-1])

    _shared_hy_bps = None
    if hy_oas is not None and not hy_oas.dropna().empty:
        _shared_hy_bps = float(hy_oas.dropna().iloc[-1]) * 100

    st.caption("Gray shading on time-series charts marks NBER-dated recession periods.")

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. REGIME CLASSIFIER
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
            "are neither too tight nor too loose. "
            "The regime indicators on this page inform context; the SAA itself is policy-driven "
            "and not adjusted in response to regime classification."
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

        with st.expander("How regimes transition", expanded=False):
            st.caption(
                "Cycle regimes shift gradually as the three input variables evolve. Mid-cycle becomes "
                "late-cycle as the yield curve flattens or inverts (Fed tightening to slow growth), "
                "unemployment troughs and begins rising from cycle lows, and credit spreads compress "
                "before widening on rising defaults. Late-cycle becomes recession when NBER officially "
                "declares it (with a 6–18 month lag) and unemployment rises sharply. Recession becomes "
                "early-cycle as the curve re-steepens (Fed easing), unemployment peaks and begins "
                "falling, and credit spreads peak and start tightening. The transitions are not "
                "discrete — they reflect gradual shifts in the underlying variables, which is why the "
                "regime classifier captures direction-of-travel rather than predicting turning points."
            )

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
                _bt_series = pd.Series(_regime_num, index=_bt_common)

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

        with st.expander("Methodology", expanded=False):
            st.caption(
                "The regime classifier maps three FRED variables — USREC (NBER recession indicator), "
                "T10Y2Y (10Y minus 2Y Treasury spread), and UNRATE (unemployment rate) — to one of "
                "four labels: Recession, Late-cycle, Mid-cycle, or Early-cycle. Rules are applied in "
                "priority order: (1) Recession when USREC = 1; (2) Early-cycle when UNRATE > 5.5% "
                "and T10Y2Y > −0.25 (labor still healing, curve not inverted); (3) Late-cycle when "
                "T10Y2Y < −0.25 or UNRATE < 4.2% (inverted curve or historically tight labor); "
                "(4) Mid-cycle as the default. Full rules and threshold rationale in "
                "docs/regime_classifier.md. This is a rules-based heuristic, not a forecast or "
                "trading signal — USREC is declared retroactively by the NBER and may lag actual "
                "recession onset by 6–18 months."
            )

    st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. GROWTH
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Growth")

    # ── Real GDP ─────────────────────────────────────────────────────────────

    st.markdown("#### Real GDP Growth (QoQ Annualized)")
    if gdp_gr is not None:

        gdp_clean   = gdp_gr.dropna()
        current_gdp = float(gdp_clean.iloc[-1])
        gdp_as_of   = gdp_clean.index[-1].strftime("%b %Y")

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
        _gdp_base = gdp_data[gdp_data.index.year != 2020] if gdp_yaxis == "Excl. 2020 outliers" else gdp_data
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
        _yr = _tight_yrange(_gdp_base, [current_gdp, 0.0])
        if _yr:
            fig_gdp.update_yaxes(range=_yr)
        st.plotly_chart(fig_gdp, width='stretch')
        st.metric("Real GDP Growth (QoQ ann.)", f"{current_gdp:.1f}%")
        st.caption(
            f"As of {gdp_as_of} (quarterly release, 1-2 quarter lag) · "
            f"{_ordinal(gdp_pctile_w)} percentile of {gdp_window} window"
        )

        st.caption(
            interpret_gdp_growth(current_gdp) + " "
            "FRED A191RL1Q225SBEA: real GDP percent change from preceding period, "
            "seasonally adjusted annual rate."
        )
        st.divider()
    else:
        _panel_error("Real GDP Growth", _gdp_err, "retry_gdp")
        st.divider()

    # ── Unemployment Rate (moved from Labor section) ──────────────────────────

    st.markdown("#### Unemployment Rate (UNRATE)")
    if unrate is not None:

        ur_clean      = unrate.dropna()
        current_ur    = float(ur_clean.iloc[-1])
        ur_since      = ur_clean.index[0].strftime("%b %Y")
        ur_1y_data    = ur_clean[ur_clean.index <= ONE_YR_AGO]
        ur_1y_ago     = float(ur_1y_data.iloc[-1]) if not ur_1y_data.empty else current_ur
        ur_chg        = (current_ur - ur_1y_ago) * 100

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
        st.metric("Unemployment Rate", f"{current_ur:.1f}%")
        st.caption(
            f"{format_ur_delta(ur_chg)} · "
            f"{_ordinal(ur_pctile_w)} percentile of {ur_window} window"
        )

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
            "Rising unemployment from a cyclical low is a key recession coincident indicator."
        )
        st.divider()
    else:
        _panel_error("Unemployment Rate", _unrate_err, "retry_ur")
        st.divider()

    # ── Manufacturing Activity — CFNAIDIFF (ISM PMI proxy) ────────────────────

    st.markdown("#### Manufacturing Activity (CFNAIDIFF — ISM PMI Proxy)")
    if cfnai_diff is not None:

        cfnai_clean   = cfnai_diff.dropna()
        current_cfnai = float(cfnai_clean.iloc[-1])
        cfnai_as_of   = cfnai_clean.index[-1].strftime("%b %Y")
        sign_cfnai    = "+" if current_cfnai >= 0 else ""

        cfnai_window = st.radio(
            "Window", ["5Y", "10Y", "20Y", "Max"],
            index=1, key="cfnai_window", horizontal=True,
        )
        cfnai_start    = _window_start(cfnai_window)
        cfnai_pctile_w = _window_pctile(cfnai_clean, current_cfnai, cfnai_start)
        cfnai_data     = cfnai_clean.loc[cfnai_start:]

        fig_cfnai = go.Figure()
        _add_recession_shading(fig_cfnai, rec_periods or [], cfnai_start)
        fig_cfnai.add_trace(go.Bar(
            x=cfnai_data.index, y=cfnai_data.values,
            name="CFNAIDIFF",
            marker_color=[_C["primary"] if v >= 0 else "#C0392B" for v in cfnai_data.values],
        ))
        fig_cfnai.add_hline(
            y=0, line_color=_C["ref"], line_width=1, line_dash="dash",
        )
        _add_current_annotation(
            fig_cfnai, current_cfnai,
            f"Current {sign_cfnai}{current_cfnai:.2f} ({_ordinal(cfnai_pctile_w)} pct, {cfnai_window})",
        )
        _apply_style(fig_cfnai)
        fig_cfnai.update_yaxes(title_text="Diffusion Index")
        _yr = _tight_yrange(cfnai_data, [current_cfnai, 0.0])
        if _yr:
            fig_cfnai.update_yaxes(range=_yr)
        st.plotly_chart(fig_cfnai, width='stretch')
        st.metric("CFNAIDIFF (PMI Proxy)", f"{sign_cfnai}{current_cfnai:.2f}")
        st.caption(
            f"As of {cfnai_as_of} (monthly, ~1-month lag) · "
            f"{_ordinal(cfnai_pctile_w)} percentile of {cfnai_window} window"
        )

        if current_cfnai > 0:
            _cfnai_direction = "above zero — above-trend economic breadth"
            _cfnai_cycle = (
                "Positive diffusion is consistent with mid-to-late-cycle expansion momentum "
                "and correlates with tightening credit spreads and support for cyclical equity sleeves."
            )
        else:
            _cfnai_direction = "below zero — below-trend economic breadth"
            _cfnai_cycle = (
                "Negative diffusion is consistent with below-trend activity or early-cycle conditions. "
                "Historically precedes credit-spread widening and supports a quality bias in equity sleeves."
            )
        st.caption(
            f"CFNAIDIFF (ISM PMI proxy; NAPM series unavailable on FRED) measures the breadth of "
            f"economic indicators above vs. below trend — zero is the neutral line, positive is expansionary. "
            f"At {sign_cfnai}{current_cfnai:.2f}, the index is {_cfnai_direction}. "
            + _cfnai_cycle
            + " FRED CFNAIDIFF."
        )
        st.divider()
    else:
        _panel_error("Manufacturing Activity (CFNAIDIFF)", _cfnai_err, "retry_cfnai")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. INFLATION & REAL RATES
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Inflation & Real Rates")

    # ── Core CPI ─────────────────────────────────────────────────────────────

    st.markdown("#### Core CPI (ex Food & Energy) — Year-over-Year")
    if core_cpi is not None:

        cpi_clean    = core_cpi.dropna()
        cpi_yoy      = cpi_clean.pct_change(12) * 100
        cpi_yoy      = cpi_yoy.dropna()
        current_cpi  = float(cpi_yoy.iloc[-1])
        cpi_as_of    = cpi_yoy.index[-1].strftime("%b %Y")

        cpi_window = st.radio(
            "Window", ["5Y", "10Y", "20Y", "Max"],
            index=1, key="cpi_window", horizontal=True,
        )
        cpi_start    = _window_start(cpi_window)
        cpi_pctile_w = _window_pctile(cpi_yoy, current_cpi, cpi_start)
        cpi_data     = cpi_yoy.loc[cpi_start:]
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
        st.metric("Core CPI YoY", f"{current_cpi:.1f}%")
        st.caption(
            f"As of {cpi_as_of} · "
            f"{_ordinal(cpi_pctile_w)} percentile of {cpi_window} window"
        )
        st.caption(
            "Core CPI strips out food and energy because those components are volatile on a "
            "month-to-month basis driven by commodity-price shocks; the core reading is the "
            "Fed's preferred gauge of underlying inflation trends because it more closely tracks "
            "the wage-and-services dynamics that monetary policy can actually influence."
        )

        if current_cpi > 4.0:
            _cpi_interp = (
                f"Core CPI at {current_cpi:.1f}% is well above the Fed's 2% target — "
                "an inflationary regime where TIPS and Real Assets provide direct portfolio hedging."
            )
        elif current_cpi > 2.5:
            _cpi_interp = (
                f"Core CPI at {current_cpi:.1f}% remains above target — "
                "disinflation in progress but not complete. "
                "Whether this reflects stalled disinflation or sticky services inflation "
                "matters for the TIPS sleeve: a persistent plateau near 3% is more bearish for "
                "nominal bonds than a CPI still trending gradually toward 2%."
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
            "12-month percent change."
        )
        st.divider()
    else:
        _panel_error("Core CPI YoY", _cpi_err, "retry_cpi")
        st.divider()

    # ── 10Y Breakeven Inflation (T10YIE) ─────────────────────────────────────

    st.markdown("#### 10-Year Breakeven Inflation (T10YIE)")
    if t10yie is not None:

        be_clean    = t10yie.dropna()
        current_be  = float(be_clean.iloc[-1])
        be_as_of    = be_clean.index[-1].strftime("%b %Y")
        be_since    = be_clean.index[0].strftime("%b %Y")

        be_window = st.radio(
            "Window", ["5Y", "10Y", "Max"],
            index=1, key="be_window", horizontal=True,
        )
        be_start    = _window_start(be_window)
        be_pctile_w = _window_pctile(be_clean, current_be, be_start)
        be_data     = be_clean.loc[be_start:]

        fig_be = go.Figure()
        _add_recession_shading(fig_be, rec_periods or [], be_start)
        fig_be.add_trace(go.Scatter(
            x=be_data.index, y=be_data.values,
            mode="lines", name="Breakeven (%)",
            line=dict(color=_C["primary"], width=2),
        ))
        fig_be.add_hline(
            y=2.0, line_dash="dash", line_color=_C["ref"], line_width=1,
            annotation_text="Fed target 2%",
            annotation_position="right", annotation_font_size=10,
        )
        _add_current_annotation(
            fig_be, current_be,
            f"Current {current_be:.2f}% ({_ordinal(be_pctile_w)} pct, {be_window})",
        )
        _apply_style(fig_be)
        fig_be.update_yaxes(title_text="Breakeven Inflation (%)")
        _yr = _tight_yrange(be_data, [current_be, 2.0])
        if _yr:
            fig_be.update_yaxes(range=_yr)
        st.plotly_chart(fig_be, width='stretch')
        st.metric("10Y Breakeven Inflation", f"{current_be:.2f}%")
        st.caption(
            f"As of {be_as_of} · {_ordinal(be_pctile_w)} percentile of {be_window} window "
            f"(data since {be_since})"
        )

        _cpi_ref = f"{_shared_cpi_pct:.1f}%" if _shared_cpi_pct is not None else "N/A"
        if _shared_cpi_pct is not None and _shared_cpi_pct > current_be + 0.3:
            _be_framing = "pricing disinflation toward target — the market expects inflation to fall from current levels"
        elif _shared_cpi_pct is not None and abs(_shared_cpi_pct - current_be) <= 0.3:
            _be_framing = "in line with current realized inflation — the market is not pricing significant directional change"
        else:
            _be_framing = "pricing persistence near or above current CPI levels"

        st.caption(
            f"10Y breakeven at {current_be:.2f}% is the market-implied average inflation rate "
            f"over 10 years (nominal 10Y yield minus TIPS yield, FRED-computed). "
            f"Compared to Core CPI at {_cpi_ref}, the market is {_be_framing}. "
            f"Breakeven is the hurdle rate for TIPS-vs-nominal-Treasury outperformance — "
            f"directly relevant to the 6% TIPS sleeve."
        )
        st.divider()
    else:
        _panel_error("10Y Breakeven Inflation (T10YIE)", _t10yie_err, "retry_be")
        st.divider()

    # ── Real 10Y Treasury Yield (DFII10) ─────────────────────────────────────

    st.markdown("#### Real 10-Year Treasury Yield (DFII10 — TIPS)")
    if dfii10 is not None:

        dfii10_clean    = dfii10.dropna()
        current_real10y = float(dfii10_clean.iloc[-1])
        real10y_as_of   = dfii10_clean.index[-1].strftime("%b %Y")
        real10y_since   = dfii10_clean.index[0].strftime("%b %Y")

        real10y_window = st.radio(
            "Window", ["5Y", "10Y", "Max"],
            index=1, key="real10y_window", horizontal=True,
        )
        real10y_start    = _window_start(real10y_window)
        real10y_pctile_w = _window_pctile(dfii10_clean, current_real10y, real10y_start)
        real10y_data     = dfii10_clean.loc[real10y_start:]

        fig_r10 = go.Figure()
        _add_recession_shading(fig_r10, rec_periods or [], real10y_start)
        fig_r10.add_trace(go.Scatter(
            x=real10y_data.index, y=real10y_data.values,
            mode="lines", name="Real 10Y (%)",
            line=dict(color=_C["primary"], width=2),
        ))
        fig_r10.add_hline(
            y=0, line_dash="dash", line_color=_C["ref"], line_width=1,
            annotation_text="0 = zero real yield (ZIRP era)",
            annotation_position="top left", annotation_font_size=9,
            annotation_font_color="#888",
        )
        _add_current_annotation(
            fig_r10, current_real10y,
            f"Current {current_real10y:+.2f}% ({_ordinal(real10y_pctile_w)} pct, {real10y_window})",
        )
        _apply_style(fig_r10)
        fig_r10.update_yaxes(title_text="Real Yield (%)")
        _yr = _tight_yrange(real10y_data, [current_real10y, 0.0])
        if _yr:
            fig_r10.update_yaxes(range=_yr)
        st.plotly_chart(fig_r10, width='stretch')
        st.metric("Real 10Y Yield (TIPS)", f"{current_real10y:+.2f}%")
        st.caption(
            f"As of {real10y_as_of} · {_ordinal(real10y_pctile_w)} percentile of {real10y_window} window "
            f"(data since {real10y_since})"
        )

        if current_real10y > 1.5:
            _real10y_interp = (
                f"Real 10Y at {current_real10y:+.2f}% is well above zero — a structural shift from "
                f"the ZIRP era of 2010–2022. Positive real rates above 1.5% raise discount rates for "
                f"long-duration growth equities, support USD strength, and make TIPS attractive as "
                f"a source of real yield with inflation protection."
            )
        elif current_real10y > 0:
            _real10y_interp = (
                f"Real 10Y at {current_real10y:+.2f}% is modestly positive — above the ZIRP era but "
                f"below the threshold typically associated with broad rate-driven equity pressure. "
                f"TIPS yield at this level offers positive real return for the TIPS sleeve."
            )
        else:
            _real10y_interp = (
                f"Real 10Y at {current_real10y:+.2f}% is negative — consistent with financial repression "
                f"or aggressive policy easing. Negative real rates are historically supportive of equities, "
                f"real assets, and gold, but reduce the real return available from the TIPS sleeve."
            )
        st.caption(
            _real10y_interp + " "
            "FRED DFII10: 10-Year Treasury Inflation-Indexed Security Yield (TIPS)."
        )
        st.divider()
    else:
        _panel_error("Real 10Y Treasury Yield (DFII10)", _dfii10_err, "retry_real10y")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. MONETARY POLICY & RATE VOLATILITY
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Monetary Policy & Rate Volatility")

    # ── 2/10 Yield Curve ─────────────────────────────────────────────────────

    st.markdown("#### 2/10 Yield Curve Spread")
    if t10y2y is not None:

        t10y2y_clean       = t10y2y.dropna()
        current_spread_bps = float(t10y2y_clean.iloc[-1]) * 100
        curve_state        = _yield_curve_state(t10y2y_clean)
        t10y2y_bps         = t10y2y_clean * 100

        yc_window = st.radio(
            "Window", ["5Y", "10Y", "20Y", "Max"],
            index=1, key="yc_window", horizontal=True,
        )
        yc_start    = _window_start(yc_window)
        yc_pctile_w = _window_pctile(t10y2y_bps, current_spread_bps, yc_start)
        yc_data     = t10y2y_bps.loc[yc_start:]
        sign = "+" if current_spread_bps >= 0 else ""
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
        st.metric("10Y − 2Y Spread", f"{sign}{current_spread_bps:.0f} bps")
        st.caption(
            f"{curve_state} · "
            f"{_ordinal(yc_pctile_w)} percentile of {yc_window} window"
        )

        st.caption(interpret_curve_spread(current_spread_bps))
        st.caption("Percentile computed relative to the selected window.")
        st.divider()
    else:
        _panel_error("2/10 Yield Curve Spread", _t10y2y_err, "retry_yc")
        st.divider()

    # ── Treasury Yield Curve (spot) ───────────────────────────────────────────

    st.markdown("#### Treasury Yield Curve")

    def _last(s) -> float | None:
        return float(s.dropna().iloc[-1]) if s is not None and not s.dropna().empty else None

    _yc_3m  = _last(dgs3mo)
    _yc_1y  = _last(dgs1)
    _yc_2y  = _last(dgs2)
    _yc_5y  = _last(dgs5)
    _yc_7y  = _last(dgs7)
    _yc_10y = _last(dgs10)
    _yc_20y = _last(dgs20)
    _yc_30y = _last(dgs30)

    _yc_maturities = [
        ("3M",  _yc_3m),
        ("1Y",  _yc_1y),
        ("2Y",  _yc_2y),
        ("5Y",  _yc_5y),
        ("7Y",  _yc_7y),
        ("10Y", _yc_10y),
        ("20Y", _yc_20y),
        ("30Y", _yc_30y),
    ]
    _yc_pts = [(m, v) for m, v in _yc_maturities if v is not None]

    if _yc_3m is not None and _yc_2y is not None and _yc_10y is not None:
        fig_yc_spot = go.Figure()
        fig_yc_spot.add_trace(go.Scatter(
            x=[m for m, _ in _yc_pts],
            y=[v for _, v in _yc_pts],
            mode="lines+markers",
            line=dict(color=_C["primary"], width=2),
            marker=dict(size=8, color=_C["primary"]),
            name="Treasury Yield (%)",
        ))
        _apply_style(fig_yc_spot)
        fig_yc_spot.update_layout(
            height=240,
            xaxis=dict(title="Maturity"),
            yaxis=dict(title="Yield (%)"),
        )
        _yr_spot = _tight_yrange(pd.Series([v for _, v in _yc_pts]), [])
        if _yr_spot:
            fig_yc_spot.update_yaxes(range=_yr_spot)
        st.plotly_chart(fig_yc_spot, width="stretch")

        _metric_pts = [(m, v) for m, v in [("3M", _yc_3m), ("2Y", _yc_2y), ("10Y", _yc_10y)] if v is not None]
        _mcols = st.columns(len(_metric_pts))
        for _ci, (m, v) in enumerate(_metric_pts):
            with _mcols[_ci]:
                st.metric(f"{m} Yield", f"{v:.2f}%")

        if _yc_10y > _yc_2y > _yc_3m:
            _yc_shape = "normally upward-sloping across all maturities"
        elif _yc_10y > _yc_3m and _yc_2y >= _yc_10y:
            _yc_shape = "humped — 2Y above 10Y, with the front end elevated relative to the back end"
        elif _yc_3m > _yc_10y:
            _yc_shape = "inverted at the front end — short rates above long rates"
        elif _yc_10y > _yc_2y and _yc_2y < _yc_3m:
            _yc_shape = "mildly inverted at the front end, steepening beyond the 2-year maturity"
        else:
            _yc_shape = "flat to mildly upward-sloping"

        _series_list = ", ".join(f"DGS{m.replace('M','MO')}" for m, _ in _yc_pts)
        st.caption(
            f"The Treasury yield curve is the term structure of risk-free nominal yields across "
            f"maturities. Shape conveys market expectations of future short rates plus a term "
            f"premium — a positively-sloped curve implies markets expect higher short rates ahead "
            f"and/or demand compensation for duration risk; an inverted curve (short above long) "
            f"has historically preceded recessions. Current shape at 3M {_yc_3m:.2f}%, "
            f"2Y {_yc_2y:.2f}%, 10Y {_yc_10y:.2f}% is {_yc_shape}, reflecting current Fed policy "
            f"and market rate expectations. FRED {_series_list}."
        )
        st.divider()
    else:
        _missing = [s for s, v in [("DGS3MO", _yc_3m), ("DGS2", _yc_2y), ("DGS10", _yc_10y)] if v is None]
        st.info(f"Treasury Yield Curve unavailable — missing series: {', '.join(_missing)}")
        st.divider()

    # ── Fed Funds Rate ────────────────────────────────────────────────────────

    st.markdown("#### Effective Federal Funds Rate")
    if dff is not None:

        dff_clean  = dff.dropna()
        current_ff = float(dff_clean.iloc[-1])

        dff_1y_data = dff_clean[dff_clean.index <= ONE_YR_AGO]
        ff_1y_ago   = float(dff_1y_data.iloc[-1]) if not dff_1y_data.empty else current_ff
        ff_1y_date  = dff_1y_data.index[-1].strftime("%b %Y") if not dff_1y_data.empty else ""
        ff_chg_bps  = (current_ff - ff_1y_ago) * 100

        ff_window = st.radio(
            "Window", ["5Y", "10Y", "20Y", "Max"],
            index=1, key="ff_window", horizontal=True,
        )
        ff_start    = _window_start(ff_window)
        ff_pctile_w = _window_pctile(dff_clean, current_ff, ff_start)
        ff_data     = dff_clean.loc[ff_start:]
        chg_sign = "+" if ff_chg_bps >= 0 else ""
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
        st.metric("Fed Funds Rate", f"{current_ff:.2f}%")
        st.caption(
            f"{chg_sign}{ff_chg_bps:.0f} bps from {ff_1y_date} · "
            f"{_ordinal(ff_pctile_w)} percentile of {ff_window} window"
        )

        st.caption(_ff_interpretation(current_ff, ff_chg_bps))
        st.divider()
    else:
        _panel_error("Effective Federal Funds Rate", _dff_err, "retry_ff")
        st.divider()

    # ── FOMC Meeting Calendar ─────────────────────────────────────────────────

    st.markdown("#### 2026 FOMC Meeting Calendar")
    _fomc_data = {
        "Date": [
            "Jan 27–28, 2026",
            "Mar 17–18, 2026",
            "Apr 28–29, 2026",
            "Jun 16–17, 2026",
            "Jul 28–29, 2026",
            "Sep 15–16, 2026",
            "Oct 27–28, 2026",
            "Dec 15–16, 2026",
        ],
        "Meeting Type": [
            "Two-day meeting",
            "Two-day meeting (SEP release)",
            "Two-day meeting",
            "Two-day meeting (SEP release)",
            "Two-day meeting",
            "Two-day meeting (SEP release)",
            "Two-day meeting",
            "Two-day meeting (SEP release)",
        ],
    }
    st.dataframe(
        pd.DataFrame(_fomc_data),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "FOMC meeting dates set 12 months in advance by the Federal Reserve. "
        "SEP releases (Summary of Economic Projections, including the dot plot of FOMC members' "
        "rate projections) occur four times per year at March, June, September, and December "
        "meetings. For real-time market-implied probabilities of rate changes at each meeting, "
        "see the CME FedWatch Tool, which derives probabilities from Fed Funds futures pricing. "
        "Source: Federal Reserve Board (federalreserve.gov)."
    )
    st.divider()

    # ── Rate Volatility (10Y Realized — VXTLT proxy) ─────────────────────────

    st.markdown("#### Rate Volatility (10Y Realized — DGS10 Rolling 21-Day)")
    if rate_vol_21 is not None and not rate_vol_21.empty:

        current_rv = float(rate_vol_21.iloc[-1])
        rv_as_of   = rate_vol_21.index[-1].strftime("%b %d, %Y")

        rv_window = st.radio(
            "Window", ["5Y", "10Y", "20Y", "Max"],
            index=1, key="rv_window", horizontal=True,
        )
        rv_start    = _window_start(rv_window)
        rv_pctile_w = _window_pctile(rate_vol_21, current_rv, rv_start)
        rv_data     = rate_vol_21.loc[rv_start:]

        fig_rv = go.Figure()
        _add_recession_shading(fig_rv, rec_periods or [], rv_start)
        fig_rv.add_trace(go.Scatter(
            x=rv_data.index, y=rv_data.values,
            mode="lines", name="Rate Vol (%)",
            line=dict(color=_C["primary"], width=1.5),
        ))
        _add_current_annotation(
            fig_rv, current_rv,
            f"Current {current_rv:.2f}% ({_ordinal(rv_pctile_w)} pct, {rv_window})",
        )
        _apply_style(fig_rv)
        fig_rv.update_yaxes(title_text="Annualized Vol (%)")
        _yr = _tight_yrange(rv_data, [current_rv])
        if _yr:
            _yr[0] = max(0.0, _yr[0])
            fig_rv.update_yaxes(range=_yr)
        st.plotly_chart(fig_rv, width='stretch')
        st.metric("Rate Volatility (10Y Realized)", f"{current_rv:.2f}%")
        st.caption(
            f"As of {rv_as_of} · {_ordinal(rv_pctile_w)} percentile of {rv_window} window  \n"
            "Rolling 21-day stdev of daily DGS10 changes, annualized (×√252). "
            "MOVE Index proxy — VXTLT (CBOE TLT vol) not available via the price cache."
        )

        if rv_pctile_w > 70:
            _rv_interp = (
                f"Rate volatility at {current_rv:.2f}% (annualized) is elevated — "
                f"{_ordinal(rv_pctile_w)} percentile of the {rv_window} window. "
                "Elevated rate vol compresses carry strategies, widens bid-ask spreads in credit, "
                "and creates an environment where active duration management adds more value "
                "than passive carry harvesting."
            )
        elif rv_pctile_w > 30:
            _rv_interp = (
                f"Rate volatility at {current_rv:.2f}% (annualized) is moderate — "
                f"{_ordinal(rv_pctile_w)} percentile of the {rv_window} window. "
                "Moderate rate vol is consistent with a stable rate environment where "
                "passive duration earns carry without significant mark-to-market risk."
            )
        else:
            _rv_interp = (
                f"Rate volatility at {current_rv:.2f}% (annualized) is low — "
                f"{_ordinal(rv_pctile_w)} percentile of the {rv_window} window. "
                "Low rate vol compresses manager dispersion in fixed income; "
                "passive duration harvesting is sufficient and active duration management "
                "adds limited edge in this environment."
            )
        st.caption(
            _rv_interp + " "
            "Relevant for evaluating active FI manager performance environments."
        )
        st.divider()
    else:
        if _dgs10_err:
            _panel_error("Rate Volatility (10Y Realized)", _dgs10_err, "retry_rv")
        else:
            st.info("Rate volatility requires DGS10 data (insufficient history for 21-day window).")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. CREDIT
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Credit")

    with st.expander("About credit spreads on this page", expanded=False):
        st.caption(
            "**Option-Adjusted Spread (OAS)** is the yield premium a corporate bond offers above "
            "a duration-matched Treasury, adjusted for embedded options like callability. OAS "
            "isolates the compensation investors demand for credit risk — wider OAS means more "
            "compensation per unit of duration."
        )
        st.caption(
            "The three indices displayed are nested by credit quality:\n"
            "- **IG (BAMLC0A0CM):** all US investment-grade corporates rated BBB and above\n"
            "- **HY (BAMLH0A0HYM2):** all US high-yield corporates rated BB+ and below\n"
            "- **CCC (BAMLH0A3HYC):** the lowest-rated subset of HY (CCC and lower ratings)"
        )
        st.caption(
            "HY OAS can be tight while CCC OAS is elevated because the HY index is dominated by "
            "BB and B issuers — roughly 85% of HY index weight. CCC names are only ~12–15% of HY "
            "weight, so distress concentrated in the tail can leave headline HY spreads compressed "
            "while CCC widens. The dispersion between HY and CCC is itself a leading indicator: "
            "rising CCC spreads typically precede broader HY weakening by 6–12 months."
        )

    # ── IG Credit Spreads ────────────────────────────────────────────────────

    st.markdown("#### IG Credit Spreads (OAS) — BAMLC0A0CM")
    if ig_oas is not None:

        ig_clean      = ig_oas.dropna()
        ig_bps        = ig_clean * 100
        current_ig    = float(ig_bps.iloc[-1])
        ig_median_bps = float(ig_bps.median())
        ig_since      = ig_bps.index[0].strftime("%b %Y")
        ig_data_start = ig_bps.index[0]

        ig_start    = "1800-01-01"
        ig_pctile_w = _window_pctile(ig_bps, current_ig, ig_start)
        ig_data     = ig_bps

        fig_ig = go.Figure()
        _add_recession_shading(fig_ig, rec_periods or [], ig_data_start.isoformat())
        fig_ig.add_trace(go.Scatter(
            x=ig_data.index, y=ig_data.values,
            mode="lines", name="IG OAS (bps)",
            line=dict(color=_C["primary"], width=2),
        ))
        fig_ig.add_hline(
            y=ig_median_bps,
            line_dash="dash", line_color=_C["ref"], line_width=1,
            annotation_text=f"Median {ig_median_bps:.0f} bps",
            annotation_position="right", annotation_font_size=10,
        )
        _add_current_annotation(
            fig_ig, current_ig,
            f"Current {current_ig:.0f} bps ({_ordinal(ig_pctile_w)} pct, {ig_since}+)",
        )
        _apply_style(fig_ig)
        fig_ig.update_xaxes(range=[ig_data_start.isoformat(), TODAY])
        fig_ig.update_yaxes(title_text="OAS (bps)")
        _yr = _tight_yrange(ig_data, [current_ig, ig_median_bps])
        if _yr:
            _yr[0] = max(0.0, _yr[0])
            fig_ig.update_yaxes(range=_yr)
        st.plotly_chart(fig_ig, width='stretch')
        st.metric("IG OAS", f"{current_ig:.0f} bps")
        st.caption(
            f"{_ordinal(ig_pctile_w)} percentile of available history (data from {ig_since})"
        )

        _hy_ig_extra = ""
        if _shared_hy_bps is not None and current_ig > 0:
            _ratio = _shared_hy_bps / current_ig
            if _ratio > 4.5:
                _ratio_framing = "elevated, consistent with tail-risk concerns in lower-quality debt"
            elif _ratio > 3.0:
                _ratio_framing = "moderate, within historical mid-cycle norms"
            else:
                _ratio_framing = "compressed, indicating tight spreads across the corporate credit stack"
            _hy_ig_extra = f" HY/IG ratio: {_ratio:.1f}× — {_ratio_framing}."

        if ig_pctile_w < 25:
            _ig_tier = "historically tight — strong demand for IG credit with low perceived default risk"
        elif ig_pctile_w < 60:
            _ig_tier = "near median — consistent with a mid-cycle credit environment"
        else:
            _ig_tier = "elevated — reflecting credit stress or risk-off positioning even in higher-quality issuers"

        st.caption(
            f"IG OAS at {current_ig:.0f} bps reflects investment-grade corporate spread over Treasuries — "
            f"{_ig_tier}.{_hy_ig_extra} "
            "FRED BAMLC0A0CM: ICE BofA US Corporate Index OAS."
        )
        st.divider()
    else:
        _panel_error("IG Credit Spreads (OAS)", _ig_oas_err, "retry_ig")
        st.divider()

    # ── HY Credit Spreads ────────────────────────────────────────────────────

    st.markdown("#### HY Credit Spreads (OAS)")
    if hy_oas is not None:

        hy_clean       = hy_oas.dropna()
        hy_bps         = hy_clean * 100
        current_hy     = float(hy_bps.iloc[-1])
        hy_median_bps  = float(hy_bps.median())
        hy_since       = hy_bps.index[0].strftime("%b %Y")
        hy_data_start  = hy_bps.index[0]

        hy_start    = "1800-01-01"
        hy_pctile_w = _window_pctile(hy_bps, current_hy, hy_start)
        hy_data     = hy_bps
        fig_hy = go.Figure()
        _add_recession_shading(fig_hy, rec_periods or [], hy_data_start.isoformat())
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
        fig_hy.update_xaxes(range=[hy_data_start.isoformat(), TODAY])
        fig_hy.update_yaxes(title_text="OAS (bps)")
        _yr = _tight_yrange(hy_data, [current_hy, hy_median_bps])
        if _yr:
            _yr[0] = max(0.0, _yr[0])
            fig_hy.update_yaxes(range=_yr)
        st.plotly_chart(fig_hy, width='stretch')
        st.metric("HY OAS", f"{current_hy:.0f} bps")
        st.caption(
            f"{_ordinal(hy_pctile_w)} percentile of available history "
            f"(data from {hy_since})"
        )

        st.caption(interpret_hy_spread(current_hy))
        st.caption(
            f"HY spreads at the {_ordinal(hy_pctile_w)} percentile of available history "
            f"({hy_since}+). "
            f"*FRED restricts this ICE BofA series to {hy_since}+; full pre-2023 history "
            "is unavailable from FRED's API.*"
        )
        st.divider()
    else:
        _panel_error("HY Credit Spreads (OAS)", _hy_oas_err, "retry_hy")
        st.divider()

    # ── CCC Credit Spreads ────────────────────────────────────────────────────

    st.markdown("#### CCC Credit Spreads (OAS) — BAMLH0A3HYC")
    if ccc_oas is not None:

        ccc_clean      = ccc_oas.dropna()
        ccc_bps        = ccc_clean * 100
        current_ccc    = float(ccc_bps.iloc[-1])
        ccc_median_bps = float(ccc_bps.median())
        ccc_since      = ccc_bps.index[0].strftime("%b %Y")
        ccc_data_start = ccc_bps.index[0]

        ccc_start    = "1800-01-01"
        ccc_pctile_w = _window_pctile(ccc_bps, current_ccc, ccc_start)
        ccc_data     = ccc_bps

        fig_ccc = go.Figure()
        _add_recession_shading(fig_ccc, rec_periods or [], ccc_data_start.isoformat())
        fig_ccc.add_trace(go.Scatter(
            x=ccc_data.index, y=ccc_data.values,
            mode="lines", name="CCC OAS (bps)",
            line=dict(color=_C["primary"], width=2),
        ))
        fig_ccc.add_hline(
            y=ccc_median_bps,
            line_dash="dash", line_color=_C["ref"], line_width=1,
            annotation_text=f"Median {ccc_median_bps:.0f} bps",
            annotation_position="right", annotation_font_size=10,
        )
        _add_current_annotation(
            fig_ccc, current_ccc,
            f"Current {current_ccc:.0f} bps ({_ordinal(ccc_pctile_w)} pct, {ccc_since}+)",
        )
        _apply_style(fig_ccc)
        fig_ccc.update_xaxes(range=[ccc_data_start.isoformat(), TODAY])
        fig_ccc.update_yaxes(title_text="OAS (bps)")
        _yr = _tight_yrange(ccc_data, [current_ccc, ccc_median_bps])
        if _yr:
            _yr[0] = max(0.0, _yr[0])
            fig_ccc.update_yaxes(range=_yr)
        st.plotly_chart(fig_ccc, width='stretch')
        st.metric("CCC OAS", f"{current_ccc:.0f} bps")
        st.caption(
            f"{_ordinal(ccc_pctile_w)} percentile of available history (data from {ccc_since})"
        )

        if ccc_pctile_w < 25:
            _ccc_interp = (
                f"CCC OAS at {current_ccc:.0f} bps is historically tight — "
                "broad risk-on appetite is reaching into distressed names. "
                "Tight CCC alongside tight BB indicates few idiosyncratic stress situations."
            )
        elif ccc_pctile_w < 60:
            _ccc_interp = (
                f"CCC OAS at {current_ccc:.0f} bps is near the median for available history — "
                "a moderate credit environment with a mix of distressed and recovering issuers."
            )
        else:
            _ccc_interp = (
                f"CCC OAS at {current_ccc:.0f} bps is elevated. "
                + (
                    f"The CCC/HY spread differential is {current_ccc - _shared_hy_bps:.0f} bps — "
                    if _shared_hy_bps is not None else ""
                )
                + "CCC widening ahead of broad HY is a leading indicator of credit stress "
                "concentrating in tail names; see the full spread stack in the Credit section above."
            )
        st.caption(
            _ccc_interp
            + " FRED BAMLH0A3HYC: ICE BofA US High Yield CCC and Lower OAS."
        )
        st.divider()
    else:
        _panel_error("CCC Credit Spreads (OAS)", _ccc_oas_err, "retry_ccc")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # 6. VALUATION
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Valuation")

    # ── CAPE ─────────────────────────────────────────────────────────────────

    st.markdown("#### CAPE / Shiller P/E")

    try:
        with st.spinner("Loading CAPE data…"):
            cape_series = _load_cape_series()
        cape_val     = float(cape_series.dropna().iloc[-1])
        cape_pctile  = macro.percentile(cape_series, cape_val)
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
        st.metric("Shiller CAPE", f"{cape_val:.1f}×")
        st.caption(
            f"{_ordinal(cape_pctile_w)} percentile of {cape_window} window "
            f"· full history: {_ordinal(cape_pctile)} pct since 1881 "
            f"· data as of {cape_as_of}  \n"
            f"Implied 10Y real return at this CAPE is approximately {cape_implied:+.2%} "
            "based on the long-run historical relationship between starting CAPE and forward "
            "10-year returns. The relationship is empirically robust over the full 145-year "
            "Shiller record but not a forecast for any single 10-year window."
        )

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

    # ── Excess CAPE Yield ─────────────────────────────────────────────────────

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

        _ecy_w = _ecy_hist[_ecy_hist.index >= pd.Timestamp(w_start_ecy)]
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
        st.metric("ECY", f"{current_ecy:.2f}%")
        st.caption(
            f"{_ordinal(ecy_pctile_w)} percentile of {ecy_window} window "
            f"(full history since {ecy_since}: {_ordinal(ecy_pctile)} pct)  \n"
            f"CAPE yield {100/cape_val:.2f}% vs real rate {_real_rate:.2f}% "
            f"({current_dgs10:.2f}% − {current_t10yie:.2f}%)"
        )

        st.caption(interpret_excess_cape(current_ecy, ecy_pctile / 100))
        st.divider()

    elif cape_ok:
        _panel_error("Excess CAPE Yield (ECY)", _dgs10_err or _t10yie_err, "retry_ecy")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # 7. CROSS-ASSET PERFORMANCE
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Cross-Asset Performance")
    st.caption(
        "Relative performance trends across equity geographies and currency — "
        "context for the SAA's 19% International Developed and 8% Emerging Markets overweights."
    )

    # ── US vs. International Equity ───────────────────────────────────────────

    st.markdown("#### US vs. International Equity")

    try:
        with st.spinner("Loading SPY / EFA price data…"):
            spy_raw = _load_price_series("SPY", "2004-01-01")
            efa_raw = _load_price_series("EFA", "2004-01-01")

        aligned = pd.concat([spy_raw, efa_raw], axis=1).dropna()
        aligned.columns = ["SPY", "EFA"]
        aligned.index   = pd.to_datetime(aligned.index)

        spy_ret12 = aligned["SPY"].pct_change(252) * 100
        efa_ret12 = aligned["EFA"].pct_change(252) * 100
        spread12  = (spy_ret12 - efa_ret12).dropna()

        current_spread_pp = float(spread12.iloc[-1])

        rolling_mean_5y = spread12.rolling(252 * 5).mean()
        current_mean_5y = (
            float(rolling_mean_5y.dropna().iloc[-1])
            if not rolling_mean_5y.dropna().empty else 0.0
        )

        us_window = st.radio(
            "Window", ["5Y", "10Y", "20Y", "Max"],
            index=1, key="us_window", horizontal=True,
        )
        us_start    = _window_start(us_window)
        spread_w    = spread12[spread12.index >= pd.Timestamp(us_start)]
        mean_w      = float(spread_w.mean()) if not spread_w.empty else 0.0
        spread_sign = "+" if current_spread_pp >= 0 else ""

        fig_us = go.Figure()
        fig_us.add_trace(go.Scatter(
            x=spread_w.index, y=spread_w.values,
            mode="lines", name="12M return spread (%)",
            line=dict(color=_C["primary"], width=2),
        ))
        fig_us.add_hline(
            y=0, line_dash="dash", line_color=_C["ref"], line_width=1,
            annotation_text="0 = parity",
            annotation_position="top left", annotation_font_size=9,
            annotation_font_color="#888",
        )
        fig_us.add_hline(
            y=mean_w,
            line_dash="dot", line_color=_C["ref"], line_width=1,
            annotation_text=f"{us_window} mean {mean_w:+.1f}%",
            annotation_position="right", annotation_font_size=10,
        )
        _add_current_annotation(
            fig_us, current_spread_pp,
            f"Current {spread_sign}{current_spread_pp:.1f}%",
        )
        _apply_style(fig_us)
        fig_us.update_yaxes(title_text="12M return spread (%, US minus Intl)")
        _yr = _tight_yrange(spread_w, [current_spread_pp, 0.0, mean_w])
        if _yr:
            fig_us.update_yaxes(range=_yr)
        fig_us.add_annotation(
            xref="paper", yref="paper",
            x=0.01, y=0.98,
            text="Positive = US outperforming international",
            showarrow=False,
            font=dict(size=9, color="#888"),
            xanchor="left", yanchor="top",
        )
        st.plotly_chart(fig_us, width='stretch')
        st.metric(
            "Trailing 12M return spread (US − Intl)",
            f"{spread_sign}{current_spread_pp:.1f}%",
        )
        st.caption(
            f"Rolling 12-month total return spread: SPY minus EFA. "
            f"Positive = US outperforming. {us_window} window mean: {mean_w:+.1f}%."
        )

        st.caption(interpret_us_vs_intl_spread(current_spread_pp, current_mean_5y))

    except Exception as exc:
        st.error(f"US vs. International data unavailable: {exc}")

    st.divider()

    # ── Broad Trade-Weighted USD Index ────────────────────────────────────────

    st.markdown("#### Broad Trade-Weighted USD Index (DTWEXBGS)")
    if dtwexbgs is not None:

        dtwex_clean    = dtwexbgs.dropna()
        current_dtwex  = float(dtwex_clean.iloc[-1])
        dtwex_as_of    = dtwex_clean.index[-1].strftime("%b %d, %Y")
        dtwex_since    = dtwex_clean.index[0].strftime("%b %Y")

        dtwex_1y_data  = dtwex_clean[dtwex_clean.index <= ONE_YR_AGO]
        dtwex_1y_ago   = float(dtwex_1y_data.iloc[-1]) if not dtwex_1y_data.empty else current_dtwex
        dtwex_1y_pct   = (current_dtwex / dtwex_1y_ago - 1) * 100 if dtwex_1y_ago != 0 else 0.0

        dtwex_window = st.radio(
            "Window", ["5Y", "10Y", "Max"],
            index=1, key="dtwex_window", horizontal=True,
        )
        dtwex_start    = _window_start(dtwex_window)
        dtwex_pctile_w = _window_pctile(dtwex_clean, current_dtwex, dtwex_start)
        dtwex_data     = dtwex_clean.loc[dtwex_start:]

        fig_dtwex = go.Figure()
        _add_recession_shading(fig_dtwex, rec_periods or [], dtwex_start)
        fig_dtwex.add_trace(go.Scatter(
            x=dtwex_data.index, y=dtwex_data.values,
            mode="lines", name="Broad USD Index",
            line=dict(color=_C["primary"], width=2),
        ))
        _add_current_annotation(
            fig_dtwex, current_dtwex,
            f"Current {current_dtwex:.1f} ({_ordinal(dtwex_pctile_w)} pct, {dtwex_window})",
        )
        _apply_style(fig_dtwex)
        fig_dtwex.update_yaxes(title_text="Index Level")
        _yr = _tight_yrange(dtwex_data, [current_dtwex])
        if _yr:
            fig_dtwex.update_yaxes(range=_yr)
        st.plotly_chart(fig_dtwex, width='stretch')
        st.metric("Broad USD Index (DTWEXBGS)", f"{current_dtwex:.1f}")
        chg_sign = "+" if dtwex_1y_pct >= 0 else ""
        st.caption(
            f"{chg_sign}{dtwex_1y_pct:.1f}% over 12 months · "
            f"{_ordinal(dtwex_pctile_w)} percentile of {dtwex_window} window "
            f"(data since {dtwex_since})"
        )

        if dtwex_pctile_w > 70:
            _dtwex_strength = "historically strong"
            _dtwex_impl = (
                "A strong dollar is a headwind for unhedged international and EM equity holdings "
                "through translation losses — meaningful for the 27% non-US equity allocation."
            )
        elif dtwex_pctile_w > 30:
            _dtwex_strength = "near its historical median"
            _dtwex_impl = (
                "A dollar near its historical median exerts a neutral translation effect on "
                "international equity sleeves — no significant currency tailwind or headwind."
            )
        else:
            _dtwex_strength = "historically weak"
            _dtwex_impl = (
                "A weak dollar is a tailwind for unhedged international and EM equity holdings "
                "through positive translation — supports the 19% International Developed and 8% EM sleeves."
            )

        st.caption(
            "The Broad Trade-Weighted USD Index (DTWEXBGS) measures the value of the US dollar "
            "against a basket of currencies of US trading partners, weighted by trade flow (goods "
            "plus services). The basket includes major currencies — euro, yen, pound, Canadian "
            "dollar, Swiss franc, Australian dollar — plus emerging-market currencies including "
            "Chinese renminbi, Mexican peso, Korean won, and others. The 'broad' version "
            "distinguishes it from the narrower DXY, which only covers six major currencies."
        )
        st.caption(
            f"Current value of {current_dtwex:.1f} is at the {_ordinal(dtwex_pctile_w)} percentile "
            f"of the {dtwex_window} window ({_dtwex_strength}). "
            + _dtwex_impl + " "
            "Dollar direction is a meaningful translation factor on the 27% non-US equity allocation "
            "(VEA + IEMG). FRED DTWEXBGS: Nominal Broad U.S. Dollar Index (goods and services)."
        )
        st.divider()
    else:
        _panel_error("Broad USD Index (DTWEXBGS)", _dtwex_err, "retry_usd")
        st.divider()

    # ═══════════════════════════════════════════════════════════════════════════
    # 8. FACTOR REGIME
    # ═══════════════════════════════════════════════════════════════════════════

    st.markdown("### Factor Regime")
    st.caption(
        "Trailing 12-month relative performance of the size and style factors. "
        "The SAA tilts toward small-cap and value; these charts show whether those "
        "tilts are currently being rewarded. Each chart overlays the academic "
        "Fama-French factor premium (long-short, the textbook factor) with a "
        "long-only ETF proxy (what a tradeable implementation captured). Above "
        "zero = the factor is outperforming over the trailing year; below zero = "
        "it is lagging. Crossings mark regime shifts."
    )

    def _factor_regime_chart(
        frame: pd.DataFrame, ff_col: str, etf_col: str,
        ff_label: str, etf_label: str,
    ) -> "go.Figure | None":
        if frame is None or frame.empty:
            return None
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=frame.index, y=frame[ff_col],
            mode="lines", name=ff_label,
            line=dict(color=_C["primary"], width=2),
            connectgaps=True,
        ))
        fig.add_trace(go.Scatter(
            x=frame.index, y=frame[etf_col],
            mode="lines", name=etf_label,
            line=dict(color=_C["current"], width=2),
            connectgaps=True,
        ))
        fig.add_hline(
            y=0, line_dash="dash", line_color=_C["ref"], line_width=1,
            annotation_text="0 = factor flat over trailing year",
            annotation_position="top left", annotation_font_size=9,
            annotation_font_color="#888",
        )
        _apply_style(fig)
        fig.update_layout(
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="left", x=0, font_size=11),
        )
        fig.update_yaxes(title_text="Trailing 12M relative return (%)")
        _both = pd.concat([frame[ff_col], frame[etf_col]])
        _yr = _tight_yrange(_both, [0.0])
        if _yr:
            fig.update_yaxes(range=_yr)
        return fig

    def _last_valid(frame: pd.DataFrame, col: str):
        s = frame[col].dropna()
        return (float(s.iloc[-1]), s.index[-1]) if not s.empty else (None, None)

    # ── Chart 1 — Size: Small-cap vs Large-cap ────────────────────────────────
    st.markdown("#### Size: Small-cap vs Large-cap (trailing 12M)")
    try:
        with st.spinner("Loading size-factor data (Fama-French SMB, IWM/IWB)…"):
            size_frame = _load_size_factor_frame()

        fig_size = _factor_regime_chart(
            size_frame, "ff_smb_12m", "etf_iwm_iwb_12m",
            "Fama-French SMB", "IWM − IWB (proxy)",
        )
        if fig_size is None:
            st.info("Size-factor data unavailable — no overlapping history.")
        else:
            st.plotly_chart(fig_size, width="stretch")

            _smb_val, _smb_dt = _last_valid(size_frame, "ff_smb_12m")
            _szp_val, _szp_dt = _last_valid(size_frame, "etf_iwm_iwb_12m")
            _mc1, _mc2 = st.columns(2)
            with _mc1:
                st.metric(
                    "Fama-French SMB (12M)",
                    f"{_smb_val:+.1f}%" if _smb_val is not None else "—",
                )
            with _mc2:
                st.metric(
                    "IWM − IWB proxy (12M)",
                    f"{_szp_val:+.1f}%" if _szp_val is not None else "—",
                )
            _smb_as_of = _smb_dt.strftime("%b %d, %Y") if _smb_dt is not None else "n/a"
            st.caption(
                f"Fama-French SMB cumulative trailing-12M premium as of {_smb_as_of}; "
                "IWM (Russell 2000) minus IWB (Russell 1000) trailing-12M total return "
                "(dividend-adjusted). A positive reading means small-caps led large-caps "
                "over the past year."
            )
    except Exception as exc:
        st.error(f"Size-factor data unavailable: {exc}")

    st.divider()

    # ── Chart 2 — Style: Value vs Growth ──────────────────────────────────────
    st.markdown("#### Style: Value vs Growth (trailing 12M)")
    try:
        with st.spinner("Loading style-factor data (Fama-French HML, IWD/IWF)…"):
            style_frame = _load_style_factor_frame()

        fig_style = _factor_regime_chart(
            style_frame, "ff_hml_12m", "etf_iwd_iwf_12m",
            "Fama-French HML", "IWD − IWF (proxy)",
        )
        if fig_style is None:
            st.info("Style-factor data unavailable — no overlapping history.")
        else:
            st.plotly_chart(fig_style, width="stretch")

            _hml_val, _hml_dt = _last_valid(style_frame, "ff_hml_12m")
            _syp_val, _syp_dt = _last_valid(style_frame, "etf_iwd_iwf_12m")
            _mc1, _mc2 = st.columns(2)
            with _mc1:
                st.metric(
                    "Fama-French HML (12M)",
                    f"{_hml_val:+.1f}%" if _hml_val is not None else "—",
                )
            with _mc2:
                st.metric(
                    "IWD − IWF proxy (12M)",
                    f"{_syp_val:+.1f}%" if _syp_val is not None else "—",
                )
            _hml_as_of = _hml_dt.strftime("%b %d, %Y") if _hml_dt is not None else "n/a"
            st.caption(
                f"Fama-French HML cumulative trailing-12M premium as of {_hml_as_of}; "
                "IWD (Russell 1000 Value) minus IWF (Russell 1000 Growth) trailing-12M "
                "total return (dividend-adjusted). A positive reading means value led "
                "growth over the past year."
            )
    except Exception as exc:
        st.error(f"Style-factor data unavailable: {exc}")

    with st.expander("Factor Regime methodology", expanded=False):
        st.caption(
            "**Fama-French legs (SMB, HML).** SMB (small-minus-big) and HML "
            "(high-minus-low book-to-market, i.e. value-minus-growth) are long-short "
            "academic factor returns from the Ken French Data Library (Dartmouth). "
            "Because each is already a long-short spread, the trailing-12-month "
            "*cumulative* return — compounding the daily factor returns over the most "
            "recent ~252 trading days — is the realized factor premium an academic "
            "long-short investor would have earned over the past year."
        )
        st.caption(
            "**ETF proxy legs.** IWM/IWB (size) and IWD/IWF (style) are long-only "
            "Russell index funds. The relative return — small minus large, value minus "
            "growth — is each leg's trailing-12-month total return (dividend-adjusted "
            "close) differenced. This approximates the factor as a tradeable investor "
            "actually experiences it, net of the long-only constraint."
        )
        st.caption(
            "**Why the two lines can diverge.** The long-short academic factor and the "
            "long-only ETF spread capture the factor differently: the academic factor "
            "can short the unattractive leg, weights by book-to-market or size across "
            "the full cross-section, and ignores fees; the ETF spread is constrained to "
            "long-only Russell construction with fund-level weighting and expense ratios. "
            "Divergence between the lines reflects these implementation effects rather "
            "than a data error."
        )
        st.caption(
            "**Relation to the SAA.** The SAA's actual small-cap sleeve (AVUV) is "
            "small-*value*, so it straddles both factors at once. These charts isolate "
            "each single factor — size and style separately — for clarity; neither line "
            "alone maps one-to-one onto AVUV. Source: Ken French Data Library (SMB, HML "
            "daily); ETF dividend-adjusted close, locally cached (IWM, IWB, IWD, IWF)."
        )

    st.divider()

    # ── Sources ───────────────────────────────────────────────────────────────

    with st.expander("Data sources & freshness"):
        def _fred_src(label: str, series: "pd.Series | None") -> str:
            if series is not None:
                last = series.dropna().index[-1].strftime("%b %d, %Y")
                return f"**{label}**: last observation **{last}**"
            return f"**{label}**: unavailable"

        _daily = [
            _fred_src("FRED DGS3MO (3-Month Treasury Rate)",                   dgs3mo),
            _fred_src("FRED DGS1 (1-Year Treasury Rate)",                      dgs1),
            _fred_src("FRED DGS2 (2-Year Treasury Rate)",                      dgs2),
            _fred_src("FRED DGS5 (5-Year Treasury Rate)",                      dgs5),
            _fred_src("FRED DGS7 (7-Year Treasury Rate)",                      dgs7),
            _fred_src("FRED DGS10 (10-Year Treasury Rate)",                    dgs10),
            _fred_src("FRED DGS20 (20-Year Treasury Rate)",                    dgs20),
            _fred_src("FRED DGS30 (30-Year Treasury Rate)",                    dgs30),
            _fred_src("FRED T10YIE (10-Year Breakeven Inflation)",             t10yie)   + " · starts Jan 2003",
            _fred_src("FRED DFII10 (10-Year TIPS Yield — Real Rate)",          dfii10)   + " · starts Jan 2003",
            _fred_src("FRED T10Y2Y (10Y−2Y Treasury spread)",                  t10y2y),
            _fred_src("FRED DFF (Fed Funds Rate)",                             dff),
            _fred_src("FRED BAMLC0A0CM (ICE BofA IG OAS)",                    ig_oas),
            _fred_src("FRED BAMLH0A0HYM2 (ICE BofA HY OAS)",                  hy_oas)   + " · starts May 2023 (FRED restriction)",
            _fred_src("FRED BAMLH0A3HYC (ICE BofA CCC OAS)",                  ccc_oas)  + " · starts May 2023 (FRED restriction)",
            _fred_src("FRED DTWEXBGS (Broad Trade-Weighted USD Index)",        dtwexbgs),
            "**ETF price series** (SPY, EFA): daily adjusted-close, locally cached in SQLite",
            "**Rate Volatility**: 21-day rolling realized vol of DGS10 daily changes, annualized (×√252) — "
            "MOVE Index proxy; VXTLT unavailable via public price feeds",
        ]
        _monthly = [
            _fred_src("FRED UNRATE (Unemployment Rate)",                        unrate),
            _fred_src("FRED CPILFESL (Core CPI, level)",                       core_cpi) + " · YoY change computed",
            _fred_src("FRED CFNAIDIFF (Chicago Fed Activity Diffusion Index)", cfnai_diff) + " · ISM PMI proxy",
            "**FRED USREC** (NBER recession indicator): lags recession end by ~12 months"
            + ("" if rec_periods is not None else " — unavailable"),
            (
                f"**Shiller CAPE** (multpl.com / Robert Shiller): "
                f"last observation **{cape_series.dropna().index[-1].strftime('%b %Y')}**"
                if cape_ok else "**Shiller CAPE**: unavailable"
            ),
        ]
        _quarterly = [
            _fred_src("FRED A191RL1Q225SBEA (Real GDP QoQ ann.)",              gdp_gr)   + " · 1-2 quarter lag",
        ]

        st.markdown("**Daily**")
        st.caption("  \n".join(_daily))
        st.markdown("**Monthly**")
        st.caption("  \n".join(_monthly))
        st.markdown("**Quarterly**")
        st.caption("  \n".join(_quarterly))
        st.caption(
            "**ICE Data Indices licensing:** The ICE BofA credit spread series "
            "(BAMLH0A0HYM2, BAMLC0A0CM, BAMLH0A3HYC) are licensed by ICE Data Indices, LLC "
            "to the Federal Reserve Bank of St. Louis (FRED) for non-commercial personal, "
            "educational, or scholarly use. Display on this site is consistent with that use case."
        )
    render_footer()
