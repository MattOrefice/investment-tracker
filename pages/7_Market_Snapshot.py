"""Market Snapshot — last-close-dated size/value, sector movers, and a mechanical market line."""
import streamlit as st

st.set_page_config(page_title="Market Snapshot", layout="wide")

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go

from src import market_snapshot as ms
from src.prices import get_prices
from src.ui_helpers import render_footer, render_page_header
render_page_header()


_PRIMARY = "#2E4057"
_REF     = "#9E9E9E"


@st.cache_data(ttl=3600, show_spinner=False)
def _load_prices() -> pd.DataFrame:
    """Dividend-adjusted close for the snapshot tickers, ~14 months back (covers YTD)."""
    start   = (date.today() - timedelta(days=430)).isoformat()
    tickers = ["IWM", "IWB", "IWD", "IWF", "SPY", "AGG", "EFA", "EEM"] + list(ms.SECTOR_ETFS)
    cols: dict[str, pd.Series] = {}
    for t in tickers:
        try:
            df = get_prices(t, start)
            s = df["adj_close"].ffill()
            s.index = pd.to_datetime(s.index)
            cols[t] = s.sort_index()
        except Exception:
            pass
    return pd.concat(cols, axis=1) if cols else pd.DataFrame()


def _hbar(labels: list[str], values: list[float], x_title: str, height: int = 180) -> go.Figure:
    """Compact horizontal RdYlGn/cmid=0 bar in the house style (values already in %)."""
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=values, colorscale="RdYlGn", cmid=0),
        text=[f"{v:+.2f}%" for v in values],
        textposition="auto",
        hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
    ))
    fig.add_vline(x=0, line_dash="dash", line_color=_REF, line_width=1)
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="white", plot_bgcolor="#FAFAFA",
        font=dict(color="#333333", size=12),
        xaxis=dict(title=x_title, gridcolor="#EBEBEB", zeroline=False),
        yaxis=dict(tickfont_size=11),
    )
    return fig


