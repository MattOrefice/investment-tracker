"""Phase 25.2 migration: add account metadata and pseudonymization columns.

Adds five columns to the accounts table:
  account_number TEXT UNIQUE  -- join key between ingestion output and account metadata
  tax_treatment  TEXT DEFAULT 'other'
  pseudonym      TEXT UNIQUE  -- stable alias used in all UI rendering
  display_name   TEXT         -- human-readable label
  managed_by     TEXT DEFAULT 'self'

Idempotent: safe to run multiple times.
Applies to both data/tracker.db and data/demo.db.
"""
import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NEW_COLUMNS = [
    ("account_number", "TEXT"),
    ("tax_treatment",  "TEXT DEFAULT 'other'"),
    ("pseudonym",      "TEXT"),
    ("display_name",   "TEXT"),
    ("managed_by",     "TEXT DEFAULT 'self'"),
]

# SQLite does not support ADD COLUMN ... UNIQUE; enforce via separate unique indexes.
# NULLs are always treated as distinct in SQLite unique indexes, so pre-existing rows
# with NULL account_number / pseudonym don't conflict.
UNIQUE_INDEXES = [
    ("ux_accounts_account_number", "accounts", "account_number"),
    ("ux_accounts_pseudonym",      "accounts", "pseudonym"),
]


def migrate_db(db_path: Path) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"  SKIP: {db_path} — not found")
        return
    print(f"\nMigrating {db_path} ...")
    conn = sqlite3.connect(str(db_path))
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
        for col_name, col_def in NEW_COLUMNS:
            if col_name in existing:
                print(f"  SKIP column {col_name!r} — already exists")
            else:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {col_name} {col_def}")
                print(f"  Added column {col_name!r}")

        for idx_name, tbl, col in UNIQUE_INDEXES:
            # Drop-then-create so a partial index from an earlier run is replaced
            conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
            conn.execute(f"CREATE UNIQUE INDEX {idx_name} ON {tbl} ({col})")
            print(f"  Ensured unique index {idx_name!r}")

        # Set stable placeholder metadata on any row that has no pseudonym yet
        # (applies to the pre-existing generic account in both tracker.db and demo.db)
        rows = conn.execute(
            "SELECT account_id, name FROM accounts WHERE pseudonym IS NULL"
        ).fetchall()
        for account_id, name in rows:
            pseudonym = f"acct_{account_id:02d}"
            # included_in_household=1 is set explicitly: this back-fills the single
            # pre-existing generic account (the self-directed taxable base), which
            # IS a household asset. Under the fail-conservative schema default
            # (included_in_household 0) it would otherwise be excluded — the placeholder
            # metadata must assert inclusion, not lean on the default.
            conn.execute(
                "UPDATE accounts "
                "SET tax_treatment='taxable', managed_by='self', "
                "    included_in_household=1, "
                "    pseudonym=?, display_name=? "
                "WHERE account_id=?",
                (pseudonym, name, account_id),
            )
            print(f"  Placeholder metadata set for account_id={account_id} ({name!r})")

        conn.commit()
        print("  Done.")
    finally:
        conn.close()


def main() -> None:
    for db_path in [ROOT / "data" / "tracker.db", ROOT / "data" / "demo.db"]:
        migrate_db(db_path)


if __name__ == "__main__":
    main()
