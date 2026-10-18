"""The reportable-quarter cap reads the lock's held-without-price rule (#648).

Decided by the owner on October 17, 2026. #645 refused a lock while a holding of the
portfolio had no close on or before a session in the quarter on which it was held
(cache._held_without_price, #639). The cap that picks the reportable quarter read only
the end rule (lock_price_frontier) and the interior-gap rule (quarter_lock_gaps), so
a page could name a quarter whose lock the capture then refused. quarter_lock_gaps now
calls the same check over the same quarter, so a page offers a quarter only once its
lock can be taken, and the note it prints says which rule held the quarter back, in
the words of the capture's refusal.

On copies of the frozen book on July 1, 2026, offline, with Q2 2026's lock removed. A
holding unpriced on days it was held is made by deleting its closes before a date.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
INCEPTION = "2025-05-01"


def _cut(path, ticker: str, before: str) -> None:
    """Delete ``ticker``'s closes before ``before``: its series now starts there."""
    con = sqlite3.connect(path)
    try:
        with con:
            assert con.execute("DELETE FROM prices WHERE ticker = ? AND price_date < ?",
                               (ticker, before)).rowcount > 0
    finally:
        con.close()


def _sessions(path, start: str, end: str) -> "list[str]":
    """The days SPY closed in [start, end]: the sessions the book's frame holds."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT price_date FROM prices WHERE ticker = 'SPY' AND price_date >= ? "
            "AND price_date <= ? ORDER BY price_date", (start, end))]
    finally:
        con.close()


def _locked(path) -> "list[str]":
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT quarter_id FROM quarter_snapshots")]
    finally:
        con.close()


@pytest.fixture
def unlocked_q2(tmp_path, monkeypatch):
    """The frozen book on the day after Q2 2026 ended, with no Q2 lock: the calendar's
    quarter is Q2, its last close is stored, and Q1 2026 is locked."""
    import streamlit as st
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    path = point_at_frozen_book(monkeypatch, tmp_path)
    pin_today(monkeypatch, date(2026, 7, 1))
    con = sqlite3.connect(path)
    try:
        with con:
            assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                               ).rowcount == 1
    finally:
        con.close()
    assert "2026Q1" in _locked(path), "premise: the quarter before is locked"
    st.cache_data.clear()
    yield path
    st.cache_data.clear()
    unpin_leftovers()


def _unpriced_note(clause: str) -> str:
    return ("Reporting Q1 2026, not Q2 2026: a holding has no price on a date it was held "
            "in Q2 2026, and a Q2 2026 lock would freeze it unpriced. No close on "
            f"{clause}. Q1 2026 does not read those closes, so the figures in this report "
            "are complete.")


def test_a_holding_unpriced_on_days_it_was_held_steps_the_quarter_back(unlocked_q2):
    """VOO, held all quarter, from May 15: the cap names every session before it, as the
    capture's refusal does, and steps back to Q1 2026."""
    from src.asof import (as_of_report_line, most_recent_reportable_quarter,
                          quarter_staleness_note)
    from src.cache import (LockCoverageError, capture_quarter_snapshot, lock_price_frontier,
                           quarter_lock_gaps)
    _cut(unlocked_q2, "VOO", "2026-05-15")
    clause = f"{', '.join(_sessions(unlocked_q2, '2026-04-01', '2026-05-14'))} for VOO"
    assert lock_price_frontier() >= "2026-06-30", "premise: the end rule passes"
    assert quarter_lock_gaps("2026Q2") == [clause]
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q1 2026"
    assert quarter_staleness_note(INCEPTION) == _unpriced_note(clause)
    assert as_of_report_line() == "Latest locked quarterly report: Q1 2026 (March 31, 2026)."
    # The same check: the capture refuses Q2 for exactly the clause the cap read.
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value).endswith(f"No close on {clause}.")
    assert "2026Q2" not in _locked(unlocked_q2)


