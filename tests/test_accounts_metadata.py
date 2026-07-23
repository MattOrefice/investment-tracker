"""Tests for household accounts metadata after the account_number PII removal.

Accounts are keyed on pseudonym; the raw ``account_number`` column no longer
exists. These tests build a pre-migration accounts table (WITH account_number,
mirroring the old live shape), apply the ``src.db._drop_account_number``
migration, then seed and assert on pseudonyms. No raw account numbers appear
anywhere in this file.
"""
import sqlite3
import pathlib

import pandas as pd
import pytest

from src.db import _drop_account_number, _add_included_in_household

ROOT = pathlib.Path(__file__).resolve().parent.parent

ALLOWED_TAX_TREATMENTS = frozenset({
    "taxable", "traditional_ira", "roth_ira", "hsa", "workplace_plan", "other"
})

# The 7 seeded household pseudonyms — the only identifiers that reach the schema.
# The 7 household accounts. The self-directed taxable book is acct_01 (the db.py
# base account carrying the ledger) after the Phase 45 identity merge — the
# household_accounts seed no longer creates a separate acct_taxable_01.
HOUSEHOLD_PSEUDONYMS = frozenset({
    "acct_01", "acct_taxable_02", "acct_trad_ira_01", "acct_roth_01",
    "acct_wkpl_01", "acct_wkpl_02", "acct_hsa_01",
})

# Pre-migration accounts shape: mirrors the old live DB (account_number present,
# with a unique index on it), plus one legacy paper-trade row.
_PRE_MIGRATION_SCHEMA = """
    CREATE TABLE accounts (
        account_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        name           TEXT NOT NULL UNIQUE,
        type           TEXT NOT NULL,
        custodian      TEXT,
        is_active      INTEGER DEFAULT 1,
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
        account_number TEXT,
        tax_treatment  TEXT DEFAULT 'other',
        pseudonym      TEXT,
        display_name   TEXT,
        managed_by     TEXT DEFAULT 'self'
    );
    CREATE UNIQUE INDEX ux_accounts_account_number ON accounts (account_number);
    CREATE UNIQUE INDEX ux_accounts_pseudonym      ON accounts (pseudonym);
    INSERT INTO accounts (name, type, custodian, pseudonym, display_name,
                          tax_treatment, managed_by)
    VALUES ('Personal Fidelity', 'taxable', 'Fidelity', 'acct_01',
            'Personal Fidelity', 'taxable', 'self');
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cols(db_path):
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    conn.close()
    return cols


def _indexes(db_path):
    conn = sqlite3.connect(str(db_path))
    idx = {row[1] for row in conn.execute("PRAGMA index_list(accounts)")}
    conn.close()
    return idx


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM accounts").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _household_rows(db_path):
    return [r for r in _rows(db_path) if r["pseudonym"] in HOUSEHOLD_PSEUDONYMS]


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def base_db(tmp_path):
    """A DB with the PRE-migration accounts schema (account_number present)."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_PRE_MIGRATION_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def migrated_db(base_db):
    conn = sqlite3.connect(str(base_db))
    _drop_account_number(conn)
    _add_included_in_household(conn)
    conn.close()
    return base_db


@pytest.fixture()
def seeded_db(migrated_db):
    from src.seed.household_accounts import seed_household_accounts
    seed_household_accounts(migrated_db)
    return migrated_db


# ── Migration tests ────────────────────────────────────────────────────────────

def test_migration_drops_account_number_column(migrated_db):
    assert "account_number" not in _cols(migrated_db)


def test_migration_keeps_metadata_columns(migrated_db):
    cols = _cols(migrated_db)
    for col in ("tax_treatment", "pseudonym", "display_name", "managed_by"):
        assert col in cols, f"Missing column after migration: {col!r}"


def test_migration_swaps_unique_indexes(migrated_db):
    idx = _indexes(migrated_db)
    assert "ux_accounts_account_number" not in idx, "account_number index must be dropped"
    assert "ux_accounts_pseudonym" in idx, "pseudonym unique index must exist"


def test_migration_idempotent(base_db):
    conn = sqlite3.connect(str(base_db))
    _drop_account_number(conn)
    _drop_account_number(conn)  # second run must not raise or duplicate
    conn.close()
    assert "account_number" not in _cols(base_db)


