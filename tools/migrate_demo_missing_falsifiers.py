"""Sync demo.db holding_rationale for the missing-falsifiers batch.

Follow-up to #156. Five holdings carried no revisit/invalidation clause — VOO,
VTV, VEA, VGIT, PDBC. This batch adds an authored falsifier to each, using the
same bold, own-paragraph treatment the other eight already have
("\\n\\n**Would revisit if** …"), so all thirteen ETF holdings carry a visually
findable falsifier.

Two of these are more than an append:
  - VGIT gets TWO falsifiers (the hedge failing, and duration becoming
    compensated), so it carries a second bold paragraph
    ("\\n\\n**Would also revisit if** …").
  - PDBC's opening sentence — "the only broad commodity ETF worth owning …" —
    was false (COMT and BCI are no-K-1 broad commodity ETFs) and collided with
    its own falsifier. It is reworded in place to name the alternatives.

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
TICKERS = ("VOO", "VTV", "VEA", "VGIT", "PDBC")

BOLD_MARKER = "\n\n**Would revisit if** "


def main() -> int:
    seed = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS}

    # Seed-sanity: refuse to run against a seed that predates the batch.
    missing = [t for t in TICKERS if not seed.get(t)]
    if missing:
        print(f"ABORT: seed has no holding_rationale for {missing} — update the seed first.")
        return 1
    unbolded = [t for t in TICKERS if BOLD_MARKER not in seed[t]]
    if unbolded:
        print(f"ABORT: seed still has no falsifier for {unbolded} — update src/seed_securities.py first.")
        return 1
    if "**Would also revisit if** " not in seed["VGIT"]:
        print("ABORT: seed VGIT lacks its second falsifier — update the seed first.")
        return 1
    if "only broad commodity ETF worth owning" in seed["PDBC"]:
        print("ABORT: seed PDBC still carries the false 'only …' opening — update the seed first.")
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
            print("demo.db missing-falsifier rationales already in sync — nothing to do.")
            return 0
        conn.commit()
        print(f"demo.db holding_rationale synced from seed for: {', '.join(updated)}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
