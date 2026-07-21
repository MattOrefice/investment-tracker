"""Phase 39: fetch price history for the six tickers the international split added.

The three tilt holdings (IDHQ, AVIV, AVDV) and their benchmarks (IQLT, EFV, SCZ)
arrive with the split and have no rows in `prices`. Without them:

  - suggest_contributions SKIPS any sleeve whose tickers are all unpriced, so the
    Suggested $ column silently under-sums against the cash entered and the
    invariant assertion in src/rebalance.py:267 fires. The Capital Deployment
    page dies before rendering.
  - the benchmark blend degrades those legs rather than pricing them.

This is a WRITER, not a data dump: it fetches through the same src.prices path
every other ticker uses, so a rebuilt database regenerates these rows instead of
depending on bytes that happen to sit in a committed .db file. (The benchmark
expense ratios are the cautionary case — they live in demo.db and nowhere in
source, so a rebuild drops them and the published figure cannot be reproduced.
Nothing here should be recoverable only from the binary.)

Exposes `migrate_db(db_path)`, so src.bootstrap.run_pending_migrations discovers
and runs it on any rebuild.

Idempotent: get_prices() fetches only the gaps it does not already have, so a
second run against a populated DB issues no network calls and writes nothing.

Short histories are expected and are NOT an error. AVIV launched 2021-09, AVDV
2019-09, while the demo price window opens 2020-01-02. Each ticker is fetched
over the window it can actually cover and its real first date is reported; the
window is never assumed uniform across the six. Every gap here predates the
2025-05-01 portfolio inception, so no reachable figure depends on it.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The six tickers the split introduced: three holdings, three benchmarks.
NEW_TICKERS = ["IDHQ", "AVIV", "AVDV", "IQLT", "EFV", "SCZ"]

# Anchor the window on a ticker that has always been in the book, so the new
# rows span the same range as the rest of the DB rather than a hardcoded date.
_ANCHOR = "VEA"


def _window(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """(start, end) price window to fill, taken from the anchor ticker."""
    row = conn.execute(
        "SELECT MIN(price_date), MAX(price_date) FROM prices WHERE ticker = ?",
        (_ANCHOR,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return row[0], row[1]


def migrate_db(db_path) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        return

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE name = 'prices'")
        if cur.fetchone() is None:
            return
        win = _window(conn)
        if win is None:
            # No anchor history in this DB (fresh/empty) — nothing to align to.
            return
        start, end = win
        # Ask for every ticker, not just the ones with zero rows. get_prices()
        # fetches only the gaps it lacks, so this is still a no-op on a populated
        # DB — but a ticker holding a PARTIAL range (an interrupted run, or a
        # one-off fetch during debugging) heals instead of being skipped forever
        # by an existence check that mistakes "has rows" for "has coverage".
        missing = list(NEW_TICKERS)
    finally:
        conn.close()

    # src.prices writes through get_connection(), which reads src.db.DB_PATH at
    # call time. Point it at the DB we were handed so the rows land there, and
    # restore it afterwards — run_pending_migrations may be invoked for a path
    # other than the process-wide default.
    from src import db as _db
    from src.prices import get_prices

    original = _db.DB_PATH
    _db.DB_PATH = str(db_path)
    try:
        for ticker in missing:
            try:
                df = get_prices(ticker, start, end)
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not
                print(f"  {ticker}: FETCH FAILED ({exc}) — left unpriced")
                continue
            if df.empty:
                print(f"  {ticker}: no data returned — left unpriced")
                continue
            first = str(df.index[0])[:10]
            note = "" if first <= start else f"  (history starts {first})"
            print(f"  {ticker}: {len(df)} rows{note}")
    finally:
        _db.DB_PATH = original


def main() -> None:
    for name in ("demo.db", "tracker.db"):
        path = ROOT / "data" / name
        if path.exists():
            print(f"\nFetching Phase 39 prices into {path} ...")
            migrate_db(path)


if __name__ == "__main__":
    main()
