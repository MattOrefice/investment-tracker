"""Phase 41: carry the provenance-sourced benchmark expense ratios (and the
DJP->DBC Real Assets benchmark, and the IQLT/EFV/SCZ security_type fix) into an
existing database.

WHY THIS EXISTS. The committed demo.db carried expense_ratio values for the 10
benchmarks (SPY 0.000945, EFA 0.0032, …) that existed NOWHERE in source —
seed_securities inserted benchmarks with expense_ratio=None — so a fresh reseed
produced NULL for all of them and the Research page's fee-savings headline
collapsed from ~18 bps to 0.00. The values lived only in the committed binary,
with no source, no date, and no regeneration path. seed_securities.BENCHMARKS now
records each ER inline with its issuer source and an as-of date; this migration
applies those same sourced values (imported from seed_securities, so there is one
source of truth) to a database that predates them.

It also:
  - replaces the stale DJP benchmark row with DBC (DJP, the iPath Bloomberg
    Commodity ETN, delisted May 2020; the Real Assets benchmark is DBC — see
    src/benchmarks.py), and
  - sets security_type='benchmark' on IQLT/EFV/SCZ, whose rows carry NULL type in
    demo.db (a split-migration artifact). No consumer filters security_type=
    'benchmark' today, so this is latent data hygiene, not a live fix — but it
    stops the first such filter from silently dropping the three intl benchmarks.

Idempotent: every write is guarded on a value actually differing, so a second run
reports 0 changes. Exposes migrate_db(db_path); run_pending_migrations discovers
and runs it (healing tracker.db on the next personal bootstrap). demo.db, being a
committed artifact, is rebuilt with this migration and committed.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.seed_securities import BENCHMARKS, HOLDINGS  # noqa: E402  single source of truth

_DBC = next(b for b in BENCHMARKS if b["ticker"] == "DBC")


def migrate_db(db_path) -> int:
    """Apply the sourced ERs / DBC row / security_type fix to ``db_path``.

    Returns the number of rows actually changed (0 on a second run)."""
    db_path = Path(db_path)
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    changed = 0
    try:
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='securities'"
        ).fetchone() is None:
            return 0

        # 1. Real Assets benchmark = DBC. Insert it if absent (tracker.db carries
        #    the stale DJP instead), then retire the delisted DJP row.
        has_dbc = conn.execute("SELECT 1 FROM securities WHERE ticker='DBC'").fetchone()
        if has_dbc is None:
            ra = conn.execute(
                "SELECT asset_class_id FROM asset_classes WHERE name='Real Assets'"
            ).fetchone()
            if ra is not None:
                conn.execute(
                    "INSERT INTO securities (ticker, name, asset_class_id, security_type, expense_ratio)"
                    " VALUES ('DBC', ?, ?, 'benchmark', ?)",
                    (_DBC["name"], ra["asset_class_id"], _DBC["expense_ratio"]),
                )
                changed += 1
        # Retire DJP (delisted). Benchmarks are never traded, but guard on FK anyway.
        djp = conn.execute("SELECT 1 FROM securities WHERE ticker='DJP'").fetchone()
        if djp is not None and conn.execute(
            "SELECT 1 FROM trades WHERE ticker='DJP'"
        ).fetchone() is None:
            conn.execute("DELETE FROM securities WHERE ticker='DJP'")
            changed += 1

        # 2. Sourced benchmark ERs + security_type='benchmark'. Guarded so a value
        #    that already matches is not rewritten (keeps the second run a no-op).
        for b in BENCHMARKS:
            row = conn.execute(
                "SELECT expense_ratio, security_type FROM securities WHERE ticker=?",
                (b["ticker"],),
            ).fetchone()
            if row is None:
                continue  # ticker absent from this book (e.g. IQLT not in the 9-sleeve personal book)
            want_er = b.get("expense_ratio")
            if row["expense_ratio"] != want_er or row["security_type"] != "benchmark":
                conn.execute(
                    "UPDATE securities SET expense_ratio=?, security_type='benchmark' WHERE ticker=?",
                    (want_er, b["ticker"]),
                )
                changed += 1

        # 3. Holding ERs — already correct in the committed books, applied here only
        #    so a reseed and a migrate converge on the same provenance-sourced value.
        for h in HOLDINGS:
            row = conn.execute(
                "SELECT expense_ratio FROM securities WHERE ticker=?", (h["ticker"],)
            ).fetchone()
            if row is not None and row["expense_ratio"] != h.get("expense_ratio"):
                conn.execute(
                    "UPDATE securities SET expense_ratio=? WHERE ticker=?",
                    (h.get("expense_ratio"), h["ticker"]),
                )
                changed += 1

        conn.commit()
    finally:
        conn.close()

    print(f"  {db_path.name}: {changed} row(s) changed")
    return changed


def main() -> None:
    for name in ("demo.db", "tracker.db"):
        path = ROOT / "data" / name
        if path.exists():
            print(f"\nApplying Phase 41 benchmark-ER provenance to {path} ...")
            migrate_db(path)


if __name__ == "__main__":
    main()
