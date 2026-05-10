"""Derive holdings and portfolio value from the trades ledger."""
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from src.db import get_connection
from src.prices import get_prices, get_dividends, _to_iso


def get_holdings_on_date(date_str: str) -> pd.DataFrame:
    """
    Return net shares per ticker as of date_str (inclusive).
    Index: ticker. Column: net_shares.
    SPAXX 'shares' represent dollar amount (NAV is always $1).
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT ticker,
                      SUM(CASE WHEN LOWER(action) = 'buy'
                               THEN shares ELSE -shares END) AS net_shares
               FROM trades
               WHERE trade_date <= ?
               GROUP BY ticker""",
            (date_str,),
        ).fetchall()

    if not rows:
        return pd.DataFrame(columns=["net_shares"])

    df = pd.DataFrame([dict(r) for r in rows]).set_index("ticker")
    return df[df["net_shares"] > 0].copy()


def get_portfolio_value_series(
    start_date: str,
    end_date: Optional[str] = None,
) -> pd.Series:
    """
    Return daily total portfolio market value over [start_date, end_date].
    Uses adj_close for ETF prices (total-return basis).
    SPAXX is valued at $1/share.
    Weekends / holidays are forward-filled from the previous trading day.
    """
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    with get_connection() as conn:
        trade_rows = conn.execute(
            """SELECT trade_date, ticker, action, shares
               FROM trades
               WHERE trade_date <= ?
               ORDER BY trade_date, trade_id""",
            (end,),
        ).fetchall()

    if not trade_rows:
        return pd.Series(0.0, index=date_range)

    trades_df = pd.DataFrame([dict(r) for r in trade_rows])
    trades_df["trade_date"] = pd.to_datetime(trades_df["trade_date"])
    trades_df["signed"] = trades_df.apply(
        lambda r: r["shares"] if r["action"].lower() == "buy" else -r["shares"],
        axis=1,
    )

    # Build daily share-change matrix from first-ever trade date
    first_trade_date = trades_df["trade_date"].min()
    full_range = pd.date_range(
        start=min(first_trade_date, pd.Timestamp(start_date)),
        end=end,
        freq="D",
    )

    daily_changes = (
        trades_df.groupby(["trade_date", "ticker"])["signed"]
        .sum()
        .unstack(fill_value=0)
        .reindex(full_range, fill_value=0)
    )

    # Cumulative holdings at each date
    holdings_matrix = daily_changes.cumsum().clip(lower=0)

    # Slice to requested range (full_range ⊇ date_range since start ≤ start_date)
    holdings_matrix = holdings_matrix.reindex(date_range)

    tickers = holdings_matrix.columns.tolist()

    # Build price matrix (adj_close, forward-filled for non-trading days).
    # SPAXX NAV is always $1, but it accrues T-bill yield.  We proxy its total
    # return through BIL (SPDR Bloomberg 1-3 Month T-Bill ETF), whose adj_close
    # already reflects reinvested T-bill yield.  BIL is normalized to start at
    # $1.00 so that shares × price still equals dollars of SPAXX held.
    price_cols: dict = {}
    for ticker in tickers:
        if ticker == "SPAXX":
            try:
                p = get_prices("BIL", start_date, end)
                p.index = pd.to_datetime(p.index)
                series = p["adj_close"].fillna(p["close"])
                p0 = series.first_valid_index()
                if p0 is not None and float(series[p0]) > 0:
                    series = series / float(series[p0])  # normalize to $1.00
                price_cols[ticker] = series.reindex(date_range).ffill()
            except Exception:
                price_cols[ticker] = pd.Series(1.0, index=date_range)
        else:
            try:
                p = get_prices(ticker, start_date, end)
                p.index = pd.to_datetime(p.index)
                series = p["adj_close"].fillna(p["close"])
                price_cols[ticker] = series.reindex(date_range).ffill()
            except Exception:
                price_cols[ticker] = pd.Series(dtype=float, index=date_range)

    prices_matrix = pd.DataFrame(price_cols, index=date_range)

    common = [t for t in tickers if t in prices_matrix.columns]

    # Apply DRIP: reinvest dividends into additional shares.
    # Dividend income is already captured in adj_close RATIOS (TWR is unaffected),
    # but adding reinvested shares makes the absolute portfolio value reconcile to
    # $inception × (1 + TWR) by offsetting the adj_close backward-adjustment gap.
    start_iso = str(date_range[0].date())
    for ticker in [t for t in common if t != "SPAXX"]:
        try:
            divs = get_dividends(ticker, start_iso, end)
        except Exception:
            continue
        if divs.empty:
            continue

        for ex_date, div_amount in divs.items():
            ex_ts = pd.Timestamp(ex_date)

            # Shares held just BEFORE this dividend (previous calendar day)
            prev_dates = holdings_matrix.index[holdings_matrix.index < ex_ts]
            if prev_dates.empty:
                continue
            if ticker not in holdings_matrix.columns:
                continue
            shares_before = float(holdings_matrix.loc[prev_dates[-1], ticker])
            if shares_before <= 0:
                continue

            # Price on or before ex_date (adj_close, forward-filled)
            if ticker not in prices_matrix.columns:
                continue
            avail = prices_matrix[ticker].loc[
                prices_matrix[ticker].index <= ex_ts
            ].dropna()
            if avail.empty:
                continue
            price = float(avail.iloc[-1])
            if price <= 0:
                continue

            reinvested = div_amount * shares_before / price

            # Add reinvested shares to all dates from ex_date onward
            on_or_after = holdings_matrix.index >= ex_ts
            holdings_matrix.loc[on_or_after, ticker] = (
                holdings_matrix.loc[on_or_after, ticker] + reinvested
            )

    return (holdings_matrix[common] * prices_matrix[common]).sum(axis=1)


