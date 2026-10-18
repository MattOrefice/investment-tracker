"""An answer holding a dividend fetch_prices cannot read is not stored at all (#658).

Decided by the owner on October 18, 2026, reversing one part of #642's decision: such an
answer is stored neither for its closes nor for its dividends.
  * The window stays a gap, so every process asks for it again, meets the event and
    records it again, until an answer reads cleanly; that answer is stored whole. This
    is how #639's failed leading fetch already survives a restart.
  * The ticker's status and the banner say why its prices stop where they do, naming
    the event.
  * The rest follows from stored state: the lock's end and interior rules, and so the
    reportable-quarter cap, see the missing closes, and tools/advance_demo_prices.py
    refuses.
  * The cap stays on stored state. The in-process records remain the capture's last
    guard, and a refusal names its cause.

On copies of the frozen book, with Yahoo stubbed at the HTTP session, so fetch_prices'
own reading of the answer runs. A new process is one with none of src.prices' records of
this one (_restart). The advance tool runs on a scratch copy, as in
tests/test_advance_demo_prices.py.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sqlite3

import pytest

from tests.conftest import FROZEN_BOOK, FROZEN_TODAY, pin_today, point_at_frozen_book, \
    unpin_leftovers
from tests.test_dividend_reads_say_what_failed_642 import _chart, _ts
from tests.test_no_data_answers_644 import NO_DATA_400

UTC = dt.timezone.utc
WINDOW = ("2026-07-21", "2026-07-24")        # after the frozen book's last close, July 20
JUL22 = str(_ts("2026-07-22"))
EVENT = "VOO, dated 2026-07-22 (its amount is not a number)"
CAUSE = ("Yahoo Finance sent a dividend dated 2026-07-22 that could not be read: its "
         "amount is not a number")


def _yahoo(monkeypatch, answer) -> list:
    """Stub the chart request with ``answer(ticker, start, end)``, the window asked as
    ISO dates. Returns each (ticker, start, end) asked, in order."""
    import src.prices as prices
    asked: list = []

    def get(url, params=None, timeout=None):
        ticker = url.rsplit("/", 1)[-1]
        start = dt.datetime.fromtimestamp(params["period1"], tz=UTC).date()
        end = dt.datetime.fromtimestamp(params["period2"], tz=UTC).date() - dt.timedelta(days=1)
        asked.append((ticker, start.isoformat(), end.isoformat()))
        return answer(ticker, start.isoformat(), end.isoformat())

    monkeypatch.setattr(prices._SESSION, "get", get)
    return asked


def _voo(dividends: dict):
    """VOO's chart for whatever window is asked, with ``dividends`` as its events."""
    return lambda ticker, start, end: _chart(start, end, dividends)


UNREADABLE = {JUL22: {"amount": "abc"}}


def _restart() -> None:
    """What a new process starts with: none of src.prices' records of this one."""
    import src.prices as prices
    prices._GAP_FAILURES.clear()
    prices._reset_no_data_memo()
    prices._reset_unread_dividends()
    prices._reset_trailing_memo()


def _rows(path, sql: str, *args) -> list:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _delete(path, sql: str, *args) -> int:
    con = sqlite3.connect(path)
    try:
        with con:
            return con.execute(sql, args).rowcount
    finally:
        con.close()


def _stored_after(path, ticker: str, day: str) -> list:
    return [r[0] for r in _rows(path, "SELECT price_date FROM prices WHERE ticker = ? AND "
                                      "price_date > ? ORDER BY price_date", ticker, day)]


def _unread() -> list:
    from src.prices import unread_dividends
    return [u.words() for u in unread_dividends()]


def _failures() -> list:
    from src.prices import gap_fetch_failures
    return [(f.ticker, f.start, f.end, f.cause) for f in gap_fetch_failures()]


@pytest.fixture
def book(tmp_path, monkeypatch):
    return point_at_frozen_book(monkeypatch, tmp_path)


# ── nothing is stored, and the status and the banner say why ─────────────────

