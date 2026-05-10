"""Rebuild the SQLite prices cache for all tracked tickers.

Pulls from the securities table to get every ticker, then calls bulk_refresh()
to fetch from Yahoo Finance from INCEPTION_DATE to today.

Usage:
    python scripts/rebuild_prices_cache.py              # all tickers
    python scripts/rebuild_prices_cache.py --since 2025-01-01   # custom start
    python scripts/rebuild_prices_cache.py --tickers VOO VEA     # specific tickers

SPAXX is excluded (it is handled as a $1.00 cash position, not a price series).
DJP is excluded (delisted 2020; Real Assets benchmark uses VNQ+PDBC instead).
"""
from __future__ import annotations

import argparse
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.db import get_connection
from src.prices import bulk_refresh

INCEPTION_DATE = "2025-05-01"
EXCLUDE = {"SPAXX", "DJP"}


def _all_tickers() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT ticker FROM securities ORDER BY ticker").fetchall()
    return sorted(r["ticker"] for r in rows if r["ticker"] not in EXCLUDE)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild prices cache from Yahoo Finance")
    parser.add_argument("--since",   default=INCEPTION_DATE, metavar="DATE",
                        help=f"Start date for refresh (default: {INCEPTION_DATE})")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER",
                        help="Specific tickers to refresh (default: all in securities table)")
    args = parser.parse_args()

    tickers = args.tickers if args.tickers else _all_tickers()
    print(f"Refreshing {len(tickers)} tickers from {args.since}...")
    bulk_refresh(tickers, args.since)


if __name__ == "__main__":
    main()
