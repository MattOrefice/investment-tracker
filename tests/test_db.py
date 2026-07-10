"""Regression guards for src.db initialization.

A fresh clone's first personal-mode launch runs against a 0-byte ``tracker.db``.
Before the schema-before-migration reorder, ``initialize_db()`` ran ``_auto_migrate``
(``ALTER TABLE trades ...``) *before* ``executescript(SCHEMA)`` created the trades
table, raising ``OperationalError: no such table: trades``. Schema creation must
precede migrations; migrations are then a no-op on a fresh DB.
"""
import sqlite3

import src.db as db


def test_initialize_db_on_empty_file_creates_trades_with_lot_source(tmp_path, monkeypatch):
    """initialize_db() against a 0-byte DB file must succeed and yield a valid
    schema — the trades table with its lot_source column — with migrations a
    no-op rather than crashing on a not-yet-created table."""
    empty = tmp_path / "fresh.db"
    empty.write_bytes(b"")                       # 0-byte file: the fresh-clone case
    assert empty.stat().st_size == 0

    monkeypatch.setattr("src.db.DB_PATH", empty)
    monkeypatch.setattr("src.db._migrated_paths", set())

    db.initialize_db()                           # must not raise (pre-fix: OperationalError)

    conn = sqlite3.connect(empty)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "trades" in tables, "schema creation must produce the trades table"
        trade_cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        assert "lot_source" in trade_cols, "trades must carry the lot_source column"
        # initialize_db ran to completion — the seed happens after migration.
        seeded = conn.execute(
            "SELECT 1 FROM accounts WHERE name = ?", ("Personal Fidelity",)
        ).fetchone()
        assert seeded is not None, "the personal account seed must have run"
    finally:
        conn.close()
