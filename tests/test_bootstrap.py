"""Personal-mode bootstrap regression tests.

The Asset Location KeyError tonight was a fresh-environment bug: on a 0-byte
tracker.db, initialize_db() creates only the base schema, so securities never
got the Phase 25.3 sleeve/tax_efficiency columns + rows and build_location_register
raised. bootstrap_personal_db() wires the existing migrations + seeds so a fresh
DB self-assembles. These tests run entirely from COMMITTED seed data (no personal
CSV, no account map), so they execute in CI — which is where the gap hid.
"""
import sqlite3

import pandas as pd

import src.db as db
import src.bootstrap as bootstrap


def _fresh_db(tmp_path, monkeypatch):
    """Point the app at a fresh 0-byte tracker.db; reset process-level caches."""
    d = tmp_path / "data"
    d.mkdir()
    fresh = d / "tracker.db"
    fresh.write_bytes(b"")                          # 0-byte: the fresh-clone case
    monkeypatch.setattr("src.db.DB_PATH", fresh)
    monkeypatch.setattr("src.db._migrated_paths", set())
    monkeypatch.setattr("src.bootstrap._bootstrapped", set())
    return fresh


def test_bootstrap_populates_securities_with_tax_efficiency(tmp_path, monkeypatch):
    """A fresh DB ends up with the securities table carrying the tax_efficiency
    column and the 65 household securities, all classified low/medium/high."""
    fresh = _fresh_db(tmp_path, monkeypatch)
    bootstrap.bootstrap_personal_db()

    conn = sqlite3.connect(str(fresh))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "securities" in tables, "securities table missing after bootstrap"
        assert "fund_compositions" in tables, "fund_compositions table missing after bootstrap"

        cols = {r[1] for r in conn.execute("PRAGMA table_info(securities)")}
        assert {"sleeve_category", "tax_efficiency", "is_in_saa"} <= cols, (
            f"securities missing Phase 25.3 columns; has {sorted(cols)}"
        )
        valid = conn.execute(
            "SELECT COUNT(*) FROM securities WHERE tax_efficiency IN ('low','medium','high')"
        ).fetchone()[0]
        # 65 = the 63 pre-Aug-2026 rows + JCPB and FCASH (the advisor's Aug-2026
        # HLIPX-to-JCPB swap; see data/seed/securities_household.csv).
        assert valid == 65, f"expected 65 household securities with valid tax_efficiency, got {valid}"
    finally:
        conn.close()


def test_bootstrap_lets_build_location_register_run(tmp_path, monkeypatch):
    """build_location_register — the Asset Location call that raised KeyError on
    the missing columns — runs against a freshly-bootstrapped DB. Synthetic
    positions keep this free of any personal CSV."""
    _fresh_db(tmp_path, monkeypatch)
    bootstrap.bootstrap_personal_db()

    from src.household import build_location_register
    from src.location_config import (
        TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY,
    )

    conn = db.get_connection()
    accounts_df = pd.read_sql_query(
        "SELECT account_id, name, type, custodian, is_active, created_at, "
        "tax_treatment, pseudonym, display_name, managed_by FROM accounts", conn)
    securities_df = pd.read_sql_query("SELECT * FROM securities", conn)
    conn.close()

    positions_df = pd.DataFrame([
        {"pseudonym": "acct_01", "symbol": "VOO",  "current_value": 10000.0,
         "total_gain_loss": 0.0, "cost_basis_total": 10000.0},
        {"pseudonym": "acct_01", "symbol": "VGIT", "current_value": 5000.0,
         "total_gain_loss": 0.0, "cost_basis_total": 5000.0},
    ])

    register = build_location_register(  # must NOT raise KeyError on securities columns
        positions_df, accounts_df, securities_df,
        TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY,
    )
    assert register is not None


def test_bootstrap_is_idempotent(tmp_path, monkeypatch):
    """Running the bootstrap a second time against the same DB neither errors nor
    duplicates rows."""
    fresh = _fresh_db(tmp_path, monkeypatch)
    bootstrap.bootstrap_personal_db()
    conn = sqlite3.connect(str(fresh))
    n1 = conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    conn.close()

    # Clear the once-per-process guard so the second call actually re-runs the
    # DB work (that's what "safe to run twice" must guarantee).
    monkeypatch.setattr("src.bootstrap._bootstrapped", set())
    monkeypatch.setattr("src.db._migrated_paths", set())
    bootstrap.bootstrap_personal_db()  # must not raise

    conn = sqlite3.connect(str(fresh))
    n2 = conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    dups = conn.execute(
        "SELECT ticker, COUNT(*) c FROM securities GROUP BY ticker HAVING c > 1"
    ).fetchall()
    conn.close()

    assert n1 == n2, f"securities row count changed on re-bootstrap: {n1} -> {n2}"
    assert not dups, f"duplicate securities after re-bootstrap: {dups}"
