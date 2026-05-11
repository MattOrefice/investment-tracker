#!/usr/bin/env python
"""
One-time DRIP payment-date migration script (Phase 18).

Updates existing DRIP lots from ex-dividend-date pricing to payment-date
pricing, matching Fidelity's actual DRIP execution mechanics.

Idempotent: a lot is skipped if its trade_date is NOT in the dividends
table (meaning it has already been migrated to payment_date, which is
not an ex-date).

Usage:
    TRACKER_MODE=demo     python scripts/migrate_drip_to_payment_date.py
    TRACKER_MODE=personal python scripts/migrate_drip_to_payment_date.py
"""
import sys
import pathlib
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd

from src.db import get_connection
from src.drip import derive_payment_date
from src.prices import get_prices

TODAY = date.today().isoformat()


def _adj_close_on_or_before(ticker: str, target_date: date, look_back_days: int = 7) -> float | None:
    """Return adj_close for ticker on or just before target_date."""
    start = (target_date - timedelta(days=look_back_days)).isoformat()
    try:
        p = get_prices(ticker, start, target_date.isoformat())
        if p.empty:
            return None
        series = p["adj_close"].fillna(p["close"]).dropna()
        return float(series.iloc[-1]) if not series.empty else None
    except Exception:
        return None


def _is_ex_date(ticker: str, trade_date: str, conn) -> bool:
    """Return True if trade_date appears in dividends table as an ex_date for ticker."""
    row = conn.execute(
        "SELECT 1 FROM dividends WHERE ticker = ? AND ex_date = ?",
        (ticker, trade_date),
    ).fetchone()
    return row is not None


def run_migration(dry_run: bool = False) -> dict[str, dict]:
    """
    Migrate DRIP lots from ex-date to payment-date pricing.

    Returns per-ticker summary dicts with keys:
        migrated, skipped, share_delta_total, basis_delta_total, errors
    """
    with get_connection() as conn:
        drip_rows = conn.execute(
            """SELECT trade_id, ticker, trade_date, shares, price
               FROM trades WHERE lot_source = 'drip'
               ORDER BY ticker, trade_date""",
        ).fetchall()

    summary: dict[str, dict] = {}

    with get_connection() as conn:
        for row in drip_rows:
            trade_id   = int(row["trade_id"])
            ticker     = row["ticker"]
            trade_date = row["trade_date"]   # current value: ex_date (before migration)
            old_shares = float(row["shares"])
            old_price  = float(row["price"])

            if ticker not in summary:
                summary[ticker] = {
                    "migrated":          0,
                    "skipped":           0,
                    "share_delta_total": 0.0,
                    "basis_delta_total": 0.0,
                    "errors":            [],
                }
            s = summary[ticker]

            # Idempotency: only migrate lots whose trade_date is a known ex_date
            if not _is_ex_date(ticker, trade_date, conn):
                s["skipped"] += 1
                continue

            ex_date      = date.fromisoformat(trade_date)
            payment_date = derive_payment_date(ex_date, ticker)

            new_price = _adj_close_on_or_before(ticker, payment_date)
            if new_price is None or new_price <= 0:
                s["errors"].append(
                    f"  {ticker} {trade_date}: no price found for payment_date {payment_date}"
                )
                s["skipped"] += 1
                continue

            # Preserve cash dividend total; recompute share count at payment_date price
            cash_div   = old_shares * old_price
            new_shares = cash_div / new_price

            share_delta = new_shares - old_shares
            basis_delta = new_price  - old_price

            s["share_delta_total"] += share_delta
            s["basis_delta_total"] += basis_delta
            s["migrated"] += 1

            if not dry_run:
                conn.execute(
                    """UPDATE trades
                       SET trade_date = ?, shares = ?, price = ?
                       WHERE trade_id = ?""",
                    (payment_date.isoformat(), new_shares, new_price, trade_id),
                )

    return summary


def main(dry_run: bool = False) -> None:
    label = "DRY RUN — " if dry_run else ""
    print(f"{label}DRIP Payment-Date Migration  —  Phase 18")
    print("=" * 60)

    summary = run_migration(dry_run=dry_run)

    total_migrated = sum(s["migrated"] for s in summary.values())
    total_skipped  = sum(s["skipped"]  for s in summary.values())
    total_errors   = sum(len(s["errors"]) for s in summary.values())

    print(f"\n  {'Ticker':<8}  {'Migrated':>9}  {'Skipped':>8}  {'Share d':>12}  {'Basis d':>10}")
    print("  " + "-" * 56)
    for ticker, s in sorted(summary.items()):
        print(
            f"  {ticker:<8}  {s['migrated']:>9}  {s['skipped']:>8}  "
            f"{s['share_delta_total']:>+12.6f}  {s['basis_delta_total']:>+10.4f} USD"
        )
        for err in s["errors"]:
            print(f"    ERROR: {err}")

    print(f"\n  Total: {total_migrated} migrated, {total_skipped} skipped, {total_errors} errors")

    if total_errors > 0:
        print("\n  WARNING: some lots could not be migrated — check errors above.")

    if not dry_run:
        # Post-migration verification: total shares by ticker
        print("\n  Post-migration share totals:")
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT ticker, SUM(shares) as total FROM trades WHERE lot_source='drip' GROUP BY ticker ORDER BY ticker"
            ).fetchall()
        for r in rows:
            print(f"    {r['ticker']:<8}  {float(r['total']):.6f} shares")

    print()
    if dry_run:
        print("  Dry run complete — no changes written.")
    else:
        print("  Migration complete.")
        print("  Re-run with dry_run=True or re-run the script to verify idempotency.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
