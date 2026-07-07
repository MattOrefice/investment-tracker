"""Benchmark series construction for performance attribution."""
import logging
from datetime import date

import pandas as pd

from src.db import get_connection
from src.prices import get_prices

# Coverage tolerance for benchmark components, mirroring the portfolio side's
# _first_adj_price/_last_adj_price 5-day tolerance in src/attribution.py: a
# component with no real price within this many days of each window bound is
# an explicit data gap, never folded in as a flat/fabricated series. The end
# bound matches the portfolio convention exactly (last price in [end-5, end]);
# the start bound is forward-tolerant (first price in [start, start+5]) by
# construction, since get_prices is bounded below by the window start and
# cannot return the prior close the portfolio side would back-fill from.
_COVERAGE_WINDOW_DAYS = 5

# Sleeve → benchmark ticker mapping (from asset_classes.benchmark_ticker).
# Real Assets benchmark was originally "VNQ+DJP" but DJP (iPath Bloomberg
# Commodity Index ETN) was delisted in May 2020.  Replaced with DBC (Invesco
# DB Commodity Index Tracking Fund), which is still active, uses a similar
# broad-commodity methodology, and has a long pricing history.
_SLEEVE_BENCHMARKS: dict[str, list[tuple[str, float]]] = {
    "US Large Core":          [("SPY",  1.0)],
    "US Large Quality":       [("QUAL", 1.0)],
    "US Large Value":         [("IWD",  1.0)],
    "US Small Cap":           [("IWM",  1.0)],
    "International Developed":[("EFA",  1.0)],
    "Emerging Markets":       [("EEM",  1.0)],
    "Core Fixed Income":      [("IEF",  1.0)],
    "TIPS":                   [("TIP",  1.0)],
    "Real Assets":            [("VNQ",  0.6), ("DBC", 0.4)],
    "Cash / SPAXX":           [("BIL",  1.0)],
}

# Fallback to holding tickers when benchmark fetch fails
_SLEEVE_HOLDINGS: dict[str, str] = {
    "US Large Core":          "VOO",
    "US Large Quality":       "SPHQ",
    "US Large Value":         "VTV",
    "US Small Cap":           "AVUV",
    "International Developed":"VEA",
    "Emerging Markets":       "IEMG",
    "Core Fixed Income":      "VGIT",
    "TIPS":                   "SCHP",
    "Real Assets":            "VNQ",
    "Cash / SPAXX":           "SPAXX",
}


def _get_price_series(ticker: str, start_date: str, end_date: str,
                      col: str = "adj_close") -> pd.Series:
    """Return a daily price series, ffilled over the full date range."""
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    if ticker == "SPAXX":
        return pd.Series(1.0, index=date_range)
    try:
        p = get_prices(ticker, start_date, end_date)
        p.index = pd.to_datetime(p.index)
        series = p[col].fillna(p["close"]) if col == "adj_close" else p[col]
        return series.reindex(date_range).ffill().bfill()
    except Exception:
        return pd.Series(dtype=float, index=date_range)


