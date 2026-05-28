"""Tests for Phase 25.4: fund composition look-through."""
import importlib.util
import pathlib
import sqlite3

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"

RFUTX_VALUE    = 66_167.75
GAOSX_VALUE    = 10_681.92
CUSIP_VALUE    =    821.02

FUND_SYMBOLS = ["RFUTX", "GAOSX", "31564E540"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _import_migration():
    spec = importlib.util.spec_from_file_location(
        "migrate_fund_compositions_phase25_4",
        ROOT / "tools" / "migrate_fund_compositions_phase25_4.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _compositions_from_db(db_path) -> pd.DataFrame:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM fund_compositions").fetchall()
    conn.close()
    return pd.DataFrame([dict(r) for r in rows])


def _securities_from_db(db_path) -> pd.DataFrame:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ticker, sleeve_category FROM securities").fetchall()
    conn.close()
    return pd.DataFrame([dict(r) for r in rows])


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def base_db(tmp_path):
    """Minimal DB for migration and seed tests."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE asset_classes (
            asset_class_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER,
            target_weight REAL NOT NULL DEFAULT 0.0,
            tolerance_band REAL NOT NULL DEFAULT 0.0
        );
        CREATE TABLE securities (
            ticker TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            asset_class_id INTEGER NOT NULL,
            sleeve_category TEXT
        );
        INSERT INTO asset_classes (name, target_weight, tolerance_band)
        VALUES ('Equity', 0.78, 0.05);
        INSERT INTO securities (ticker, name, asset_class_id, sleeve_category)
        VALUES ('VOO',       'Vanguard S&P 500 ETF',         1, 'us_large_core');
        INSERT INTO securities (ticker, name, asset_class_id, sleeve_category)
        VALUES ('RFUTX',     'American Funds 2060',          1, 'target_date');
        INSERT INTO securities (ticker, name, asset_class_id, sleeve_category)
        VALUES ('GAOSX',     'JPMorgan Global Allocation',   1, 'multi_asset');
        INSERT INTO securities (ticker, name, asset_class_id, sleeve_category)
        VALUES ('31564E540', 'Fidelity Freedom Index 2065',  1, 'target_date');
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
    from src.seed.fund_compositions import seed_fund_compositions
    seed_fund_compositions(migrated_db)
    return migrated_db


# ── Migration tests ────────────────────────────────────────────────────────────

def test_migration_creates_table(migrated_db):
    conn = sqlite3.connect(str(migrated_db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "fund_compositions" in tables


def test_migration_idempotent(base_db):
    migration = _import_migration()
    migration.migrate_db(base_db)
    migration.migrate_db(base_db)  # must not raise
    conn = sqlite3.connect(str(base_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='fund_compositions'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_migration_correct_schema(migrated_db):
    conn = sqlite3.connect(str(migrated_db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(fund_compositions)")}
    conn.close()
    assert cols == {"fund_symbol", "underlying_sleeve", "weight", "as_of_date", "source"}


# ── Seed / weight-sum tests ────────────────────────────────────────────────────

def test_fund_weights_sum_to_one(seeded_db):
    df = _compositions_from_db(seeded_db)
    for symbol in FUND_SYMBOLS:
        total = df[df["fund_symbol"] == symbol]["weight"].sum()
        assert abs(total - 1.0) <= 0.01, (
            f"{symbol} weights sum to {total:.4f}, expected 1.00 ± 0.01"
        )


def test_rfutx_has_6_sleeves(seeded_db):
    df = _compositions_from_db(seeded_db)
    rfutx_rows = df[df["fund_symbol"] == "RFUTX"]
    assert len(rfutx_rows) == 6, f"RFUTX expected 6 composition rows, got {len(rfutx_rows)}"


def test_gaosx_has_7_sleeves(seeded_db):
    df = _compositions_from_db(seeded_db)
    gaosx_rows = df[df["fund_symbol"] == "GAOSX"]
    assert len(gaosx_rows) == 7, f"GAOSX expected 7 composition rows, got {len(gaosx_rows)}"


def test_seed_idempotent(seeded_db):
    from src.seed.fund_compositions import seed_fund_compositions
    before = len(_compositions_from_db(seeded_db))
    seed_fund_compositions(seeded_db)
    after  = len(_compositions_from_db(seeded_db))
    assert before == after, f"Row count changed on re-seed: {before} → {after}"


# ── look_through_position unit tests (DataFrame-based, no DB) ─────────────────

@pytest.fixture()
def compositions_df():
    from src.seed.fund_compositions import _COMPOSITIONS
    return pd.DataFrame(
        [{"fund_symbol": s, "underlying_sleeve": sl, "weight": w}
         for s, sl, w in _COMPOSITIONS]
    )


@pytest.fixture()
def securities_df():
    return pd.DataFrame([
        {"ticker": "VOO",       "sleeve_category": "us_large_core"},
        {"ticker": "RFUTX",     "sleeve_category": "target_date"},
        {"ticker": "GAOSX",     "sleeve_category": "multi_asset"},
        {"ticker": "31564E540", "sleeve_category": "target_date"},
    ])


def test_rfutx_look_through_row_count(compositions_df, securities_df):
    from src.household import look_through_position
    result = look_through_position("RFUTX", RFUTX_VALUE, compositions_df, securities_df)
    assert len(result) == 6, f"Expected 6 rows for RFUTX, got {len(result)}"


def test_rfutx_look_through_sums_to_holding_value(compositions_df, securities_df):
    from src.household import look_through_position
    result = look_through_position("RFUTX", RFUTX_VALUE, compositions_df, securities_df)
    total = result["dollar_value"].sum()
    assert abs(total - RFUTX_VALUE) <= 1.00, (
        f"RFUTX look-through sum ${total:,.2f} differs from holding value "
        f"${RFUTX_VALUE:,.2f} by more than $1"
    )


def test_plain_saa_ticker_single_row(compositions_df, securities_df):
    from src.household import look_through_position
    result = look_through_position("VOO", 10_000.00, compositions_df, securities_df)
    assert len(result) == 1, f"VOO (no composition) should return 1 row, got {len(result)}"
    assert result.iloc[0]["sleeve"] == "us_large_core"
    assert abs(result.iloc[0]["dollar_value"] - 10_000.00) < 0.01


def test_missing_symbol_raises_value_error(compositions_df, securities_df):
    from src.household import look_through_position
    empty_sec = pd.DataFrame([{"ticker": "VOO", "sleeve_category": "us_large_core"}])
    with pytest.raises(ValueError, match="no composition or sleeve_category"):
        look_through_position(
            "FAKEXXX", 1000.00, compositions_df, empty_sec
        )


def test_symbol_with_no_sleeve_category_raises(compositions_df):
    from src.household import look_through_position
    sec_no_sleeve = pd.DataFrame([{"ticker": "FAKEXXX", "sleeve_category": None}])
    with pytest.raises(ValueError):
        look_through_position("FAKEXXX", 500.00, compositions_df, sec_no_sleeve)


# ── Live look-through against tracker.db ──────────────────────────────────────

def _skip_if_no_holdings():
    holdings_csv = ROOT / "data" / "uploads" / "Portfolio_Positions_May-27-2026.csv"
    if not holdings_csv.exists():
        pytest.skip("Holdings CSV not present (personal-mode file)")


def test_live_rfutx_look_through():
    _skip_if_no_holdings()
    comp_df = _compositions_from_db(TRACKER_DB)
    sec_df  = _securities_from_db(TRACKER_DB)
    from src.household import look_through_position
    result = look_through_position("RFUTX", RFUTX_VALUE, comp_df, sec_df)
    assert len(result) == 6
    assert abs(result["dollar_value"].sum() - RFUTX_VALUE) <= 1.00


def test_live_gaosx_look_through():
    _skip_if_no_holdings()
    comp_df = _compositions_from_db(TRACKER_DB)
    sec_df  = _securities_from_db(TRACKER_DB)
    from src.household import look_through_position
    result = look_through_position("GAOSX", GAOSX_VALUE, comp_df, sec_df)
    assert len(result) == 7
    assert abs(result["dollar_value"].sum() - GAOSX_VALUE) <= 1.00


def test_live_cusip_look_through():
    _skip_if_no_holdings()
    comp_df = _compositions_from_db(TRACKER_DB)
    sec_df  = _securities_from_db(TRACKER_DB)
    from src.household import look_through_position
    result = look_through_position("31564E540", CUSIP_VALUE, comp_df, sec_df)
    assert len(result) == 6
    assert abs(result["dollar_value"].sum() - CUSIP_VALUE) <= 1.00
