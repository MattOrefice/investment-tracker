"""Correct demo.db's VGIT holding_rationale jurisdiction from DC to Pennsylvania.

#283. The rationale rendered on the Research page said Treasury interest is exempt
from state and local taxes — correct, and contradicted by the register until #278 —
and called the jurisdiction "a high-income-tax jurisdiction like DC". Every other
jurisdiction reference in the repo is PA (twelve of them); this sentence landed at
1454d0b on 2026-05-01 and TAX_PROFILE's PA rates at e1c88c6 on 2026-07-09, ten weeks
later. The prose predates the tax model and was never revisited.

NOT A STRAIGHT DC->PA SWAP. PA's 3.07% flat rate is one of the LOWEST state income
taxes, so "high-income-tax jurisdiction" is true of DC and false of PA. The
replacement drops the characterisation and keeps the claim: "a real, if modest,
after-tax advantage at Pennsylvania's flat rate". No rate is stated — TAX_PROFILE
holds 3.07% and duplicating it here is the mechanism #228 and #284 are about.

WHY A MIGRATION AT ALL. src/seed_securities.py is INSERT-ONLY: it guards every write
with `if existing is None` and has no UPDATE path, so editing the seed changes
nothing for a database that already holds the row. Source and DB diverged the first
time either was edited and nothing reconciles them.

Targets data/demo.db EXPLICITLY (never get_db_path) so it cannot touch tracker.db
regardless of TRACKER_MODE. Idempotent: re-running reports "already migrated" and
writes nothing. Exposes main() only — NOT migrate_db(db_path) — so
bootstrap.run_pending_migrations does not auto-run it; see tools/sync_personal_prose.py
for why prose migrations are deliberately manual.

HOW THIS DIFFERS FROM ITS TEMPLATE (tools/migrate_demo_vea_rationale.py):

  - a TARGETED SUBSTRING SWAP rather than copying the whole seed string in, so the
    rest of the prose is preserved byte-for-byte. That is the personal template's
    approach (migrate_personal_vea_rationale.py); taking it here keeps the two sides
    of this pair identical in mechanism.
  - it ASSERTS cursor.rowcount == 1. No existing migration in tools/ checks rowcount,
    and a rationale UPDATE whose WHERE matches too widely succeeds SILENTLY — it
    would surface only in a rendered diff, on a tracked binary, after the commit.
    The ticker predicate makes a wide match unlikely; the assertion is what makes it
    impossible to ship unnoticed.
"""
import sqlite3
from pathlib import Path

DEMO_DB = Path(__file__).resolve().parent.parent / "data" / "demo.db"

_OLD = ("a real after-tax advantage in a high-income-tax jurisdiction like DC over "
        "investment-grade corporate bond funds")
_NEW = ("a real, if modest, after-tax advantage at Pennsylvania's flat rate over "
        "investment-grade corporate bond funds")


def main() -> int:
    if not DEMO_DB.exists():
        print(f"ABORT: {DEMO_DB} not found.")
        return 1

    conn = sqlite3.connect(DEMO_DB)
    try:
        row = conn.execute(
            "SELECT holding_rationale FROM securities WHERE ticker = 'VGIT'"
        ).fetchone()
        if row is None or row[0] is None:
            print("ABORT: no VGIT rationale row in demo.db securities.")
            return 1
        text = row[0]

        if _NEW in text:
            print("demo.db VGIT jurisdiction already migrated — nothing to do.")
            return 0
        if _OLD not in text:
            # Abort rather than guess. A partial or hand-edited row is a state this
            # script cannot reason about, and overwriting it would destroy prose it
            # did not author.
            print("ABORT: demo.db VGIT rationale does not contain the expected "
                  "pre-migration text. It has been edited by something else; "
                  "inspect it before running this.")
            return 1

        updated = text.replace(_OLD, _NEW, 1)
        cur = conn.execute(
            "UPDATE securities SET holding_rationale = ? WHERE ticker = 'VGIT'",
            (updated,),
        )
        if cur.rowcount != 1:
            conn.rollback()
            print(f"ABORT: UPDATE matched {cur.rowcount} rows, expected exactly 1. "
                  "Rolled back — a rationale write that matches widely is silent "
                  "and would surface only in a rendered diff.")
            return 1
        conn.commit()
        print("demo.db VGIT jurisdiction updated: DC -> Pennsylvania's flat rate "
              "(1 row).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
