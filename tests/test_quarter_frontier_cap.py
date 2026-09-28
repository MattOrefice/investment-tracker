"""The data-frontier cap on quarter selection — the guard that did not exist.

Failure this pins (measured on committed demo.db, 2026-07-27, before the cap):
with the price frontier at 2026-07-20 and the wall clock past Oct 1, the report
selected Q3 2026 (closed 2026-09-30) because selection was a pure function of the
calendar. What rendered was not a uniform "stale" report — it was worse, because
the two halves failed differently:

  - attribution/benchmarks/holdings BROKE VISIBLY: BF returned an empty frame with
    all 14 holdings and all 14 benchmark legs recorded as gaps, every benchmark
    column rendered N/A, and current market value collapsed to the cash sleeve
    ($21 of a ~$1,304 book) because get_current_market_value's 7-day lookback
    found nothing;
  - the executive summary and trailing-period table FORWARD-FILLED SILENTLY:
    "Portfolio returned -1.25% in Q3 2026" was really the 2026-06-30 -> 2026-07-20
    move, three weeks under a quarter's label, and "1 Month" printed 0.00% because
    that whole window sat inside the flat tail.

The headline number a reader quotes was therefore wrong while the rest of the page
announced breakage. Hence a cap on *selection* rather than a warning on render.

Note on coverage boundaries: src/attribution.py's reconciliation tests do NOT
cover this. They anchor on the holdings' common frontier read from the DB, which
is invariant to the wall clock (measured at 2026-07-27, 2026-10-01, 2026-10-15 and
2027-01-05 -> 2026-07-20 every time), so they stay green through exactly the
regression this file exists to catch.

Every test pins ``frontier`` explicitly, so these run offline and are independent
of whatever the committed price cache happens to hold.
"""
from datetime import date, timedelta

import pytest

from src.asof import (
    _most_recent_completed_quarter,
    most_recent_reportable_quarter,
    quarter_staleness_note,
    reportable_quarter_phrase,
    as_of_report_line,
)

# The exact production condition: wall clock past Q3's close, demo's committed
# price frontier still at 2026-07-20.
_TODAY_PAST_Q3 = date(2026, 10, 15)
_FRONTIER = "2026-07-20"
_INCEPTION = "2025-05-01"          # demo.db's MIN(trade_date)

_Q2_2026 = (date(2026, 3, 31), date(2026, 6, 30), "Q2 2026")


# ── The cap itself ────────────────────────────────────────────────────────────

def test_calendar_would_have_chosen_q3():
    """Precondition: uncapped, the calendar picks Q3 2026. Without this the test
    below could pass because the date arithmetic never reached Q3 at all."""
    _, q_end, label = _most_recent_completed_quarter(_TODAY_PAST_Q3)
    assert (label, q_end) == ("Q3 2026", date(2026, 9, 30))


def test_frontier_caps_selection_back_to_q2():
    """The guard: past Q3's close with data ending 2026-07-20, the reportable
    quarter is Q2 2026 — not the calendar's Q3 2026."""
    assert most_recent_reportable_quarter(
        _INCEPTION, _TODAY_PAST_Q3, frontier=_FRONTIER
    ) == _Q2_2026


def test_uncapped_selection_still_returns_q3():
    """A far-future frontier leaves the pure inception logic untouched, so the cap
    is demonstrably the thing doing the work above — not an unrelated change to
    quarter arithmetic."""
    q = most_recent_reportable_quarter(
        _INCEPTION, _TODAY_PAST_Q3, frontier=date(2099, 1, 1)
    )
    assert q[2] == "Q3 2026"


def test_cap_steps_back_multiple_quarters():
    """Two full quarters past the frontier steps back two quarters, not one — the
    loop keeps walking rather than assuming a single step suffices."""
    assert most_recent_reportable_quarter(
        _INCEPTION, date(2027, 1, 15), frontier=_FRONTIER
    ) == _Q2_2026


# Migrated with #458. These pinned the 5-day allowance: data through 2026-06-26 (4 days
# short) still reported Q2, and one day more capped it. The cap now follows the lock's
# rule (#454): the close of the quarter's last NYSE session, and no earlier one.

