"""Tests for Phase 25.2: accounts metadata and pseudonymization."""
import importlib.util
import sqlite3
import pathlib

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

ALLOWED_TAX_TREATMENTS = frozenset({
    "taxable", "traditional_ira", "roth_ira", "hsa", "workplace_plan", "other"
})

RAW_ACCOUNT_NUMBERS = [
    "acct_taxable_02",
    "acct_roth_01",
    "acct_trad_ira_01",
    "acct_taxable_01",
    "74369",
    "acct_hsa_01",
    "acct_wkpl_02",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _import_migration():
    spec = importlib.util.spec_from_file_location(
        "migrate_accounts_phase25_2",
        ROOT / "tools" / "migrate_accounts_phase25_2.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cols(db_path):
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    conn.close()
    return cols


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM accounts").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def base_db(tmp_path):
    """Minimal DB with the pre-migration accounts schema and 1 legacy row."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            type       TEXT NOT NULL,
            custodian  TEXT,
            is_active  INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO accounts (name, type, custodian)
        VALUES ('Personal Fidelity', 'taxable', 'Fidelity');
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def migrated_db(base_db):
    _import_migration().migrate_db(base_db)
    return base_db


@pytest.fixture()
def seeded_db(migrated_db):
    from src.seed.household_accounts import seed_household_accounts
    seed_household_accounts(migrated_db)
    return migrated_db


# ── Migration tests ────────────────────────────────────────────────────────────

def test_migration_adds_all_columns(migrated_db):
    cols = _cols(migrated_db)
    for col in ("account_number", "tax_treatment", "pseudonym", "display_name", "managed_by"):
        assert col in cols, f"Missing column after migration: {col!r}"


def test_migration_idempotent(base_db):
    migration = _import_migration()
    migration.migrate_db(base_db)
    migration.migrate_db(base_db)  # second run must not raise or duplicate
    cols = _cols(base_db)
    for col in ("account_number", "tax_treatment", "pseudonym", "display_name", "managed_by"):
        assert col in cols


def test_migration_sets_placeholder_on_legacy_row(migrated_db):
    rows = _rows(migrated_db)
    legacy = next(r for r in rows if r["account_id"] == 1)
    assert legacy["pseudonym"] is not None and legacy["pseudonym"] != ""
    assert legacy["display_name"] == "Personal Fidelity"


# ── Seed tests ─────────────────────────────────────────────────────────────────

def test_seed_creates_7_household_accounts(seeded_db):
    rows = _rows(seeded_db)
    household = [r for r in rows if r["account_number"] is not None]
    assert len(household) == 7, f"Expected 7 household accounts, got {len(household)}"


def test_all_household_accounts_have_metadata(seeded_db):
    rows = _rows(seeded_db)
    for row in rows:
        if row["account_number"] is None:
            continue
        for col in ("tax_treatment", "pseudonym", "display_name", "managed_by"):
            assert row[col], (
                f"account_number={row['account_number']!r} has null/empty {col!r}"
            )


def test_exactly_one_self_managed(seeded_db):
    rows = _rows(seeded_db)
    self_managed = [
        r for r in rows
        if r.get("managed_by") == "self" and r["account_number"] is not None
    ]
    assert len(self_managed) == 1, f"Expected 1 self-managed household account, got {len(self_managed)}"
    assert self_managed[0]["account_number"] == "acct_taxable_01"


def test_pseudonyms_unique(seeded_db):
    rows = _rows(seeded_db)
    pseudonyms = [r["pseudonym"] for r in rows if r["pseudonym"]]
    assert len(pseudonyms) == len(set(pseudonyms)), "Duplicate pseudonyms found"


def test_tax_treatments_in_enum(seeded_db):
    rows = _rows(seeded_db)
    for row in rows:
        if row["account_number"] is None:
            continue
        assert row["tax_treatment"] in ALLOWED_TAX_TREATMENTS, (
            f"Invalid tax_treatment {row['tax_treatment']!r} for {row['account_number']!r}"
        )


def test_uuid_account_maps_to_acct_wkpl_02(seeded_db):
    rows = _rows(seeded_db)
    uuid_acct = "acct_wkpl_02"
    uuid_row = next((r for r in rows if r["account_number"] == uuid_acct), None)
    assert uuid_row is not None, f"UUID account {uuid_acct!r} not found after seeding"
    assert uuid_row["pseudonym"] == "acct_wkpl_02"


def test_account_id_1_untouched_after_seed(seeded_db):
    rows = _rows(seeded_db)
    acct1 = next((r for r in rows if r["account_id"] == 1), None)
    assert acct1 is not None, "account_id=1 was deleted by seeding"
    assert acct1["name"] == "Personal Fidelity", (
        f"account_id=1 name changed to {acct1['name']!r}"
    )
    assert acct1["account_number"] is None, (
        "account_id=1 should have no account_number; paper-trade trades FK to this row"
    )


def test_seed_idempotent(seeded_db):
    from src.seed.household_accounts import seed_household_accounts
    seed_household_accounts(seeded_db)  # second run must not raise or duplicate
    rows = _rows(seeded_db)
    household = [r for r in rows if r["account_number"] is not None]
    assert len(household) == 7


# ── get_account_display tests ──────────────────────────────────────────────────

def _accounts_df(db_path):
    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query("SELECT * FROM accounts", conn)
    conn.close()
    return df


def test_get_account_display_no_raw_account_number(seeded_db):
    from src.household import get_account_display
    df = _accounts_df(seeded_db)
    result = get_account_display("acct_taxable_01", df)
    assert "account_number" not in result
    for v in result.values():
        assert "acct_taxable_01" not in str(v), "Raw account number leaked into display dict"


def test_get_account_display_correct_keys_and_values(seeded_db):
    from src.household import get_account_display
    df = _accounts_df(seeded_db)
    result = get_account_display("acct_taxable_01", df)
    assert set(result.keys()) == {"pseudonym", "display_name", "tax_treatment", "managed_by"}
    assert result["pseudonym"] == "acct_taxable_01"
    assert result["managed_by"] == "self"
    assert result["tax_treatment"] == "taxable"


def test_get_account_display_unknown_returns_empty_strings(seeded_db):
    from src.household import get_account_display
    df = _accounts_df(seeded_db)
    result = get_account_display("UNKNOWN_ACCT_99", df)
    assert result == {"pseudonym": "", "display_name": "", "tax_treatment": "", "managed_by": ""}


def test_get_account_display_uuid_account(seeded_db):
    from src.household import get_account_display
    df = _accounts_df(seeded_db)
    result = get_account_display("acct_wkpl_02", df)
    assert result["pseudonym"] == "acct_wkpl_02"
    assert result["managed_by"] == "external"


# ── CI grep: no raw account numbers in pages/ ─────────────────────────────────

def test_no_raw_account_numbers_in_pages():
    pages_dir = ROOT / "pages"
    for acct_num in RAW_ACCOUNT_NUMBERS:
        for py_file in pages_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            assert acct_num not in content, (
                f"Raw account number {acct_num!r} found in {py_file.relative_to(ROOT)}"
            )
