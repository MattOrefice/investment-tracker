"""DRIP lot persistence — compute and persist DRIP reinvestment lots from inception.

SPAXX EXCLUSION: SPAXX is a money market sweep. Interest accrues to the cash
balance, not as new shares. SPAXX is excluded from all backfill operations.
Its return is proxied through BIL adj_close in get_portfolio_value_series and
is unchanged by this module.

PDBC NOTE: PDBC distributions have mixed tax character (qualified dividend,
return of capital, ordinary income). Phase 17 treats PDBC identically to other
ETFs — distributions are reinvested at payment-date adj_close. Tax-character
splitting for PDBC cost basis is deferred to a future phase.

PRICE CONVENTION: DRIP share counts and cost_basis_per_share use adj_close
(total-return basis), consistent with the TWR methodology used throughout.
This ensures get_portfolio_value_series produces identical results whether
DRIP is computed in-memory or from persisted lots.

PAYMENT DATE: DRIP executes on payment_date (the date Fidelity actually
reinvests the dividend), not on ex_dividend_date. Payment date is derived
as ex_date + PAYMENT_DATE_OFFSET_TRADING_DAYS trading days (weekday arithmetic,
Saturday/Sunday skipped). NYSE holidays are not currently modeled — a future
phase may add pandas_market_calendars for exact holiday-aware arithmetic.
"""
from __future__ import annotations

import warnings
from datetime import date, timedelta

import pandas as pd

from src.db import get_connection
from src.prices import get_dividends, get_prices

_SPAXX_TICKER = "SPAXX"

# Trading days to add to ex_dividend_date to derive payment_date.
# Vanguard, iShares, Schwab, and Invesco ETFs in the SAA typically execute
# DRIP reinvestment 1–3 trading days after ex-date; +2 is the adopted default.
PAYMENT_DATE_OFFSET_TRADING_DAYS = 2

# Per-ticker overrides if a specific fund is known to deviate from the default.
# Keys are ticker symbols; values are integer trading-day offsets.
_PAYMENT_DATE_OVERRIDES: dict[str, int] = {}


def derive_payment_date(ex_date: date, ticker: str = "") -> date:
    """Return the DRIP payment date for a distribution with the given ex_date.

    Adds PAYMENT_DATE_OFFSET_TRADING_DAYS (or a per-ticker override) trading
    days to ex_date, skipping Saturday and Sunday. NYSE holidays are not
    modeled (no pandas_market_calendars dependency).

    Args:
        ex_date: The ex-dividend date of the distribution.
        ticker:  Ticker symbol, used to look up per-ticker offset overrides.

    Returns:
        The derived payment date as a datetime.date.
    """
    offset = _PAYMENT_DATE_OVERRIDES.get(ticker, PAYMENT_DATE_OFFSET_TRADING_DAYS)
    d = ex_date
    added = 0
    while added < offset:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon=0 … Fri=4
            added += 1
    return d


