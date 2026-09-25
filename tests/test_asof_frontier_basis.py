"""#265 — two freshness definitions, both kept, each stated.

A coverage record's frontier is "how current is what THIS PAGE served" (it may include
today); the committed frontier is "the latest SETTLED close every holding has"
(strictly before today). Two pages on one book can differ by a day, so each banner
says which it means. And "1 days behind" is fixed (it counts weekdays now: "1 weekday").
"""
from datetime import date

from src.asof import as_of_live_line
from src.coverage import PriceCoverage

TODAY = date(2026, 8, 18)


def _cov(frontier: str) -> PriceCoverage:
    return PriceCoverage(requested=("VOO",), resolved=("VOO",), unresolved=(),
                         as_of_requested=TODAY.isoformat(), frontier_served=frontier)


def test_the_committed_frontier_says_settled_closes():
    line = as_of_live_line(TODAY, frontier="2026-08-17")
    assert "(settled closes)" in line and "as served" not in line, line


def test_a_coverage_record_says_as_served_to_this_page():
    line = as_of_live_line(TODAY, coverage=_cov("2026-08-16"))
    assert "(as served to this page)" in line and "settled" not in line, line


def test_one_weekday_behind_is_singular_and_two_is_plural():
    """TODAY is a Tuesday, so Monday's close is the latest expected (the count is of
    missing closes since #368's follow-up): Friday's frontier misses one, Thursday's
    two."""
    assert "— 1 weekday behind" in as_of_live_line(TODAY, frontier="2026-08-14")
    assert "1 weekdays" not in as_of_live_line(TODAY, frontier="2026-08-14")
    assert "— 2 weekdays behind" in as_of_live_line(TODAY, frontier="2026-08-13")


def test_state_one_is_unchanged_when_fully_current():
    """The 'Live data as of' sentence carries no basis: it is only reachable when
    what was served is complete and current, which needs no qualifier."""
    assert as_of_live_line(TODAY, coverage=_cov(TODAY.isoformat())).startswith("Live data as of")
