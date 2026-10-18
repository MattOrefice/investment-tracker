"""The NYSE holiday table behind the banner's count of missing closes (audit item 12).

demo_refresh.is_session counted every weekday, so the day after a market holiday read
a current frontier as a close behind, and the banner said "weekdays" because that is
what it counted. The table is static, with no calendar dependency, so it runs out:
the first test here fails from January 1 of its last year, a year before it does.

Since #466 (decided by the owner on September 28, 2026) the table holds 2025 as well,
January 9, 2025 included: the national day of mourning, a closure no calendar
scheduled, entered by hand from NYSE's notice. The close-out refuses to run when the
table does not reach the end of next year, by the same rule as the first test.

Since #644 (decided by the owner on October 18, 2026) it reaches back to 1990, the
earliest start any get_prices caller passes, each year from NYSE's own published
calendars, and the committed books' SPY closes agree with it on every day they hold.
"""
import importlib.util
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.demo_refresh as refresh

ROOT = Path(__file__).resolve().parent.parent


def test_the_table_is_extended_before_its_last_year():
    first, last = refresh.NYSE_HOLIDAY_YEARS
    assert refresh.holiday_table_refusal() is None, (
        f"The NYSE holiday table in src/demo_refresh.py ends in {last}. Extend it from "
        "nyse.com/markets/hours-calendars: past its last year every weekday counts as a "
        "session again, and the banner reads the day after a holiday as a close behind.")


def test_the_refusal_needs_the_table_through_the_end_of_next_year():
    last = refresh.NYSE_HOLIDAY_YEARS[1]
    assert refresh.holiday_table_refusal(date(last - 1, 1, 1)) is None
    assert refresh.holiday_table_refusal(date(last - 1, 12, 31)) is None
    refusal = refresh.holiday_table_refusal(date(last, 1, 1))
    assert refusal and f"ends with {last}" in refusal, refusal
    assert f"through December 31, {last + 1}" in refusal, refusal


UNSCHEDULED = {
    date(1994, 4, 27),                                       # President Nixon's funeral
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),                                       # President Reagan
    date(2007, 1, 2),                                        # President Ford
    date(2012, 10, 29), date(2012, 10, 30),                  # Hurricane Sandy
    date(2018, 12, 5),                                       # President George H. W. Bush
    date(2025, 1, 9),                                        # President Carter
}


def test_the_table_covers_its_stated_years_and_only_weekdays():
    """MIGRATED at #644 (decided by the owner on October 18, 2026): the span read (2025,
    2028) and the one unscheduled closure January 9, 2025. The scheduled count per year
    runs from 7 (1994: no New Year's closure, and no Martin Luther King Jr. Day before
    1998) to 10."""
    first, last = refresh.NYSE_HOLIDAY_YEARS
    assert (first, last) == (1990, 2028)
    years = {d.year for d in refresh.NYSE_HOLIDAYS}
    assert years == set(range(first, last + 1))
    assert all(d.weekday() < 5 for d in refresh.NYSE_HOLIDAYS), "a weekend is no session anyway"
    scheduled = refresh.NYSE_HOLIDAYS - refresh.NYSE_UNSCHEDULED_CLOSURES
    counts = {y: sum(d.year == y for d in scheduled) for y in years}
    assert all(7 <= n <= 10 for n in counts.values()), counts
    assert [y for y, n in counts.items() if n == 7] == [1994]
    assert refresh.NYSE_UNSCHEDULED_CLOSURES == UNSCHEDULED
    assert not refresh.NYSE_UNSCHEDULED_CLOSURES & refresh._SCHEDULED_CLOSURES


def test_every_year_and_every_unscheduled_closure_cites_nyses_own_document():
    first, last = refresh.NYSE_HOLIDAY_YEARS
    assert set(refresh.NYSE_CALENDAR_SOURCES) == set(range(first, last + 1))
    for year, docs in refresh.NYSE_CALENDAR_SOURCES.items():
        assert docs, year
        for key in docs:
            words, _url = refresh.NYSE_DOCUMENTS[key]
            assert words.startswith(("NYSE", "nyse.com")), (year, key, words)
    assert set(refresh.NYSE_UNSCHEDULED_NOTICES) == UNSCHEDULED
    for day, (why, doc, url) in refresh.NYSE_UNSCHEDULED_NOTICES.items():
        assert why and doc, day
    archived = [u for _w, u in refresh.NYSE_DOCUMENTS.values() if u]
    assert all(u.startswith("https://web.archive.org/web/") for u in archived), archived


def test_the_table_reaches_every_start_a_price_read_passes():
    """Asset Evaluation's SAMPLE_START and the lock's read start fall inside it, each an
    NYSE holiday the table now knows, and so does the Correlations page's pair history,
    the earliest start any get_prices caller passes."""
    import re
    from src.asset_evaluation import SAMPLE_START
    from src.cache import _INPUT_START
    first = date(refresh.NYSE_HOLIDAY_YEARS[0], 1, 1)
    capture = (ROOT / "src" / "cache.py").read_text(encoding="utf-8")
    lock_start = re.search(r'inception_str = "(\d{4}-\d{2}-\d{2})"', capture).group(1)
    pages = (ROOT / "pages" / "9_Correlations.py").read_text(encoding="utf-8")
    pair_start = re.search(r'_PAIR_HISTORY_START = "(\d{4}-\d{2}-\d{2})"', pages).group(1)
    for start in (SAMPLE_START, _INPUT_START, lock_start, pair_start):
        assert date.fromisoformat(start) >= first, start
    assert (SAMPLE_START, _INPUT_START, lock_start, pair_start) == (
        "2018-01-01", "2020-01-01", "2020-01-01", "1990-01-01")
    for start in (SAMPLE_START, _INPUT_START, pair_start):
        assert not refresh.is_session(date.fromisoformat(start)), start


