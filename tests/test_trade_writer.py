"""Tests for src/trade_writer.py — write guard, batch writes, thesis inheritance."""
import sys
import pathlib
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.trade_writer import build_thesis_lookup, write_trades_batch


# ── Minimal DB fixture ────────────────────────────────────────────────────────

@pytest.fixture()
def minimal_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with accounts + trades table for write tests."""
    db_path = tmp_path / "test_writer.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        );
        INSERT INTO accounts (name, type) VALUES ('Test Account', 'taxable');

        CREATE TABLE trades (
            trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            ticker     TEXT NOT NULL,
            thesis_id  INTEGER,
            trade_date TEXT NOT NULL,
            action     TEXT NOT NULL,
            shares     REAL NOT NULL,
            price      REAL NOT NULL,
            fees       REAL DEFAULT 0,
            notes      TEXT,
            lot_source TEXT DEFAULT 'initial',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("src.db.DB_PATH", db_path)
    monkeypatch.setattr("src.db._migrated_paths", set())
    return db_path


def _count_trades(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    return n


def _valid_trade(**overrides) -> dict:
    t = {
        "ticker":      "VOO",
        "trade_date":  "2026-05-11",
        "action":      "Buy",
        "shares":      0.1926,
        "price":       519.21,
        "total_value": 100.00,
    }
    t.update(overrides)
    return t


# ── Test 4: write guard disabled → returns False, no write, toast fired ──────

def test_write_guard_blocks_when_disabled(minimal_db):
    with patch("src.trade_writer.is_write_enabled", return_value=False), \
         patch("src.trade_writer.write_guard_toast") as mock_toast:
        result = write_trades_batch([_valid_trade()], 100.0, account_id=1)

    assert result is False
    mock_toast.assert_called_once()
    assert _count_trades(minimal_db) == 0


# ── Test 5: valid input → all rows written, returns True ─────────────────────

def test_write_trades_batch_success(minimal_db):
    trades = [_valid_trade(ticker="VOO"), _valid_trade(ticker="VEA")]
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        result = write_trades_batch(trades, 200.0, account_id=1)

    assert result is True
    assert _count_trades(minimal_db) == 2


# ── Test 6: empty list returns True, no writes ───────────────────────────────

def test_write_trades_batch_empty_list(minimal_db):
    with patch("src.trade_writer.is_write_enabled", return_value=True):
        result = write_trades_batch([], 0.0, account_id=1)

    assert result is True
    assert _count_trades(minimal_db) == 0


# ── Test 7: action normalized to title case ───────────────────────────────────

def test_action_normalized_to_title_case(minimal_db):
    for raw_action in ("buy", "BUY", "Buy"):
        with patch("src.trade_writer.is_write_enabled", return_value=True), \
             patch("src.trade_writer.write_guard_toast"):
            write_trades_batch([_valid_trade(action=raw_action)], 100.0, account_id=1)

    conn = sqlite3.connect(str(minimal_db))
    actions = [r[0] for r in conn.execute("SELECT action FROM trades").fetchall()]
    conn.close()
    assert all(a == "Buy" for a in actions), f"Non-title-case actions: {actions}"


# ── Test 8: missing required field (ticker) returns False, no partial write ──

def test_missing_required_field_returns_false(minimal_db):
    bad_trade = _valid_trade(ticker="")   # empty ticker → invalid
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        result = write_trades_batch([bad_trade], 100.0, account_id=1)

    assert result is False
    assert _count_trades(minimal_db) == 0


# ── Test 9: integrity assertion fires when total_value diverges > $0.10 ──────

def test_integrity_assertion_fires_on_divergence(minimal_db):
    trades = [_valid_trade(total_value=100.0)]
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        with pytest.raises(AssertionError, match="write_trades_batch"):
            write_trades_batch(trades, 200.0, account_id=1)   # 100 vs 200 → $100 divergence

    assert _count_trades(minimal_db) == 0


# ── Test 10: lot_source defaults to "Manual" ─────────────────────────────────

def test_lot_source_defaults_to_manual(minimal_db):
    trade = _valid_trade()   # no lot_source key
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        write_trades_batch([trade], 100.0, account_id=1)

    conn = sqlite3.connect(str(minimal_db))
    row = conn.execute("SELECT lot_source FROM trades LIMIT 1").fetchone()
    conn.close()
    assert row[0] == "Manual"


# ── Test 11: thesis inheritance ───────────────────────────────────────────────

def test_build_thesis_lookup_active_thesis():
    pos_theses = [
        {"thesis_id": 7, "title": "US Large Core — VOO", "status": "active"},
        {"thesis_id": 8, "title": "TIPS — SCHP",         "status": "active"},
        {"thesis_id": 9, "title": "Core Fixed Income — VGIT", "status": "closed"},
    ]
    lookup = build_thesis_lookup(pos_theses)
    assert lookup["VOO"] == 7
    assert lookup["SCHP"] == 8
    assert "VGIT" not in lookup          # closed → excluded


def test_build_thesis_lookup_no_active_thesis():
    pos_theses = [
        {"thesis_id": 9, "title": "US Large Core — VOO", "status": "closed"},
    ]
    lookup = build_thesis_lookup(pos_theses)
    assert lookup.get("VOO") is None    # closed → not in map


def test_write_trades_batch_carries_thesis_id(minimal_db):
    trade = _valid_trade(thesis_id=42)
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        write_trades_batch([trade], 100.0, account_id=1)

    conn = sqlite3.connect(str(minimal_db))
    row = conn.execute("SELECT thesis_id FROM trades LIMIT 1").fetchone()
    conn.close()
    assert row[0] == 42


def test_write_trades_batch_no_thesis_id_when_missing(minimal_db):
    trade = _valid_trade()   # no thesis_id
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        write_trades_batch([trade], 100.0, account_id=1)

    conn = sqlite3.connect(str(minimal_db))
    row = conn.execute("SELECT thesis_id FROM trades LIMIT 1").fetchone()
    conn.close()
    assert row[0] is None


# ── Part C: account_id is REQUIRED and never guessed ─────────────────────────

def test_write_trades_batch_raises_when_account_id_none(minimal_db):
    """A None account_id must RAISE — not resolve to a plausible default. A
    wrong-account write is unrecoverable, so refusing to guess is the point."""
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        with pytest.raises(ValueError, match="explicit account_id"):
            write_trades_batch([_valid_trade()], 100.0, account_id=None)
    assert _count_trades(minimal_db) == 0


def test_write_trades_batch_raises_on_unspecified_account(minimal_db):
    """Omitting account_id entirely is a hard error (no positional default)."""
    with pytest.raises(TypeError):
        write_trades_batch([_valid_trade()], 100.0)  # type: ignore[call-arg]


def test_write_trades_batch_raises_on_inactive_or_unknown_account(minimal_db):
    """An account_id that is not an active account must RAISE rather than write —
    a non-existent account is as unrecoverable a mis-file as the wrong one."""
    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        with pytest.raises(ValueError, match="not an active account"):
            write_trades_batch([_valid_trade()], 100.0, account_id=999)
    assert _count_trades(minimal_db) == 0


def test_write_trades_batch_writes_to_the_named_account(minimal_db):
    """The batch lands under the account the caller named — nothing else."""
    # Add a second account so 'lowest active id' and 'the named one' can differ.
    conn = sqlite3.connect(str(minimal_db))
    conn.execute("INSERT INTO accounts (name, type) VALUES ('Roth', 'retirement')")
    conn.commit()
    roth_id = conn.execute("SELECT account_id FROM accounts WHERE name='Roth'").fetchone()[0]
    conn.close()

    with patch("src.trade_writer.is_write_enabled", return_value=True), \
         patch("src.trade_writer.write_guard_toast"):
        assert write_trades_batch([_valid_trade()], 100.0, account_id=roth_id) is True

    conn = sqlite3.connect(str(minimal_db))
    rows = [r[0] for r in conn.execute("SELECT account_id FROM trades").fetchall()]
    conn.close()
    assert rows == [roth_id], f"expected the named account {roth_id}, got {rows}"
