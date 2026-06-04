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

# History base. The ETF proxies (IWM/IWB/IWD/IWF) inception ~2000, so prices are
# fetched from _FETCH_START and the computed series is trimmed to _HISTORY_START
# (~1 trading-year later, once the rolling-12m warmup has filled for both legs).
# This fixed ~25-year base is the denominator for the historical percentile and
# the extent of the "Max" window; the lookback selector only reshapes the
# *displayed* date range, never this base.
_FETCH_START   = "2000-01-01"
_HISTORY_START = "2001-01-01"

# Lookback-window options surfaced on the page; "Max" means the full base above.
WINDOW_YEARS = {"1Y": 1, "3Y": 3, "5Y": 5, "10Y": 10}

# Percentile bands for the interpretation prose (0–100 scale), consistent with
# the 25/75 boundaries in src.prose_helpers.percentile_label.
_PCTILE_LOW  = 25.0
_PCTILE_HIGH = 75.0


# ── date / formatting helpers ───────────────────────────────────────────────────

def _ordinal(n: float) -> str:
    """Format a percentile as an ordinal, e.g. 58 -> '58th' (matches the page)."""
    n = int(round(n))
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


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
    history_start: Optional[str],
    ff_df: Optional[pd.DataFrame],
    small_prices: Optional[pd.Series],
    large_prices: Optional[pd.Series],
) -> pd.DataFrame:
    """
    Shared builder for the size and style factor frames.

    Returns the full ETF-era history (from ``history_start`` onward) so the page
    can compute a fixed full-history percentile and offer a "Max" window. The
    page applies the lookback selector to the *display* only.
    """
    end = end or date.today().isoformat()

    if ff_df is None:
        ff_df = load_factors("us")
    if small_prices is None:
        small_prices = _adj_close(small_ticker, _FETCH_START, end)
    if large_prices is None:
        large_prices = _adj_close(large_ticker, _FETCH_START, end)

    ff_leg  = rolling_12m_ff_premium(ff_df, ff_col, window=window).rename(ff_out)
    etf_leg = rolling_12m_etf_relative(small_prices, large_prices, window=window).rename(etf_out)

    frame = pd.concat([ff_leg, etf_leg], axis=1).sort_index()
    frame.index = pd.to_datetime(frame.index)
    if history_start is not None:
        frame = frame[frame.index >= pd.Timestamp(history_start)]
    return frame