@pytest.mark.parametrize("book", ["data/demo.db", "tests/fixtures/frozen_book.db"])
def test_the_committed_books_spy_closes_agree_with_every_year_of_the_table(book):
    """SPY, the session reference, holds a close on every weekday the table calls a
    session and on none it calls a closure, on every day of every year it holds: demo.db
    from SPY's first close, January 29, 1993, and the frozen book from 2024."""
    conn = sqlite3.connect(f"file:{ROOT / book}?mode=ro", uri=True)
    try:
        closes = {date.fromisoformat(d) for (d,) in conn.execute(
            "SELECT price_date FROM prices WHERE ticker = 'SPY'")}
    finally:
        conn.close()
    first, last = min(closes), min(max(closes), date(refresh.NYSE_HOLIDAY_YEARS[1], 12, 31))
    wrong, day = [], first
    while day <= last:
        if refresh.is_session(day) != (day in closes):
            wrong.append(day)
        day += timedelta(days=1)
    assert wrong == []
    assert first <= (date(1993, 1, 29) if "demo" in book else date(2024, 1, 2))


@pytest.mark.parametrize("book", ["data/demo.db", "tests/fixtures/frozen_book.db"])
def test_the_committed_books_spy_closes_agree_with_2025s_table(book):
    """SPY, the session reference, holds a close on every 2025 weekday the table calls a
    session and on none it calls a closure, January 9 among them."""
    conn = sqlite3.connect(f"file:{ROOT / book}?mode=ro", uri=True)
    try:
        closes = {date.fromisoformat(d) for (d,) in conn.execute(
            "SELECT price_date FROM prices WHERE ticker = 'SPY' AND price_date "
            "BETWEEN '2025-01-01' AND '2025-12-31'")}
    finally:
        conn.close()
    weekdays = [date(2025, 1, 1) + timedelta(days=i) for i in range(365)]
    weekdays = [d for d in weekdays if d.weekday() < 5]
    assert len(weekdays) == 261 and len(closes) == 250, (len(weekdays), len(closes))
    assert [d for d in weekdays if refresh.is_session(d) != (d in closes)] == []


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_each_close_out_tool_refuses_to_run_on_a_short_table(monkeypatch, tmp_path, capsys):
    """Before it reads or writes anything."""
    import src.db as db
    monkeypatch.setattr(refresh, "holiday_table_refusal",
                        lambda today=None: "the table is short")

    def _ran(*_a, **_k):
        raise AssertionError("the tool ran past the refusal")

    advance = _tool("advance_demo_prices")
    monkeypatch.setattr(advance, "advance", _ran)
    assert advance.main(["advance_demo_prices.py", "2026-09-30"]) == 1

    check = _tool("check_price_histories")
    monkeypatch.setattr(check, "history_ends", _ran)
    assert check.main(["check_price_histories.py"]) == 1

    # Past a missing refusal the lock tool points src.db at demo.db and switches off the
    # price layer's gap fills before it locks: the state is restored here, and the
    # switch stops it.
    import src.prices as prices
    path = db.DB_PATH
    for name in ("DB_PATH", "_migrated_paths", "_RUNTIME_CACHE"):
        monkeypatch.setattr(db, name, getattr(db, name))
    monkeypatch.setattr(prices, "set_gap_fetch", _ran)
    assert _tool("lock_demo_quarters").main() == 1
    assert db.DB_PATH == path, "the lock tool pointed the app at demo.db before refusing"

    book = tmp_path / "absent.db"
    with pytest.raises(SystemExit, match="ABORT: the table is short"):
        _tool("complete_demo_lock_inputs").main("2026Q3", ["umd"], book)
    assert not book.exists(), "the completion tool opened the book before refusing"

    assert capsys.readouterr().out.count("ABORT: the table is short") == 3


@pytest.mark.parametrize("day, session", [
    (date(2025, 1, 9), False),     # the national day of mourning, by NYSE's notice
    (date(2025, 1, 10), True),
    (date(2025, 4, 18), False),    # Good Friday
    (date(2025, 6, 19), False),    # Juneteenth
    (date(2025, 11, 28), True),    # the day after Thanksgiving closes early: a session
    (date(2025, 12, 31), True),
    (date(2026, 9, 7), False),     # Labor Day
    (date(2026, 7, 3), False),     # Independence Day observed (July 4 is a Saturday)
    (date(2027, 12, 24), False),   # Christmas observed
    (date(2026, 11, 27), True),    # the day after Thanksgiving closes early: a session
    (date(2028, 1, 3), True),      # no New Year's closure in 2028 (January 1 is a Saturday)
    (date(2026, 9, 8), True),
    (date(2026, 9, 12), False),    # a Saturday
])
def test_is_session(day, session):
    assert refresh.is_session(day) is session


def test_the_next_close_skips_a_holiday():
    # Friday September 4, 2026 after the close: Monday is Labor Day, so Tuesday.
    assert refresh.next_close_after(datetime(2026, 9, 4, 21, tzinfo=timezone.utc)) == (
        datetime(2026, 9, 8, 20, 30, tzinfo=timezone.utc))


def test_the_banner_says_trading_days():
    from src.asof import as_of_live_line
    line = as_of_live_line(date(2026, 9, 24), frontier=date(2026, 9, 18))
    assert line.endswith(", 3 trading days behind."), line
    assert "weekday" not in line
