"""Phase 25.3 migration: household securities sleeve-mapping columns.

Changes:
  1. Adds a parking-lot asset class ('Other / Non-SAA') for household
     securities that have no SAA sleeve. parent_id=NULL, target_weight=0.00
     so it is invisible to SAA machinery (filtered by AND target_weight > 0
     in pages/1_SAA.py; excluded from benchmark-weight queries via
     WHERE parent_id IS NOT NULL).
  2. Adds sleeve_category TEXT, tax_efficiency TEXT, is_in_saa INTEGER
     to the securities table.

Idempotent: safe to run multiple times.
Applies to both data/tracker.db and data/demo.db.
"""
import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PARKING_LOT_NAME = "Other / Non-SAA"

NEW_SEC_COLUMNS = [
    ("sleeve_category", "TEXT"),
    ("tax_efficiency",  "TEXT"),
    ("is_in_saa",       "INTEGER"),
]


def migrate_db(db_path: Path) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"  SKIP: {db_path} — not found")
        return
    print(f"\nMigrating {db_path} ...")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # ── 1. Parking-lot asset class ─────────────────────────────────────────
        existing = conn.execute(
            "SELECT asset_class_id FROM asset_classes WHERE name = ?",
            (PARKING_LOT_NAME,),
        ).fetchone()
        if existing:
            print(f"  SKIP asset class {PARKING_LOT_NAME!r} — already exists "
                  f"(id={existing['asset_class_id']})")
        else:
            conn.execute(
                "INSERT INTO asset_classes (name, target_weight, tolerance_band) "
                "VALUES (?, 0.00, 0.00)",
                (PARKING_LOT_NAME,),
            )
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            print(f"  Added asset class {PARKING_LOT_NAME!r} (id={new_id})")

        # ── 2. New columns on securities ───────────────────────────────────────
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(securities)")}
        for col_name, col_def in NEW_SEC_COLUMNS:
            if col_name in existing_cols:
                print(f"  SKIP column securities.{col_name!r} — already exists")
            else:
                conn.execute(
                    f"ALTER TABLE securities ADD COLUMN {col_name} {col_def}"
                )
                print(f"  Added column securities.{col_name!r}")

        conn.commit()
        print("  Done.")
    finally:
        conn.close()


def main() -> None:
    for db_path in [ROOT / "data" / "tracker.db", ROOT / "data" / "demo.db"]:
        migrate_db(db_path)


if __name__ == "__main__":
    main()
