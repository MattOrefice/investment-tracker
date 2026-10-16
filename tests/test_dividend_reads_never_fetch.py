"""A dividend read never fetches, and bulk_refresh refuses a start that would leave a gap
(#494, decided by the owner on October 16, 2026).

get_dividends fetched a window's prices whenever the window held no stored dividend, and
fetch_prices stores every close it receives. A start later than the day after the
ticker's last stored close left the closes in between unstored: a gap inside the history.
On a copy of the frozen book, whose VOO closes end July 20, 2026, a read of July 28 to 30
stored July 28, 29 and 30 and nothing from July 21 to 27. get_dividends reads only what
is stored now, and bulk_refresh refuses such a start.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src import prices
from tests.conftest import point_at_frozen_book


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A copy of the frozen book, offline, gap fetches on, and every price fetch
    recorded rather than made."""
    copy = point_at_frozen_book(monkeypatch, tmp_path)
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    calls: "list[tuple]" = []

    def recorded(ticker, start, end, *a, **k):
        calls.append((ticker, start, end))
        return pd.DataFrame()

    monkeypatch.setattr(prices, "fetch_prices", recorded)
    return copy, calls


def _closes(copy, ticker) -> "list[str]":
    con = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT price_date FROM prices WHERE ticker = ? ORDER BY price_date", (ticker,))]
    finally:
        con.close()


def test_a_dividend_read_over_a_window_with_no_dividend_fetches_nothing(book):
    copy, calls = book
    before = _closes(copy, "VOO")
    assert before[-1] == "2026-07-20", "premise: the frozen book's VOO closes end July 20"
    got = prices.get_dividends("VOO", "2026-07-28", "2026-07-30")
    assert got.empty
    assert calls == [], calls
    assert _closes(copy, "VOO") == before


def test_a_stored_dividend_is_still_read(book):
    """The contrast: a window that holds a stored dividend returns it, with no fetch."""
    copy, calls = book
    con = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
    try:
        ex_date, amount = con.execute(
            "SELECT ex_date, amount FROM dividends WHERE ticker = 'VOO' ORDER BY ex_date DESC "
            "LIMIT 1").fetchone()
    finally:
        con.close()
    got = prices.get_dividends("VOO", ex_date, ex_date)
    assert list(got.values) == [pytest.approx(amount)] and calls == []


def test_bulk_refresh_refuses_a_start_past_the_day_after_the_last_close(book):
    _copy, calls = book
    with pytest.raises(ValueError) as refused:
        prices.bulk_refresh(["VOO", "VTV"], "2026-07-28")
    message = str(refused.value)
    assert "VOO (last stored close 2026-07-20)" in message
    assert "VTV (last stored close 2026-07-20)" in message
    assert calls == [], "nothing is fetched once the start is refused"


def test_bulk_refresh_takes_the_day_after_and_a_ticker_with_nothing_stored(book):
    """The edge: the day after the last close is accepted, and so is any start for a
    ticker with no stored close."""
    _copy, calls = book
    prices.bulk_refresh(["VOO"], "2026-07-21")
    prices.bulk_refresh(["ZZZZ"], "2026-07-28")
    assert [c[:2] for c in calls] == [("VOO", "2026-07-21"), ("ZZZZ", "2026-07-28")]


def test_one_late_ticker_refuses_the_whole_call(book):
    _copy, calls = book
    with pytest.raises(ValueError, match=r"VOO \(last stored close 2026-07-20\)"):
        prices.bulk_refresh(["ZZZZ", "VOO"], "2026-07-28")
    assert calls == []
