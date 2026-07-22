"""Sync demo.db's VEA holding_rationale from src.seed_securities.

The committed demo.db still carried the pre-restructure VEA rationale citing
"this sleeve's 19% weight" — the old undivided International Developed target.
On the 12-sleeve book International Core is ~7.1%, so the sentence was stale on
the demo Research page. The seed prose was reworded to not hardcode a weight;
this script copies the seed text into demo.db so the committed DB matches.

Targets data/demo.db EXPLICITLY (never get_db_path) so it cannot touch
tracker.db regardless of TRACKER_MODE. Idempotent: re-running after the sync
reports "already in sync" and writes nothing.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.seed_securities import HOLDINGS

DEMO_DB = Path(__file__).resolve().parent.parent / "data" / "demo.db"


def main() -> int:
    seed_text = next(h["holding_rationale"] for h in HOLDINGS if h["ticker"] == "VEA")
    if "19% weight" in seed_text:
        print("ABORT: seed itself still contains the stale '19% weight' string.")
        return 1

    conn = sqlite3.connect(DEMO_DB)
    try:
        row = conn.execute(
            "SELECT holding_rationale FROM securities WHERE ticker = 'VEA'"
        ).fetchone()
        if row is None:
            print("ABORT: no VEA row in demo.db securities.")
            return 1
        if row[0] == seed_text:
            print("demo.db VEA rationale already in sync — nothing to do.")
            return 0
        conn.execute(
            "UPDATE securities SET holding_rationale = ? WHERE ticker = 'VEA'",
            (seed_text,),
        )
        conn.commit()
        print("demo.db VEA rationale updated to match src.seed_securities.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
