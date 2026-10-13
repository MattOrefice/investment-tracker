"""A quarter whose lock would be refused for a gap is not the reportable quarter (#491).

A lock refuses prices with a gap inside a ticker's history (tests/test_no_lock_from_a_gap.py).
The cap that picks the reportable quarter read end dates only, so with the quarter's last
close stored and a close missing before it, the Performance page named the new quarter
and showed the lock's refusal in place of its figures. Now it steps back, the way it does
when the last close is missing (tests/test_cap_follows_the_lock.py): the page, the default
report and the banner show the quarter before, and the note names the missing closes.

Frozen book, offline, prices carried forward through September 30, today pinned to
October 1, 2026.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
INCEPTION = "2025-05-01"
OCT_1 = date(2026, 10, 1)
NOTE = ("Reporting Q2 2026, not Q3 2026: the price data has a gap inside a ticker's history, "
        "and a Q3 2026 lock would freeze it. No close on 2026-08-12 for VOO. Q2 2026 does "
        "not read those closes, so the figures in this report are complete.")


def _extend_prices_through(book, day: str):
    """Carry every ticker's last close forward, weekdays, through ``day``."""
    con = sqlite3.connect(book)
    last = {t: (d, c) for t, d, c in con.execute(
        "SELECT p.ticker, p.price_date, p.close FROM prices p JOIN (SELECT ticker, "
        "MAX(price_date) mx FROM prices GROUP BY ticker) m ON p.ticker = m.ticker "
        "AND p.price_date = m.mx")}
    rows = []
    for t, (d, c) in last.items():
        cur = date.fromisoformat(d) + timedelta(days=1)
        while cur <= date.fromisoformat(day):
            if cur.weekday() < 5:
                rows.append((t, cur.isoformat(), c))
            cur += timedelta(days=1)
    with con:
        con.executemany("INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close) "
                        "VALUES (?, ?, ?, NULL)", rows)
    con.close()


def _run(book, sql, *args) -> int:
    con = sqlite3.connect(book)
    try:
        with con:
            return con.execute(sql, args).rowcount
    finally:
        con.close()


def _delete_close(book, ticker, day) -> None:
    assert _run(book, "DELETE FROM prices WHERE ticker = ? AND price_date = ?", ticker, day) == 1


@pytest.fixture
def october_1(tmp_path, monkeypatch):
    """The frozen book on October 1, 2026, every ticker priced through September 30:
    the last close is stored, so the frontier gate passes."""
    import streamlit as st
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    path = point_at_frozen_book(monkeypatch, tmp_path)
    _extend_prices_through(path, "2026-09-30")
    pin_today(monkeypatch, OCT_1)
    st.cache_data.clear()
    yield path
    st.cache_data.clear()
    unpin_leftovers()


def _performance():
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_with_a_close_missing_inside_q3_the_page_stays_on_q2_and_names_it(october_1):
    from src import reports
    from src.asof import (_most_recent_completed_quarter, as_of_report_line,
                          most_recent_reportable_quarter, quarter_staleness_note)
    from src.cache import (LockCoverageError, capture_quarter_snapshot, lock_price_frontier,
                           quarter_lock_gaps)
    _delete_close(october_1, "VOO", "2026-08-12")
    assert _most_recent_completed_quarter(OCT_1)[2] == "Q3 2026", "premise: Q3 has ended"
    assert lock_price_frontier() == "2026-09-30", "premise: the last close is stored"
    assert quarter_lock_gaps("2026Q3") == ["2026-08-12 for VOO"]
    with pytest.raises(LockCoverageError, match="No close on 2026-08-12 for VOO"):
        capture_quarter_snapshot("2026Q3")      # premise: this is what the page would meet

    # The default report's quarter, and the note it prints.
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q2 2026"
    assert quarter_staleness_note(INCEPTION) == NOTE
    html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                   is_demo=True).decode("utf-8")
    assert "No close on 2026-08-12 for VOO." in html
    # The banner's locked-report line.
    assert as_of_report_line() == "Latest locked quarterly report: Q2 2026 (June 30, 2026)."
    # The page: Q2's tiles, the note, and neither Q3 nor the lock's refusal.
    at = _performance()
    labels = [m.label for m in at.metric]
    assert "Q2 2026 return" in labels, labels
    assert not [lb for lb in labels if "Q3 2026" in lb], labels
    assert NOTE in [str(c.value) for c in at.caption]
    shown = " ".join(str(e.value) for e in list(at.error) + list(at.warning) + list(at.info)
                     + list(at.markdown))
    assert "Cannot lock" not in shown, "the page named Q3 and showed the lock's refusal"


