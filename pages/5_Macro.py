"""Macro Dashboard — five-panel regime indicator view."""
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import macro, shiller
from src.prices import get_prices

st.set_page_config(page_title="Macro Dashboard", layout="wide")

TODAY      = date.today().isoformat()
ONE_YR_AGO = (date.fromisoformat(TODAY) - timedelta(days=365)).isoformat()

_C = {
    "primary":   "#2E4057",
    "ref":       "#9E9E9E",
    "recession": "#9E9E9E",
}

_CHART_H      = 320
_CHART_H_CAPE = 360


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
    fig.update_xaxes(gridcolor="#EBEBEB", showgrid=True, zeroline=False, tickfont_size=11)
    fig.update_yaxes(gridcolor="#EBEBEB", showgrid=True, zeroline=False, tickfont_size=11)
    return fig


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

    hdr_l, hdr_r = st.columns([3, 1])
    with hdr_l:
        st.caption(
            f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
            "Data: FRED & Shiller."
        )
    with hdr_r:
        if st.button("Force refresh", type="secondary"):
            macro.clear_macro_cache()
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # ── Panel 1: CAPE ────────────────────────────────────────────────────────

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
        col_l, col_r = st.columns([1, 2])

        cape_as_of = cape_series.dropna().index[-1].strftime("%b %Y")
        with col_l:
            st.metric("Shiller CAPE", f"{cape_val:.1f}×")
            st.caption(f"{_ordinal(cape_pctile)} percentile since 1881 · data as of {cape_as_of}")
            st.markdown(
                f"**Implied forward 10Y real return:** ~{cape_implied:.1%}  \n"
                "*Historical relationship, not a forecast.*"
            )
            cape_window = st.radio(
                "Window", ["20Y", "50Y", "Max"],
                index=2, key="cape_window", horizontal=True,
            )

        with col_r:
            w_start  = _window_start(cape_window)
            filtered = cape_series[cape_series.index >= w_start].dropna()

            fig_cape = go.Figure()
            fig_cape.add_trace(go.Scatter(
                x=filtered.index, y=filtered.values,
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
            _apply_style(fig_cape, height=_CHART_H_CAPE)
            fig_cape.update_yaxes(title_text="CAPE (×)")
            st.plotly_chart(fig_cape, width='stretch')

        pctile_label = (
            "extremely elevated" if cape_pctile > 90 else
            "very elevated"      if cape_pctile > 80 else
            "elevated"           if cape_pctile > 65 else
            "near the median"    if cape_pctile > 40 else
            "below median"
        )
        st.caption(
            f"CAPE in the {_ordinal(cape_pctile)} percentile historically — {pctile_label}. "
            "Periods of similarly elevated valuation (1929, 1999, 2021) preceded materially "
            "below-average forward returns. Most directly relevant to the International "
            "Developed and US Large Value sleeves, where the discount-to-US-CAPE thesis "
            "depends on US valuations remaining above historical norms."
        )

    st.divider()

    # ── Load FRED data (shared across panels 2-4) ────────────────────────────

    try:
        with st.spinner("Loading FRED data…"):
            rec_periods = _load_recession_periods()
            t10y2y      = _load_fred("T10Y2Y",       "1976-06-01")
            dff         = _load_fred("DFF",           "1954-07-01")
            hy_oas      = _load_fred("BAMLH0A0HYM2", "1996-12-31")
        fred_ok = True
    except Exception as exc:
        fred_ok = False
        st.error(f"FRED data unavailable: {exc}")

    # ── Panel 2: Yield Curve ─────────────────────────────────────────────────

    if fred_ok:
        st.markdown("#### 2/10 Yield Curve Spread")

        t10y2y_clean       = t10y2y.dropna()
        # FRED T10Y2Y is in percent; multiply by 100 for basis points
        current_spread_bps = float(t10y2y_clean.iloc[-1]) * 100
        curve_state        = _yield_curve_state(t10y2y_clean)

        col_m, col_w = st.columns([3, 1])
        with col_m:
            sign = "+" if current_spread_bps >= 0 else ""
            st.metric("10Y − 2Y Spread", f"{sign}{current_spread_bps:.0f} bps")
            st.caption(curve_state)
        with col_w:
            yc_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="yc_window",
            )

        yc_start = _window_start(yc_window)
        yc_data  = (t10y2y_clean * 100).loc[yc_start:]

        fig_yc = go.Figure()
        _add_recession_shading(fig_yc, rec_periods, yc_start)
        fig_yc.add_trace(go.Scatter(
            x=yc_data.index, y=yc_data.values,
            mode="lines", name="10Y−2Y (bps)",
            line=dict(color=_C["primary"], width=2),
        ))
        fig_yc.add_hline(y=0, line_dash="dash", line_color=_C["ref"], line_width=1)
        _apply_style(fig_yc)
        fig_yc.update_yaxes(title_text="Spread (bps)")
        st.plotly_chart(fig_yc, width='stretch')

        st.caption(
            "Yield curve inversions (spread < 0) have preceded each of the last seven "
            "recessions with a 12–18 month lead time. The 2022–2023 inversion "
            "preceded the 2023 banking stress episode; normalization signals end-of-cycle "
            "dynamics. Gray shading marks NBER-dated recessions."
        )

        st.divider()

    # ── Panel 3: Fed Funds ───────────────────────────────────────────────────

    if fred_ok:
        st.markdown("#### Effective Federal Funds Rate")

        dff_clean  = dff.dropna()
        current_ff = float(dff_clean.iloc[-1])

        dff_1y_data = dff_clean[dff_clean.index <= ONE_YR_AGO]
        ff_1y_ago   = float(dff_1y_data.iloc[-1]) if not dff_1y_data.empty else current_ff
        ff_1y_date  = dff_1y_data.index[-1].strftime("%b %Y") if not dff_1y_data.empty else ""
        ff_chg_bps  = (current_ff - ff_1y_ago) * 100

        col_m, col_w = st.columns([3, 1])
        with col_m:
            chg_sign = "+" if ff_chg_bps >= 0 else ""
            st.metric("Fed Funds Rate", f"{current_ff:.2f}%")
            st.caption(f"{chg_sign}{ff_chg_bps:.0f} bps from {ff_1y_date}")
        with col_w:
            ff_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="ff_window",
            )

        ff_start = _window_start(ff_window)
        ff_data  = dff_clean.loc[ff_start:]

        fig_ff = go.Figure()
        _add_recession_shading(fig_ff, rec_periods, ff_start)
        fig_ff.add_trace(go.Scatter(
            x=ff_data.index, y=ff_data.values,
            mode="lines", name="Fed Funds (%)",
            line=dict(color=_C["primary"], width=2),
        ))
        _apply_style(fig_ff)
        fig_ff.update_yaxes(title_text="Rate (%)")
        st.plotly_chart(fig_ff, width='stretch')

        st.caption(_ff_interpretation(current_ff, ff_chg_bps))

        st.divider()

    # ── Panel 4: HY Credit Spreads ───────────────────────────────────────────

    if fred_ok:
        st.markdown("#### HY Credit Spreads (OAS)")

        # FRED BAMLH0A0HYM2 is in percent; multiply by 100 for basis points
        hy_clean      = hy_oas.dropna()
        hy_bps        = hy_clean * 100
        current_hy    = float(hy_bps.iloc[-1])
        hy_median_bps = float(hy_bps.median())
        hy_pctile     = macro.percentile(hy_bps, current_hy)
        hy_since      = hy_bps.index[0].strftime("%b %Y")

        col_m, col_w = st.columns([3, 1])
        with col_m:
            st.metric("HY OAS", f"{current_hy:.0f} bps")
            st.caption(f"{_ordinal(hy_pctile)} percentile since {hy_since}")
        with col_w:
            hy_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="hy_window",
            )

        hy_start = _window_start(hy_window)
        hy_data  = hy_bps.loc[hy_start:]

        fig_hy = go.Figure()
        _add_recession_shading(fig_hy, rec_periods, hy_start)
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
        _apply_style(fig_hy)
        fig_hy.update_yaxes(title_text="OAS (bps)")
        st.plotly_chart(fig_hy, width='stretch')

        hy_framing = (
            "suggests late-cycle complacency — limited cushion for additional compression"
            if hy_pctile < 40 else
            "is near the median for the available window, consistent with a neutral credit environment"
            if hy_pctile < 60 else
            "reflects elevated stress or risk aversion, pricing in meaningful default risk"
        )
        st.caption(
            f"HY spreads at the {_ordinal(hy_pctile)} percentile {hy_framing}. "
            "Low spreads historically correspond to late-cycle dynamics where credit risk "
            "is under-priced. Relevant context for sizing within the Income sleeve. "
            "Gray shading marks NBER-dated recessions. "
            f"*Note: FRED restricted this ICE BofA series to {hy_since}+ in 2023; "
            "historical percentile reflects the available window.*"
        )

        st.divider()

    # ── Panel 5: US vs. International ────────────────────────────────────────

    st.markdown("#### US vs. International Equity")

    try:
        with st.spinner("Loading SPY / EFA price data…"):
            spy_raw = _load_price_series("SPY", "2004-01-01")
            efa_raw = _load_price_series("EFA", "2004-01-01")

        aligned = pd.concat([spy_raw, efa_raw], axis=1).dropna()
        aligned.columns = ["SPY", "EFA"]
        aligned.index = pd.to_datetime(aligned.index)  # normalise date vs Timestamp
        ratio = (aligned["SPY"] / aligned["EFA"]).dropna()

        twenty_start  = _window_start("20Y")
        ratio_20y     = ratio[ratio.index >= pd.Timestamp(twenty_start)]
        current_ratio = float(ratio.iloc[-1])
        ratio_pctile  = macro.percentile(ratio_20y, current_ratio)

        ratio_1y_base = ratio[ratio.index <= ONE_YR_AGO]
        ratio_1y_ago  = float(ratio_1y_base.iloc[-1]) if not ratio_1y_base.empty else current_ratio
        rel_perf_bps  = (current_ratio / ratio_1y_ago - 1) * 10000

        col_m, col_w = st.columns([3, 1])
        with col_m:
            st.metric("US/Intl Ratio Percentile (20Y)", _ordinal(ratio_pctile))
            rel_dir = "outperformed" if rel_perf_bps >= 0 else "underperformed"
            st.caption(
                f"US {rel_dir} international by {abs(rel_perf_bps):.0f} bps "
                "over the last 12 months"
            )
        with col_w:
            us_window = st.radio(
                "Window", ["5Y", "10Y", "20Y", "Max"],
                index=1, key="us_window",
            )

        us_start   = _window_start(us_window)
        ratio_w    = ratio[ratio.index >= pd.Timestamp(us_start)]
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
        fig_us.update_yaxes(title_text="Ratio (normalized to 1.0)")
        st.plotly_chart(fig_us, width='stretch')

        us_label = (
            "extreme"   if ratio_pctile > 90 else
            "very high" if ratio_pctile > 75 else
            "elevated"  if ratio_pctile > 55 else
            "moderate"
        )
        st.caption(
            f"US outperformance vs. international is at the {_ordinal(ratio_pctile)} percentile "
            f"of its 20-year history — {us_label} relative to history. "
            "Extended US outperformance has historically mean-reverted via valuation "
            "convergence and dollar cycle turns, supporting the International Developed "
            "sleeve’s 19% weight and its valuation-driven thesis."
        )

    except Exception as exc:
        st.error(f"US vs. International data unavailable: {exc}")

    st.divider()

    # ── Sources ───────────────────────────────────────────────────────────────

    with st.expander("Data sources"):
        st.caption(
            "**FRED** (Federal Reserve Bank of St. Louis): T10Y2Y (10Y−2Y Treasury spread), "
            "DFF (Effective Federal Funds Rate), BAMLH0A0HYM2 (ICE BofA HY OAS — available "
            "from May 2023 only; FRED restricted series access in 2023), "
            "USREC (NBER recession indicator).  \n"
            "**Shiller / Yale**: CAPE from Robert Shiller’s dataset at https://shillerdata.com/.  \n"
            "**Yahoo Finance**: SPY and EFA price data via local prices cache."
        )
