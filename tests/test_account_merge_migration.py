"""Phase 45: the acct_taxable_01 -> acct_01 identity merge migration.

Guards that migrate_accounts_phase45_merge deletes the redundant acct_taxable_01
row (survivor is acct_01, which keeps the trade ledger), preserves acct_01 and its
trades with the FK intact, ABORTS rather than deleting a row that carries trades,
and is idempotent / a no-op where there is nothing to merge (a fresh reseed, an
already-merged DB, or demo — which never had acct_taxable_01).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# accounts (both self-directed taxable identities) + a trades FK onto accounts.
_SCHEMA = """
CREATE TABLE accounts (
    account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,
    tax_treatment TEXT DEFAULT 'other',
    pseudonym     TEXT,
    display_name  TEXT,
    managed_by    TEXT DEFAULT 'external',
    included_in_household INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX ux_accounts_pseudonym ON accounts (pseudonym);
CREATE TABLE trades (
    trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    ticker     TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);
"""


def _m45():
    spec = importlib.util.spec_from_file_location(
        "m45", str(_ROOT / "tools" / "migrate_accounts_phase45_merge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db_path, taxable_01_trades=0):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO accounts (account_id, name, type, tax_treatment, pseudonym,"
        " display_name, managed_by, included_in_household) VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "Personal Fidelity", "taxable", "taxable", "acct_01",
             "Personal Fidelity", "self", 1),
            (2, "Individual Taxable (Self-Directed)", "taxable", "taxable",
             "acct_taxable_01", "Individual Taxable (Self-Directed)", "self", 1),
            (3, "Roth IRA", "retirement", "roth_ira", "acct_roth_01", "Roth IRA",
             "external", 1),
        ],
    )
    # acct_01 always carries the ledger; acct_taxable_01 usually carries none.
    conn.executemany("INSERT INTO trades (account_id, ticker) VALUES (?,?)",
                     [(1, "VOO"), (1, "VEA"), (1, "VTI")])
    for i in range(taxable_01_trades):
        conn.execute("INSERT INTO trades (account_id, ticker) VALUES (2, ?)", (f"X{i}",))
    conn.commit()
    conn.close()


def _pseudonyms(db_path):
    conn = sqlite3.connect(str(db_path))
    out = [r[0] for r in conn.execute("SELECT pseudonym FROM accounts ORDER BY account_id")]
    conn.close()
    return out


def test_merge_deletes_redundant_row_and_keeps_the_ledger(tmp_path):
    db = tmp_path / "merge.db"
    _seed(db)
    assert _m45().migrate_db(db) == 1

    conn = sqlite3.connect(str(db))
    accts = _pseudonyms(db)
    n_acct1_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=1").fetchone()[0]
    acct1 = conn.execute(
        "SELECT name, display_name FROM accounts WHERE account_id=1").fetchone()
    conn.close()

    assert "acct_taxable_01" not in accts, "redundant row was not deleted"
    assert "acct_01" in accts, "survivor was removed"
    assert n_acct1_trades == 3, "the trade ledger moved or was lost"
    # The migration does NOT touch acct_01's name/display_name (that is bootstrap's job).
    assert acct1 == ("Personal Fidelity", "Personal Fidelity")


def test_merge_preserves_the_trades_foreign_key(tmp_path):
    db = tmp_path / "fk.db"
    _seed(db)
    _m45().migrate_db(db)

    conn = sqlite3.connect(str(db))
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    orphans = conn.execute(
        "SELECT COUNT(*) FROM trades t WHERE NOT EXISTS "
        "(SELECT 1 FROM accounts a WHERE a.account_id = t.account_id)").fetchone()[0]
    conn.close()
    assert violations == [] and orphans == 0


def test_merge_aborts_when_the_redundant_row_carries_trades(tmp_path):
    """The duplication becoming REAL (both rows holding a ledger) must abort, not
    silently delete a ledger — and the message must point at deciding an
    authoritative ledger, not re-running the migration."""
    db = tmp_path / "real.db"
    _seed(db, taxable_01_trades=2)
    with pytest.raises(RuntimeError, match="carries 2 trade"):
        _m45().migrate_db(db)
    # nothing deleted — both identities and every trade still present
    assert "acct_taxable_01" in _pseudonyms(db)
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 5  # 3 + 2
    conn.close()


def test_merge_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    _seed(db)
    assert _m45().migrate_db(db) == 1
    assert _m45().migrate_db(db) == 0, "second run must be a no-op"


def test_merge_is_a_noop_without_the_redundant_row(tmp_path):
    """A DB that never had acct_taxable_01 (demo, or a fresh post-merge reseed) is
    left untouched."""
    db = tmp_path / "demo_like.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO accounts (account_id, name, type, tax_treatment, pseudonym,"
        " display_name, managed_by, included_in_household) VALUES "
        "(1,'Personal Fidelity','taxable','taxable','acct_01','Personal Fidelity','self',1)")
    conn.execute("INSERT INTO trades (account_id, ticker) VALUES (1,'VOO')")
    conn.commit(); conn.close()

    assert _m45().migrate_db(db) == 0
    assert _pseudonyms(db) == ["acct_01"]


def test_exposes_migrate_db_for_discovery():
    assert callable(getattr(_m45(), "migrate_db", None))