def test_nothing_of_the_answer_is_stored_and_the_status_and_banner_say_why(book, monkeypatch):
    """Through get_prices' trailing gap fetch: the closes and the readable dividend are
    not stored, the frame stops at the store's last close, and the status and the
    banner name the event."""
    from src.asof import as_of_live_line
    from src.holdings import _price_and_status
    from src.prices import get_prices
    asked = _yahoo(monkeypatch, _voo({**UNREADABLE, str(_ts("2026-07-23")): {"amount": 0.4}}))
    frame = get_prices("VOO", "2026-07-01", WINDOW[1])
    assert asked == [("VOO", *WINDOW)]
    assert str(max(frame.index)) == "2026-07-20"
    assert _stored_after(book, "VOO", "2026-07-20") == []
    assert _rows(book, "SELECT ex_date FROM dividends WHERE ticker = 'VOO' AND ex_date > ?",
                 "2026-07-20") == []
    assert _unread() == [EVENT]
    assert _failures() == [("VOO", *WINDOW, CAUSE)]

    _frame, st = _price_and_status("VOO", "2026-07-01", WINDOW[1])
    assert (st.resolved, st.served_through, st.fetch_failure, st.unread_dividends) == (
        True, "2026-07-20", CAUSE, EVENT)

    banner = as_of_live_line(dt.date(2026, 7, 27), frontier=dt.date(2026, 7, 20))
    assert f"for 1 ticker ({CAUSE})" in banner, banner
    assert banner.endswith(
        " A price update held a dividend that could not be read, so none of it is stored "
        f"and VOO's prices stop at July 20, 2026: {EVENT}."), banner


# ── the window stays a gap until an answer reads cleanly ─────────────────────

def test_a_new_process_asks_for_the_window_again_and_records_it_again(book, monkeypatch):
    """Within the process the failed window waits out RETRY_AFTER, as any failed gap
    fetch does (#637). A new process has no record, but the store still lacks the
    window, so it asks again, meets the event and records it again."""
    from src.prices import get_prices
    asked = _yahoo(monkeypatch, _voo(UNREADABLE))
    get_prices("VOO", "2026-07-01", WINDOW[1])
    get_prices("VOO", "2026-07-01", WINDOW[1])
    assert asked == [("VOO", *WINDOW)], "not asked again within RETRY_AFTER"

    _restart()
    assert _unread() == [] and _failures() == [], "the records die with the process"
    frame = get_prices("VOO", "2026-07-01", WINDOW[1])
    assert asked == [("VOO", *WINDOW)] * 2, "the new process asks again"
    assert str(max(frame.index)) == "2026-07-20"
    assert _unread() == [EVENT] and _failures() == [("VOO", *WINDOW, CAUSE)]
    assert _stored_after(book, "VOO", "2026-07-20") == []


def test_a_clean_answer_later_stores_the_window_with_its_dividend(book, monkeypatch):
    import src.prices as prices
    from src.asof import as_of_live_line
    from src.demo_refresh import RETRY_AFTER
    from src.holdings import _price_and_status
    asked = _yahoo(monkeypatch, _voo(UNREADABLE))
    prices.get_prices("VOO", "2026-07-01", WINDOW[1])
    assert _unread() == [EVENT]

    later = prices._now() + RETRY_AFTER + dt.timedelta(seconds=1)
    monkeypatch.setattr(prices, "_now", lambda: later)
    _yahoo(monkeypatch, _voo({JUL22: {"amount": 0.5}}))
    frame = prices.get_prices("VOO", "2026-07-01", WINDOW[1])
    assert asked == [("VOO", *WINDOW)]
    assert str(max(frame.index)) == WINDOW[1]
    assert _stored_after(book, "VOO", "2026-07-20") == ["2026-07-21", "2026-07-22",
                                                        "2026-07-23", "2026-07-24"]
    assert _rows(book, "SELECT ex_date, amount FROM dividends WHERE ticker = 'VOO' AND "
                       "ex_date > ?", "2026-07-20") == [("2026-07-22", 0.5)]
    assert _unread() == [] and _failures() == []
    _frame, st = _price_and_status("VOO", "2026-07-01", WINDOW[1])
    assert (st.fetch_failure, st.unread_dividends) == (None, None)
    assert "dividend" not in as_of_live_line(dt.date(2026, 7, 27),
                                             frontier=dt.date(2026, 7, 24))


