"""The reportable-quarter cap follows the lock's rule (#458).

#454 made a lock need the close of the quarter's last NYSE session for every ticker it
covers. The cap that picks the reportable quarter (asof.most_recent_reportable_quarter:
the Performance page's "(locked)" tiles, the default report, the banner's
locked-report line) still allowed prices ending up to five days short, and read the
holdings' frontier only. For one to five days after a quarter ended without its last
close stored, the page named the new quarter and showed the lock's refusal. Now it
steps back to the previous quarter until the lock can be taken, as CLAUDE.md's
close-out says. Frozen book, offline, prices carried forward, today pinned.
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


def _drop(book, sql, *args):
    con = sqlite3.connect(book)
    with con:
        n = con.execute(sql, args).rowcount
    con.close()
    return n


@pytest.fixture
def october_1(tmp_path, monkeypatch):
    """The frozen book on October 1, 2026 (after midnight ET), every ticker priced
    through September 29: September 30's close is not stored."""
    import streamlit as st
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    path = point_at_frozen_book(monkeypatch, tmp_path)
    _extend_prices_through(path, "2026-09-29")
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


def test_on_october_1_without_september_30s_close_the_page_stays_on_q2(october_1):
    from src import reports
    from src.asof import (_most_recent_completed_quarter, as_of_report_line,
                          most_recent_reportable_quarter, quarter_staleness_note)
    from src.cache import lock_price_frontier
    assert _most_recent_completed_quarter(OCT_1)[2] == "Q3 2026", "premise: Q3 has ended"
    assert lock_price_frontier() == "2026-09-29", "premise: September 30's close is missing"

    # The default report's quarter, and the note it prints.
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q2 2026"
    note = quarter_staleness_note(INCEPTION)
    assert note == ("Reporting Q2 2026, not Q3 2026: price data ends September 29, 2026, "
                    "which cannot support a quarter that closed September 30, 2026. Q2 2026 "
                    "closed before that date, so the figures in this report are complete.")
    html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                   is_demo=True).decode("utf-8")
    assert "Reporting Q2 2026, not Q3 2026" in html
    # The banner's locked-report line.
    assert as_of_report_line() == "Latest locked quarterly report: Q2 2026 (June 30, 2026)."
    # The (locked) tiles.
    at = _performance()
    labels = [m.label for m in at.metric]
    assert "Q2 2026 return" in labels, labels
    assert not [lb for lb in labels if "Q3 2026" in lb], labels
    shown = " ".join(str(e.value) for e in list(at.error) + list(at.warning) + list(at.markdown))
    assert "Cannot lock" not in shown, "the page named Q3 and showed the lock's refusal"


def test_with_september_30s_close_stored_it_moves_to_q3(october_1):
    """The contrast: the day the last close is stored, the quarter is reportable, and
    the lock's gate would pass (no short coverage)."""
    from src.asof import as_of_report_line, most_recent_reportable_quarter
    from src.cache import lock_price_frontier
    _extend_prices_through(october_1, "2026-09-30")
    assert lock_price_frontier() == "2026-09-30"
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q3 2026"
    assert as_of_report_line() == ("Latest locked quarterly report: Q3 2026 "
                                   "(September 30, 2026).")


def test_a_benchmarks_missing_close_holds_it_back_too(october_1):
    """The lock covers every benchmark constituent, not only the holdings, so a
    benchmark's missing close holds the quarter back. SPY is a benchmark and not a
    holding: the holdings' frontier reaches September 30 without it."""
    from src.asof import most_recent_reportable_quarter
    from src.holdings import committed_price_frontier, get_holdings_on_date, get_portfolio_account_id
    _extend_prices_through(october_1, "2026-09-30")
    assert _drop(october_1, "DELETE FROM prices WHERE ticker = 'SPY' AND "
                            "price_date = '2026-09-30'") == 1
    held = get_holdings_on_date("2026-09-30", account_id=get_portfolio_account_id())
    assert "SPY" not in held.index, "premise: SPY is not a holding"
    assert committed_price_frontier() == "2026-09-30", "premise: the holdings reach Sep 30"
    assert most_recent_reportable_quarter(INCEPTION)[2] == "Q2 2026"
