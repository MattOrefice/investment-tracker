"""The NYSE holiday table behind the banner's count of missing closes (audit item 12).

demo_refresh.is_session counted every weekday, so the day after a market holiday read
a current frontier as a close behind, and the banner said "weekdays" because that is
what it counted. The table is static, with no calendar dependency, so it runs out:
the first test here fails from January 1 of its last year, a year before it does.
"""
from datetime import date, datetime, timezone

import pytest

import src.demo_refresh as refresh


def test_the_table_is_extended_before_its_last_year():
    first, last = refresh.NYSE_HOLIDAY_YEARS
    assert date.today().year < last, (
        f"The NYSE holiday table in src/demo_refresh.py ends in {last}. Extend it from "
        "nyse.com/markets/hours-calendars: past its last year every weekday counts as a "
        "session again, and the banner reads the day after a holiday as a close behind.")


def test_the_table_covers_its_stated_years_and_only_weekdays():
    first, last = refresh.NYSE_HOLIDAY_YEARS
    years = {d.year for d in refresh.NYSE_HOLIDAYS}
    assert years == set(range(first, last + 1))
    assert all(d.weekday() < 5 for d in refresh.NYSE_HOLIDAYS), "a weekend is no session anyway"
    assert all(8 <= sum(d.year == y for d in refresh.NYSE_HOLIDAYS) <= 10 for y in years)


@pytest.mark.parametrize("day, session", [
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
    assert line.endswith("— 3 trading days behind."), line
    assert "weekday" not in line
