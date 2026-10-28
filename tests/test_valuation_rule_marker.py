"""A quarter lock records the valuation rule it is on, and a raw-close lock holds the
book's ledger (#369's first PR, decided by the owner on October 27 and 28, 2026).

The rule is set by the quarter a lock covers, not by when it is captured: a quarter
ending on or after December 31, 2026 is on the raw-close rule, and any earlier quarter
is on adjusted levels whenever its lock is taken. Every lock captured from this PR on
carries its marker; a lock with none, as every lock taken before it, reads as adjusted
levels. A raw-close lock holds the ledger through the quarter's last day, so a later lot
rebuild cannot move it; an adjusted-levels lock holds none, as before. Locks already
hold their tickers' dividends through their last day (#382). Nothing reads the marker
or the ledger yet.

The captures run on a scratch copy of demo.db, offline, with the gap fetch off as
tools/lock_demo_quarters.py takes the committed locks, and today pinned to January 4,
2027. demo.db's prices end September 30, 2026, so the copy gets a flat close on every
NYSE session from October 1 to December 31 for each ticker it prices, which a Q4 2026
lock needs to be taken at all. It also gets one trade inside Q4 and one after it, so the
ledger's last day shows.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src import cache
from tests.conftest import FROZEN_BOOK, lock_with_the_gap_fetch_off, pin_today, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
JAN_4_2027 = date(2027, 1, 4)
IN_Q4 = ("VOO", "2026-11-16")
AFTER_Q4 = ("VOO", "2027-01-04")


def _point_at(mp, copy: Path) -> None:
    import src.db as db
    import src.macro as macro
    import src.prices as prices

    def _offline(*_a, **_k):
        raise OSError("network blocked: the lock reads the scratch copy only")

    mp.setattr(socket, "getaddrinfo", _offline)
    mp.setattr(prices._SESSION, "get", _offline)
    mp.setattr(macro, "fetch_fred_series", _offline)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    prices._reset_trailing_memo()
    lock_with_the_gap_fetch_off(mp)


def _extend_through_q4(copy: Path) -> None:
    """A flat close on every Q4 2026 NYSE session for each ticker, and one VOO trade on
    each side of the quarter's end."""
    from src.demo_refresh import is_session
    con = sqlite3.connect(copy)
    try:
        with con:
            last = dict(con.execute(
                "SELECT ticker, close FROM prices p WHERE price_date = "
                "(SELECT MAX(price_date) FROM prices q WHERE q.ticker = p.ticker)").fetchall())
            ends = {r[0] for r in con.execute(
                "SELECT MAX(price_date) FROM prices GROUP BY ticker")}
            assert ends == {"2026-09-30"}, f"premise: every history ends September 30: {ends}"
            days = [d.isoformat() for d in (date(2026, 10, 1) + timedelta(n) for n in range(92))
                    if is_session(d)]
            con.executemany(
                "INSERT INTO prices (ticker, price_date, close, adj_close) VALUES (?, ?, ?, NULL)",
                [(t, d, c) for t, c in last.items() for d in days])
            for ticker, day in (IN_Q4, AFTER_Q4):
                con.execute(
                    "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
                    "fees, notes, lot_source) VALUES (1, ?, ?, 'Buy', 0.1, ?, 0, 'test', 'Manual')",
                    (ticker, day, last[ticker]))
    finally:
        con.close()


@pytest.fixture
def book_in_2027(tmp_path, monkeypatch):
    """A scratch copy of demo.db carried through Q4 2026, with today January 4, 2027."""
    copy = tmp_path / "demo.db"
    shutil.copyfile(DEMO_DB, copy)
    os.chmod(copy, 0o644)
    _extend_through_q4(copy)
    _point_at(monkeypatch, copy)
    pin_today(monkeypatch, JAN_4_2027)
    yield copy
    unpin_leftovers()


def _payload(copy: Path, quarter_id: str) -> dict:
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE quarter_id = ?",
                          (quarter_id,)).fetchone()
    finally:
        con.close()
    return json.loads(row[0])


def _ledger_through(copy: Path, end: str) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(
            f"SELECT {', '.join(cache.LEDGER_COLUMNS)} FROM trades WHERE trade_date <= ? "
            "ORDER BY trade_date, trade_id", con, params=(end,))
    finally:
        con.close()


