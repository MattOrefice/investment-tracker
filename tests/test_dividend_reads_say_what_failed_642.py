"""Three price-layer reads no longer turn a failure into an answer (#642).

Decided by the owner on October 18, 2026:
  * fetch_prices' dividend parse: an event it cannot read (an amount missing, not a
    number, or not above zero; a date that is not a timestamp or is out of range) is
    recorded with the ticker, its date as sent and why, and shown where the ticker's
    price status shows. An out-of-range date is one of these, never "a network error".
    A lock refuses a quarter whose window holds such a record for a ticker it reads.
    #642 stored the answer's closes all the same; #658 reversed that the same day, and
    the answer is not stored at all (tests/test_unread_answer_not_stored_658.py).
  * _cached_row_count returns 0 only for a missing prices table; any other error raises.
  * get_dividends' cache read never writes: a missing table reads as no dividends, and
    any other error raises, so a capture fails rather than locking an empty history.

On copies of the frozen book, with Yahoo stubbed at the HTTP session, so fetch_prices'
own reading of the answer runs. A store that cannot be read is a connection whose one
statement raises sqlite's "database is locked".
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
from tests.test_no_data_answers_644 import _Answer, _yahoo

UTC = dt.timezone.utc
WINDOW = ("2026-07-21", "2026-07-24")        # after the frozen book's last close, July 20


def _ts(day: str, hour: int = 13, minute: int = 30) -> int:
    d = dt.date.fromisoformat(day)
    return int(dt.datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC).timestamp())


def _chart(start: str, end: str, dividends: dict) -> _Answer:
    """Yahoo's chart answer: a close of 100 on every weekday in [start, end], and
    ``dividends`` as its events, keyed as Yahoo keys them."""
    day, last, days = dt.date.fromisoformat(start), dt.date.fromisoformat(end), []
    while day <= last:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += dt.timedelta(days=1)
    stamps = [_ts(d, 20, 0) for d in days]
    past = _ts("2000-01-03", 0, 0)
    return _Answer(200, {"chart": {"result": [{
        "meta": {"currentTradingPeriod": {"regular": {"start": past, "end": past + 3600}}},
        "timestamp": stamps,
        "indicators": {"quote": [{"close": [100.0] * len(stamps)}],
                       "adjclose": [{"adjclose": [100.0] * len(stamps)}]},
        "events": {"dividends": dividends}}], "error": None}})


@pytest.fixture
def book(tmp_path, monkeypatch):
    return point_at_frozen_book(monkeypatch, tmp_path)


def _rows(path, sql: str, *args) -> list:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _unread() -> list:
    from src.prices import unread_dividends
    return [(u.ticker, u.raw_date, u.ex_date, u.why, u.start, u.end) for u in unread_dividends()]


# ── the parse ────────────────────────────────────────────────────────────────

JUL22 = str(_ts("2026-07-22"))

UNREADABLE = [
    pytest.param(JUL22, {"amount": "abc"}, "2026-07-22", "its amount is not a number",
                 id="non-numeric amount"),
    pytest.param(JUL22, {"amount": float("nan")}, "2026-07-22", "its amount is not a number",
                 id="not-a-number amount"),
    pytest.param(JUL22, {}, "2026-07-22", "it has no amount", id="missing amount"),
    pytest.param(JUL22, {"amount": None}, "2026-07-22", "it has no amount", id="null amount"),
    pytest.param(JUL22, {"amount": 0}, "2026-07-22", "its amount is 0, not above zero",
                 id="zero amount"),
    pytest.param(JUL22, {"amount": -0.25}, "2026-07-22", "its amount is -0.25, not above zero",
                 id="negative amount"),
    pytest.param("2026-07-22", {"amount": 0.5}, None, "its date is not a timestamp",
                 id="bad timestamp"),
    pytest.param(str(10 ** 20), {"amount": 0.5}, None, "its date is out of range",
                 id="out-of-range timestamp"),
    pytest.param(JUL22, 0.5, "2026-07-22", "it is not a dividend record", id="not a record"),
]


@pytest.mark.parametrize("raw, event, ex_date, why", UNREADABLE)
def test_an_unreadable_dividend_is_recorded_and_nothing_is_stored(book, monkeypatch,
                                                                  raw, event, ex_date, why):
    """The event is recorded with the ticker, its date as sent and why. None of the
    answer is stored since #658: not its closes, not its readable dividend."""
    from src.prices import YahooUnreadDividend, fetch_prices
    readable = str(_ts("2026-07-23"))
    _yahoo(monkeypatch, _chart(*WINDOW, {raw: event, readable: {"amount": 0.4}}))
    with pytest.raises(YahooUnreadDividend) as err:
        fetch_prices("VOO", *WINDOW)
    assert str(err.value) == (
        f"Nothing stored for 'VOO' ({WINDOW[0]} to {WINDOW[1]}): Yahoo Finance sent a "
        f"dividend dated {ex_date or raw} that could not be read: {why}.")
    assert _rows(book, "SELECT price_date FROM prices WHERE ticker = 'VOO' AND price_date "
                       ">= ?", WINDOW[0]) == []
    assert _rows(book, "SELECT ex_date FROM dividends WHERE ticker = 'VOO' AND ex_date >= ?",
                 WINDOW[0]) == []
    assert _unread() == [("VOO", raw, ex_date, why, *WINDOW)]


