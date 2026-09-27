"""Carry the writing sweep's Research copy into data/demo.db (audit item 14b, Markets).

The fund rationales on the Research page are seeded prose: src/seed_securities.py
inserts them once, so editing the seed changes a fresh build only. This carries the same
text into the committed demo book, from the seed itself, for every fund whose rationale
differs, and the SPAXX text (pages/8_Research.py, src/seed_position_theses.py) into the
three columns of the SPAXX position thesis that repeat it.

Each UPDATE is keyed on the primary key and the row's current text, must change exactly
one row, or the run rolls back; a second run changes nothing. Targets data/demo.db
explicitly (never get_db_path) and runs from main() only. The personal book is not
touched.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

SPAXX_THESIS_TITLE = "Cash / SPAXX — SPAXX"
SPAXX_COLUMNS = ("macro_view", "view_summary", "vehicle_rationale")


def main() -> int:
    from src.seed_position_theses import SPAXX_VEHICLE_RATIONALE
    from src.seed_securities import HOLDINGS
    seed = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS if h.get("holding_rationale")}
    conn = sqlite3.connect(DEMO_DB)
    funds = theses = 0
    try:
        with conn:
            for ticker, text in seed.items():
                row = conn.execute("SELECT holding_rationale FROM securities WHERE ticker = ?",
                                   (ticker,)).fetchone()
                if row is None or row[0] == text:
                    continue
                n = conn.execute("UPDATE securities SET holding_rationale = ? WHERE ticker = ? "
                                 "AND holding_rationale = ?", (text, ticker, row[0])).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {ticker} update changed {n} rows, not 1")
                funds += 1
            row = conn.execute("SELECT thesis_id, macro_view, view_summary, vehicle_rationale "
                               "FROM theses WHERE title = ?", (SPAXX_THESIS_TITLE,)).fetchall()
            if len(row) != 1:
                raise SystemExit(f"ABORT: {len(row)} '{SPAXX_THESIS_TITLE}' theses")
            thesis_id, *current = row[0]
            if any(c != SPAXX_VEHICLE_RATIONALE for c in current):
                if len(set(current)) != 1:
                    raise SystemExit("ABORT: the SPAXX thesis columns no longer hold one text")
                n = conn.execute(
                    "UPDATE theses SET macro_view = ?, view_summary = ?, vehicle_rationale = ? "
                    "WHERE thesis_id = ? AND macro_view = ? AND view_summary = ? AND "
                    "vehicle_rationale = ?",
                    (SPAXX_VEHICLE_RATIONALE,) * 3 + (thesis_id, *current)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: the SPAXX thesis update changed {n} rows, not 1")
                theses += 1
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {funds} fund rationale(s), {theses} SPAXX thesis row(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