def test_the_rule_is_set_by_the_quarter():
    assert cache.valuation_rule_for(date(2026, 6, 30)) == cache.ADJUSTED_LEVELS_RULE
    assert cache.valuation_rule_for(date(2026, 9, 30)) == cache.ADJUSTED_LEVELS_RULE
    assert cache.valuation_rule_for(date(2026, 12, 31)) == cache.RAW_CLOSE_RULE
    assert cache.valuation_rule_for(date(2027, 3, 31)) == cache.RAW_CLOSE_RULE


def test_a_q4_2026_lock_is_on_the_raw_close_rule_and_holds_the_ledger(book_in_2027):
    snap, _ = cache.capture_quarter_snapshot("2026Q4")
    assert snap.valuation_rule == cache.RAW_CLOSE_RULE
    assert cache.lock_valuation_rule(snap) == cache.RAW_CLOSE_RULE

    expected = _ledger_through(book_in_2027, "2026-12-31")
    pd.testing.assert_frame_equal(snap.ledger, expected)
    days = set(snap.ledger["trade_date"])
    assert IN_Q4[1] in days and AFTER_Q4[1] not in days
    assert max(days) == IN_Q4[1]
    assert (snap.ledger["lot_source"] == "drip").sum() == 86, "the DRIP lots are held too"

    payload = _payload(book_in_2027, "2026Q4")
    assert payload["valuation_rule"] == cache.RAW_CLOSE_RULE
    assert payload["ledger"]["columns"] == list(cache.LEDGER_COLUMNS)
    served = cache.get_quarter_snapshot("2026Q4")[0]
    assert served.valuation_rule == cache.RAW_CLOSE_RULE
    pd.testing.assert_frame_equal(served.ledger, expected)


def test_a_pre_q4_quarter_captured_late_is_on_adjusted_levels_and_holds_no_ledger(book_in_2027):
    """Q3 2026 captured in January 2027 is on adjusted levels, as Q3 is decided to be, and
    holds no ledger. Its prices are the committed lock's: the marker is all it adds."""
    committed = cache.get_quarter_snapshot("2026Q3")[0]
    con = sqlite3.connect(book_in_2027)
    try:
        with con:
            con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q3'")
    finally:
        con.close()
    snap, _ = cache.capture_quarter_snapshot("2026Q3")
    assert snap.valuation_rule == cache.ADJUSTED_LEVELS_RULE
    assert snap.ledger is None
    payload = _payload(book_in_2027, "2026Q3")
    assert payload["valuation_rule"] == cache.ADJUSTED_LEVELS_RULE
    assert "ledger" not in payload
    pd.testing.assert_frame_equal(snap.adj_close, committed.adj_close)
    pd.testing.assert_frame_equal(snap.close, committed.close)


@pytest.mark.parametrize("book", [DEMO_DB, FROZEN_BOOK], ids=["demo.db", "frozen_book.db"])
def test_every_existing_lock_reads_as_adjusted_levels(book, tmp_path, monkeypatch):
    copy = tmp_path / book.name
    shutil.copyfile(book, copy)
    os.chmod(copy, 0o644)
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        quarters = [r[0] for r in con.execute("SELECT quarter_id FROM quarter_snapshots")]
    finally:
        con.close()
    assert len(quarters) >= 5, quarters
    _point_at(monkeypatch, copy)
    for quarter in quarters:
        payload = _payload(copy, quarter)
        assert "valuation_rule" not in payload and "ledger" not in payload, quarter
        snap = cache.get_quarter_snapshot(quarter)[0]
        assert snap.valuation_rule is None and snap.ledger is None, quarter
        assert cache.lock_valuation_rule(snap) == cache.ADJUSTED_LEVELS_RULE, quarter


def test_a_lock_with_no_marker_reads_as_adjusted_levels():
    bare = cache.SnapshotFrames(adj_close=pd.DataFrame())
    assert cache.lock_valuation_rule(bare) == cache.ADJUSTED_LEVELS_RULE
    assert cache.lock_valuation_rule(None) == cache.ADJUSTED_LEVELS_RULE