def test_a_clean_answer_without_the_event_clears_its_record_too(book, monkeypatch):
    """The window is stored whole, so the record no longer says why the store stops,
    whether or not the provider still sends the event."""
    from src.prices import YahooUnreadDividend, fetch_prices
    _yahoo(monkeypatch, _voo(UNREADABLE))
    with pytest.raises(YahooUnreadDividend):
        fetch_prices("VOO", *WINDOW)
    _yahoo(monkeypatch, _voo({}))
    fetch_prices("VOO", "2026-07-21", "2026-07-27")
    assert _unread() == []
    assert _stored_after(book, "VOO", "2026-07-20")[:4] == ["2026-07-21", "2026-07-22",
                                                            "2026-07-23", "2026-07-24"]


def test_an_unread_answer_to_a_narrower_window_keeps_another_windows_record(book, monkeypatch):
    """Control: a clean answer clears only the records its window holds."""
    from src.prices import YahooUnreadDividend, fetch_prices
    _yahoo(monkeypatch, _voo(UNREADABLE))
    with pytest.raises(YahooUnreadDividend):
        fetch_prices("VOO", *WINDOW)
    _yahoo(monkeypatch, _voo({}))
    fetch_prices("VOO", "2026-07-23", "2026-07-24")
    assert _unread() == [EVENT]


# ── the lock, the cap and the advance tool see the missing closes ────────────

@pytest.fixture
def unlocked_q2(book, monkeypatch):
    """The frozen book on July 21, 2026 with no Q2 2026 lock, read by the lock as
    personal mode reads it: the gap fetch on. Yahoo says "Data doesn't exist" for every
    window but the ones a test gives VOO."""
    import src.cache as cache
    import src.prices as prices
    pin_today(monkeypatch)
    monkeypatch.setattr(cache, "_prices_module", prices)     # the lock's own reads
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    assert _delete(book, "DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'") == 1
    yield book
    unpin_leftovers()


def _cap() -> "dt.date":
    from src.asof import most_recent_reportable_quarter
    return most_recent_reportable_quarter("2025-05-01", today=FROZEN_TODAY)[1]


def test_the_end_rule_refuses_the_lock_and_the_cap_steps_back(unlocked_q2, monkeypatch):
    """VOO's closes after June 25 are missing and its update holds an unreadable
    dividend. The lock fetches the update, stores none of it, and refuses on the closes
    it lacks; the cap, on stored state, stays on Q1."""
    from src.cache import LockCoverageError, capture_quarter_snapshot
    assert _delete(unlocked_q2, "DELETE FROM prices WHERE ticker = 'VOO' AND price_date > "
                                "'2026-06-25'") > 0
    assert _cap() == dt.date(2026, 3, 31), "premise: the store lacks VOO's last closes"
    event = {str(_ts("2026-06-29")): {"amount": "abc"}}
    asked = _yahoo(monkeypatch, lambda t, s, e: _chart(s, e, event)
                   if (t, s) == ("VOO", "2026-06-26") else NO_DATA_400)
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert ("VOO", "2026-06-26", "2026-06-30") in asked
    assert str(err.value) == (
        "Cannot lock 2026Q2: the price data does not reach the quarter's end (2026-06-30), "
        "so a lock now would freeze the quarter on older prices. Nothing was locked. "
        "Short: prices end 2026-06-25, missing 2026-06-26 to 2026-06-30 for VOO.")
    assert _stored_after(unlocked_q2, "VOO", "2026-06-25") == []
    assert _cap() == dt.date(2026, 3, 31)


def test_the_interior_rule_and_the_cap_see_a_re_ask_that_stored_nothing(unlocked_q2,
                                                                         monkeypatch):
    """The re-ask for closes missing inside a history (#501) stores nothing either, so
    the gap stays named."""
    from src.cache import quarter_lock_gaps
    from src.prices import YahooUnreadDividend, fetch_prices
    days = ["2026-06-10", "2026-06-11", "2026-06-12"]
    assert _delete(unlocked_q2, "DELETE FROM prices WHERE ticker = 'VOO' AND price_date "
                                "BETWEEN ? AND ?", days[0], days[-1]) == 3
    gaps = quarter_lock_gaps("2026Q2")
    assert gaps and all("VOO" in g for g in gaps), gaps
    _yahoo(monkeypatch, _voo({str(_ts("2026-06-11")): {}}))
    with pytest.raises(YahooUnreadDividend):
        fetch_prices("VOO", days[0], days[-1], only_missing=days)
    assert _rows(unlocked_q2, "SELECT COUNT(*) FROM prices WHERE ticker = 'VOO' AND "
                              "price_date BETWEEN ? AND ?", days[0], days[-1]) == [(0,)]
    assert quarter_lock_gaps("2026Q2") == gaps
    assert _cap() == dt.date(2026, 3, 31)


