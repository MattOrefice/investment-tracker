"""Sync demo.db holding_rationale for the bold-revisit-clause batch.

Follow-up to the international-tilt prose batch (#155). The three tilt holdings
(IDHQ/AVIV/AVDV) already render their invalidation clause as a bold, own-line
paragraph — "\\n\\n**Would revisit if** …" — inherited from the SAA sleeve prose
style. This batch brings the five EXISTING holdings that carry a revisit clause
up to the same treatment so the falsifier is visually findable everywhere:

  - SPHQ, AVUV, SCHP, VNQ: the trailing "Would revisit if …" sentence is split
    onto its own paragraph and the three-word lead-in is bolded. Formatting only;
    the words are byte-identical otherwise.
  - IEMG: its clause was mid-sentence ("… at current prices; would revisit if …").
    That needed a small prose edit — semicolon -> period, lowercase -> capital —
    before the same split+bold could apply.

seed() only INSERTs securities that are absent, so an existing demo.db never
picks up rationale edits from a re-seed — this script copies the seed text in.

Targets data/demo.db EXPLICITLY (never get_db_path) so it cannot touch
tracker.db regardless of TRACKER_MODE. Idempotent: once synced it reports
"already in sync" and writes nothing.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.seed_securities import HOLDINGS

DEMO_DB = Path(__file__).resolve().parent.parent / "data" / "demo.db"
TICKERS = ("SPHQ", "AVUV", "IEMG", "SCHP", "VNQ")

# The marker that distinguishes an upgraded rationale from the plain original.
BOLD_MARKER = "\n\n**Would revisit if** "


def main() -> int:
    seed = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS}

    # Seed-sanity: refuse to run against a seed that predates the bold batch.
    missing = [t for t in TICKERS if not seed.get(t)]
    if missing:
        print(f"ABORT: seed has no holding_rationale for {missing} — update the seed first.")
        return 1
    unbolded = [t for t in TICKERS if BOLD_MARKER not in seed[t]]
    if unbolded:
        print(f"ABORT: seed still carries the plain (un-bolded) revisit clause for {unbolded} — "
              "update src/seed_securities.py first.")
        return 1

    conn = sqlite3.connect(DEMO_DB)
    try:
        updated = []
        for t in TICKERS:
            row = conn.execute(
                "SELECT holding_rationale FROM securities WHERE ticker = ?", (t,)
            ).fetchone()
            if row is None:
                print(f"ABORT: no {t} row in demo.db securities.")
                return 1
            if row[0] != seed[t]:
                conn.execute(
                    "UPDATE securities SET holding_rationale = ? WHERE ticker = ?",
                    (seed[t], t),
                )
                updated.append(t)
        if not updated:
            print("demo.db revisit-clause rationales already in sync — nothing to do.")
            return 0
        conn.commit()
        print(f"demo.db holding_rationale synced from seed for: {', '.join(updated)}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
