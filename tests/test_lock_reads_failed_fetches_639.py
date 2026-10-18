"""A lock reads the record of a failed fetch of a ticker's earlier history (#639).

Decided by the owner on October 17 and October 18, 2026: a recorded leading-fetch
failure for a ticker the lock reads is a gap, and the lock refuses. The record holds
only failures since #644: a window with no NYSE session is never fetched, and Yahoo's
"Data doesn't exist" for a window before a listing is an answer.

On copies of the frozen book on July 21, 2026, with Q2 2026's lock removed and the
lock's reads as personal mode makes them: the gap fetch on, so a ticker whose stored
history starts after the lock's 2020-01-01 is fetched back to it. The frozen book's
histories start in 2024, so every ticker the lock reads is asked for that stretch.
Yahoo is stubbed at the HTTP session, so fetch_prices' own reading of the answer runs:
it answers "Data doesn't exist" for every ticker but VTV, whose answer each test sets.
Every other frozen-book test takes its locks with the gap fetch off
(conftest.lock_with_the_gap_fetch_off); these restore the lock's own reads.
"""
from __future__ import annotations

import sqlite3

import pytest
import requests

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
from tests.test_no_data_answers_644 import NO_DATA_400, _store, _yahoo

LOCK_START = "2020-01-01"


def _locked(path) -> "list[str]":
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT quarter_id FROM quarter_snapshots")]
    finally:
        con.close()


@pytest.fixture
def book(tmp_path, monkeypatch):
    import src.cache as cache
    import src.prices as prices
    path = point_at_frozen_book(monkeypatch, tmp_path)
    pin_today(monkeypatch)
    monkeypatch.setattr(cache, "_prices_module", prices)     # the lock's own reads
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    con = sqlite3.connect(path)
    try:
        with con:
            assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                               ).rowcount == 1
    finally:
        con.close()
    tickers, _gaps = cache._get_all_snapshot_tickers()
    assert "VTV" in tickers, "premise: the lock reads VTV"
    yield path
    unpin_leftovers()


def _vtv(answer):
    return lambda ticker: answer if ticker == "VTV" else NO_DATA_400


def _recorded() -> list:
    from src.prices import gap_fetch_failures
    return [(f.ticker, f.start, f.cause) for f in gap_fetch_failures()]


def test_a_failed_fetch_of_a_tickers_earlier_history_refuses_the_lock(book, monkeypatch):
    from src.cache import LockCoverageError, capture_quarter_snapshot
    asked = _yahoo(monkeypatch, _vtv(requests.ConnectionError("offline")))
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert "VTV" in asked
    assert _recorded() == [("VTV", LOCK_START, "a network error: ConnectionError")]
    assert str(err.value) == (
        "Cannot lock 2026Q2: the price update for the earlier history of a ticker the "
        "lock reads failed, so a lock now would freeze that history short. Nothing was "
        "locked. Failed: VTV (a network error: ConnectionError).")
    assert "2026Q2" not in _locked(book)


def test_a_failure_recorded_before_the_lock_refuses_it_too(book, monkeypatch):
    """A read of the same window failed minutes earlier: within the half-hour retry the
    lock asks nothing, and reads the record."""
    from src.cache import LockCoverageError, capture_quarter_snapshot
    from src.prices import get_prices
    asked = _yahoo(monkeypatch, _vtv(requests.ConnectionError("offline")))
    get_prices("VTV", LOCK_START, "2026-06-30")
    assert asked == ["VTV"] and _recorded()
    with pytest.raises(LockCoverageError, match=r"Failed: VTV \(a network error"):
        capture_quarter_snapshot("2026Q2")
    assert asked.count("VTV") == 1, "the lock did not ask again"
    assert "2026Q2" not in _locked(book)


def test_a_data_doesnt_exist_answer_does_not_refuse_it(book, monkeypatch):
    """Every ticker the lock reads answered that no data exists before its history: the
    lock is taken, from the stored history, and nothing is recorded."""
    from src.cache import capture_quarter_snapshot
    asked = _yahoo(monkeypatch, NO_DATA_400)
    snap, _ = capture_quarter_snapshot("2026Q2")
    assert "VTV" in asked
    assert _recorded() == []
    assert "2026Q2" in _locked(book)
    assert str(min(snap.adj_close["VTV"].dropna().index)) == "2024-01-02"


def test_a_holiday_only_window_does_not_refuse_it(book, monkeypatch):
    """VTV's history starts January 2, 2020, so the stretch before it is New Year's Day
    alone. It is never fetched, so VTV's network error is never met, and the lock is
    taken."""
    from src.cache import capture_quarter_snapshot
    _store(book, "VTV", "2020-01-02")
    asked = _yahoo(monkeypatch, _vtv(requests.ConnectionError("offline")))
    snap, _ = capture_quarter_snapshot("2026Q2")
    assert "VTV" not in asked, "a window with no session is never fetched"
    assert _recorded() == []
    assert "2026Q2" in _locked(book)
    assert str(min(snap.adj_close["VTV"].dropna().index)) == "2020-01-02"


def test_every_other_frozen_book_lock_is_taken_with_the_gap_fetch_off(tmp_path, monkeypatch):
    """The shared fixture: the frozen book's stored history is its whole record, so a lock
    taken from it asks for nothing and records nothing, as the demo's lock tool takes
    the committed book's. A read outside the lock still fetches, and its failure is
    recorded."""
    import src.prices as prices
    from src.cache import capture_quarter_snapshot
    path = point_at_frozen_book(monkeypatch, tmp_path)
    pin_today(monkeypatch)
    try:
        monkeypatch.setattr(prices, "_GAP_FETCH", True)
        con = sqlite3.connect(path)
        with con:
            con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'")
        con.close()
        asked = _yahoo(monkeypatch, requests.ConnectionError("offline"))
        capture_quarter_snapshot("2026Q2")
        assert asked == [] and _recorded() == []
        assert "2026Q2" in _locked(path)
        assert prices._GAP_FETCH is True, "the gap fetch is restored after the lock"
        prices.get_prices("VTV", LOCK_START, "2026-06-30")
        assert asked == ["VTV"]
        assert _recorded() == [("VTV", LOCK_START, "a network error: ConnectionError")]
    finally:
        unpin_leftovers()