def test_spaxx_is_read_through_bil(unlocked_q2):
    from src.asof import most_recent_reportable_quarter, quarter_staleness_note
    from src.cache import quarter_lock_gaps
    _cut(unlocked_q2, "BIL", "2026-05-15")
    clause = f"{', '.join(_sessions(unlocked_q2, '2026-04-01', '2026-05-14'))} for SPAXX (BIL)"
    assert quarter_lock_gaps("2026Q2") == [clause]
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q1 2026"
    assert quarter_staleness_note(INCEPTION) == _unpriced_note(clause)


def test_a_market_holiday_is_not_named(unlocked_q2):
    """VOO from Monday April 6, 2026: April 1 and 2 are named, Good Friday is not."""
    from src.cache import quarter_lock_gaps
    _cut(unlocked_q2, "VOO", "2026-04-06")
    assert quarter_lock_gaps("2026Q2") == ["2026-04-01, 2026-04-02 for VOO"]


def test_a_ticker_the_portfolio_does_not_hold_may_start_late(unlocked_q2):
    """SPY, which the lock covers and the portfolio does not hold, from May 15: a late
    listing is not a gap, so the cap offers Q2, and its lock is taken."""
    from src.asof import most_recent_reportable_quarter, quarter_staleness_note
    from src.cache import capture_quarter_snapshot, quarter_lock_gaps
    _cut(unlocked_q2, "SPY", "2026-05-15")
    assert quarter_lock_gaps("2026Q2") == []
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q2 2026"
    assert quarter_staleness_note(INCEPTION) is None
    capture_quarter_snapshot("2026Q2")
    assert "2026Q2" in _locked(unlocked_q2)


def test_both_rules_are_named_each_in_its_own_words(unlocked_q2):
    """An interior gap and an unpriced holding at once: the interior clause first, as
    the capture checks it first, and each with the words of its own refusal."""
    from src.asof import quarter_staleness_note
    from src.cache import quarter_lock_gaps, quarter_lock_refusals
    _cut(unlocked_q2, "VOO", "2026-05-15")
    con = sqlite3.connect(unlocked_q2)
    with con:
        assert con.execute("DELETE FROM prices WHERE ticker = 'VTV' AND "
                           "price_date = '2026-06-10'").rowcount == 1
    con.close()
    unpriced = f"{', '.join(_sessions(unlocked_q2, '2026-04-01', '2026-05-14'))} for VOO"
    assert quarter_lock_refusals("2026Q2") == (["2026-06-10 for VTV"], [unpriced])
    assert quarter_lock_gaps("2026Q2") == ["2026-06-10 for VTV", unpriced]
    assert quarter_staleness_note(INCEPTION) == (
        "Reporting Q1 2026, not Q2 2026: the price data has a gap inside a ticker's "
        "history, and a Q2 2026 lock would freeze it. No close on 2026-06-10 for VTV. "
        "A holding has no price on a date it was held in Q2 2026, and a Q2 2026 lock "
        f"would freeze it unpriced. No close on {unpriced}. Q1 2026 does not read those "
        "closes, so the figures in this report are complete.")


def test_the_page_names_the_quarter_its_lock_can_take(unlocked_q2):
    """The Performance page shows Q1 2026's tiles and the note, and neither Q2 nor the
    lock's refusal."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    _cut(unlocked_q2, "VOO", "2026-05-15")
    clause = f"{', '.join(_sessions(unlocked_q2, '2026-04-01', '2026-05-14'))} for VOO"
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    labels = [m.label for m in at.metric]
    assert "Q1 2026 return" in labels, labels
    assert not [lb for lb in labels if "Q2 2026" in lb], labels
    assert _unpriced_note(clause) in [str(c.value) for c in at.caption]
    shown = " ".join(str(e.value) for e in list(at.error) + list(at.warning) + list(at.info)
                     + list(at.markdown))
    assert "Cannot lock" not in shown, "the page named Q2 and showed the lock's refusal"
