"""In personal mode during market hours the banner says a current value includes
today's unsettled price, and when it was quoted (audit item 4f, #160 kept).

#160 keeps an open session's bar out of the cache but hands it to the caller, so the
current value on Performance and Risk includes a price that is not a close, while the
banner described settled closes only. Now whatever serves such a bar records it with
its quote time, and the banner adds one sentence while no stored close covers it.
Staged here with a Yahoo response whose regular session is open against the real clock.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from pathlib import Path

import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent


class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def _chart(session_open: bool, quoted: datetime, close: float = 101.5, *,
           day: "date | None" = None, now: "datetime | None" = None):
    """Two bars, the day before and the session's day, with the session open or closed.
    The session is ``day``'s (the real clock's UTC date by default), opening 13:30 UTC,
    and open until two hours after ``now`` (the real clock by default)."""
    now = now or datetime.now(timezone.utc)
    day = day or now.date()
    today = datetime(day.year, day.month, day.day, 13, 30, tzinfo=timezone.utc)
    yday = today - timedelta(days=1)
    start = int(today.timestamp())
    end = int((now + timedelta(hours=2)).timestamp()) if session_open else int(
        (now - timedelta(minutes=1)).timestamp())
    return {"chart": {"result": [{
        "meta": {"regularMarketTime": int(quoted.timestamp()),
                 "currentTradingPeriod": {"regular": {"start": start, "end": end}}},
        "timestamp": [int(yday.timestamp()), int(today.timestamp())],
        "indicators": {"quote": [{"close": [close, close]}]},
        "events": {},
    }], "error": None}}


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A copy of the frozen book (VOO stored through 2026-07-20), with the live-mark
    record cleared and gap fetches on, as in personal mode. The network block the
    frozen book installs is replaced per test by a staged Yahoo response. Today is
    NOT pinned: an open session is judged against the real clock."""
    from tests.conftest import point_at_frozen_book
    path = point_at_frozen_book(monkeypatch, tmp_path)
    monkeypatch.setattr(prices, "_LIVE_MARKS", {})
    prices._reset_trailing_memo()
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    yield path
    prices._reset_trailing_memo()


def _serve(monkeypatch, session_open, quoted):
    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: _Resp(_chart(session_open, quoted)))
    today = datetime.now(timezone.utc).date()
    return prices.get_prices("VOO", "2026-07-01", today.isoformat())


def test_an_open_sessions_bar_is_served_recorded_and_named_in_the_banner(book, monkeypatch):
    from src import asof
    import src.holdings as holdings
    quoted = datetime.now(timezone.utc).replace(hour=15, minute=2, second=0, microsecond=0)
    frame = _serve(monkeypatch, True, quoted)
    today = datetime.now(timezone.utc).date()
    assert max(frame.index) == today, "the live bar reaches the caller (#160 kept)"
    stored = sqlite3.connect(book).execute(
        "SELECT MAX(price_date) FROM prices WHERE ticker = 'VOO'").fetchone()[0]
    assert stored == (today - timedelta(days=1)).isoformat(), "and is never cached"
    mark = prices.live_marks()["VOO"]
    assert mark["date"] == today.isoformat()

    monkeypatch.setattr(holdings, "committed_price_frontier", lambda *a, **k: stored)
    line = asof.as_of_live_line(today, frontier=stored)
    et = quoted.astimezone(asof.ET).strftime("%I:%M %p").lstrip("0")
    assert line.endswith(f" Current values include today’s unsettled price, quoted at {et} ET."), line


def test_a_closed_sessions_bar_is_stored_and_the_banner_says_nothing_more(book, monkeypatch):
    from src import asof
    import src.holdings as holdings
    quoted = datetime.now(timezone.utc) - timedelta(minutes=5)
    _serve(monkeypatch, False, quoted)
    today = datetime.now(timezone.utc).date()
    assert prices.live_marks() == {}
    monkeypatch.setattr(holdings, "committed_price_frontier", lambda *a, **k: today.isoformat())
    assert "unsettled" not in asof.as_of_live_line(today, frontier=today.isoformat())


def test_a_mark_a_stored_close_now_covers_is_not_named(book, monkeypatch):
    """The next day, or once the close is stored, the old mark says nothing."""
    from src import asof
    import src.holdings as holdings
    _serve(monkeypatch, True, datetime.now(timezone.utc))
    today = datetime.now(timezone.utc).date()
    monkeypatch.setattr(holdings, "committed_price_frontier", lambda *a, **k: today.isoformat())
    assert prices.live_marks(), "premise: a mark was served"
    assert "unsettled" not in asof.as_of_live_line(today, frontier=today.isoformat())


def test_a_page_whose_coverage_saw_the_bar_says_so_too(book, monkeypatch):
    """Performance passes its current-value coverage, whose served frontier is then
    TODAY: the banner's "Live data as of" state. It carries the sentence as well."""
    from src import asof
    import src.holdings as holdings
    quoted = datetime.now(timezone.utc).replace(hour=15, minute=2, second=0, microsecond=0)
    _serve(monkeypatch, True, quoted)
    today = datetime.now(timezone.utc).date()
    stored = (today - timedelta(days=1)).isoformat()
    monkeypatch.setattr(holdings, "committed_price_frontier", lambda *a, **k: stored)
    line = asof.as_of_live_line(today, frontier=today.isoformat())
    assert line.startswith("Live data as of"), line
    assert "Current values include today’s unsettled price, quoted at " in line, line