def fetch_distributions(
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    Return historical distributions for ticker as a DataFrame.

    Columns: ex_date (datetime.date), dividend_per_share (float).
    Rows sorted chronologically. Returns empty DataFrame if none in window.
    Pulls from the dividends table (already populated via fetch_prices).
    """
    s = get_dividends(ticker, start_date, end_date)
    if s.empty:
        return pd.DataFrame(columns=["ex_date", "dividend_per_share"])
    df = pd.DataFrame({"ex_date": s.index, "dividend_per_share": s.values})
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
    return df.sort_values("ex_date").reset_index(drop=True)


def compute_drip_lots(
    ticker: str,
    initial_shares: pd.Series,
    distributions: pd.DataFrame,
    price_history: pd.Series,
) -> list[dict]:
    """
    Compute DRIP reinvestment lots. Pure function — no DB access.

    For each distribution event, in chronological order:
      1. shares_before = initial shares held strictly BEFORE ex_date
                         + cumulative DRIP shares from prior events
      2. payment_date  = derive_payment_date(ex_date, ticker)
      3. price         = adj_close on or before payment_date
      4. new_shares    = (shares_before × dividend_per_share) / price
      5. cumulative_drip += new_shares  (compounding for subsequent events)

    The dividend entitlement is determined by shares held on ex_date (Fidelity
    pays based on the record date, which is T+1 from ex_date). Reinvestment
    executes at the payment_date closing price, matching Fidelity DRIP mechanics.

    Events where shares_before == 0 are skipped. This correctly excludes
    distributions dated on or before inception when no portfolio existed yet.

    Args:
        ticker: ticker symbol, stored on each returned lot dict.
        initial_shares: cumulative NON-DRIP shares indexed by datetime.date,
            built from buy/sell trades with lot_source != 'drip'.
        distributions: DataFrame with ex_date (datetime.date) and
            dividend_per_share (float), sorted chronologically.
        price_history: adj_close Series indexed by datetime.date.

    Returns:
        List of lot dicts with keys: ticker, ex_date (datetime.date),
        purchase_date (datetime.date, = payment_date), shares (float),
        cost_basis_per_share (float), lot_source='drip'.
        Empty list if no qualifying events.
    """
    if distributions.empty:
        return []

    cumulative_drip = 0.0
    lots: list[dict] = []

    for _, row in distributions.iterrows():
        ex_date = row["ex_date"]  # datetime.date
        dps     = float(row["dividend_per_share"])

        # Shares held strictly before ex_date (dividend entitlement date)
        before = initial_shares[initial_shares.index < ex_date]
        shares_initial = float(before.iloc[-1]) if not before.empty else 0.0
        shares_before  = shares_initial + cumulative_drip

        if shares_before <= 0:
            continue

        # Derive payment_date and look up adj_close on or before it
        payment_date = derive_payment_date(ex_date, ticker)
        avail = price_history[price_history.index <= payment_date].dropna()
        if avail.empty:
            continue
        price = float(avail.iloc[-1])
        if price <= 0:
            continue

        cash_div   = shares_before * dps
        new_shares = cash_div / price
        cumulative_drip += new_shares

        lots.append(
            {
                "ticker":               ticker,
                "ex_date":              ex_date,
                "purchase_date":        payment_date,
                "shares":               new_shares,
                "cost_basis_per_share": price,
                "lot_source":           "drip",
            }
        )

    return lots


def persist_drip_lots(ticker: str, lots: list[dict]) -> int:
    """
    Insert DRIP lots into the trades table.

    Idempotent: a lot is skipped if a drip row already exists for the same
    (ticker, trade_date). Returns count of new rows inserted.
    """
    if not lots:
        return 0

    with get_connection() as conn:
        row = conn.execute(
            "SELECT account_id FROM accounts WHERE is_active=1 ORDER BY account_id LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("No active account found in accounts table.")
        account_id = int(row["account_id"])

        inserted = 0
        for lot in lots:
            pd = lot["purchase_date"]
            date_str = pd.isoformat() if hasattr(pd, "isoformat") else str(pd)

            already = conn.execute(
                "SELECT 1 FROM trades WHERE ticker=? AND trade_date=? AND lot_source='drip'",
                (ticker, date_str),
            ).fetchone()
            if already:
                continue

            conn.execute(
                """INSERT INTO trades
                   (account_id, ticker, trade_date, action, shares, price,
                    fees, notes, lot_source)
                   VALUES (?, ?, ?, 'buy', ?, ?, 0, 'DRIP', 'drip')""",
                (
                    account_id,
                    ticker,
                    date_str,
                    float(lot["shares"]),
                    float(lot["cost_basis_per_share"]),
                ),
            )
            inserted += 1

    return inserted


def backfill_all_drip_lots(
    start_date: str = "2025-05-01",
    end_date: str | None = None,
) -> dict[str, int]:
    """
    Backfill DRIP lots for all holdings from start_date to end_date (default: today).

    Skips SPAXX (money market sweep; no DRIP reinvestment as shares).
    Safe to re-run: persist_drip_lots skips already-inserted lots.

    Returns dict mapping ticker → count of new lots inserted.
    """
    end = end_date or date.today().isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM trades WHERE ticker != ? ORDER BY ticker",
            (_SPAXX_TICKER,),
        ).fetchall()
    tickers = [r["ticker"] for r in rows]

    results: dict[str, int] = {}

    for ticker in tickers:
        distributions = fetch_distributions(ticker, start_date, end)
        if distributions.empty:
            warnings.warn(
                f"{ticker}: no distributions found in {start_date}–{end}; skipping."
            )
            results[ticker] = 0
            continue

        # Cumulative non-DRIP shares timeline (buys minus sells, excluding drip lots)
        with get_connection() as conn:
            trade_rows = conn.execute(
                """SELECT trade_date,
                          SUM(CASE WHEN LOWER(action)='buy' THEN shares ELSE -shares END) AS net
                   FROM trades
                   WHERE ticker = ?
                     AND (lot_source IS NULL OR lot_source != 'drip')
                   GROUP BY trade_date
                   ORDER BY trade_date""",
                (ticker,),
            ).fetchall()

        if not trade_rows:
            results[ticker] = 0
            continue

        dates          = [date.fromisoformat(r["trade_date"]) for r in trade_rows]
        nets           = [float(r["net"]) for r in trade_rows]
        initial_shares = pd.Series(nets, index=dates).cumsum()

        try:
            price_df = get_prices(ticker, start_date, end)
        except Exception as exc:
            warnings.warn(f"{ticker}: price fetch failed ({exc}); skipping.")
            results[ticker] = 0
            continue

        price_history       = price_df["adj_close"].fillna(price_df["close"])
        price_history.index = pd.to_datetime(price_history.index).date

        lots = compute_drip_lots(ticker, initial_shares, distributions, price_history)
        n    = persist_drip_lots(ticker, lots)
        results[ticker] = n

    return results
