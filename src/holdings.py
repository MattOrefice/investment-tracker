"""Derive holdings and portfolio value from the trades ledger."""
from datetime import date, timedelta
from typing import Optional

import pandas as pd

try:
    from src.db import get_connection
    from src.prices import get_prices, _to_iso
except ImportError:
    from db import get_connection
    from prices import get_prices, _to_iso


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

    # Build price matrix (adj_close, forward-filled for non-trading days)
    price_cols: dict = {}
    for ticker in tickers:
        if ticker == "SPAXX":
            price_cols[ticker] = pd.Series(1.0, index=date_range)
        else:
            try:
                p = get_prices(ticker, start_date, end)
                p.index = pd.to_datetime(p.index)
                # Fill missing adj_close from close (rare, belt-and-braces)
                series = p["adj_close"].fillna(p["close"])
                price_cols[ticker] = series.reindex(date_range).ffill()
            except Exception:
                price_cols[ticker] = pd.Series(dtype=float, index=date_range)

    prices_matrix = pd.DataFrame(price_cols, index=date_range)

    common = [t for t in tickers if t in prices_matrix.columns]
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