def test_an_out_of_range_date_in_a_gap_fetch_is_not_a_network_error(book, monkeypatch):
    """On Windows datetime.fromtimestamp raises OSError for it, which the gap fetch
    recorded as "a network error". The gap fetch's record names the event instead, and
    the gap stays one (#658)."""
    from src.prices import gap_fetch_failures, get_prices
    _yahoo(monkeypatch, _chart(*WINDOW, {str(10 ** 20): {"amount": 0.5}}))
    frame = get_prices("VOO", "2026-07-01", WINDOW[1])
    assert str(max(frame.index)) == "2026-07-20", "the store's last close"
    assert [(f.ticker, f.start, f.end, f.cause) for f in gap_fetch_failures()] == [
        ("VOO", *WINDOW, f"Yahoo Finance sent a dividend dated {10 ** 20} that could not "
                         "be read: its date is out of range")]
    assert _unread() == [("VOO", str(10 ** 20), None, "its date is out of range", *WINDOW)]


def test_the_ticker_status_and_the_banner_show_it(book, monkeypatch):
    from src.asof import as_of_live_line
    from src.holdings import _price_and_status
    from src.prices import YahooUnreadDividend, fetch_prices, set_gap_fetch
    _yahoo(monkeypatch, _chart(*WINDOW, {JUL22: {}}))
    with pytest.raises(YahooUnreadDividend):
        fetch_prices("VOO", *WINDOW)
    set_gap_fetch(False)                       # as in the demo once its refresh runs
    words = "VOO, dated 2026-07-22 (it has no amount)"
    _frame, st = _price_and_status("VOO", "2026-07-01", WINDOW[1])
    assert st.resolved and st.unread_dividends == words
    assert st.served_through == "2026-07-20"
    _frame, st = _price_and_status("VOO", "2026-07-01", "2026-07-21")
    assert st.unread_dividends is None, "a window that does not hold it"
    banner = as_of_live_line(dt.date(2026, 7, 27), frontier=dt.date(2026, 7, 20))
    assert banner.endswith(
        " A price update held a dividend that could not be read, so none of it is stored "
        f"and VOO's prices stop at July 20, 2026: {words}."), banner


def test_without_one_the_banner_says_nothing_of_dividends(book, monkeypatch):
    from src.asof import as_of_live_line
    from src.prices import fetch_prices
    _yahoo(monkeypatch, _chart(*WINDOW, {JUL22: {"amount": 0.5}}))
    fetch_prices("VOO", *WINDOW)
    assert _unread() == []
    assert "dividend" not in as_of_live_line(dt.date(2026, 7, 27), frontier=dt.date(2026, 7, 24))


def test_a_later_answer_that_reads_it_clears_it(book, monkeypatch):
    from src.prices import YahooUnreadDividend, fetch_prices
    _yahoo(monkeypatch, _chart(*WINDOW, {JUL22: {"amount": "abc"}}))
    with pytest.raises(YahooUnreadDividend):
        fetch_prices("VOO", *WINDOW)
    assert _unread()
    _yahoo(monkeypatch, _chart(*WINDOW, {JUL22: {"amount": 0.5}}))
    fetch_prices("VOO", *WINDOW)
    assert _unread() == []
    assert ("2026-07-22", 0.5) in _rows(book, "SELECT ex_date, amount FROM dividends "
                                              "WHERE ticker = 'VOO'")


# ── the lock ─────────────────────────────────────────────────────────────────

@pytest.fixture
def unlocked_q2(book):
    """The frozen book on July 21, 2026 with no Q2 2026 lock."""
    import src.cache as cache
    con = sqlite3.connect(book)
    with con:
        assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                           ).rowcount == 1
    con.close()
    with pytest.MonkeyPatch.context() as mp:
        pin_today(mp)
        tickers, _gaps = cache._get_all_snapshot_tickers()
        assert "VOO" in tickers and "ZZZZ" not in tickers, "premise: what the lock reads"
        yield book
    unpin_leftovers()


def _locked(path) -> list:
    return [r[0] for r in _rows(path, "SELECT quarter_id FROM quarter_snapshots")]