def _component_series(
    ticker: str, start_date: str, end_date: str
) -> tuple[pd.Series | None, list[str]]:
    """Daily ffilled adj_close series for one benchmark component, plus the
    window bound(s) — ISO dates — at which the component has no real price
    within _COVERAGE_WINDOW_DAYS. An empty gap list means full coverage.

    Returns (None, [bounds]) — never a flat or fabricated series — when the
    component cannot be priced near a bound, whether because the fetch raised,
    came back empty, or a stale cache silently ends inside the window
    (get_prices swallows gap-fetch failures, so coverage is judged on the
    returned prices, not on whether the fetch raised). Mirrors the portfolio
    side's _first_adj_price/_last_adj_price None contract in src/attribution.py.
    """
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    if ticker == "SPAXX":
        return pd.Series(1.0, index=date_range), []
    try:
        p = get_prices(ticker, start_date, end_date)
    except Exception:
        logging.exception(
            "Benchmark price lookup failed for %s in [%s, %s]",
            ticker, start_date, end_date,
        )
        return None, [start_date, end_date]
    if p.empty:
        logging.warning(
            "No benchmark prices for %s in [%s, %s]", ticker, start_date, end_date
        )
        return None, [start_date, end_date]
    p.index = pd.to_datetime(p.index)
    # Uncached direct fetches can carry duplicate date labels (get_prices only
    # dedups on the cache-concat path) — dedup here so reindex can't raise.
    p = p[~p.index.duplicated(keep="last")]
    series = p["adj_close"].fillna(p["close"])
    first_real = series.first_valid_index()
    last_real = series.last_valid_index()
    gaps: list[str] = []
    if first_real is None or (
        first_real - pd.Timestamp(start_date)
    ).days > _COVERAGE_WINDOW_DAYS:
        gaps.append(start_date)
    if last_real is None or (
        pd.Timestamp(end_date) - last_real
    ).days > _COVERAGE_WINDOW_DAYS:
        gaps.append(end_date)
    if gaps:
        logging.warning(
            "Benchmark component %s lacks price coverage near %s (window [%s, %s])",
            ticker, ", ".join(gaps), start_date, end_date,
        )
        return None, gaps
    return series.reindex(date_range).ffill().bfill(), gaps


def get_sp500_series(start_date: str, end_date: str | None = None) -> pd.Series:
    """
    Daily total-return series for SPY (adj_close), normalized so the first
    available value equals the portfolio's starting value on start_date.
    Returns a Series indexed by pd.Timestamp with dollar values starting at 1.0.
    Call site multiplies by starting portfolio value to align scales.
    """
    end = end_date or date.today().isoformat()
    series = _get_price_series("SPY", start_date, end, col="adj_close")
    first_valid = series.first_valid_index()
    if first_valid is None or series[first_valid] == 0:
        return series
    return series / series[first_valid]   # normalized index starting at 1.0