def _mark(ticker, day):
    prices._LIVE_MARKS[ticker] = {"date": day.isoformat(),
                                  "quoted_at": datetime.now(timezone.utc).isoformat()}


def test_only_todays_marks_for_held_tickers_are_named(book, monkeypatch):
    """The record is per process. A bar for a ticker the book does not hold (BTC-USD
    trades around the clock; Asset Evaluation fetches it), or yesterday's bar, must not
    make the banner claim one. Before this, the full suite showed it: 19 banner tests
    red, from a bar an earlier test served."""
    from src import asof
    import src.holdings as holdings
    today = date(2026, 7, 21)                  # the frozen book's next day
    # Stored through the Friday before, so yesterday (Monday) is newer than any stored
    # close: only the date rule can keep its mark out.
    stored = "2026-07-17"
    monkeypatch.setattr(holdings, "committed_price_frontier", lambda *a, **k: stored)
    held = holdings.valuation_tickers(today.isoformat())
    assert "VOO" in held and "BTC-USD" not in held, held
    assert ("BIL" in held) == ("SPAXX" in holdings.get_holdings_on_date(
        today.isoformat(), account_id=holdings.get_portfolio_account_id()).index)

    _mark("BTC-USD", today)
    _mark("VOO", today - timedelta(days=1))
    assert "unsettled" not in asof.as_of_live_line(today, frontier=stored)
    _mark("VOO", today)
    assert "Current values include today’s unsettled price" in asof.as_of_live_line(
        today, frontier=stored)


def test_the_record_is_reset_for_every_test(request):
    """The reset is autouse (tests/conftest.py). Registration is what makes it hold for
    a test that never asks for it, so that is what this checks."""
    assert "_no_live_marks_carried_between_tests" in request.fixturenames
    assert prices._LIVE_MARKS == {}


# The page renders read the clock twice: the price layer judges the session open
# against datetime.now(), and the banner dates "today" by the machine's date. They used
# to stage the session on the UTC date of the real clock, so from 8 PM to midnight
# Eastern, on an Eastern machine, the staged session was tomorrow in New York and the
# banner rightly did not call it today's (#441). The clock is pinned, and the session is
# staged on New York's date, written out rather than computed with the app's helper.
SESSION_DAY = date(2026, 9, 28)                                  # a Monday
QUOTED = datetime(2026, 9, 28, 15, 2, tzinfo=timezone.utc)       # 11:02 AM EDT
IN_SESSION = QUOTED
EVENING = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)       # 9:00 PM EDT, Sep 28


def _pin_clock(monkeypatch, instant: datetime) -> None:
    """datetime.now() for the price layer, and date.today() everywhere, at ``instant``
    and New York's date then: SESSION_DAY for both instants."""
    from tests.conftest import pin_today

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.astimezone().replace(tzinfo=None)

    monkeypatch.setattr(prices, "datetime", _Now)
    pin_today(monkeypatch, SESSION_DAY)


@pytest.mark.parametrize("page", ["7_Risk.py", "2_Performance.py"])
@pytest.mark.parametrize("instant", [IN_SESSION, EVENING], ids=["11:02 AM ET", "9 PM ET"])
def test_the_page_banner_says_so_after_the_page_serves_the_bar(book, monkeypatch, page,
                                                                 instant):
    """Rendered, in personal mode, with the session's bar quoted at 11:02 AM Eastern.
    The record starts empty, so the sentence can only come from bars THIS render served:
    Risk wrote its banner before pricing its current value, and now writes it again after.

    At 9 PM Eastern the process still serves that bar and no stored close covers it, so
    the banner still names it. That is the hour the unpinned test failed in: its UTC
    date is the next day, and a session staged on it is tomorrow in New York."""
    import streamlit as st
    import src.config as config
    from streamlit.testing.v1 import AppTest
    from tests.conftest import unpin_leftovers
    monkeypatch.setattr(config, "IS_DEMO", False)
    con = sqlite3.connect(book)
    last = dict(con.execute(
        "SELECT ticker, close FROM prices p WHERE price_date = "
        "(SELECT MAX(price_date) FROM prices q WHERE q.ticker = p.ticker)").fetchall())
    con.close()

    def _get(url, *a, **k):
        ticker = url.split("/chart/")[1].split("?")[0]
        return _Resp(_chart(True, QUOTED, float(last.get(ticker, 100.0)),
                            day=SESSION_DAY, now=instant))

    monkeypatch.setattr(prices._SESSION, "get", _get)
    _pin_clock(monkeypatch, instant)
    try:
        assert prices.live_marks() == {}, "premise: no mark before the render"
        st.cache_data.clear()
        at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300).run()
    finally:
        unpin_leftovers()
    assert not at.exception, [str(e.value) for e in at.exception]
    caps = [str(c.value) for c in at.caption]
    assert any("Current values include today’s unsettled price, quoted at 11:02 AM ET." in c
               for c in caps), [c for c in caps if "Prices through" in c or "Live data" in c]
    st.cache_data.clear()
