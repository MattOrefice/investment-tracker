"""Carry the international-tilt prose batch into tracker.db (personal book).

Three authored tilt rationales (IDHQ, AVIV, AVDV) go from NULL -> seed text, and
two shipped rationales are edited in place to drop false/colliding superlatives:
  - AVUV: "highest-conviction factor pick in the portfolio" -> "in the US book"
          (resolves the collision with AVDV's own highest-conviction claim);
          ER sentence "is the highest in the portfolio" -> "is a real cost …
          rather than index replication".
  - IEMG: "the most egregious benchmark-vs-holding gap in the portfolio" ->
          "the largest same-index fee gap in the portfolio at 61 bps".

The two in-place edits are TARGETED substring swaps (not a full seed copy) so any
personal-specific wording elsewhere in those rationales is preserved byte-for-byte
— the same discipline as tools/migrate_personal_vea_rationale.py. The three NULL
tilt rationales are set from the seed (authored prose, identical in both books).

Targets data/tracker.db EXPLICITLY (never get_db_path) so it cannot touch
demo.db regardless of TRACKER_MODE. Idempotent: re-running after the sync reports
"already migrated" and writes nothing. tracker.db is gitignored, so this sync does
not travel with the repo. Run it together with the other three personal prose
migrations via `python tools/sync_personal_prose.py`, which sequences all four in
order and documents why they are manual, not auto-discovered on bootstrap.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.seed_securities import HOLDINGS

TRACKER_DB = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

# NULL -> seed text (authored prose, no personal-specific content).
SET_FROM_SEED = ("IDHQ", "AVIV", "AVDV")

# In-place substring swaps (OLD must be present verbatim, else abort).
SUBS = {
    "AVUV": [
        ("AVUV is the highest-conviction factor pick in the portfolio.",
         "AVUV is the highest-conviction factor pick in the US book."),
        ("The 0.25% ER is the highest in the portfolio but buys genuine factor exposure.",
         "The 0.25% ER is a real cost but buys genuine factor exposure rather than index replication."),
    ],
    "IEMG": [
        ("seven times more expensive and the most egregious benchmark-vs-holding gap in the portfolio.",
         "seven times more expensive, and the largest same-index fee gap in the portfolio at 61 bps."),
    ],
}


def _current(conn, ticker):
    row = conn.execute(
        "SELECT holding_rationale FROM securities WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row  # None if no row; (value,) otherwise


def main() -> int:
    if not TRACKER_DB.exists():
        print(f"ABORT: {TRACKER_DB} not found — nothing to migrate on this machine.")
        return 1

    seed = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS}
    if any(not seed.get(t) for t in SET_FROM_SEED):
        print("ABORT: seed has no rationale for one of IDHQ/AVIV/AVDV — update the seed first.")
        return 1

    conn = sqlite3.connect(TRACKER_DB)
    try:
        changed = []

        # 1. NULL tilt rationales -> seed text.
        for t in SET_FROM_SEED:
            row = _current(conn, t)
            if row is None:
                print(f"ABORT: no {t} row in tracker.db securities.")
                return 1
            cur = row[0]
            if cur == seed[t]:
                continue
            if cur is not None:
                print(f"ABORT: {t} already has non-seed rationale in tracker.db — "
                      "the text has diverged; refusing to overwrite. Inspect manually.")
                return 1
            conn.execute(
                "UPDATE securities SET holding_rationale = ? WHERE ticker = ?", (seed[t], t)
            )
            changed.append(t)

        # 2. In-place substring swaps on the two shipped rationales.
        for t, pairs in SUBS.items():
            row = _current(conn, t)
            if row is None or row[0] is None:
                print(f"ABORT: no {t} rationale row in tracker.db securities.")
                return 1
            text = row[0]
            new = text
            for old, repl in pairs:
                if repl in new:
                    continue  # already applied
                if old not in new:
                    print(f"ABORT: expected sentence not found in tracker.db {t} rationale — "
                          "the text has diverged; refusing to guess. Inspect manually.")
                    return 1
                new = new.replace(old, repl, 1)
            if new != text:
                conn.execute(
                    "UPDATE securities SET holding_rationale = ? WHERE ticker = ?", (new, t)
                )
                changed.append(t)

        if not changed:
            print("tracker.db international-tilt rationales already migrated — nothing to do.")
            return 0
        conn.commit()
        print(f"tracker.db holding_rationale updated for: {', '.join(changed)}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