_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Market Snapshot")

    with st.spinner("Loading market data…"):
        prices_df = _load_prices()

    if prices_df.empty:
        st.warning("Market data unavailable — check the connection or API limits.")
        render_footer()
        st.stop()

    price_map = {t: prices_df[t].dropna() for t in prices_df.columns}
    asof      = ms.latest_common_date(price_map)
    asof_str  = asof.strftime("%b %d, %Y") if asof is not None else "n/a"
    rt        = ms.trailing_returns(price_map)

    st.caption(
        f"Market data as of **{asof_str}** (last exchange close). Daily close only — "
        "not intraday; the as-of date is the latest close in the data, not the calendar date."
    )
    st.divider()

    # ── Section 1 — Size & Value ───────────────────────────────────────────────
    st.subheader("Size & Value")
    st.caption(
        "The SAA tilts toward small-cap and value. These are trailing *relative* returns — "
        "small minus large (IWM − IWB) and value minus growth (IWD − IWF) — by window. The "
        "1M / 3M / YTD columns carry the signal; 1D / 1W are mostly noise, shown for completeness."
    )

    _size  = ms.relative_trailing(rt, "IWM", "IWB")
    _style = ms.relative_trailing(rt, "IWD", "IWF")
    sv_tbl = pd.DataFrame(
        {
            "Factor": [
                "Size: small − large (IWM − IWB)",
                "Value: value − growth (IWD − IWF)",
            ],
            **{w: [_size[w] * 100, _style[w] * 100] for w in ms.WINDOWS},
        }
    )
    _sv_cfg = {
        w: st.column_config.NumberColumn(
            "1D (since prior close)" if w == "1D" else w, format="%+.2f%%"
        )
        for w in ms.WINDOWS
    }
    st.dataframe(sv_tbl, hide_index=True, width="stretch", column_config=_sv_cfg)

    # Compact YTD visual of the two relative-return legs (house RdYlGn/cmid=0 style).
    # A positive (green) bar means the SAA's tilt was rewarded YTD.
    _sv_legs = ["Value: value − growth", "Size: small − large"]   # ascending → first at bottom
    _sv_vals = [_style["YTD"] * 100, _size["YTD"] * 100]
    _fig_sv = go.Figure(go.Bar(
        x=_sv_vals,
        y=_sv_legs,
        orientation="h",
        marker=dict(color=_sv_vals, colorscale="RdYlGn", cmid=0),
        text=[f"{v:+.2f}%" for v in _sv_vals],
        textposition="auto",
        hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
    ))
    _fig_sv.add_vline(x=0, line_dash="dash", line_color=_REF, line_width=1)
    _fig_sv.update_layout(
        height=180,
        margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="white",
        plot_bgcolor="#FAFAFA",
        font=dict(color="#333333", size=12),
        xaxis=dict(title="YTD relative return (%)", gridcolor="#EBEBEB", zeroline=False),
        yaxis=dict(tickfont_size=11),
    )
    st.plotly_chart(_fig_sv, width="stretch", config={"displayModeBar": False})
    st.divider()

    # ── Section 2 — Regional Leadership ────────────────────────────────────────
    st.subheader("Regional Leadership")
    st.caption(
        "US vs Developed-International vs Emerging-Markets recent *relative* returns — "
        "the third equity-regime axis alongside size and style. Ties to the SAA's "
        "International Developed and Emerging Markets sleeves. Positive = US leading; "
        "negative = the international / EM sleeve leading."
    )
    _us_intl = ms.relative_trailing(rt, "SPY", "EFA")
    _us_em   = ms.relative_trailing(rt, "SPY", "EEM")
    reg_tbl = pd.DataFrame(
        {
            "Pair": [
                "US − Intl Developed (SPY − EFA)",
                "US − Emerging Mkts (SPY − EEM)",
            ],
            **{w: [_us_intl[w] * 100, _us_em[w] * 100] for w in ms.WINDOWS},
        }
    )
    st.dataframe(reg_tbl, hide_index=True, width="stretch", column_config=_sv_cfg)
    st.plotly_chart(
        _hbar(
            ["US − Emerging Mkts (SPY − EEM)", "US − Intl Developed (SPY − EFA)"],
            [_us_em["YTD"] * 100, _us_intl["YTD"] * 100],
            "YTD relative return (%)",
        ),
        width="stretch", config={"displayModeBar": False},
    )
    st.caption(
        "Recent moves, plus EM. For the multi-year structural US-vs-International trend "
        "(rolling 12-month spread), see the Macro page's Cross-Asset Performance section."
    )
    st.divider()

    # ── Section 3 — Sector movers ──────────────────────────────────────────────
    st.subheader("Sector Movers")
    st.caption("The 11 SPDR Select Sector ETFs ranked by trailing return.")

    win = st.radio(
        "Window", ms.WINDOWS, index=ms.WINDOWS.index("YTD"), horizontal=True, key="sector_window",
        format_func=lambda w: "1D (since prior close)" if w == "1D" else w,
    )

    ranked = ms.rank_sectors(rt, win)
    if ranked.empty:
        st.info("Sector data unavailable for this window.")
    else:
        _asc = ranked.iloc[::-1]  # ascending → highest at top of horizontal bar
        fig = go.Figure(go.Bar(
            x=(_asc[win] * 100).values,
            y=_asc["Sector"].values,
            orientation="h",
            marker=dict(
                color=(_asc[win] * 100).values, colorscale="RdYlGn",
                cmid=0, colorbar=dict(title="%", thickness=12),
            ),
            text=[f"{v * 100:+.1f}%" for v in _asc[win].values],
            textposition="auto",
            hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
        ))
        fig.add_vline(x=0, line_dash="dash", line_color=_REF, line_width=1)
        fig.update_layout(
            height=380,
            margin=dict(l=0, r=0, t=8, b=0),
            paper_bgcolor="white",
            plot_bgcolor="#FAFAFA",
            font=dict(color="#333333", size=12),
            xaxis=dict(title=f"{win} return (%)", gridcolor="#EBEBEB", zeroline=False),
            yaxis=dict(tickfont_size=11),
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

        _best, _worst = ranked.iloc[0], ranked.iloc[-1]
        _wlbl = "1D" if win == "1D" else win
        b_col, w_col = st.columns(2)
        b_col.metric(f"Best ({_wlbl})",  _best["Sector"],  f"{_best[win] * 100:+.2f}%")
        w_col.metric(f"Worst ({_wlbl})", _worst["Sector"], f"{_worst[win] * 100:+.2f}%")
    st.divider()

    # ── Section 4 — Stocks vs Bonds ────────────────────────────────────────────
    st.subheader("Stocks vs Bonds")
    st.caption(
        "SPY vs AGG trailing *relative* return — a risk-on / risk-off read over "
        "meaningful windows (1M / 3M / YTD), not a one-day tape. Positive = stocks "
        "leading bonds (risk-on); negative = bonds leading (risk-off)."
    )
    _svb = ms.relative_trailing(rt, "SPY", "AGG")
    _svb_wins = ["1M", "3M", "YTD"]   # ascending → YTD at the top of the horizontal bar
    st.plotly_chart(
        _hbar(
            _svb_wins,
            [_svb[w] * 100 for w in _svb_wins],
            "SPY − AGG relative return (%)",
        ),
        width="stretch", config={"displayModeBar": False},
    )
    st.divider()

    # ── Section 5 — Broad-Market Trend ─────────────────────────────────────────
    st.subheader("Broad-Market Trend")
    st.caption(
        "The S&P 500 (SPY) versus its 200-day moving average — a standard trend filter. "
        "Above the average = uptrend, below = downtrend. Daily close, dated to the latest bar."
    )
    _trend = ms.trend_vs_moving_average(
        price_map.get("SPY", pd.Series(dtype=float)), window=200
    )
    if _trend.sufficient:
        _side = "above" if _trend.pct_from_ma >= 0 else "below"
        st.metric("S&P 500 vs 200-day MA", f"{_trend.pct_from_ma * 100:+.1f}%")
        st.caption(
            f"S&P 500 closed at {_trend.price:,.2f} versus a 200-day average of "
            f"{_trend.moving_average:,.2f} — **{abs(_trend.pct_from_ma) * 100:.1f}% {_side}** "
            f"its 200-day moving average (**{_trend.direction}**)."
        )
    else:
        st.info("Insufficient price history for a 200-day moving average.")

    st.caption(
        "**Methodology.** Daily exchange close (not intraday). The as-of date is the latest "
        "close present in the data, never the calendar date. \"1D\" is the move since the prior "
        "close and spans weekends (Friday → Monday). Returns use dividend-adjusted close (total "
        "return), keyed off each series' own latest date. Tickers: IWM / IWB (size), IWD / IWF "
        "(value), SPY / EFA / EEM (regional), the 11 SPDR Select Sector ETFs, SPY / AGG (stocks "
        "vs bonds), and the SPY 200-day moving average (broad-market trend). Prices are locally "
        "cached daily closes."
    )

    render_footer()