def test_migration_preserves_legacy_row(migrated_db):
    legacy = next(r for r in _rows(migrated_db) if r["account_id"] == 1)
    assert legacy["name"] == "Personal Fidelity"
    assert legacy["pseudonym"] == "acct_01"


# ── Seed tests ─────────────────────────────────────────────────────────────────

def test_household_has_7_accounts_acct01_plus_6_seeded(seeded_db):
    # 7 household accounts = acct_01 (the self-directed taxable base account, from
    # db.py, carrying the ledger) + the 6 accounts household_accounts seeds. The
    # self-directed book is NOT a separately-seeded acct_taxable_01 (Phase 45 merge).
    rows = _household_rows(seeded_db)
    assert len(rows) == 7
    assert {r["pseudonym"] for r in rows} - {"acct_01"} == {
        "acct_taxable_02", "acct_trad_ira_01", "acct_roth_01",
        "acct_wkpl_01", "acct_wkpl_02", "acct_hsa_01",
    }


def test_all_household_accounts_have_metadata(seeded_db):
    for row in _household_rows(seeded_db):
        for col in ("tax_treatment", "pseudonym", "display_name", "managed_by"):
            assert row[col], f"pseudonym={row['pseudonym']!r} has null/empty {col!r}"


def test_exactly_one_self_managed(seeded_db):
    self_managed = [r for r in _household_rows(seeded_db) if r.get("managed_by") == "self"]
    assert len(self_managed) == 1, f"Expected 1 self-managed household account, got {len(self_managed)}"
    assert self_managed[0]["pseudonym"] == "acct_01"


def test_pseudonyms_unique(seeded_db):
    pseudonyms = [r["pseudonym"] for r in _rows(seeded_db) if r["pseudonym"]]
    assert len(pseudonyms) == len(set(pseudonyms)), "Duplicate pseudonyms found"


def test_tax_treatments_in_enum(seeded_db):
    for row in _household_rows(seeded_db):
        assert row["tax_treatment"] in ALLOWED_TAX_TREATMENTS, (
            f"Invalid tax_treatment {row['tax_treatment']!r} for {row['pseudonym']!r}"
        )


def test_workplace_uuid_row_maps_to_acct_wkpl_02(seeded_db):
    row = next((r for r in _rows(seeded_db) if r["pseudonym"] == "acct_wkpl_02"), None)
    assert row is not None, "acct_wkpl_02 not found after seeding"
    assert row["tax_treatment"] == "workplace_plan"
    assert row["managed_by"] == "external"


def test_account_id_1_untouched_after_seed(seeded_db):
    acct1 = next((r for r in _rows(seeded_db) if r["account_id"] == 1), None)
    assert acct1 is not None, "account_id=1 was deleted by seeding"
    assert acct1["name"] == "Personal Fidelity", f"account_id=1 name changed to {acct1['name']!r}"
    assert "account_number" not in acct1, "account_number column must be gone entirely"


def test_seed_idempotent(seeded_db):
    from src.seed.household_accounts import seed_household_accounts
    seed_household_accounts(seeded_db)  # second run must not raise or duplicate
    assert len(_household_rows(seeded_db)) == 7


# ── get_account_display tests (keyed on pseudonym) ─────────────────────────────

def _accounts_df(db_path):
    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query("SELECT * FROM accounts", conn)
    conn.close()
    return df


def test_get_account_display_no_account_number_key(seeded_db):
    from src.household import get_account_display
    result = get_account_display("acct_01", _accounts_df(seeded_db))
    assert "account_number" not in result


def test_get_account_display_correct_keys_and_values(seeded_db):
    from src.household import get_account_display
    result = get_account_display("acct_01", _accounts_df(seeded_db))
    assert set(result.keys()) == {"pseudonym", "display_name", "tax_treatment", "managed_by"}
    assert result["pseudonym"] == "acct_01"
    assert result["managed_by"] == "self"
    assert result["tax_treatment"] == "taxable"


def test_get_account_display_unknown_returns_empty_strings(seeded_db):
    from src.household import get_account_display
    result = get_account_display("acct_does_not_exist", _accounts_df(seeded_db))
    assert result == {"pseudonym": "", "display_name": "", "tax_treatment": "", "managed_by": ""}


def test_get_account_display_workplace_uuid_account(seeded_db):
    from src.household import get_account_display
    result = get_account_display("acct_wkpl_02", _accounts_df(seeded_db))
    assert result["pseudonym"] == "acct_wkpl_02"
    assert result["managed_by"] == "external"