def test_with_the_close_stored_it_moves_to_q3(october_1):
    """The contrast: the same book with nothing deleted."""
    from src.asof import as_of_report_line, most_recent_reportable_quarter, quarter_staleness_note
    from src.cache import quarter_lock_gaps
    assert quarter_lock_gaps("2026Q3") == []
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q3 2026"
    assert quarter_staleness_note(INCEPTION) is None
    assert as_of_report_line() == ("Latest locked quarterly report: Q3 2026 "
                                   "(September 30, 2026).")


def test_a_locked_quarter_is_reported_whatever_the_prices_hold_now(october_1):
    """A gap in May 2026 refuses Q3's lock, whose frame runs from 2020. Q2 2026 is
    already locked, and a persisted lock is served as written, so Q2 is reported."""
    from src.asof import most_recent_reportable_quarter, quarter_staleness_note
    from src.cache import quarter_lock_gaps
    _delete_close(october_1, "VOO", "2026-05-06")
    assert quarter_lock_gaps("2026Q3") == ["2026-05-06 for VOO"]
    assert quarter_lock_gaps("2026Q2") == [], "already locked"
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q2 2026"
    assert "No close on 2026-05-06 for VOO." in quarter_staleness_note(INCEPTION)


def test_an_unlocked_quarter_with_the_gap_is_stepped_past_too(october_1):
    """The same gap with Q2's lock removed: Q2's own lock would be refused, so the
    report steps back to Q1 2026, which is locked."""
    from src.asof import most_recent_reportable_quarter, quarter_staleness_note
    from src.cache import quarter_lock_gaps
    _delete_close(october_1, "VOO", "2026-05-06")
    assert _run(october_1, "DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'") == 1
    assert quarter_lock_gaps("2026Q2") == ["2026-05-06 for VOO"]
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q1 2026"
    assert quarter_staleness_note(INCEPTION).startswith("Reporting Q1 2026, not Q3 2026: ")


def test_a_gap_after_the_quarter_does_not_hold_it_back(october_1):
    """Q3's frame ends September 30. A close missing in a later month is outside it."""
    from src.asof import most_recent_reportable_quarter
    from src.cache import quarter_lock_gaps
    _extend_prices_through(october_1, "2026-10-09")
    _delete_close(october_1, "VOO", "2026-10-06")
    pin_today_later = date(2026, 10, 12)
    assert quarter_lock_gaps("2026Q3") == []
    assert most_recent_reportable_quarter(INCEPTION, pin_today_later)[2] == "Q3 2026"


def test_a_pinned_frontier_pins_the_gap_gate_too(october_1):
    """A caller that passes a frontier is pinning the data, and reads no prices: the
    pure-logic tests of the cap stay off the cache."""
    from src.asof import most_recent_reportable_quarter, quarter_staleness_note
    _delete_close(october_1, "VOO", "2026-08-12")
    assert most_recent_reportable_quarter(INCEPTION, frontier="2026-09-30")[2] == "Q3 2026"
    assert quarter_staleness_note(INCEPTION, frontier="2026-09-30") is None
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q2 2026"


def test_the_gate_reads_a_book_with_no_locks_table_and_does_not_create_it(october_1):
    """The personal book holds no locks table until its first lock. The gate must not
    be what creates it: a page's banner reads this, and a read does not write."""
    from src.cache import quarter_lock_gaps
    _delete_close(october_1, "VOO", "2026-08-12")
    con = sqlite3.connect(october_1)
    with con:
        con.execute("DROP TABLE quarter_snapshots")
    con.close()
    assert quarter_lock_gaps("2026Q3") == ["2026-08-12 for VOO"]
    con = sqlite3.connect(f"file:{Path(october_1).as_posix()}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = "
                           "'quarter_snapshots'").fetchone() == (0,)
    finally:
        con.close()
