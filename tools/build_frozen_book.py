"""Build tests/fixtures/frozen_book.db, the FROZEN TEST BOOK (#302, test half).

The personal-mode render tests used to copy data/tracker.db, the owner's real book,
and then depended on how stale its price cache was against today's date: 16 of them
went red when the cache fell more than a week behind (#302). They also skipped in CI,
where tracker.db does not exist. This book replaces it for them:

  * PUBLIC ORIGIN ONLY. Built from data/demo.db, the public paper-trade book, NEVER
    from tracker.db. It is committed, so nothing personal may reach it; the builder
    refuses any other source.
  * FROZEN. Built once and committed. A later demo.db refresh does not move it: to
    change it, re-run this builder deliberately and review the diff like any data PR.
  * #304's FORMAT. Raw closes with adj_close NULL, and the dividend record the
    adjustment is computed from on read. Checked, not assumed: the builder refuses a
    source that still stores adj_close.
  * NOTHING DATE-KEYED. macro_cache rows are keyed on the fetch date and would make a
    render depend on the day it runs, so they are dropped.

Trimmed to what the pages read: every ticker's prices and dividends from 2024-01-01
(the book's inception is 2025-05-01; the margin covers trailing windows before it).

Usage:  python tools/build_frozen_book.py            # writes tests/fixtures/frozen_book.db
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "demo.db"
TARGET = ROOT / "tests" / "fixtures" / "frozen_book.db"
FROM = "2024-01-01"


def build(source: Path = SOURCE, target: Path = TARGET) -> Path:
    if source.resolve() != SOURCE.resolve():
        raise SystemExit(f"refusing to build the frozen book from {source}: only the "
                         "public demo.db may seed a committed fixture")
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    stored = src.execute("SELECT COUNT(adj_close) FROM prices").fetchone()[0]
    src.close()
    if stored:
        raise SystemExit(f"{source} still stores adj_close on {stored} rows: migrate it "
                         "to #304's format first (tools/migrate_unadjusted_prices_304.py)")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.chmod(0o644)
        target.unlink()
    shutil.copyfile(source, target)
    target.chmod(0o644)
    c = sqlite3.connect(target)
    with c:
        c.execute("DELETE FROM prices WHERE price_date < ?", (FROM,))
        c.execute("DELETE FROM dividends WHERE ex_date < ?", (FROM,))
        c.execute("DELETE FROM macro_cache")
    c.execute("VACUUM")
    c.close()
    return target


if __name__ == "__main__":
    out = build()
    c = sqlite3.connect(f"file:{out.as_posix()}?mode=ro", uri=True)
    print(out, f"{out.stat().st_size:,} bytes;",
          "prices", c.execute("SELECT COUNT(*), MIN(price_date), MAX(price_date) FROM prices").fetchone(),
          "dividends", c.execute("SELECT COUNT(*) FROM dividends").fetchone()[0])
    sys.exit(0)