def build_size_factor_frame(
    end: Optional[str] = None,
    window: int = _TRADING_YEAR,
    history_start: Optional[str] = _HISTORY_START,
    ff_df: Optional[pd.DataFrame] = None,
    iwm_prices: Optional[pd.Series] = None,
    iwb_prices: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """
    Size factor frame: ``ff_smb_12m`` (Fama-French SMB trailing-12m cumulative)
    and ``etf_iwm_iwb_12m`` (IWM minus IWB trailing-12m relative), both percent,
    on a monotonic datetime index spanning the full ETF-era history.

    Fetching is injectable (``ff_df``, ``iwm_prices``, ``iwb_prices``) for tests.
    """
    return _build_factor_frame(
        ff_col="SMB", ff_out="ff_smb_12m",
        small_ticker="IWM", large_ticker="IWB", etf_out="etf_iwm_iwb_12m",
        end=end, window=window, history_start=history_start,
        ff_df=ff_df, small_prices=iwm_prices, large_prices=iwb_prices,
    )


def build_style_factor_frame(
    end: Optional[str] = None,
    window: int = _TRADING_YEAR,
    history_start: Optional[str] = _HISTORY_START,
    ff_df: Optional[pd.DataFrame] = None,
    iwd_prices: Optional[pd.Series] = None,
    iwf_prices: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """
    Style factor frame: ``ff_hml_12m`` (Fama-French HML trailing-12m cumulative)
    and ``etf_iwd_iwf_12m`` (IWD minus IWF trailing-12m relative), both percent,
    on a monotonic datetime index spanning the full ETF-era history.

    Fetching is injectable (``ff_df``, ``iwd_prices``, ``iwf_prices``) for tests.
    """
    return _build_factor_frame(
        ff_col="HML", ff_out="ff_hml_12m",
        small_ticker="IWD", large_ticker="IWF", etf_out="etf_iwd_iwf_12m",
        end=end, window=window, history_start=history_start,
        ff_df=ff_df, small_prices=iwd_prices, large_prices=iwf_prices,
    )


# ── percentile, window filtering, interpretation ────────────────────────────────

def factor_percentile(series: pd.Series, current_value: float) -> float:
    """
    Historical percentile rank (0–100) of ``current_value`` within ``series``.

    Delegates to ``src.macro.percentile`` so the Factor Regime percentile uses
    the exact same method as every other Macro indicator: the fraction of
    observations less than or equal to the current value.
    """
    from src.macro import percentile as _macro_percentile
    return _macro_percentile(series, current_value)


def filter_window(
    frame: pd.DataFrame,
    window: str,
    today: Optional[str] = None,
) -> pd.DataFrame:
    """
    Trailing-window view of ``frame`` for the page's lookback selector.

    ``window`` is one of "1Y", "3Y", "5Y", "10Y", "Max". "Max" (or an empty
    frame) returns ``frame`` unchanged. This reshapes the *displayed* date range
    only — it does not affect the percentile base, which is the full frame.
    """
    if frame is None or frame.empty or window == "Max":
        return frame
    today = today or date.today().isoformat()
    cutoff = pd.Timestamp(today) - pd.DateOffset(years=WINDOW_YEARS[window])
    return frame[frame.index >= cutoff]


# Per-factor vocabulary for the interpretation prose.
_FACTOR_VOCAB = {
    "size": {
        "winner": "small-caps", "loser": "large-caps", "verb": "have",
        "ff_name": "Fama-French SMB premium", "proxy_name": "IWM−IWB",
        "tilt": "small-cap tilt",
    },
    "style": {
        "winner": "value", "loser": "growth", "verb": "has",
        "ff_name": "Fama-French HML premium", "proxy_name": "IWD−IWF",
        "tilt": "value tilt",
    },
}


def _strength_phrase(pctile: float, winning: bool) -> str:
    """Qualitative 'how extreme' fragment, conditioned on win/lag direction."""
    if winning:
        if pctile >= _PCTILE_HIGH:
            return "well above its typical strength"
        if pctile < _PCTILE_LOW:
            return "though below its typical strength"
        return "around its typical strength"
    # lagging: a low percentile is deep in the tail (extreme drag); a high
    # percentile means the current value sits near the better end of history.
    if pctile < _PCTILE_LOW:
        return "an extreme reading by historical standards"
    if pctile >= _PCTILE_HIGH:
        return "a mild reading by historical standards"
    return "a middling reading by historical standards"


def interpret_factor(
    ff_value: float,
    ff_pctile: float,
    etf_value: float,
    etf_pctile: float,
    factor_name: str,
) -> str:
    """
    Dynamic interpretation of one factor's trailing-12m state.

    Leads with the Fama-French reading (the textbook long-short factor, but
    carrying ~1-month publication lag), conditioned on BOTH its sign (winning vs
    lagging over the trailing year) and its historical percentile (how extreme
    the reading is). Then cites the long-only ETF proxy as the current
    cross-check, noting agreement or disagreement of direction. Closes on what
    the reading means for the corresponding SAA tilt.

    Percentiles are on the 0–100 scale (see ``factor_percentile``). Matches the
    institutional voice of ``src.macro.interpret_*``.
    """
    v = _FACTOR_VOCAB[factor_name]
    ff_pos  = ff_value >= 0
    etf_pos = etf_value >= 0
    agree   = ff_pos == etf_pos

    # Sentence 1 — Fama-French reading (sign × percentile).
    if ff_pos:
        s1 = (
            f"Over the trailing year, {v['winner']} {v['verb']} outperformed {v['loser']} — "
            f"the {v['ff_name']} is {ff_value:+.1f}%, in the {_ordinal(ff_pctile)} "
            f"percentile of its history, {_strength_phrase(ff_pctile, winning=True)}."
        )
    else:
        s1 = (
            f"Over the trailing year, {v['winner']} {v['verb']} lagged {v['loser']} — "
            f"the {v['ff_name']} is {ff_value:+.1f}%, in the {_ordinal(ff_pctile)} "
            f"percentile of its history, {_strength_phrase(ff_pctile, winning=False)}."
        )

    # Sentence 2 — ETF proxy cross-check (current to today; FF carries a lag).
    if agree:
        s2 = (
            f" The long-only proxy ({v['proxy_name']}) reads {etf_value:+.1f}%, "
            "current to today and confirming the same direction."
        )
    else:
        s2 = (
            f" The long-only proxy ({v['proxy_name']}), current to today, reads "
            f"{etf_value:+.1f}% — the opposite sign, so the most recent month sends "
            "a mixed signal rather than a clean confirmation."
        )

    # Sentence 3 — SAA tilt read.
    if ff_pos and agree:
        s3 = f" The SAA's {v['tilt']} is currently being rewarded."
    elif ff_pos and not agree:
        s3 = (
            f" The SAA's {v['tilt']} is being rewarded on the academic measure, "
            "but the proxy's more recent reading suggests that edge may be fading."
        )
    elif (not ff_pos) and agree:
        s3 = (
            f" The SAA's {v['tilt']} has been a headwind over the trailing year, "
            "a drag on relative performance."
        )
    else:
        s3 = (
            f" The SAA's {v['tilt']} has been a headwind on the academic measure, "
            "though the proxy's more recent reading hints the drag may be turning."
        )

    return s1 + s2 + s3
