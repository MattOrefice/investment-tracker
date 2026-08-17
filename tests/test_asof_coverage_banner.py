"""PR 3 — the freshness banner stops reading the clock (#189).

Written BEFORE the implementation. `as_of_live_line` currently returns
`f"Live data as of {date.today()}"` having consulted nothing, on every page and
every render — including, verified, a render with all 106,971 price rows deleted.

Four states, and the banner must be able to fail into each:

  1 fully current        frontier == today, nothing unresolved  -> the ORIGINAL sentence
  2 current but stale    frontier <  today, nothing unresolved  -> vintage, no freshness claim
  3 incomplete           something unresolved                   -> vintage + coverage
  4 nothing committed    frontier is None                       -> absence, never freshness

State 1 is pinned deliberately: without it a banner that never says "Live data as
of" would pass every other test, and the property "unchanged when the data really
is current" would be untested.

The banner is called from twelve sites and only three of them (pages 1, 2, 11) can
supply a coverage record. The rest resolve a FRONTIER and must say nothing about
coverage — the frontier is blind to it by construction (holdings.py:200-206 skips a
holding with no committed price), so silence there is the honest limit of what is
known, not an omission. test_no_record_makes_no_coverage_claim is what holds that
line, and it is the assertion the second mutant exists to break.

Tests call through the MODULE (asof.as_of_live_line, not a from-import) so the
mutation harness can substitute an implementation.
"""
from datetime import date, timedelta

import pytest

import src.asof as asof
from src.coverage import PriceCoverage, Unresolved

TODAY = date(2026, 8, 16)
FRONTIER = "2026-08-10"
LONG_TODAY = "August 16, 2026"
MARKER_SIGNATURE = "no committed price"
FRESHNESS_CLAIM = "Live data as of"


def _cov(unresolved=(), frontier=FRONTIER, requested=("VOO", "VEA", "SCHP")):
    resolved = tuple(t for t in requested if t not in {u.ticker for u in unresolved})
    return PriceCoverage(
        requested=requested, resolved=resolved, unresolved=unresolved,
        as_of_requested=TODAY.isoformat(), frontier_served=frontier)


# ── state 1 — fully current ───────────────────────────────────────────────────

def test_state1_fully_current_keeps_the_original_sentence():
    """The sentence was never wrong in this state; it was wrong because it was
    UNCONDITIONAL. Preserving it here is also what keeps the change honest — a
    banner that never claims freshness is as uninformative as one that always
    does."""
    line = asof.as_of_live_line(
        TODAY, frontier=TODAY.isoformat(), coverage=_cov(frontier=TODAY.isoformat()))
    assert FRESHNESS_CLAIM in line and LONG_TODAY in line
    assert MARKER_SIGNATURE not in line


# ── state 2 — current but stale ───────────────────────────────────────────────

def test_state2_stale_withdraws_the_freshness_claim():
    """Annotating rather than withdrawing asks the reader to hold two
    contradictory claims: "Live data as of today (6 days stale)"."""
    line = asof.as_of_live_line(TODAY, frontier=FRONTIER, coverage=_cov())
    assert FRESHNESS_CLAIM not in line, f"still claims freshness while stale: {line!r}"
    assert "August 10, 2026" in line
    assert "6 days" in line, f"the size of the lag is the actionable part: {line!r}"
    assert MARKER_SIGNATURE not in line, "nothing is unresolved in state 2"


def test_state2_has_no_threshold_below_which_it_claims_freshness():
    """No grace period. staleness_note uses 70/45 days because it guards a REFRESH
    CYCLE and a committed factor file is expected to lag; prices are expected to be
    current, so a threshold here would recreate the defect below its own cutoff."""
    one_day_back = (TODAY - timedelta(days=1)).isoformat()
    line = asof.as_of_live_line(TODAY, frontier=one_day_back, coverage=_cov(frontier=one_day_back))
    assert FRESHNESS_CLAIM not in line, f"one day stale still claims freshness: {line!r}"


# ── state 3 — incomplete ──────────────────────────────────────────────────────

def test_state3_incomplete_states_both_vintage_and_coverage():
    line = asof.as_of_live_line(
        TODAY, frontier=FRONTIER,
        coverage=_cov(unresolved=(Unresolved("VOO", "no_cached_rows"),
                                  Unresolved("VEA", "no_cached_rows"))))
    assert FRESHNESS_CLAIM not in line
    assert MARKER_SIGNATURE in line, f"no coverage claim in state 3: {line!r}"
    assert "2 of 3" in line, f"the count is the actionable part: {line!r}"
    assert "August 10, 2026" in line, "vintage must survive alongside coverage"


def test_state3_does_not_name_tickers():
    """The page's own marker names them in context; the banner is a one-line global
    statement and naming them twice makes the banner the place people read, which
    is the wrong place for per-holding detail."""
    line = asof.as_of_live_line(
        TODAY, frontier=FRONTIER,
        coverage=_cov(unresolved=(Unresolved("VOO", "no_cached_rows"),)))
    assert "VOO" not in line


# ── state 4 — nothing committed ───────────────────────────────────────────────

def test_state4_absence_never_presents_as_freshness():
    """staleness_note's existing rule (asof.py:38-42) applied to prices. This is the
    state that renders today under total price loss while the banner says "Live data
    as of <today>"."""
    line = asof.as_of_live_line(TODAY, frontier=None, coverage=None)
    assert FRESHNESS_CLAIM not in line
    assert LONG_TODAY not in line, f"a date claim survived with nothing committed: {line!r}"
    assert "No committed price data" in line


# ── the nine pages: vintage yes, coverage no ──────────────────────────────────

def test_no_record_makes_no_coverage_claim():
    """Nine of twelve callers have no coverage record. They report VINTAGE, which
    committed_price_frontier can answer for any page, and say NOTHING about
    coverage, which they cannot know.

    "Prices through August 10" makes no completeness claim; "Live data as of
    August 10" would. That distinction is the whole design.
    """
    line = asof.as_of_live_line(TODAY, frontier=FRONTIER, coverage=None)
    assert MARKER_SIGNATURE not in line, (
        f"claimed coverage with no record to consult: {line!r}")
    assert "holdings" not in line
    assert "August 10, 2026" in line, "vintage is still knowable without a record"
    assert FRESHNESS_CLAIM not in line


def test_no_record_and_current_still_reaches_state1():
    """Non-vacuity for the test above: suppressing the freshness claim whenever the
    record is absent would pass it and be wrong."""
    line = asof.as_of_live_line(TODAY, frontier=TODAY.isoformat(), coverage=None)
    assert FRESHNESS_CLAIM in line


# ── the frontier is resolved, not assumed ─────────────────────────────────────

def test_omitted_frontier_is_looked_up_not_assumed_fresh():
    """The default must be "go and look", not "assume fresh" — that default is what
    lets the nine pages stop lying without being touched."""
    calls = []

    def _fake_frontier(*_a, **_k):
        calls.append(1)
        return FRONTIER

    import src.holdings
    original = src.holdings.committed_price_frontier
    src.holdings.committed_price_frontier = _fake_frontier
    try:
        line = asof.as_of_live_line(TODAY)
    finally:
        src.holdings.committed_price_frontier = original
    assert calls, "the banner assumed a frontier instead of resolving one"
    assert FRESHNESS_CLAIM not in line
