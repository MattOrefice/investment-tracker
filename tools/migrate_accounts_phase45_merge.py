"""Phase 45: merge the duplicated self-directed taxable identity into acct_01.

WHY THIS EXISTS. The self-directed taxable book existed under TWO account rows —
the same real account modelled twice:

  * acct_01 (id 1, "Personal Fidelity"): created by db.py's initialize_db, carries
    the entire trade ledger (get_portfolio_account resolves it), and
  * acct_taxable_01 (id 2, "Individual Taxable (Self-Directed)"): seeded by
    src/seed/household_accounts.py, carried the positions CSV / account_map entry /
    household-page identity, and held NO trades.

They were proven identical (every ticker's trade-derived net share count equals the
positions-CSV quantity), and only ever reconciled by coincidence. The duplication
was silent because the two never cross-checked, and it was a latent landmine:
get_portfolio_account() raises on >1 taxable+self candidate, so the whole
trade-derived app would STOP the moment acct_taxable_01 acquired a trade.

THE MERGE. Survivor = acct_01 (it holds the trade ledger — the one thing that must
not move, and it is the base account shared with demo). acct_taxable_01 is
absorbed: its household identity is repointed to acct_01 (account_map.json value,
DIRECTABLE_PSEUDONYMS, the performance seed, and acct_01's display_name — the last
set in bootstrap_personal_db, personal-only). This migration performs the one
remaining piece: deleting the now-redundant acct_taxable_01 row from an existing
DB. The seed no longer creates it, so a reseed cannot reintroduce it.

SAFETY. acct_taxable_01 carries no trades, so deleting it orphans nothing (the only
FK onto accounts is trades.account_id). The delete is guarded: if acct_taxable_01
is ever found carrying trades, this ABORTS rather than deleting a ledger.

Idempotent: absent (already merged / a fresh reseed / demo, which never had it) ->
0 changes. Exposes migrate_db(db_path); run_pending_migrations discovers it and
heals tracker.db on the next personal bootstrap. demo.db has no acct_taxable_01, so
this is a no-op there and demo.db is not rebuilt.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_MERGED_PSEUDONYM = "acct_taxable_01"
_SURVIVOR_PSEUDONYM = "acct_01"


def migrate_db(db_path) -> int:
    """Delete the redundant acct_taxable_01 row (survivor is acct_01).

    Returns 1 if it was deleted, 0 if absent (idempotent). Raises if
    acct_taxable_01 unexpectedly carries a trade ledger."""
    db_path = Path(db_path)
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchone() is None:
            return 0

        row = conn.execute(
            "SELECT account_id FROM accounts WHERE pseudonym = ?", (_MERGED_PSEUDONYM,)
        ).fetchone()
        if row is None:
            print(f"  {db_path.name}: 0 (no {_MERGED_PSEUDONYM} — already merged / demo / fresh)")
            return 0

        merged_id = row[0]
        n_trades = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE account_id = ?", (merged_id,)
        ).fetchone()[0]
        if n_trades > 0:
            raise RuntimeError(
                f"{db_path.name}: refusing to delete {_MERGED_PSEUDONYM} "
                f"(account_id={merged_id}) — it carries {n_trades} trade(s).\n"
                "\n"
                f"This migration merges the historical duplicate self-directed taxable\n"
                f"identity into {_SURVIVOR_PSEUDONYM}, which is safe ONLY while\n"
                f"{_MERGED_PSEUDONYM} holds no ledger. Trades on BOTH rows means the\n"
                "duplication became REAL: two account rows each carrying a trade ledger,\n"
                f"not one account modelled twice. Deleting {_MERGED_PSEUDONYM} now would\n"
                "destroy those trades.\n"
                "\n"
                "The fix is NOT to re-run this migration or delete this guard. Decide\n"
                "which ledger is authoritative for the real account, re-book or reconcile\n"
                f"the trades onto {_SURVIVOR_PSEUDONYM} (account_id=1) by hand, verify\n"
                "reconcile_account_shares() balances, and only then remove the emptied\n"
                f"{_MERGED_PSEUDONYM} row. Investigate how it acquired trades first — a\n"
                "trade booked to the wrong account is the likely cause."
            )

        conn.execute("DELETE FROM accounts WHERE account_id = ?", (merged_id,))

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            conn.rollback()
            raise RuntimeError(
                f"{db_path.name}: foreign_key_check reported {len(violations)} "
                f"violation(s) after deleting {_MERGED_PSEUDONYM}: {violations[:5]}"
            )

        conn.commit()
    finally:
        conn.close()

    print(f"  {db_path.name}: deleted {_MERGED_PSEUDONYM} (merged into {_SURVIVOR_PSEUDONYM})")
    return 1


def main() -> None:
    for name in ("demo.db", "tracker.db"):
        path = ROOT / "data" / name
        if path.exists():
            print(f"\nApplying Phase 45 account-identity merge to {path} ...")
            migrate_db(path)


if __name__ == "__main__":
    main()