def test_a_weekend_quarter_end_needs_the_session_before_it_and_no_earlier():
    """A quarter ending on a weekend must NOT be capped away by its own missing close:
    Q3 2028 ends Saturday, September 30, so Friday's close reports it and Thursday's
    does not."""
    assert most_recent_reportable_quarter(
        _INCEPTION, date(2028, 10, 15), frontier="2028-09-29")[2] == "Q3 2028"
    assert most_recent_reportable_quarter(
        _INCEPTION, date(2028, 10, 15), frontier="2028-09-28")[2] == "Q2 2028"


def test_a_weekday_quarter_end_needs_its_own_close():
    """Q2 2026 ends Tuesday, June 30: that day's close reports it; Monday's, one day
    short and inside the old allowance, does not."""
    assert most_recent_reportable_quarter(
        _INCEPTION, date(2026, 7, 20), frontier="2026-06-30") == _Q2_2026
    q = most_recent_reportable_quarter(_INCEPTION, date(2026, 7, 20), frontier="2026-06-29")
    assert q[2] == "Q1 2026"


def test_no_frontier_is_uncapped_not_suppressed(monkeypatch):
    """A DB with no committed prices resolves to no frontier. That must mean "no
    opinion" (report the calendar quarter), never "suppress everything" — a bare or
    freshly-seeded database must not render as though no quarter ever closed.

    Patches the resolver rather than passing None, because None is the request to
    resolve; this pins what happens when that resolution comes back empty.
    """
    import src.cache as cache
    monkeypatch.setattr(cache, "lock_price_frontier", lambda *a, **k: None)
    q = most_recent_reportable_quarter(_INCEPTION, _TODAY_PAST_Q3)
    assert q is not None and q[2] == "Q3 2026"
    assert quarter_staleness_note(_INCEPTION, _TODAY_PAST_Q3) is None


# ── The disclosure ────────────────────────────────────────────────────────────

def test_staleness_note_names_both_quarters_and_the_frontier():
    """An honestly stale Q2 has to explain itself: which quarter it is reporting,
    which one it skipped, the data date that forced it, and — because the reader of
    a public PDF cannot ask — that the printed figures are themselves complete."""
    note = quarter_staleness_note(_INCEPTION, _TODAY_PAST_Q3, frontier=_FRONTIER)
    assert note == (
        "Reporting Q2 2026, not Q3 2026: price data ends July 20, 2026, which "
        "cannot support a quarter that closed September 30, 2026. Q2 2026 closed "
        "before that date, so the figures in this report are complete."
    )


def test_staleness_note_silent_when_not_stepped_back():
    """The normal case prints nothing — a disclosure on every report trains readers
    to ignore it."""
    assert quarter_staleness_note(
        _INCEPTION, date(2026, 7, 20), frontier=_FRONTIER
    ) is None


def test_staleness_note_silent_for_pre_inception_suppression():
    """A step-back caused by inception is already covered by the NO_COMPLETED_QUARTER
    empty state; explaining it a second time as a data-frontier problem would be
    wrong as well as redundant."""
    assert quarter_staleness_note(
        date(2026, 10, 1), _TODAY_PAST_Q3, frontier=_FRONTIER
    ) is None


# ── Every quarter-naming surface tracks the cap ───────────────────────────────

def test_banner_and_tooltip_track_the_capped_quarter():
    """The as-of banner and the attribution tooltip must not name Q3 while the PDF
    renders Q2 — one helper feeds all three, and this pins that."""
    line = as_of_report_line(_TODAY_PAST_Q3, _INCEPTION, frontier=_FRONTIER)
    assert "Q2 2026" in line and "Q3" not in line
    assert reportable_quarter_phrase(
        _INCEPTION, _TODAY_PAST_Q3, frontier=_FRONTIER
    ) == "(Q2 2026)"


# ── End-to-end through the report's own selection path ────────────────────────

@pytest.mark.parametrize("today,expected", [
    (date(2026, 7, 27), "Q2 2026"),   # today: calendar and frontier agree
    (date(2026, 10, 1), "Q2 2026"),   # first day Q3 is calendar-complete
    (date(2026, 10, 15), "Q2 2026"),  # the reported failure condition
    (date(2027, 1, 5), "Q2 2026"),    # a further quarter on, still capped
])
def test_report_period_label_never_outruns_the_frontier(today, expected):
    """The label the PDF cover would carry, across the dates that used to change it.
    Before the cap this returned Q2/Q3/Q3/Q4 respectively."""
    from src.reports import _format_period_label
    q = most_recent_reportable_quarter(_INCEPTION, today, frontier=_FRONTIER)
    assert _format_period_label(q[0].isoformat(), q[1].isoformat()) == expected
