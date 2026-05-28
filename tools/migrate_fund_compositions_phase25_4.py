"""Phase 25.4 migration: fund_compositions look-through table.

Creates:
  fund_compositions(
    fund_symbol       TEXT,
    underlying_sleeve TEXT,
    weight            REAL,       -- 0..1; rows per fund sum to 1.00
    as_of_date        TEXT,
    source            TEXT,
    PRIMARY KEY (fund_symbol, underlying_sleeve)
  )

Idempotent (CREATE TABLE IF NOT EXISTS).
Applies to both data/tracker.db and data/demo.db.
Seeding is personal-mode only — run src/seed/fund_compositions.py
separately against tracker.db.
"""
import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def migrate_db(db_path: Path) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"  SKIP: {db_path} — not found")
        return
    print(f"\nMigrating {db_path} ...")
    conn = sqlite3.connect(str(db_path))
    try:
        tables_before = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fund_compositions (
                fund_symbol       TEXT NOT NULL,
                underlying_sleeve TEXT NOT NULL,
                weight            REAL NOT NULL,
                as_of_date        TEXT,
                source            TEXT,
                PRIMARY KEY (fund_symbol, underlying_sleeve)
            )
        """)
        conn.commit()

        if "fund_compositions" in tables_before:
            print("  SKIP table 'fund_compositions' — already exists")
        else:
            print("  Created table 'fund_compositions'")
        print("  Done.")
    finally:
        conn.close()


def main() -> None:
    for db_path in [ROOT / "data" / "tracker.db", ROOT / "data" / "demo.db"]:
        migrate_db(db_path)


if __name__ == "__main__":
    main()
