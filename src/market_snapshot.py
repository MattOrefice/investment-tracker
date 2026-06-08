"""
Market Snapshot — last-close-dated trailing-window returns and movers.

Streamlit-free, testable. All returns are SIMPLE total returns on the
dividend-adjusted close, keyed off each series' own latest date (or a passed
``as_of``) — never the calendar date. Daily-close data only; "1D" is the move
since the prior close, which spans weekends (Fri → Mon).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import NamedTuple, Optional

import pandas as pd

# Trailing windows shown on the page, in display order.
WINDOWS = ["1D", "1W", "1M", "3M", "YTD"]

# The 11 SPDR Select Sector ETFs → display names.
SECTOR_ETFS: dict[str, str] = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLV":  "Health Care",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
    "XLC":  "Communication Services",
}


def _window_return(series: pd.Series, window: str, as_of: Optional[str] = None) -> float:
    """
    Simple total return of a price series over one trailing window.

    Keys off the series' latest bar on/before ``as_of`` (or the series end).
    Base prices use the first available bar on/after the calendar cutoff
    (mirroring src.reports._bm_period_return), so a cutoff on a non-trading day
    falls forward to the next session. Returns NaN if there is too little data.
    """
    s = series.dropna().sort_index()
    if len(s) < 2:
        return float("nan")

    if as_of is not None:
        s = s[s.index <= pd.Timestamp(as_of)]
        if len(s) < 2:
            return float("nan")

    last_val  = float(s.iloc[-1])
    last_date = pd.Timestamp(s.index[-1])

    if window == "1D":
        prev = float(s.iloc[-2])
        return last_val / prev - 1 if prev else float("nan")

    if window == "YTD":
        prior_year = s[s.index < pd.Timestamp(date(last_date.year, 1, 1))]
        base = float(prior_year.iloc[-1]) if not prior_year.empty else float(s.iloc[0])
        return last_val / base - 1 if base else float("nan")

    if window == "1W":
        cutoff = last_date - timedelta(days=7)
    elif window == "1M":
        cutoff = last_date - pd.DateOffset(months=1)
    elif window == "3M":
        cutoff = last_date - pd.DateOffset(months=3)
    else:
        return float("nan")

    sliced = s[s.index >= cutoff]
    if len(sliced) < 2:
        return float("nan")
    base = float(sliced.iloc[0])
    return last_val / base - 1 if base else float("nan")


def trailing_returns(
    price_map: dict[str, pd.Series],
    windows: Optional[list[str]] = None,
    as_of: Optional[str] = None,
) -> pd.DataFrame:
    """
    DataFrame [ticker × window] of simple total returns (fractions).

    ``price_map``: {ticker: dividend-adjusted price Series, date-indexed}.
    Each return keys off the series' own latest date (or ``as_of``).
    """
    windows = list(windows) if windows is not None else list(WINDOWS)
    data = {
        ticker: {w: _window_return(s, w, as_of) for w in windows}
        for ticker, s in price_map.items()
    }
    df = pd.DataFrame.from_dict(data, orient="index")
    return df[windows] if not df.empty else df


def rank_sectors(returns_df: pd.DataFrame, window: str) -> pd.DataFrame:
    """
    Sector ETFs ranked by the chosen window's return, descending.

    Returns a DataFrame with columns Ticker, Sector, <window> (fraction),
    limited to tickers present in SECTOR_ETFS with a non-NaN return.
    """
    sectors = [t for t in returns_df.index if t in SECTOR_ETFS]
    col = returns_df.loc[sectors, window].dropna().sort_values(ascending=False)
    return pd.DataFrame({
        "Ticker": list(col.index),
        "Sector": [SECTOR_ETFS[t] for t in col.index],
        window:   col.values,
    })


def relative_trailing(
    returns_df: pd.DataFrame,
    long_t: str,
    short_t: str,
    windows: Optional[list[str]] = None,
) -> pd.Series:
    """
    Per-window relative return ``long_t − short_t`` (e.g. IWM−IWB, IWD−IWF).
    """
    windows = list(windows) if windows is not None else list(WINDOWS)
    out = {}
    for w in windows:
        lv = returns_df.loc[long_t, w]  if long_t  in returns_df.index else float("nan")
        sv = returns_df.loc[short_t, w] if short_t in returns_df.index else float("nan")
        out[w] = lv - sv
    return pd.Series(out)[windows]


def latest_common_date(price_map: dict[str, pd.Series]) -> Optional[pd.Timestamp]:
    """
    The latest date reached by ALL fetched series (min of per-ticker last dates),
    so the page never dates itself to data a ticker lacks. None if no data.
    """
    last_dates = [
        pd.Timestamp(s.dropna().index.max())
        for s in price_map.values()
        if not s.dropna().empty
    ]
    return min(last_dates) if last_dates else None


class MovingAverageTrend(NamedTuple):
    """Broad-market trend read: latest price vs its moving average."""
    price:          float
    moving_average: float
    pct_from_ma:    float   # price / ma − 1 (fraction); positive = above the MA
    direction:      str     # "uptrend" | "downtrend" | "insufficient data"
    sufficient:     bool    # True when ≥ window bars were available


def trend_vs_moving_average(
    price_series: pd.Series, window: int = 200
) -> MovingAverageTrend:
    """
    Latest close vs its ``window``-day simple moving average — a standard trend
    filter. Above the MA = uptrend, below = downtrend.

    Uses the dividend-adjusted close passed in. Returns an "insufficient data"
    state (NaNs, sufficient=False) if fewer than ``window`` bars are available.
    """
    s = price_series.dropna().sort_index()
    if len(s) < window:
        return MovingAverageTrend(
            float("nan"), float("nan"), float("nan"), "insufficient data", False
        )
    price = float(s.iloc[-1])
    ma    = float(s.rolling(window).mean().iloc[-1])
    pct   = price / ma - 1 if ma else float("nan")
    direction = "uptrend" if price >= ma else "downtrend"
    return MovingAverageTrend(price, ma, pct, direction, True)
