"""Check that every ticker's committed price history ends on the same date (#406 item 8).

The demo's IWB and IWF closes ended 2026-06-08 while IWM, IWD and the holdings ran to
2026-07-20, and nothing said so except one panel's "ETF proxy as of" caption. The
quarterly close-out's snapshot advance brings every ticker through the quarter's end;
this is the step that confirms it did, so no ticker lags silently again.

A series that trades every day (BTC-USD) is compared on its newest WEEKDAY close. On
a quarter that ends on a weekend it holds a Saturday or Sunday row that no
exchange-traded fund can have, and that is not a lag.

Reads data/demo.db by default, read-only; pass a path to check another book. Prints
each end date with the tickers that end on it, and exits 1 unless there is exactly
one.

Usage:  python tools/check_price_histories.py [path/to/book.db]
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"


def history_ends(db_path: Path) -> "dict[str, list[str]]":
    """{end date: [tickers]}: each ticker's newest weekday close in ``db_path``."""
    con = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT ticker, price_date FROM prices ORDER BY ticker, price_date")
        newest: "dict[str, str]" = {}
        for ticker, day in rows:
            if date.fromisoformat(day).weekday() < 5:
                newest[ticker] = day
    finally:
        con.close()
    ends: "dict[str, list[str]]" = {}
    for ticker, day in sorted(newest.items()):
        ends.setdefault(day, []).append(ticker)
    return dict(sorted(ends.items()))


def main(argv: "list[str]") -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEMO_DB
    ends = history_ends(path)
    for day, tickers in ends.items():
        print(f"{day}: {len(tickers)} ticker(s): {', '.join(tickers)}")
    if len(ends) == 1:
        print(f"Every ticker's history in {path.name} ends on {next(iter(ends))}.")
        return 0
    print(f"NOT ALIGNED: {path.name}'s price histories end on {len(ends)} different dates. "
          f"Every ticker before {max(ends)} lags.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
