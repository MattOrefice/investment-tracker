"""The banner's freshness count is of missing closes, not calendar days (#368 follow-up).

Under the settled-closes-before-today rule the latest close expected is the last
session before today, and a frontier there is current: no count. The count used to be
calendar days, so current data read "1 day behind", a Monday "3 days behind", and a
running demo whose later fetch failed called up-to-date data one day behind.

The calendar is demo_refresh's, the one that times the next New York close: weekdays,
no holiday table. So the day after a market holiday reads one behind, pinned below.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import src.demo_refresh as refresh
from src.asof import as_of_live_line
from src.coverage import PriceCoverage, Unresolved

THU, FRI, SAT, SUN, MON = (date(2026, 9, 24) + timedelta(days=i) for i in range(5))


@pytest.fixture(autouse=True)
def _no_refresh(monkeypatch):
    monkeypatch.setattr(refresh, "_STATE", None)


def test_the_last_session_before_today_is_current_and_gets_no_count():
    assert as_of_live_line(FRI, frontier=THU) == (
        "Prices through September 24, 2026 (settled closes).")


@pytest.mark.parametrize("today", [SAT, SUN, MON])
def test_a_weekend_adds_nothing(today):
    assert as_of_live_line(today, frontier=FRI) == (
        "Prices through September 25, 2026 (settled closes).")


@pytest.mark.parametrize("frontier, n", [
    (THU, "1 weekday"),                      # Friday's close is missing
    (date(2026, 9, 21), "4 weekdays"),       # Tuesday to Friday
    (date(2026, 9, 19), "5 weekdays"),       # a Saturday frontier: Monday to Friday
])
def test_the_count_is_the_closes_missing_before_today(frontier, n):
    assert as_of_live_line(MON, frontier=frontier).endswith(f"(settled closes) — {n} behind.")


def test_a_failed_refresh_on_current_data_reports_the_failure_without_a_count(monkeypatch):
    """The case this was filed for: a running demo whose later fetch fails is still
    serving the latest settled close, and must not call it behind."""
    attempted = datetime(2026, 9, 24, 20, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(refresh, "_STATE", refresh.RefreshState(
        status="failed", attempted_at=attempted,
        next_attempt_at=attempted + refresh.RETRY_AFTER,
        served_through="2026-09-23", fetched_before="2026-09-23T20:30+00:00"))
    assert as_of_live_line(THU, frontier="2026-09-23") == (
        "Prices through September 23, 2026 (settled closes). The daily price fetch failed "
        "September 24, 2026 at 20:30 UTC; serving prices last fetched September 23, 2026 "
        "at 20:30 UTC until it retries after September 24, 2026 at 21:00 UTC.")


def test_a_market_holiday_counts_as_a_session():
    """The calendar's known limit, pinned so it is changed on purpose: it has no
    holiday table. Labor Day 2026 is Monday September 7, so on Tuesday the Friday
    frontier is current, but the banner reads one weekday behind."""
    assert as_of_live_line(date(2026, 9, 8), frontier=date(2026, 9, 4)).endswith(
        "— 1 weekday behind.")


def test_the_count_uses_the_refresh_calendar(monkeypatch):
    """The same calendar as next_close_after, not a copy: teach it the holiday and
    the banner follows."""
    monkeypatch.setattr(refresh, "is_session",
                        lambda d: d.weekday() < 5 and d != date(2026, 9, 7))
    assert as_of_live_line(date(2026, 9, 8), frontier=date(2026, 9, 4)) == (
        "Prices through September 4, 2026 (settled closes).")
    assert refresh.next_close_after(datetime(2026, 9, 4, 21, tzinfo=timezone.utc)) == (
        datetime(2026, 9, 8, 20, 30, tzinfo=timezone.utc))


def _cov(frontier, today=FRI):
    return PriceCoverage(
        requested=("VOO", "VEA"), resolved=("VOO",),
        unresolved=(Unresolved("VEA", "no_cached_rows"),),
        as_of_requested=today.isoformat(), frontier_served=frontier.isoformat())


def test_incomplete_states_the_gap_with_or_without_a_count():
    assert as_of_live_line(FRI, coverage=_cov(THU)) == (
        "Prices through September 24, 2026 (as served to this page) — 1 of 2 holdings "
        "have no committed price.")
    assert as_of_live_line(FRI, coverage=_cov(date(2026, 9, 22))) == (
        "Prices through September 22, 2026 (as served to this page) — 2 weekdays behind, "
        "and 1 of 2 holdings have no committed price.")


def test_state_one_and_state_four_are_unchanged():
    full = PriceCoverage(requested=("VOO",), resolved=("VOO",), unresolved=(),
                         as_of_requested=FRI.isoformat(), frontier_served=FRI.isoformat())
    assert as_of_live_line(FRI, coverage=full) == "Live data as of September 25, 2026."
    assert as_of_live_line(FRI, frontier=None) == "No committed price data."
