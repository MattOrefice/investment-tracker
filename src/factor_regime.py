"""
Factor Regime — trailing-12-month size and style factor performance.

Two factors, each shown two ways:

  SIZE (small vs large)
    - Fama-French SMB: long-short academic factor. SMB is already
      small-minus-big by construction, so the trailing-12-month
      *cumulative* SMB return IS the realized size premium over the
      past year.
    - ETF proxy: IWM (Russell 2000, small) vs IWB (Russell 1000, large).
      small_minus_large = IWM_12m_return - IWB_12m_return.

  STYLE (value vs growth)
    - Fama-French HML: long-short academic factor. HML is already
      high-minus-low (value-minus-growth), so its trailing-12-month
      cumulative return IS the realized value premium over the past year.
    - ETF proxy: IWD (Russell 1000 Value) vs IWF (Russell 1000 Growth).
      value_minus_growth = IWD_12m_return - IWF_12m_return.

The academic legs read Ken French daily factor data via
``src.factors.load_factors`` (decimal daily returns). The ETF legs use
dividend-adjusted close from ``src.prices.get_prices`` — the same cached
price path the Macro page already uses for SPY/EFA. Both legs are expressed in
percent and overlaid so the long-short factor can be compared with the
long-only tradeable spread.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from src.factors import load_factors
from src.prices import get_prices

# Trailing-12-month window in trading days (matches the 252-day convention the
# Macro page already uses for the SPY/EFA cross-asset spread).
_TRADING_YEAR = 252

# Default plotted history. The rolling-12m series needs ~1 trading year of
# warmup before the first plotted point, so fetching reaches further back.
_DISPLAY_YEARS = 10
_WARMUP_YEARS  = 2  # buffer covering the 12-month rolling warmup plus slack


# ── date helpers ───────────────────────────────────────────────────────────────

def _years_before(iso: str, years: int) -> str:
    """ISO date string ``years`` earlier (leap-day safe)."""
    d = date.fromisoformat(iso[:10])
    try:
        return d.replace(year=d.year - years).isoformat()
    except ValueError:  # Feb 29
        return d.replace(year=d.year - years, day=28).isoformat()


def _adj_close(ticker: str, start: str, end: Optional[str] = None) -> pd.Series:
    """Dividend-adjusted daily close for ``ticker`` as a datetime-indexed Series."""
    df = get_prices(ticker, start, end or date.today().isoformat())
    s = df["adj_close"].ffill()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


# ── core computations ──────────────────────────────────────────────────────────

def rolling_12m_ff_premium(
    ff_df: pd.DataFrame,
    factor_col: str,
    window: int = _TRADING_YEAR,
) -> pd.Series:
    """
    Trailing-window cumulative compounded return of a Ken French factor column.

    SMB and HML are long-short returns, so compounding them over a trailing
    window gives the realized factor premium over that window. For each date
    the value is ``(prod(1 + r) over the trailing ``window`` rows) - 1``,
    expressed in percent.

    ``ff_df`` holds *decimal* daily (or monthly, in tests) factor returns; pass
    ``window=12`` for a monthly series, ``window=252`` for daily.
    """
    s = ff_df[factor_col].dropna().astype(float)
    # Compound via log returns: daily/monthly factor returns are never <= -100%,
    # so 1 + r > 0 and log1p is well-defined.
    cum_log = np.log1p(s).rolling(window).sum()
    return (np.expm1(cum_log) * 100.0).dropna()


def rolling_12m_etf_relative(
    small_or_value_prices: pd.Series,
    large_or_growth_prices: pd.Series,
    window: int = _TRADING_YEAR,
) -> pd.Series:
    """
    Rolling relative total return of two price series, in percentage points.

    Computes each leg's trailing-window total return (``pct_change(window)`` on
    dividend-adjusted close) and returns ``small_minus_large = leg1 - leg2``.
    The inputs are inner-aligned on their common dates first so both legs use
    the same calendar.
    """
    aligned = pd.concat(
        [small_or_value_prices.astype(float), large_or_growth_prices.astype(float)],
        axis=1,
    ).dropna()
    aligned.columns = ["leg1", "leg2"]

    leg1_ret = aligned["leg1"].pct_change(window) * 100.0
    leg2_ret = aligned["leg2"].pct_change(window) * 100.0
    return (leg1_ret - leg2_ret).dropna()


# ── frame builders ─────────────────────────────────────────────────────────────

def _build_factor_frame(
    ff_col: str,
    ff_out: str,
    small_ticker: str,
    large_ticker: str,
    etf_out: str,
    end: Optional[str],
    window: int,
    display_start: Optional[str],
    ff_df: Optional[pd.DataFrame],
    small_prices: Optional[pd.Series],
    large_prices: Optional[pd.Series],
) -> pd.DataFrame:
    """Shared builder for the size and style factor frames."""
    end = end or date.today().isoformat()
    if display_start is None:
        display_start = _years_before(end, _DISPLAY_YEARS)
    fetch_start = _years_before(display_start, _WARMUP_YEARS)

    if ff_df is None:
        ff_df = load_factors("us")
    if small_prices is None:
        small_prices = _adj_close(small_ticker, fetch_start, end)
    if large_prices is None:
        large_prices = _adj_close(large_ticker, fetch_start, end)

    ff_leg  = rolling_12m_ff_premium(ff_df, ff_col, window=window).rename(ff_out)
    etf_leg = rolling_12m_etf_relative(small_prices, large_prices, window=window).rename(etf_out)

    frame = pd.concat([ff_leg, etf_leg], axis=1).sort_index()
    frame.index = pd.to_datetime(frame.index)
    frame = frame[frame.index >= pd.Timestamp(display_start)]
    return frame


def build_size_factor_frame(
    end: Optional[str] = None,
    window: int = _TRADING_YEAR,
    display_start: Optional[str] = None,
    ff_df: Optional[pd.DataFrame] = None,
    iwm_prices: Optional[pd.Series] = None,
    iwb_prices: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """
    Size factor frame: ``ff_smb_12m`` (Fama-French SMB trailing-12m cumulative)
    and ``etf_iwm_iwb_12m`` (IWM minus IWB trailing-12m relative), both percent,
    on a monotonic datetime index.

    Fetching is injectable (``ff_df``, ``iwm_prices``, ``iwb_prices``) for tests.
    """
    return _build_factor_frame(
        ff_col="SMB", ff_out="ff_smb_12m",
        small_ticker="IWM", large_ticker="IWB", etf_out="etf_iwm_iwb_12m",
        end=end, window=window, display_start=display_start,
        ff_df=ff_df, small_prices=iwm_prices, large_prices=iwb_prices,
    )


def build_style_factor_frame(
    end: Optional[str] = None,
    window: int = _TRADING_YEAR,
    display_start: Optional[str] = None,
    ff_df: Optional[pd.DataFrame] = None,
    iwd_prices: Optional[pd.Series] = None,
    iwf_prices: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """
    Style factor frame: ``ff_hml_12m`` (Fama-French HML trailing-12m cumulative)
    and ``etf_iwd_iwf_12m`` (IWD minus IWF trailing-12m relative), both percent,
    on a monotonic datetime index.

    Fetching is injectable (``ff_df``, ``iwd_prices``, ``iwf_prices``) for tests.
    """
    return _build_factor_frame(
        ff_col="HML", ff_out="ff_hml_12m",
        small_ticker="IWD", large_ticker="IWF", etf_out="etf_iwd_iwf_12m",
        end=end, window=window, display_start=display_start,
        ff_df=ff_df, small_prices=iwd_prices, large_prices=iwf_prices,
    )