def get_sleeve_weights_on_date(date_str: str) -> pd.DataFrame:
    """
    Return actual vs. target weight per sleeve as of date_str.
    Columns: Market Value, Actual Weight, Target Weight, Drift (actual - target).
    Index: sleeve name.
    Uses close price (unadjusted) for current market value.
    """
    holdings = get_holdings_on_date(date_str)
    if holdings.empty:
        return pd.DataFrame()

    with get_connection() as conn:
        sec_rows = conn.execute(
            """SELECT s.ticker, ac.name AS sleeve
               FROM securities s
               JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id""",
        ).fetchall()
        ticker_to_sleeve = {r["ticker"]: r["sleeve"] for r in sec_rows}

        target_rows = conn.execute(
            """SELECT name, target_weight
               FROM asset_classes WHERE parent_id IS NOT NULL""",
        ).fetchall()
        target_weights = {r["name"]: r["target_weight"] for r in target_rows}

    # Look back up to 5 trading days to find the most recent price
    look_back = (date.fromisoformat(date_str) - timedelta(days=7)).isoformat()

    values_by_sleeve: dict[str, float] = {}
    for ticker, row in holdings.iterrows():
        sleeve = ticker_to_sleeve.get(ticker, "Unknown")
        shares = float(row["net_shares"])

        if ticker == "SPAXX":
            try:
                inception = "2025-05-01"
                p = get_prices("BIL", inception, date_str)
                if not p.empty:
                    bil = p["adj_close"].fillna(p["close"])
                    p0_idx = bil.first_valid_index()
                    if p0_idx is not None and float(bil[p0_idx]) > 0:
                        bil = bil / float(bil[p0_idx])
                    price = float(bil.ffill().iloc[-1])
                else:
                    price = 1.0
            except Exception:
                price = 1.0
        else:
            try:
                p = get_prices(ticker, look_back, date_str)
                price = float(p["close"].iloc[-1]) if not p.empty else 0.0
            except Exception:
                price = 0.0

        values_by_sleeve[sleeve] = values_by_sleeve.get(sleeve, 0.0) + shares * price

    total = sum(values_by_sleeve.values())
    if total == 0:
        return pd.DataFrame()

    rows = []
    for sleeve, mv in sorted(values_by_sleeve.items(), key=lambda x: -x[1]):
        target = target_weights.get(sleeve, 0.0)
        actual = mv / total
        rows.append(
            {
                "Sleeve":         sleeve,
                "Market Value":   round(mv, 2),
                "Actual Weight":  round(actual, 4),
                "Target Weight":  round(target, 4),
                "Drift":          round(actual - target, 4),
            }
        )

    return pd.DataFrame(rows).set_index("Sleeve")


def get_inception_date() -> str:
    """Return ISO date of the first recorded trade, falling back to '2025-05-01'."""
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
        return row[0] if row and row[0] else "2025-05-01"
    except Exception:
        return "2025-05-01"
