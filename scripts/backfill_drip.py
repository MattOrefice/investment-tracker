#!/usr/bin/env python
"""
One-time DRIP backfill script.

Persists DRIP reinvestment lots from inception into the trades table.
Run once after Phase 17 is deployed; re-running is safe (idempotent).
SPAXX is excluded automatically.

Usage:
    TRACKER_MODE=demo     python scripts/backfill_drip.py
    TRACKER_MODE=personal python scripts/backfill_drip.py

Do NOT invoke this from the Streamlit app — CLI only.
"""
import sys
import pathlib
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.db import get_connection
from src.drip import backfill_all_drip_lots
from src.holdings import (
    get_external_cashflow_series,
    get_portfolio_account_id,
    get_portfolio_value_series,
)
from src.prices import get_prices
from src.returns import period_return

INCEPTION = "2025-05-01"
TODAY     = date.today().isoformat()


def _twr_summary(label: str) -> None:
    _acct  = get_portfolio_account_id()
    values = get_portfolio_value_series(INCEPTION, TODAY, account_id=_acct)
    cf     = get_external_cashflow_series(INCEPTION, TODAY, account_id=_acct).reindex(values.index).fillna(0.0)

    print(f"\n  TWR — {label} ({TODAY})")
    print(f"  {'Period':<8} {'Return':>10}")
    print("  " + "-" * 22)
    for p in ["1M", "3M", "YTD", "1Y", "SI"]:
        r = period_return("daily", values, cf, p)
        print(f"  {p:<8} {r * 100:>+10.4f}%")
    print(f"\n  Current portfolio value: ${float(values.iloc[-1]):,.2f}")


def main() -> None:
    print(f"DRIP Backfill  —  {INCEPTION} to {TODAY}")
    print("=" * 60)

    results = backfill_all_drip_lots(start_date=INCEPTION, end_date=TODAY)

    print(f"\n  {'Ticker':<8}  {'New DRIP Lots':>14}")
    print("  " + "-" * 26)
    for ticker, count in sorted(results.items()):
        status = str(count) if count > 0 else "0 (already current)"
        print(f"  {ticker:<8}  {status:>14}")

    total = sum(results.values())
    print(f"\n  Total new DRIP lots inserted: {total}")

    if total > 0:
        # Fetch current price per DRIP ticker for value summary
        print(f"\n  {'Ticker':<8}  {'DRIP Shares':>12}  {'Current Price':>14}  {'DRIP Value':>12}")
        print("  " + "-" * 54)
        look_back = (date.fromisoformat(TODAY) - __import__("datetime").timedelta(days=7)).isoformat()

        with get_connection() as conn:
            drip_rows = conn.execute(
                """SELECT ticker, SUM(shares) as total_drip_shares
                   FROM trades WHERE lot_source='drip'
                   GROUP BY ticker ORDER BY ticker"""
            ).fetchall()

        for r in drip_rows:
            ticker      = r["ticker"]
            drip_shares = float(r["total_drip_shares"])
            try:
                p     = get_prices(ticker, look_back, TODAY)
                price = float(p["adj_close"].fillna(p["close"]).dropna().iloc[-1]) if not p.empty else 0.0
            except Exception:
                price = 0.0
            print(f"  {ticker:<8}  {drip_shares:>12.6f}  ${price:>13.2f}  ${drip_shares * price:>11.2f}")

    _twr_summary("post-backfill")


if __name__ == "__main__":
    main()