def test_a_lock_refuses_a_quarter_whose_window_holds_one(unlocked_q2, monkeypatch):
    """The window's closes are already stored, so no stored rule sees anything: the
    record is the capture's last guard (#658)."""
    from src.cache import LockCoverageError, capture_quarter_snapshot
    from src.prices import YahooUnreadDividend, fetch_prices
    _yahoo(monkeypatch, _chart("2026-06-15", "2026-06-17", {str(_ts("2026-06-16")): {}}))
    with pytest.raises(YahooUnreadDividend):
        fetch_prices("VOO", "2026-06-15", "2026-06-17")
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value) == (
        "Cannot lock 2026Q2: a price update sent a dividend for a ticker the lock reads "
        "that could not be read, so a lock now would freeze total return without it. "
        "Nothing was locked. Unread: VOO, dated 2026-06-16 (it has no amount).")
    assert "2026Q2" not in _locked(unlocked_q2)


@pytest.mark.parametrize("ticker, start, end, day", [
    pytest.param("VOO", *WINDOW, "2026-07-22", id="dated after the quarter"),
    pytest.param("ZZZZ", "2026-06-15", "2026-06-17", "2026-06-16", id="a ticker it does not read"),
])
def test_a_record_outside_the_locks_reads_does_not_refuse_it(unlocked_q2, monkeypatch,
                                                             ticker, start, end, day):
    from src.cache import capture_quarter_snapshot
    from src.prices import YahooUnreadDividend, fetch_prices
    _yahoo(monkeypatch, _chart(start, end, {str(_ts(day)): {}}))
    with pytest.raises(YahooUnreadDividend):
        fetch_prices(ticker, start, end)
    assert _unread()
    capture_quarter_snapshot("2026Q2")
    assert "2026Q2" in _locked(unlocked_q2)


# ── a store that cannot be read ──────────────────────────────────────────────

class _LockedOn:
    """A connection whose statements starting with ``sql`` raise "database is locked"."""

    def __init__(self, conn, sql: str):
        self._conn, self._sql = conn, sql

    def execute(self, sql, *args):
        if sql.lstrip().startswith(self._sql):
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)


def _lock_statement(monkeypatch, sql: str) -> None:
    import src.prices as prices
    real = prices.get_connection
    monkeypatch.setattr(prices, "get_connection", lambda: _LockedOn(real(), sql))


def test_the_row_count_is_zero_only_for_a_missing_prices_table(book):
    from src.prices import _cached_row_count, classify_miss
    assert _cached_row_count("VOO", "2026-07-01", "2026-07-20") > 0, "premise"
    con = sqlite3.connect(book)
    with con:
        con.execute("DROP TABLE prices")
    con.close()
    assert _cached_row_count("VOO", "2026-07-01", "2026-07-20") == 0
    assert classify_miss("VOO", "2026-07-01", "2026-07-20") == "no_cached_rows"


def test_any_other_row_count_error_raises(book, monkeypatch):
    """A locked store may hold rows: it is not a cold cache."""
    from src.prices import _cached_row_count, classify_miss
    _lock_statement(monkeypatch, "SELECT COUNT(*) FROM prices")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _cached_row_count("VOO", "2026-07-01", "2026-07-20")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        classify_miss("VOO", "2026-07-01", "2026-07-20", error=OSError("offline"))


def test_the_dividend_read_never_writes_and_a_missing_table_is_no_dividends(book):
    from src.prices import get_dividends
    con = sqlite3.connect(book)
    with con:
        con.execute("DROP TABLE dividends")
    con.close()
    assert get_dividends("VOO", "2024-01-01", "2026-07-20").empty
    assert _rows(book, "SELECT name FROM sqlite_master WHERE name = 'dividends'") == [], (
        "the read created no table")


def test_on_a_read_only_store_a_missing_table_is_no_dividends(book, monkeypatch):
    import src.prices as prices
    con = sqlite3.connect(book)
    with con:
        con.execute("DROP TABLE dividends")
    con.close()
    monkeypatch.setattr(prices, "get_connection", lambda: sqlite3.connect(
        f"file:{book.as_posix()}?mode=ro", uri=True))
    assert prices.get_dividends("VOO", "2024-01-01", "2026-07-20").empty


def test_any_other_dividend_read_error_raises(book, monkeypatch):
    from src.prices import get_dividends
    assert not get_dividends("VOO", "2024-01-01", "2026-07-20").empty, "premise"
    _lock_statement(monkeypatch, "SELECT ex_date, amount FROM dividends")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        get_dividends("VOO", "2024-01-01", "2026-07-20")


def test_a_capture_fails_rather_than_locking_an_empty_dividend_history(unlocked_q2,
                                                                         monkeypatch):
    from src.cache import capture_quarter_snapshot
    _lock_statement(monkeypatch, "SELECT ex_date, amount FROM dividends")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        capture_quarter_snapshot("2026Q2")
    assert "2026Q2" not in _locked(unlocked_q2)
