"""Yahoo's own "no data here" answers are answers, not failures (#644).

Decided by the owner on October 17, 2026, and rebuilt on October 18 on a holiday table
that covers the reads (src.demo_refresh, back to 1990):
  * a window with no trading session is never fetched and never recorded;
  * an HTTP 400 that says the data doesn't exist for the window ("Data doesn't exist for
    startDate = ...") means no closes exist there. It is remembered like a success,
    never recorded as a failure, and never shown on the banner;
  * an empty answer for a window that has a session is still a failure, and so is a
    network error.

On copies of the frozen book, with Yahoo stubbed at the HTTP session, so fetch_prices'
own reading of the answer runs. A history that starts on a date is made by storing its
first closes there.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest
import requests

from tests.conftest import point_at_frozen_book

TODAY = dt.date(2026, 7, 21)
NO_DATA = "Data doesn't exist for startDate = 1672704000, endDate = 1704931200"


class _Answer:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


NO_DATA_400 = _Answer(400, {"chart": {"result": None, "error": {
    "code": "Bad Request", "description": NO_DATA}}})
EMPTY_200 = _Answer(200, {"chart": {"result": [{"meta": {}, "indicators": {"quote": [{}]}}],
                                    "error": None}})
OTHER_400 = _Answer(400, {"chart": {"result": None, "error": {
    "code": "Bad Request", "description": "Invalid input - start date cannot be after end"}}})


@pytest.fixture
def book(tmp_path, monkeypatch):
    return point_at_frozen_book(monkeypatch, tmp_path)


def _yahoo(monkeypatch, answer) -> list:
    """Stub the chart request: ``answer`` (or answer(ticker)) is returned, or raised if it
    is an exception. Returns the list of tickers asked, in order."""
    import src.prices as prices
    asked: list = []

    def get(url, params=None, timeout=None):
        ticker = url.rsplit("/", 1)[-1]
        asked.append(ticker)
        a = answer(ticker) if callable(answer) else answer
        if isinstance(a, BaseException):
            raise a
        return a

    monkeypatch.setattr(prices._SESSION, "get", get)
    return asked


def _store(path, ticker: str, *days: str) -> None:
    con = sqlite3.connect(path)
    try:
        with con:
            con.executemany("INSERT OR REPLACE INTO prices (ticker, price_date, close, "
                            "adj_close) VALUES (?, ?, 100.0, NULL)",
                            [(ticker, d) for d in days])
    finally:
        con.close()


def _banner() -> str:
    from src.asof import as_of_live_line
    return as_of_live_line(TODAY, frontier=dt.date(2026, 7, 20))


def _recorded() -> list:
    from src.prices import gap_fetch_failures
    return [(f.ticker, f.start, f.end, f.cause) for f in gap_fetch_failures()]


# ── a window with no session is never fetched ────────────────────────────────

@pytest.mark.parametrize("ticker, start, first", [
    ("VTV", "2018-01-01", "2018-01-02"),     # Asset Evaluation's start, New Year's Day
    ("VOO", "2020-01-01", "2020-01-02"),     # the lock's start
])
def test_a_read_from_a_holiday_before_the_first_close_asks_nothing(book, monkeypatch,
                                                                   ticker, start, first):
    """A held fund whose history starts the next day: the leading window is the holiday
    alone. Yahoo answers such a window with an empty 200 (measured on #644), which
    recorded a failure and put it on the banner."""
    from src.prices import get_prices
    _store(book, ticker, first)
    asked = _yahoo(monkeypatch, EMPTY_200)
    frame = get_prices(ticker, start, first)
    assert [str(d) for d in frame.index] == [first]
    assert asked == [], "a window with no session is never fetched"
    assert _recorded() == []
    assert "price update failed" not in _banner()


def test_a_weekend_after_the_last_close_asks_nothing(book, monkeypatch):
    from src.prices import get_prices
    con = sqlite3.connect(book)
    with con:
        con.execute("DELETE FROM prices WHERE ticker = 'VOO' AND price_date > '2026-07-17'")
    con.close()
    asked = _yahoo(monkeypatch, EMPTY_200)
    get_prices("VOO", "2026-07-01", "2026-07-19")         # Friday's close, then Sat-Sun
    assert asked == [] and _recorded() == []


def test_a_series_that_trades_every_day_is_still_fetched_on_a_weekend(book, monkeypatch):
    from src.prices import get_prices
    _store(book, "BTC-USD", "2026-07-17")
    asked = _yahoo(monkeypatch, EMPTY_200)
    get_prices("BTC-USD", "2026-07-01", "2026-07-19")
    assert asked == ["BTC-USD"], "a weekend is BTC-USD's session"
    assert [r[3] for r in _recorded()] == ["Yahoo Finance returned no price data"]


# ── Yahoo's "Data doesn't exist" is an answer ────────────────────────────────

@pytest.mark.parametrize("ticker, start, first", [
    ("IBIT", "2023-01-03", "2024-01-11"),    # before IBIT's listing
    ("AVIV", "2021-06-01", "2021-09-30"),    # a held fund, before its listing
])
def test_a_read_from_before_a_listing_records_nothing(book, monkeypatch, ticker, start,
                                                      first):
    """The window holds sessions, so it is asked, and Yahoo says no data exists there.
    Nothing is recorded, the banner is clean, and the answer is remembered: a second
    read asks nothing."""
    from src.prices import get_prices
    _store(book, ticker, first)
    asked = _yahoo(monkeypatch, NO_DATA_400)
    get_prices(ticker, start, first)
    assert asked == [ticker]
    assert _recorded() == []
    assert "price update failed" not in _banner()
    get_prices(ticker, start, first)
    assert asked == [ticker], "remembered like a success"


def test_the_answer_clears_a_failure_recorded_for_the_window(book, monkeypatch):
    """As a success does: the network was down, and once it is back Yahoo says no data
    exists there."""
    import src.prices as prices
    from src.demo_refresh import RETRY_AFTER
    _store(book, "AVIV", "2021-09-30")
    _yahoo(monkeypatch, requests.exceptions.ConnectionError("down"))
    prices.get_prices("AVIV", "2021-06-01", "2021-09-30")
    assert [r[3] for r in _recorded()] == ["a network error: ConnectionError"]
    later = prices._now() + RETRY_AFTER
    monkeypatch.setattr(prices, "_now", lambda: later)
    _yahoo(monkeypatch, NO_DATA_400)
    prices.get_prices("AVIV", "2021-06-01", "2021-09-30")
    assert _recorded() == []


def test_another_400_is_still_a_failure(book, monkeypatch):
    from src.prices import get_prices
    _store(book, "AVIV", "2021-09-30")
    _yahoo(monkeypatch, OTHER_400)
    get_prices("AVIV", "2021-06-01", "2021-09-30")
    assert [r[3] for r in _recorded()] == ["Yahoo Finance returned HTTP 400"]


def test_fetch_prices_still_raises_it_for_every_other_caller(book, monkeypatch):
    """A YahooResponseError still, so a caller that is not a gap fetch reads it as before."""
    from src.prices import YahooNoData, YahooResponseError, fetch_prices
    _yahoo(monkeypatch, NO_DATA_400)
    with pytest.raises(YahooNoData) as err:
        fetch_prices("IBIT", "2023-01-03", "2024-01-10")
    assert isinstance(err.value, YahooResponseError)
    assert err.value.reason == "answered that no price data exists for the window"


# ── a failure is still a failure ─────────────────────────────────────────────

def test_a_network_error_still_records_and_shows(book, monkeypatch):
    """The control: VTV from December 2017, a window with sessions, and the network down."""
    from src.prices import get_prices
    _store(book, "VTV", "2018-01-02")
    _yahoo(monkeypatch, requests.exceptions.ConnectionError("down"))
    get_prices("VTV", "2017-12-01", "2018-01-02")
    assert _recorded() == [("VTV", "2017-12-01", "2018-01-01",
                            "a network error: ConnectionError")]
    assert "price update failed" in _banner()
    assert "(a network error: ConnectionError)" in _banner()


def test_an_empty_answer_for_a_window_with_sessions_is_still_a_failure(book, monkeypatch):
    from src.prices import get_prices
    _store(book, "VTV", "2018-01-02")
    _yahoo(monkeypatch, EMPTY_200)
    get_prices("VTV", "2017-12-01", "2018-01-02")
    assert [r[3] for r in _recorded()] == ["Yahoo Finance returned no price data"]
    assert "price update failed" in _banner()