def get_custom_blended_series(start_date: str, end_date: str | None = None) -> pd.Series:
    """
    Daily value series for a $1-normalized SAA benchmark.

    Allocates $1 across benchmark tickers at target weights on start_date,
    then marks to market daily using adj_close prices.  Returns a Series
    indexed by pd.Timestamp starting at 1.0.
    """
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT name, target_weight
               FROM asset_classes
               WHERE parent_id IS NOT NULL""",
        ).fetchall()
    sleeve_weights = {r["name"]: r["target_weight"] for r in rows}

    # Build price matrix for each component ticker
    price_cols: dict[str, pd.Series] = {}
    weight_map: dict[str, float] = {}   # component ticker → effective weight

    for sleeve, components in _SLEEVE_BENCHMARKS.items():
        sleeve_wt = sleeve_weights.get(sleeve, 0.0)
        for ticker, frac in components:
            effective_wt = sleeve_wt * frac
            p = _get_price_series(ticker, start_date, end, col="adj_close")
            if ticker in price_cols:
                # Sum weight when same ticker appears in multiple sleeves
                weight_map[ticker] = weight_map.get(ticker, 0.0) + effective_wt
            else:
                price_cols[ticker] = p
                weight_map[ticker] = effective_wt

    if not price_cols:
        return pd.Series(1.0, index=date_range)

    # Determine the number of "shares" for each ticker so that on day 0 the
    # portfolio value equals $1.00 total.
    prices_df = pd.DataFrame(price_cols, index=date_range)
    first_row = prices_df.ffill().bfill().iloc[0]

    shares: dict[str, float] = {}
    for ticker, wt in weight_map.items():
        p0 = float(first_row.get(ticker, 0))
        if p0 > 0:
            shares[ticker] = wt / p0   # units bought with `wt` dollars at price p0

    daily_value = pd.Series(0.0, index=date_range)
    for ticker, n_shares in shares.items():
        daily_value += prices_df[ticker].ffill() * n_shares

    # Normalize to start at 1.0
    first_val = daily_value.iloc[0]
    if first_val > 0:
        daily_value = daily_value / first_val

    return daily_value


def get_sleeve_benchmark_returns(
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Return a DataFrame of per-sleeve benchmark cumulative returns over the period.
    Columns = sleeve names, index = dates, values = (price_t / price_0) - 1.

    Benchmark gaps: a component with no real price within _COVERAGE_WINDOW_DAYS
    of each window bound (see _component_series) is dropped from its sleeve's
    blend and recorded as a (sleeve, ticker, date) tuple on the returned frame's
    ``.attrs["benchmark_gaps"]``. A sleeve whose components ALL fail carries an
    explicit NaN sentinel column — never a flat series that reads as a
    fabricated 0.0 return; a partially-priceable blend renormalizes to its
    surviving components (still flagged, so the substitution is visible).
    Callers must treat NaN as missing data, never as a return of zero.
    """
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    result: dict[str, pd.Series] = {}
    benchmark_gaps: list[tuple[str, str, str]] = []

    for sleeve, components in _SLEEVE_BENCHMARKS.items():
        # Build blended price series for multi-component sleeves
        sleeve_series = pd.Series(0.0, index=date_range)
        total_frac = 0.0

        for ticker, frac in components:
            p, missing_bounds = _component_series(ticker, start_date, end)
            if missing_bounds:
                # Explicit data gap: drop the component from the blend and flag
                # it, never fold in a flat/fabricated series.
                benchmark_gaps.extend(
                    (sleeve, ticker, bound) for bound in missing_bounds
                )
                continue
            if p.empty:   # degenerate window (start > end) — nothing to price
                continue
            p0 = float(p.iloc[0])
            if p0 > 0:
                # Normalized so each component starts at frac (proportional weight)
                sleeve_series += (p / p0) * frac
                total_frac += frac
            else:
                # A non-positive anchor price is unusable data, not a flat market
                benchmark_gaps.append((sleeve, ticker, start_date))

        if total_frac > 0:
            sleeve_series = sleeve_series / total_frac   # renormalize if any component failed
            result[sleeve] = sleeve_series - 1.0         # convert to return
        else:
            # Every component failed: carry an explicit missing-data sentinel,
            # never a flat series that reads as a fabricated 0.0 return.
            result[sleeve] = pd.Series(float("nan"), index=date_range)

    df = pd.DataFrame(result, index=date_range)
    df.attrs["benchmark_gaps"] = benchmark_gaps
    return df


def get_naive_60_40_series(start_date: str, end_date: str | None = None) -> pd.Series:
    """
    $1-normalized daily return series for a 60/40 naive benchmark.

    Computes as 0.6 × daily SPY return + 0.4 × daily AGG return at the return
    level (not a portfolio simulation — avoids rebalancing-frequency assumptions).
    Returns a Series indexed by pd.Timestamp starting at 1.0 on start_date.
    """
    end = end_date or date.today().isoformat()
    spy = _get_price_series("SPY", start_date, end, col="adj_close")
    agg = _get_price_series("AGG", start_date, end, col="adj_close")

    spy_ret = spy.pct_change().fillna(0.0)
    agg_ret = agg.pct_change().fillna(0.0)

    naive_ret  = 0.6 * spy_ret + 0.4 * agg_ret
    cumulative = (1 + naive_ret).cumprod()

    first = float(cumulative.iloc[0]) if not cumulative.empty else 1.0
    return cumulative / first if first > 0 else cumulative


def get_naive_series(kind: str, start_date: str, end_date: str | None = None) -> pd.Series:
    """
    $1-normalized naive benchmark series.

    kind='60_40': 60% SPY + 40% AGG (calls get_naive_60_40_series).
    kind='spy':   pure SPY total return (calls get_sp500_series).
    """
    if kind == "spy":
        return get_sp500_series(start_date, end_date)
    return get_naive_60_40_series(start_date, end_date)
