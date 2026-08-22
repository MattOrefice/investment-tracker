"""Carry the bold-revisit-clause batch into tracker.db (personal book).

Follow-up to #155. The three international-tilt holdings already render their
invalidation clause as a bold, own-line paragraph ("\\n\\n**Would revisit if** …").
This batch brings the five EXISTING holdings that carry a revisit clause up to
the same treatment (SPHQ, AVUV, IEMG, SCHP, VNQ) so the falsifier is visually
findable everywhere.

Each edit is a TARGETED substring swap (not a full seed copy) so any
personal-specific wording elsewhere in these rationales is preserved
byte-for-byte — the same discipline as tools/migrate_personal_intl_rationales.py.
For SPHQ/AVUV/SCHP/VNQ the swap is formatting only (split the trailing sentence
onto its own paragraph, bold the three-word lead-in). For IEMG the swap also
carries the small prose edit its buried clause needed: semicolon -> period,
lowercase -> capital.

Targets data/tracker.db EXPLICITLY (never get_db_path) so it cannot touch
demo.db regardless of TRACKER_MODE. Idempotent: re-running after the sync reports
"already migrated" and writes nothing. tracker.db is gitignored, so this sync does
not travel with the repo. Run it together with the other three personal prose
migrations via `python tools/sync_personal_prose.py`, which sequences them in
order and documents why they are manual, not auto-discovered on bootstrap.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.seed_securities import HOLDINGS

TRACKER_DB = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

BOLD_MARKER = "\n\n**Would revisit if** "

# In-place substring swaps (OLD must be present verbatim, else abort). OLD is the
# plain, single-paragraph clause; NEW splits it onto its own paragraph and bolds
# only the "Would revisit if" lead-in. The continuation after the lead-in is left
# untouched by keeping the swap boundary at the closing "**".
SUBS = {
    "SPHQ": [
        ("did the accruals screen add or cost value? Would revisit if",
         "did the accruals screen add or cost value?\n\n**Would revisit if**"),
    ],
    "AVUV": [
        ("earns its fee over time. Would revisit if",
         "earns its fee over time.\n\n**Would revisit if**"),
    ],
    "IEMG": [
        # Prose edit + format: "; would revisit if" -> ".\n\n**Would revisit if**".
        ("appropriate at current prices; would revisit if",
         "appropriate at current prices.\n\n**Would revisit if**"),
    ],
    "SCHP": [
        ("essentially free to carry. Would revisit if",
         "essentially free to carry.\n\n**Would revisit if**"),
    ],
    "VNQ": [
        ("carrying the tax friction. Would revisit if",
         "carrying the tax friction.\n\n**Would revisit if**"),
    ],
}


def _current(conn, ticker):
    return conn.execute(
        "SELECT holding_rationale FROM securities WHERE ticker = ?", (ticker,)
    ).fetchone()


def main() -> int:
    if not TRACKER_DB.exists():
        print(f"ABORT: {TRACKER_DB} not found — nothing to migrate on this machine.")
        return 1

    # Repo-consistency guard: the seed must already carry the upgraded format, or
    # source and DB would diverge in opposite directions.
    seed = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS}
    unbolded = [t for t in SUBS if BOLD_MARKER not in (seed.get(t) or "")]
    if unbolded:
        print(f"ABORT: seed still carries the plain (un-bolded) revisit clause for {unbolded} — "
              "update src/seed_securities.py first.")
        return 1

    conn = sqlite3.connect(TRACKER_DB)
    try:
        changed = []
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
                    print(f"ABORT: expected clause not found in tracker.db {t} rationale — "
                          "the text has diverged; refusing to guess. Inspect manually.")
                    return 1
                new = new.replace(old, repl, 1)
            if new != text:
                conn.execute(
                    "UPDATE securities SET holding_rationale = ? WHERE ticker = ?", (new, t)
                )
                changed.append(t)

        if not changed:
            print("tracker.db revisit-clause rationales already migrated — nothing to do.")
            return 0
        conn.commit()
        print(f"tracker.db holding_rationale updated for: {', '.join(changed)}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
