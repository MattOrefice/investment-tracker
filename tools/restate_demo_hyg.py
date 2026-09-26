"""Correct the demo's committed quarter locks whose HYG input stopped short (#386).

Until #386 the FI regression's credit proxy, HYG, came from
data/cache/prices_hyg.parquet, a committed file that ended 2026-05-05 while nothing
refreshed it, and a quarter lock took it as read with no coverage gate. A quarter
ending after that date locked a proxy held flat for its last weeks. This re-reads
HYG from the price layer through each such quarter's end and records the
correction, which the report's cover states. A lock whose HYG already covered its
quarter is left exactly as it is, and nothing else in any lock changes.

Targets data/demo.db explicitly (never get_db_path), offline: the committed prices
only, no fetch. main() only, so bootstrap never runs it.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ.setdefault("DEMO_DAILY_FETCH", "0")
    import src.db as db
    import src.prices as prices
    from src.asof import today_et
    from src.cache import restate_short_hyg

    db.DB_PATH = DEMO_DB
    db._migrated_paths = set()
    db._RUNTIME_CACHE = None
    prices.set_gap_fetch(False)
    prices._SESSION.get = lambda *a, **k: (_ for _ in ()).throw(
        OSError("offline: the correction reads committed data only"))

    with db.get_connection() as conn:
        quarters = [r[0] for r in conn.execute(
            "SELECT quarter_id FROM quarter_snapshots ORDER BY quarter_id")]
    today = today_et()
    fixed = []
    for qid in quarters:
        got = restate_short_hyg(qid, today)
        if got is None:
            print(f"{qid}: HYG covered the quarter; left as it is")
        else:
            print(f"{qid}: HYG restated, was through {got[0]}, now through {got[1]}")
            fixed.append(qid)
    print(f"{len(fixed)} of {len(quarters)} lock(s) corrected in {DEMO_DB.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
