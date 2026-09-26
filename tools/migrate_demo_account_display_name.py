"""Label demo.db's portfolio account as the paper-trade portfolio it is.

The public demo's one account is a simulated paper-trade book, but its display_name
was "Personal Fidelity", the author's real account. Every page that reads the ledger
names the account from display_name (the Performance value tile, the Capital
Deployment account picker, the Tax Lots account column), so the demo presented itself
as the author's own account.

Personal mode never reads this: bootstrap relabels acct_01 in tracker.db on every
personal start. Only display_name changes. `name` stays "Personal Fidelity" because it
is initialize_db's idempotency key: renaming it would make the demo's startup insert a
second account.

Same shape as tools/migrate_demo_vgit_jurisdiction.py: targets data/demo.db
explicitly, refuses a row it does not recognise, requires rowcount == 1, and is
idempotent. main() only, so bootstrap never runs it.
"""
import sqlite3
from pathlib import Path

DEMO_DB = Path(__file__).resolve().parent.parent / "data" / "demo.db"

_OLD = "Personal Fidelity"
NEW = "Paper-trade portfolio"


def main() -> int:
    if not DEMO_DB.exists():
        print(f"ABORT: {DEMO_DB} not found.")
        return 1

    conn = sqlite3.connect(DEMO_DB)
    try:
        row = conn.execute(
            "SELECT display_name, name FROM accounts WHERE pseudonym = 'acct_01'"
        ).fetchone()
        if row is None:
            print("ABORT: no acct_01 row in demo.db accounts.")
            return 1
        if row[0] == NEW:
            print("demo.db account display_name already migrated — nothing to do.")
            return 0
        if row[0] != _OLD or row[1] != _OLD:
            print(f"ABORT: acct_01 is {row!r}, not the expected pre-migration "
                  f"({_OLD!r}, {_OLD!r}). Inspect it before running this.")
            return 1
        cur = conn.execute(
            "UPDATE accounts SET display_name = ? "
            "WHERE pseudonym = 'acct_01' AND display_name = ?",
            (NEW, _OLD),
        )
        if cur.rowcount != 1:
            conn.rollback()
            print(f"ABORT: UPDATE matched {cur.rowcount} rows, expected exactly 1. "
                  "Rolled back.")
            return 1
        conn.commit()
        print(f"demo.db acct_01 display_name: {_OLD!r} -> {NEW!r} (1 row).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
