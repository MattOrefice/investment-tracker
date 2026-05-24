"""Benchmark series construction for performance attribution."""
from datetime import date

import pandas as pd

from src.db import get_connection
from src.prices import get_prices

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
    """
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    result: dict[str, pd.Series] = {}

    for sleeve, components in _SLEEVE_BENCHMARKS.items():
        # Build blended price series for multi-component sleeves
        sleeve_series = pd.Series(0.0, index=date_range)
        total_frac = 0.0

        for ticker, frac in components:
            p = _get_price_series(ticker, start_date, end, col="adj_close")
            if p.first_valid_index() is None:
                continue
            p0 = float(p.ffill().iloc[0])
            if p0 > 0:
                # Normalized so each component starts at frac (proportional weight)
                sleeve_series += (p.ffill() / p0) * frac
                total_frac += frac

        if total_frac > 0:
            sleeve_series = sleeve_series / total_frac   # renormalize if any component failed
        else:
            sleeve_series = pd.Series(1.0, index=date_range)

        result[sleeve] = sleeve_series - 1.0   # convert to return

    return pd.DataFrame(result, index=date_range)


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


# Convenience: scalar benchmark return for a single sleeve over a period
def sleeve_benchmark_return(sleeve: str, start_date: str, end_date: str) -> float:
    """Return the total return of the benchmark for a single sleeve over the period."""
    df = get_sleeve_benchmark_returns(start_date, end_date)
    if sleeve not in df.columns or df.empty:
        return 0.0
    # Last non-NaN value
    series = df[sleeve].dropna()
    return float(series.iloc[-1]) if not series.empty else 0.0
