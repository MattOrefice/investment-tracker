"""Reword tracker.db's VEA holding_rationale to drop the stale hardcoded weight.

The personal book's VEA rationale cites "this sleeve's 19% weight" while the
actual International Developed target is 20.41% — stale independently of the
demo restructure (19% was the original pre-ex-cash-normalization target). Same
weight-free phrasing as the demo fix (tools/migrate_demo_vea_rationale.py),
applied as a targeted substring replacement so the rest of the personal prose
is preserved byte-for-byte.

Targets data/tracker.db EXPLICITLY (never get_db_path) so it cannot touch
demo.db regardless of TRACKER_MODE. Idempotent: re-running after the reword
reports "already migrated" and writes nothing. tracker.db is gitignored, so this
sync does not travel with the repo. Run it together with the other three personal
prose migrations via `python tools/sync_personal_prose.py`, which sequences them
in order and documents why they are manual, not auto-discovered on bootstrap.
"""
import sqlite3
from pathlib import Path

TRACKER_DB = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

_OLD = "The valuation thesis driving this sleeve's 19% weight holds"
_NEW = "The valuation thesis behind this sleeve's weight holds"


def main() -> int:
    if not TRACKER_DB.exists():
        print(f"ABORT: {TRACKER_DB} not found — nothing to migrate on this machine.")
        return 1

    conn = sqlite3.connect(TRACKER_DB)
    try:
        row = conn.execute(
            "SELECT holding_rationale FROM securities WHERE ticker = 'VEA'"
        ).fetchone()
        if row is None or row[0] is None:
            print("ABORT: no VEA rationale row in tracker.db securities.")
            return 1
        text = row[0]
        if _NEW in text:
            print("tracker.db VEA rationale already migrated — nothing to do.")
            return 0
        if _OLD not in text:
            print(
                "ABORT: expected sentence not found in tracker.db VEA rationale — "
                "the text has diverged; refusing to guess. Inspect manually."
            )
            return 1
        conn.execute(
            "UPDATE securities SET holding_rationale = ? WHERE ticker = 'VEA'",
            (text.replace(_OLD, _NEW),),
        )
        conn.commit()
        print("tracker.db VEA rationale reworded (weight-free phrasing).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
