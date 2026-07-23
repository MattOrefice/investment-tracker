"""Benchmark expense-ratio provenance + the phase-41 migration.

Guards that the 10 benchmark ERs are regenerable from source (they lived only in
the committed demo.db binary before), each carries an in-code source + as-of date,
DJP is retired in favour of DBC, and the migration heals a legacy DB idempotently.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.seed_securities import BENCHMARKS, HOLDINGS

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TEN = {"SPY", "QUAL", "IWD", "IWM", "EFA", "EEM", "IEF", "TIP", "BIL", "DBC"}
_INTL = {"IQLT", "EFV", "SCZ"}   # now sourced too (exhibit completeness)
_ALL_BENCH = _TEN | _INTL


def _m41():
    spec = importlib.util.spec_from_file_location(
        "m41", str(_ROOT / "tools" / "migrate_benchmark_ers_phase41.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Source provenance ─────────────────────────────────────────────────────────

def test_djp_retired_dbc_is_the_real_assets_benchmark():
    tickers = {b["ticker"] for b in BENCHMARKS}
    assert "DJP" not in tickers, "DJP delisted May 2020 — must not be a source benchmark"
    assert "DBC" in tickers
    dbc = next(b for b in BENCHMARKS if b["ticker"] == "DBC")
    assert dbc["asset_class"] == "Real Assets"


def test_every_benchmark_carries_sourced_er_with_provenance():
    """All 13 benchmarks — including the three international-split ones (now
    sourced for exhibit completeness) — carry a positive ER + issuer source + date."""
    by = {b["ticker"]: b for b in BENCHMARKS}
    for t in _ALL_BENCH:
        b = by[t]
        assert isinstance(b["expense_ratio"], float) and b["expense_ratio"] > 0, f"{t} ER missing"
        assert b.get("er_source"), f"{t} has no er_source"
        assert b.get("er_as_of"), f"{t} has no er_as_of (date recorded next to the value)"


def test_intl_benchmarks_pair_against_their_holdings():
    """The exhibit now has a benchmark ER for every international tilt sleeve, so
    each shows a real holding-vs-benchmark comparison rather than a blank."""
    by = {b["ticker"]: b for b in BENCHMARKS}
    for t in _INTL:
        assert isinstance(by[t]["expense_ratio"], float) and by[t]["expense_ratio"] > 0


def test_dbc_er_is_corrected_value_not_the_wrong_binary_one():
    dbc = next(b for b in BENCHMARKS if b["ticker"] == "DBC")
    assert dbc["expense_ratio"] == 0.0085, "DBC is 0.85% (mgmt fee); the binary's 0.70% was wrong"


def test_every_holding_carries_er_provenance():
    for h in HOLDINGS:
        assert isinstance(h["expense_ratio"], float), f"{h['ticker']} ER not a float"
        assert h.get("er_source"), f"{h['ticker']} holding has no er_source"
        assert h.get("er_as_of"), f"{h['ticker']} holding has no er_as_of"


# ── Regenerability: a reseed reproduces the ERs (the entire point) ────────────

def _reseed(tmp_path, monkeypatch):
    db = tmp_path / "reseed.db"
    import src.db as _db
    monkeypatch.setattr(_db, "DB_PATH", db)
    monkeypatch.setattr(_db, "_migrated_paths", set(), raising=False)
    from src.db import initialize_db
    from src import seed_saa, seed_securities
    initialize_db(); seed_saa.seed(); seed_securities.seed()
    return db


def test_fresh_reseed_regenerates_benchmark_ers(tmp_path, monkeypatch):
    db = _reseed(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(db))
    ers = dict(conn.execute(
        "SELECT ticker, expense_ratio FROM securities WHERE security_type='benchmark'"))
    conn.close()
    for t in _ALL_BENCH:
        assert ers.get(t) is not None, f"{t} came back NULL on reseed — not regenerable"
    assert ers.get("DBC") == 0.0085 and "DJP" not in ers


# ── Migration: heals a legacy DB, idempotent, discoverable ───────────────────

def test_migration_heals_legacy_state_and_is_idempotent(tmp_path, monkeypatch):
    db = _reseed(tmp_path, monkeypatch)
    # Recreate the committed-demo.db defect: wrong DBC ER + NULL intl types.
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE securities SET expense_ratio=0.007 WHERE ticker='DBC'")
    conn.execute("UPDATE securities SET security_type=NULL WHERE ticker IN ('IQLT','EFV','SCZ')")
    conn.commit(); conn.close()

    m = _m41()
    changed = m.migrate_db(db)
    assert changed >= 4

    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
    dbc = conn.execute("SELECT expense_ratio, security_type FROM securities WHERE ticker='DBC'").fetchone()
    assert dbc["expense_ratio"] == 0.0085 and dbc["security_type"] == "benchmark"
    for t in _INTL:
        st = conn.execute("SELECT security_type FROM securities WHERE ticker=?", (t,)).fetchone()["security_type"]
        assert st == "benchmark", f"{t} security_type not healed"
    conn.close()

    assert m.migrate_db(db) == 0, "second run must be a no-op (idempotent)"


def test_migration_converts_djp_to_dbc_on_a_personal_style_book(tmp_path, monkeypatch):
    """A book carrying the stale DJP row (no DBC) is healed to DBC."""
    db = _reseed(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(db))
    ra = conn.execute("SELECT asset_class_id FROM asset_classes WHERE name='Real Assets'").fetchone()[0]
    conn.execute("DELETE FROM securities WHERE ticker='DBC'")
    conn.execute("INSERT INTO securities (ticker,name,asset_class_id,security_type,expense_ratio) VALUES ('DJP','iPath Bloomberg Commodity ETN',?,'benchmark',NULL)", (ra,))
    conn.commit(); conn.close()

    m = _m41(); m.migrate_db(db)
    conn = sqlite3.connect(str(db))
    have = {r[0] for r in conn.execute("SELECT ticker FROM securities WHERE ticker IN ('DJP','DBC')")}
    dbc_er = conn.execute("SELECT expense_ratio FROM securities WHERE ticker='DBC'").fetchone()[0]
    conn.close()
    assert have == {"DBC"}, f"expected DJP retired, DBC present; got {have}"
    assert dbc_er == 0.0085


def test_migration_exposes_migrate_db_for_discovery():
    m = _m41()
    assert callable(getattr(m, "migrate_db", None)), (
        "run_pending_migrations only runs migrations exposing migrate_db(db_path)")
