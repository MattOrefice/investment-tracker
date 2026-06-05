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
    tickers = ["IWM", "IWB", "IWD", "IWF", "SPY", "AGG", "UUP"] + list(ms.SECTOR_ETFS)
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
    st.dataframe(sv_tbl, hide_index=True, use_container_width=True, column_config=_sv_cfg)
    st.caption(
        "Positive size = small-caps led large; positive value = value led growth, over the "
        "trailing window. A positive reading means the SAA's tilt was rewarded recently."
    )
    st.divider()

    # ── Section 2 — Sector movers ──────────────────────────────────────────────
    st.subheader("Sector Movers")
    st.caption("The 11 SPDR Select Sector ETFs ranked by trailing return.")

    win = st.radio(
        "Window", ms.WINDOWS, index=2, horizontal=True, key="sector_window",
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

    # ── Section 3 — Market line ────────────────────────────────────────────────
    st.subheader("Market Line")

    def _last_1d(ticker: str) -> float:
        return float(rt.loc[ticker, "1D"]) if ticker in rt.index else float("nan")

    _line = ms.mechanical_market_line({
        "equities": _last_1d("SPY"),
        "bonds":    _last_1d("AGG"),
        "usd":      _last_1d("UUP"),
    })
    st.markdown(f"**{_line}**")
    st.caption(
        "A deterministic, sign-based readout of SPY (equities), AGG (bonds), and UUP "
        "(US dollar) since the prior close — derived purely from the figures, not commentary."
    )
    st.divider()

    st.caption(
        "**Methodology.** Daily exchange close (not intraday). The as-of date is the latest "
        "close present in the data, never the calendar date. \"1D\" is the move since the prior "
        "close and spans weekends (Friday → Monday). Returns use dividend-adjusted close (total "
        "return), keyed off each series' own latest date. Tickers: IWM / IWB (size), IWD / IWF "
        "(value), the 11 SPDR Select Sector ETFs, and SPY / AGG / UUP for the market line. "
        "Prices are locally cached daily closes."
    )

    render_footer()
