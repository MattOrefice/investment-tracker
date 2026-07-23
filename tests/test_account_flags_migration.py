"""Phase 42: the account-flag default flip (fail-open -> fail-conservative).

Guards that migrate_accounts_phase42_flags recreates the accounts table with the
conservative defaults (managed_by 'external', included_in_household 0) while
preserving every existing row verbatim and the trades -> accounts foreign key,
and that it is idempotent. Also proves the WHOLE point: after the flip, an account
created without the flags is excluded / not-self, not silently pulled in.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# The accounts + trades shape as it existed BEFORE this migration: permissive
# defaults on both flags, and a trades FK onto accounts (the relationship the
# recreate must not break).
_PRE_FLIP = """
    CREATE TABLE accounts (
        account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL UNIQUE,
        type          TEXT NOT NULL,
        custodian     TEXT,
        is_active     INTEGER DEFAULT 1,
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        tax_treatment TEXT DEFAULT 'other',
        pseudonym     TEXT,
        display_name  TEXT,
        managed_by    TEXT DEFAULT 'self',
        included_in_household INTEGER DEFAULT 1
    );
    CREATE UNIQUE INDEX ux_accounts_pseudonym ON accounts (pseudonym);
    CREATE TABLE trades (
        trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        ticker     TEXT NOT NULL,
        FOREIGN KEY (account_id) REFERENCES accounts(account_id)
    );
"""


def _m42():
    spec = importlib.util.spec_from_file_location(
        "m42", str(_ROOT / "tools" / "migrate_accounts_phase42_flags.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_PRE_FLIP)
    # A genuine self-directed taxable base account with trades, a household account
    # left on the default, and one explicitly EXCLUDED account (the case the flag
    # exists for — a forfeitable workplace sleeve).
    conn.executemany(
        "INSERT INTO accounts (name, type, tax_treatment, pseudonym, display_name,"
        " managed_by, included_in_household) VALUES (?,?,?,?,?,?,?)",
        [
            ("Base Taxable", "taxable", "taxable", "acct_01", "Base Taxable", "self", 1),
            ("Advisor IRA",  "retirement", "roth_ira", "acct_roth", "Advisor IRA", "external", 1),
            ("Forfeitable",  "retirement", "workplace_plan", "acct_ppp", "PPP", "external", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO trades (account_id, ticker) VALUES (?,?)",
        [(1, "SPY"), (1, "VTI"), (2, "QQQ")],
    )
    conn.commit()
    conn.close()


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    out = {r["account_id"]: dict(r) for r in conn.execute("SELECT * FROM accounts")}
    conn.close()
    return out


def _defaults(db_path):
    conn = sqlite3.connect(str(db_path))
    d = {r[1]: r[4] for r in conn.execute("PRAGMA table_info(accounts)")}
    conn.close()
    return d


def test_migration_flips_defaults_and_preserves_every_row(tmp_path):
    db = tmp_path / "pre.db"
    _seed(db)
    before = _rows(db)

    assert _m42().migrate_db(db) == 1
    after = _rows(db)

    # Every row preserved verbatim — the recreate is a copy, not a reseed.
    assert before == after, "a row changed during the recreate"
    # The explicitly-excluded account is still excluded; the self account still self.
    assert after[3]["included_in_household"] == 0
    assert after[1]["managed_by"] == "self" and after[1]["included_in_household"] == 1
    # Go-forward defaults are now fail-conservative.
    d = _defaults(db)
    assert d["managed_by"] == "'external'" and d["included_in_household"] == "0"


def test_migration_preserves_the_trades_foreign_key(tmp_path):
    db = tmp_path / "fk.db"
    _seed(db)
    _m42().migrate_db(db)

    conn = sqlite3.connect(str(db))
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    orphans = conn.execute(
        "SELECT COUNT(*) FROM trades t WHERE NOT EXISTS "
        "(SELECT 1 FROM accounts a WHERE a.account_id = t.account_id)"
    ).fetchone()[0]
    conn.close()
    assert violations == [] and orphans == 0 and n_trades == 3


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    _seed(db)
    assert _m42().migrate_db(db) == 1
    snapshot = _rows(db)
    assert _m42().migrate_db(db) == 0, "second run must be a no-op"
    assert _rows(db) == snapshot


def test_after_flip_a_flagless_insert_defaults_conservative(tmp_path):
    db = tmp_path / "fwd.db"
    _seed(db)
    _m42().migrate_db(db)

    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        "INSERT INTO accounts (name, type, pseudonym) VALUES (?,?,?)",
        ("Forgot Flags", "taxable", "acct_forgot"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT managed_by, included_in_household FROM accounts WHERE account_id=?",
        (cur.lastrowid,),
    ).fetchone()
    conn.close()
    assert row == ("external", 0), (
        "a flag-less account must inherit the fail-conservative defaults, not be "
        "pulled into the household ('self'/1)"
    )


def test_exposes_migrate_db_for_discovery():
    assert callable(getattr(_m42(), "migrate_db", None)), (
        "run_pending_migrations only runs migrations exposing migrate_db(db_path)")
