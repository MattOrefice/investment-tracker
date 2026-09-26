"""Lock every completed quarter of the demo book into data/demo.db, inputs included.

The public demo rebuilds from the repo: each fresh container seeds its runtime cache
from the committed demo.db (src.db, #373) and captured a quarter's lock the first
time a report asked for it, reading whatever factor and CAPE files were committed
THEN. So a committed refresh of those files restated every quarter the demo had
not yet locked in that container (#382). Committing the locks makes a completed
quarter's report depend only on the repo as of its lock.

Run this BEFORE committing a market-data refresh, so the locks hold the inputs the
quarters were reported on. It never re-locks: a quarter already locked is left
exactly as it is. A quarter whose inputs were pending when locked is completed by
the report path (cache.complete_quarter_inputs), not here.

Targets data/demo.db explicitly (never get_db_path), offline: the committed prices
and dividends only, no fetch. main() only, so bootstrap never runs it.
"""
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))


def _completed_quarters(first_trade: date, today: date) -> "list[str]":
    out = []
    y, q = first_trade.year, (first_trade.month - 1) // 3 + 1
    while True:
        end_month = q * 3
        end = date(y, end_month, 31 if end_month in (3, 12) else 30)
        if end >= today:
            return out
        out.append(f"{y}Q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1


def main() -> int:
    os.environ.setdefault("DEMO_DAILY_FETCH", "0")
    import src.db as db
    import src.prices as prices
    from src.cache import capture_quarter_snapshot, get_quarter_snapshot

    db.DB_PATH = DEMO_DB
    db._migrated_paths = set()
    db._RUNTIME_CACHE = None
    prices.set_gap_fetch(False)
    prices._SESSION.get = lambda *a, **k: (_ for _ in ()).throw(
        OSError("offline: demo locks read committed data only"))

    with db.get_connection() as conn:
        first = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()[0]
    if not first:
        print("ABORT: demo.db holds no trades.")
        return 1
    quarters = _completed_quarters(date.fromisoformat(first), date.today())
    locked, kept = [], []
    for qid in quarters:
        if get_quarter_snapshot(qid)[0] is not None:
            kept.append(qid)
            continue
        snap, _ = capture_quarter_snapshot(qid)
        pend = f", pending {sorted(snap.inputs_pending)}" if snap.inputs_pending else ""
        print(f"locked {qid}: {len(snap.adj_close.columns)} price series, inputs "
              f"{sorted(snap.inputs)}{pend}, gaps {len(snap.gaps)}")
        locked.append(qid)
    if kept:
        print(f"already locked, left as they are: {', '.join(kept)}")
    print(f"{len(locked)} quarter(s) locked into {DEMO_DB.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
