"""Phase 40: fetch price history for the two international tilt-sleeve controls.

The Factor Profile page regresses each developed-international tilt fund (IDHQ,
AVIV, AVDV) against a passive, Canada-matched control fund and renders the
control's residual as a visible number. Two of those controls — IVLU (iShares
MSCI Intl Value Factor) and ISVL (iShares International Developed Small Cap Value
Factor) — are new to the book and have no rows in `prices`. The third control,
IQLT, is already the Quality-sleeve SAA benchmark and was priced by the Phase 39
migration.

Without prices, run_intl_tilt_regressions falls through to a live Yahoo fetch at
render time, which (a) is slow on the public demo, and (b) appends rows into the
committed demo.db during local dev — the exact drift the committed-DB convention
avoids. This WRITER pre-warms them through the same src.prices path every other
ticker uses, so a rebuilt database regenerates these rows rather than depending
on bytes in a committed .db file.

The controls are deliberately NOT `securities` rows (a security row would make
sleeve_holdings() treat a control as a held instrument), so this warms prices
only. IVLU launched 2015-04, ISVL 2021-03 — both predate the 2025-05-01 demo
inception, so full window coverage is available and no reachable figure depends
on the pre-inception gap.

Exposes `migrate_db(db_path)`, so src.bootstrap.run_pending_migrations discovers
and runs it on any rebuild. Idempotent: get_prices() fetches only the gaps it
lacks, so a second run issues no network calls and writes nothing.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The two controls introduced by Phase 40. IQLT is already priced (Phase 39).
NEW_TICKERS = ["IVLU", "ISVL"]

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
        # Ask for every control, not just those with zero rows. get_prices()
        # fetches only the gaps it lacks, so this is still a no-op on a populated
        # DB — but a ticker holding a PARTIAL range (an interrupted run) heals
        # instead of being skipped by an existence check that mistakes "has rows"
        # for "has coverage".
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
            print(f"\nFetching Phase 40 control prices into {path} ...")
            migrate_db(path)


if __name__ == "__main__":
    main()
