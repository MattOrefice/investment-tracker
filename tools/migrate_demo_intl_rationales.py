"""Sync demo.db holding_rationale for the international-tilt prose batch.

Carries the authored holding-level rationales for the three international tilt
holdings (IDHQ, AVIV, AVDV — previously NULL) into the committed demo.db, plus
the two de-superlatived edits that landed in the same pass:
  - AVUV: "highest-conviction factor pick in the portfolio" -> "in the US book"
          (the conviction claim now collides with AVDV's; scoping resolves it);
          and the ER sentence drops the stale "highest in the portfolio" ranking.
  - IEMG: "the most egregious benchmark-vs-holding gap in the portfolio" ->
          "the largest same-index fee gap in the portfolio at 61 bps" (the old
          claim was false in absolute terms — VNQ 0.12% vs the Real Assets
          benchmark DBC 0.85% is a wider 73 bps gap).

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
TICKERS = ("IDHQ", "AVIV", "AVDV", "AVUV", "IEMG")


def main() -> int:
    seed = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS}

    # Seed-sanity: refuse to run against a seed that predates the prose batch.
    missing = [t for t in TICKERS if not seed.get(t)]
    if missing:
        print(f"ABORT: seed has no holding_rationale for {missing} — update the seed first.")
        return 1
    if "most egregious" in seed["IEMG"] or "highest in the portfolio" in seed["AVUV"]:
        print("ABORT: seed still carries the pre-fix superlatives — update the seed first.")
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
            print("demo.db international-tilt rationales already in sync — nothing to do.")
            return 0
        conn.commit()
        print(f"demo.db holding_rationale synced from seed for: {', '.join(updated)}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
