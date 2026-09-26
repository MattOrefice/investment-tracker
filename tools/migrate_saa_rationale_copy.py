"""Carry the SAA page's rewritten sleeve rationales into data/demo.db (item 14, writing sweep).

The rationales are seeded prose: src/seed_saa.py inserts them once, so editing the seed
changes a fresh build only. This applies the same text to the committed demo book. Each
UPDATE is guarded on the row carrying exactly the old text or the new, changes one row,
and the run rolls back otherwise; a second run changes nothing. Targets data/demo.db
explicitly (never get_db_path) and runs from main() only, so bootstrap never runs it.
The personal book's rationales are not touched.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))


def main() -> int:
    from src.seed_saa import SUB_CLASSES
    new = {s["name"]: s["rationale"] for s in SUB_CLASSES if s.get("rationale")}
    conn = sqlite3.connect(DEMO_DB)
    changed = 0
    try:
        with conn:
            for name, text in new.items():
                row = conn.execute("SELECT rationale FROM asset_classes WHERE name = ? AND "
                                   "parent_id IS NOT NULL", (name,)).fetchall()
                if not row:
                    continue                      # a sleeve this book does not carry
                (current,) = row[0]
                if current == text:
                    continue
                n = conn.execute("UPDATE asset_classes SET rationale = ? WHERE name = ? AND "
                                 "parent_id IS NOT NULL AND rationale = ?",
                                 (text, name, current)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {name} update changed {n} rows, not 1")
                changed += 1
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {changed} rationale(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