def test_the_record_is_the_last_guard_for_a_ticker_with_nothing_stored(unlocked_q2,
                                                                       monkeypatch):
    """A ticker the lock reads and the portfolio does not hold, with no stored closes,
    is fetched whole; the answer raises, and the capture would hold it as a disclosed
    gap (#204). No stored rule sees it, so the record refuses the lock, naming it."""
    import src.cache as cache
    from src.cache import LockCoverageError, capture_quarter_snapshot
    from src.holdings import get_holdings_on_date, get_portfolio_account_id
    tickers, _gaps = cache._get_all_snapshot_tickers()
    held = set(get_holdings_on_date("2026-06-30", account_id=get_portfolio_account_id()).index)
    assert "IEF" in tickers and "IEF" not in held, "premise: the lock reads IEF"
    assert _delete(unlocked_q2, "DELETE FROM prices WHERE ticker = 'IEF'") > 0
    event = {str(_ts("2026-06-01")): {}}
    _yahoo(monkeypatch, lambda t, s, e: _chart(s, e, event) if t == "IEF" else NO_DATA_400)
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value) == (
        "Cannot lock 2026Q2: a price update sent a dividend for a ticker the lock reads "
        "that could not be read, so a lock now would freeze total return without it. "
        "Nothing was locked. Unread: IEF, dated 2026-06-01 (it has no amount).")
    assert _rows(unlocked_q2, "SELECT COUNT(*) FROM prices WHERE ticker = 'IEF'") == [(0,)]


@pytest.fixture
def advance(tmp_path, monkeypatch):
    """(the advance tool, a writable scratch copy of the frozen book it targets), with
    the provider stand-in of tests/test_advance_demo_prices.py sending VOO a dividend
    whose amount is not a number."""
    import src.prices as prices
    from tests.test_advance_demo_prices import _provider, _tool
    tool = _tool()
    copy = tmp_path / "book.db"
    shutil.copyfile(FROZEN_BOOK, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(tool, "DEMO_DB", copy)
    monkeypatch.setattr(tool, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(prices._SESSION, "get", _provider(
        tool.DAILY, dividends=[("VOO", dt.date(2026, 7, 22), "abc")]))
    return tool, copy


def test_the_advance_tool_refuses_and_writes_nothing(advance):
    from tests.test_advance_demo_prices import THROUGH, TODAY, _sha
    tool, copy = advance
    before = _sha(copy)
    with pytest.raises(tool.AdvanceError) as err:
        tool.advance(THROUGH, today=TODAY)
    assert str(err.value) == (
        "the fetch failed for VOO (YahooUnreadDividend: Nothing stored for 'VOO' "
        "(2026-07-21 to 2026-07-24): Yahoo Finance sent a dividend dated 2026-07-22 that "
        "could not be read: its amount is not a number.). Nothing was written.")
    assert _sha(copy) == before


def test_on_the_scratch_copy_the_missing_session_check_refuses_too(advance, tmp_path):
    """Read off the scratch copy the fetch left: VOO gained no close, and the check
    that every ticker gains its sessions names them."""
    from tests.test_advance_demo_prices import THROUGH
    tool, copy = advance
    target = THROUGH.isoformat()
    work = tmp_path / "work.db"
    shutil.copyfile(copy, work)
    ends = tool._ends(copy)
    failed = tool._fetch_into(work, ends, target)
    assert [t for t, _why in failed] == ["VOO"]
    new_prices, new_divs, changed = tool._delta(copy, work, target)
    assert not [r for r in new_prices if r[0] == "VOO"]
    with pytest.raises(tool.AdvanceError) as err:
        tool._check(ends, target, new_prices, new_divs, changed)
    assert str(err.value) == (
        "the fetched delta failed its checks, nothing was written:\n  VOO: after "
        "2026-07-20 it should gain 4 close(s) through 2026-07-24 and gained 0; missing "
        "['2026-07-21', '2026-07-22', '2026-07-23', '2026-07-24'], unexpected []")
