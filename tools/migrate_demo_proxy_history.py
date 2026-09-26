"""Backfill the demo's IWB and IWF price history to match IWM and IWD (audit item 5).

The Macro page's factor-regime panels rank a trailing-12-month ETF spread (IWM − IWB
for size, IWD − IWF for style) against its history. In data/demo.db, IWM and IWD are
priced from 2000-05-26 but IWB and IWF only from 2025-04-04, so their 12-month spreads
begin in April 2026 and the demo ranked an IWM − IWB reading against a few months:
"+0.6%, 2nd percentile of full history" beside HML's +17.9% at the 64th. The demo's daily
refresh only fetches FORWARD from each ticker's newest row, so nothing would ever fill
the gap. This fetches each ticker's settled closes (and the dividends the same response
carries) from IWM's first date through the day before its first stored row.

Targets data/demo.db explicitly (never get_db_path). Needs the network. Idempotent: a
ticker whose history already starts on or before the target date is left alone. main()
only, so bootstrap never runs it.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

TICKERS = ("IWB", "IWF")
REFERENCE = "IWM"     # the long leg the proxy spread is taken against


def main() -> int:
    import src.db as db
    from src import prices

    db.DB_PATH = DEMO_DB
    db._migrated_paths = set()
    db._RUNTIME_CACHE = None

    with db.get_connection() as conn:
        target = conn.execute("SELECT MIN(price_date) FROM prices WHERE ticker = ?",
                              (REFERENCE,)).fetchone()[0]
    if not target:
        print(f"ABORT: {REFERENCE} has no prices in {DEMO_DB.name}.")
        return 1
    for t in TICKERS:
        with db.get_connection() as conn:
            first, n0 = conn.execute("SELECT MIN(price_date), COUNT(*) FROM prices "
                                     "WHERE ticker = ?", (t,)).fetchone()
        if first and first <= target:
            print(f"{t}: history already starts {first}; left as it is")
            continue
        end = (date.fromisoformat(first) - timedelta(days=1)).isoformat() if first else None
        frame = prices.fetch_prices(t, target, end or date.today().isoformat())
        with db.get_connection() as conn:
            first2, n1 = conn.execute("SELECT MIN(price_date), COUNT(*) FROM prices "
                                      "WHERE ticker = ?", (t,)).fetchone()
        print(f"{t}: fetched {len(frame)} closes {target}..{end}; rows {n0} -> {n1}, "
              f"now from {first2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
