"""Phase 42: flip the two fail-OPEN account-flag defaults to fail-conservative.

WHY THIS EXISTS. The accounts schema shipped with permissive column defaults:

    managed_by            TEXT    DEFAULT 'self'
    included_in_household  INTEGER DEFAULT 1

Both fail OPEN. A new account created without setting these inherits them, and a
forgotten flag then means:
  - included_in_household=1  -> the account silently enters every household total,
    allocation, and liquidity calc (the exact defect PR #107 fixed for one account
    worth $78,410);
  - managed_by='self'        -> the account is pulled into the self-directed taxable
    book: it satisfies get_portfolio_account()'s
    `tax_treatment='taxable' AND managed_by='self'` predicate and can make the
    resolver ambiguous (>1 match -> raise) or, worse, silently fold a non-directed
    account's positions into the trade-ledger book.

The safe read for BOTH is the conservative one: a not-yet-classified account is
observed-not-actionable (managed_by 'external') and NOT a household asset
(included_in_household 0). Wrongly-external renders visibly (an account shows up as
advisor-managed / excluded — noticed and corrected); wrongly-self / wrongly-included
is SILENT. So the defaults become 'external' / 0; every genuine household or
self-directed account is set explicitly by its writer (src/db.py's base-account
insert, the phase-25.2 metadata back-fill, and src/seed/household_accounts.py).

SQLite cannot ALTER a column's DEFAULT, so an already-built DB (the committed
demo.db, a personal tracker.db) keeps the old permissive default until its accounts
table is RECREATED. This migration does exactly that: create an accounts table with
the conservative defaults, copy every existing row verbatim (preserving each
account's explicit flag values and its account_id, so the trades.account_id foreign
key stays valid), drop the old table, rename, and rebuild the pseudonym unique index.

Data safety:
  - Every existing explicit value survives — the copy is column-by-column, not a
    reseed. An account already marked included_in_household=0 (e.g. the forfeitable
    Moody's PPP sleeve) stays 0; a household account marked 1 stays 1.
  - The trades -> accounts FK is checked with PRAGMA foreign_key_check AFTER the
    rebuild; a violation raises and the change is abandoned rather than committed.
  - Idempotent: if the accounts table already carries the conservative defaults, it
    returns 0 without touching anything. A second run is a guaranteed no-op.

Exposes migrate_db(db_path); run_pending_migrations discovers and runs it (healing
tracker.db on the next personal bootstrap). demo.db, a committed artifact, is rebuilt
with this migration applied and committed.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The accounts columns, in order, that the rebuild copies. Must match the live
# schema (src/db.py SCHEMA). account_number is intentionally absent — it was
# dropped as PII; a legacy DB that still carries it simply won't have it copied.
_ACCOUNT_COLUMNS = [
    "account_id", "name", "type", "custodian", "is_active", "created_at",
    "tax_treatment", "pseudonym", "display_name", "managed_by",
    "included_in_household",
]

# The recreated table, byte-for-byte the live schema EXCEPT the two flipped
# defaults (managed_by 'external', included_in_household 0).
_CREATE_ACCOUNTS_NEW = """
CREATE TABLE accounts_new (
    account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,
    custodian     TEXT,
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    tax_treatment TEXT DEFAULT 'other',
    pseudonym     TEXT,
    display_name  TEXT,
    managed_by    TEXT DEFAULT 'external',
    included_in_household INTEGER DEFAULT 0
)
"""


def migrate_db(db_path) -> int:
    """Recreate ``accounts`` with fail-conservative defaults, preserving rows.

    Returns 1 if the table was recreated, 0 if it was already conservative /
    absent (idempotent)."""
    db_path = Path(db_path)
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchone() is None:
            return 0

        info = {r[1]: r[4] for r in conn.execute("PRAGMA table_info(accounts)")}
        # A minimal/legacy accounts table without the flag columns is not this
        # migration's concern — the phase-25.2 / included_in_household migrations
        # add them first, and this runs after. Nothing to flip here.
        if "included_in_household" not in info or "managed_by" not in info:
            return 0

        # Idempotence: PRAGMA dflt_value is the literal DDL default — '0' for the
        # integer, "'external'" (quotes included) for the text column.
        already = (
            str(info["included_in_household"]) == "0"
            and str(info["managed_by"]) == "'external'"
        )
        if already:
            print(f"  {db_path.name}: 0 (accounts already fail-conservative)")
            return 0

        have = [r[1] for r in conn.execute("PRAGMA table_info(accounts)")]
        missing = [c for c in _ACCOUNT_COLUMNS if c not in have]
        if missing:
            raise RuntimeError(
                f"{db_path.name}: accounts is missing expected columns {missing}; "
                "refusing to recreate (run the earlier account migrations first)."
            )

        cols = ", ".join(_ACCOUNT_COLUMNS)
        # Standard SQLite table redefinition with FKs off during the swap. No
        # explicit BEGIN/COMMIT — executescript commits any pending txn first,
        # matching the prices-table rebuild in src/db.py::_auto_migrate.
        conn.executescript(
            "PRAGMA foreign_keys = OFF;\n"
            + _CREATE_ACCOUNTS_NEW + ";\n"
            + f"INSERT INTO accounts_new ({cols}) SELECT {cols} FROM accounts;\n"
            "DROP TABLE accounts;\n"
            "ALTER TABLE accounts_new RENAME TO accounts;\n"
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_pseudonym"
            "    ON accounts (pseudonym);\n"
            "PRAGMA foreign_keys = ON;\n"
        )

        # Prove the trades -> accounts FK survived the drop/rename before we keep
        # the change. Any dangling child row aborts the migration.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            conn.rollback()
            raise RuntimeError(
                f"{db_path.name}: foreign_key_check reported {len(violations)} "
                f"violation(s) after the accounts rebuild: {violations[:5]}"
            )

        conn.commit()
    finally:
        conn.close()

    print(f"  {db_path.name}: accounts recreated with fail-conservative defaults")
    return 1


def main() -> None:
    for name in ("demo.db", "tracker.db"):
        path = ROOT / "data" / name
        if path.exists():
            print(f"\nApplying Phase 42 account-flag defaults to {path} ...")
            migrate_db(path)


if __name__ == "__main__":
    main()
