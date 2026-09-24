"""#368 item 1: a quarter locks at its own end, and the lock is actually read.

Two defects, one PR, because the rule cannot hold without the second fix:

  * THE LOCK WAS INERT. snapshot_price_context swapped the src.prices.get_prices
    attribute, and every report section imports get_prices by name, so no section
    saw the lock. Measured: corrupting VOO in the DB AFTER capture moved 86 "locked"
    figures. Every test of the lock called the module attribute, the one path it did
    reach. The lock is now a ContextVar consulted inside get_prices.
  * THE LOCK MOVED WITH LATER DIVIDENDS. dividend_adjusted anchors on every stored
    dividend, so an ex-date after the quarter rescaled the quarter's levels. The
    rule (option B): prices and dividends through the quarter's last day only.

All on copies of the frozen test book (demo-derived, offline), so these run in CI.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

Q2 = ("2026-03-31", "2026-06-30")


@pytest.fixture(autouse=True)
def _no_chart_images(monkeypatch):
    """Chart PNGs are dropped before any comparison here, and rendering them is
    nearly all of these tests' runtime. The figures under test do not depend on it."""
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A frozen-book copy, offline, with today pinned to the book's next day."""
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


def _add_post_frontier_dividends(path):
    """Realistic ex-dates AFTER the book's 2026-07-20 frontier, with the flat closes
    a fetch would have brought, as a daily fetch will once it lands."""
    c = sqlite3.connect(path)
    for t, ex, amt in [("VOO", "2026-09-22", 1.80), ("VTV", "2026-09-22", 1.05),
                       ("VEA", "2026-09-22", 0.45), ("IEMG", "2026-09-15", 0.60),
                       ("VGIT", "2026-08-01", 0.17), ("SCHP", "2026-08-01", 0.08)]:
        last = c.execute("SELECT close FROM prices WHERE ticker = ? AND price_date = "
                         "'2026-07-20'", (t,)).fetchone()[0]
        d = date(2026, 7, 21)
        while d <= date(2026, 9, 23):
            if d.weekday() < 5:
                c.execute("INSERT OR REPLACE INTO prices VALUES (?, ?, ?, NULL)",
                          (t, d.isoformat(), last))
            d += timedelta(days=1)
        c.execute("INSERT OR REPLACE INTO dividends VALUES (?, ?, ?)", (t, ex, amt))
    c.commit()
    c.close()


def _locked_sections(snap):
    """The quarter-locked report sections, built as generate_quarterly_report_bytes
    builds them, with chart images dropped (compare figures, not PNG bytes)."""
    from src import reports
    from src.cache import snapshot_price_context

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if not k.endswith("b64")}
        if isinstance(o, (list, tuple)):
            return [strip(v) for v in o]
        return o

    with snapshot_price_context(snap):
        return repr(strip({"perf": reports._build_performance_section(*Q2),
                           "attr": reports._build_attribution_section(*Q2),
                           "hold": reports._build_holdings_section(Q2[1])}))


# ── the lock reaches every caller ─────────────────────────────────────────────

def test_the_lock_reaches_a_caller_that_imported_get_prices_by_name(book):
    """The defect's exact shape: attribution holds its own `get_prices` binding."""
    import src.attribution as attribution
    from src.cache import snapshot_price_context
    snap = pd.DataFrame({"VOO": [100.0, 102.0]},
                        index=[date(2026, 6, 29), date(2026, 6, 30)])
    with snapshot_price_context(snap):
        got = attribution.get_prices("VOO", "2026-06-29", "2026-06-30")
    assert list(got["adj_close"]) == [100.0, 102.0]
    live = attribution.get_prices("VOO", "2026-06-29", "2026-06-30")
    assert list(live["adj_close"]) != [100.0, 102.0], "the lock outlived its block"


def test_locked_sections_ignore_a_change_to_the_live_cache(book):
    """Capture Q2, then corrupt VOO in the DB. A lock that is read cannot see it;
    before this PR, 86 formatted figures moved."""
    from src.cache import capture_quarter_snapshot
    snap, _ = capture_quarter_snapshot("2026Q2")
    before = _locked_sections(snap)
    c = sqlite3.connect(book)
    assert c.execute("UPDATE prices SET close = close * 1.10 WHERE ticker = 'VOO'").rowcount
    c.commit()
    c.close()
    assert _locked_sections(snap) == before


def test_the_lock_never_replaces_the_function(book):
    """Callers keep one function object, which is what lets the lock reach them."""
    import src.prices as prices
    from src.cache import snapshot_price_context
    original = prices.get_prices
    with snapshot_price_context(pd.DataFrame({"VOO": [1.0]}, index=[date(2026, 1, 2)])):
        assert prices.get_prices is original
        assert prices._PRICE_LOCK.get() is not None
    assert prices._PRICE_LOCK.get() is None


# ── the rule: dividends through the quarter's last day ────────────────────────

def test_q2_lock_is_identical_with_and_without_later_dividends(tmp_path, monkeypatch):
    """#368's acceptance. The same Q2, captured on a copy that has only the committed
    data and on one that has also taken ex-dates from July to September. Before the
    rule, those dividends moved 31+ formatted Q2 figures. Killed by admitting
    post-quarter dividends (drop `through=` in capture_quarter_snapshot)."""
    from src.cache import capture_quarter_snapshot
    pin_today(monkeypatch)
    try:
        sides = {}
        for name, add in (("plain", False), ("later_dividends", True)):
            with pytest.MonkeyPatch.context() as mp:
                (tmp_path / name).mkdir()
                path = point_at_frozen_book(mp, tmp_path / name)
                if add:
                    _add_post_frontier_dividends(path)
                snap, _ = capture_quarter_snapshot("2026Q2")
                sides[name] = (snap.adj_close, _locked_sections(snap))
        pd.testing.assert_frame_equal(sides["plain"][0], sides["later_dividends"][0])
        assert sides["plain"][1] == sides["later_dividends"][1]
    finally:
        unpin_leftovers()


def test_the_lock_drops_the_books_own_post_quarter_ex_dates(book):
    """The frozen book, like demo.db, already carries seven 2026-07-01 ex-dates.
    Under the rule they do not enter Q2: VGIT's June 30 level in the lock equals its
    close (no ex-date between June 30 and the quarter's end), where the unlocked
    read scales it by the July 1 dividend."""
    from src.cache import capture_quarter_snapshot
    from src.prices import get_prices
    snap, _ = capture_quarter_snapshot("2026Q2")
    locked = float(snap.adj_close["VGIT"].loc[date(2026, 6, 30)])
    close = float(snap.close["VGIT"].loc[date(2026, 6, 30)])
    unlocked = float(get_prices("VGIT", "2026-06-30", "2026-06-30")["adj_close"].iloc[0])
    assert locked == close
    assert unlocked < close, "fixture premise: a post-quarter ex-date scales the live read"


def test_dividend_adjusted_through_admits_only_ex_dates_on_or_before_it(book):
    from src.prices import dividend_adjusted, get_prices
    frame = get_prices("VGIT", "2026-06-01", "2026-06-30")[["close"]]
    all_divs = dividend_adjusted("VGIT", frame)
    to_q_end = dividend_adjusted("VGIT", frame, through="2026-06-30")
    to_jul_1 = dividend_adjusted("VGIT", frame, through="2026-07-01")
    assert float(to_q_end.iloc[-1]) == float(frame["close"].iloc[-1])
    assert float(to_jul_1.iloc[-1]) < float(frame["close"].iloc[-1])
    pd.testing.assert_series_equal(all_divs, to_jul_1)   # 07-01 is the book's last ex-date


# ── coverage, or no lock ──────────────────────────────────────────────────────

def test_a_quarter_the_data_does_not_reach_is_refused_naming_the_dates(tmp_path, monkeypatch):
    """After September 30, a container whose fetch failed holds prices ending July
    20. Locking Q3 there would freeze it on July 20 prices; it refuses instead, and
    nothing is persisted."""
    from src.cache import LockCoverageError, capture_quarter_snapshot, get_quarter_snapshot
    pin_today(monkeypatch, date(2026, 10, 1))
    try:
        point_at_frozen_book(monkeypatch, tmp_path)
        with pytest.raises(LockCoverageError) as exc:
            capture_quarter_snapshot("2026Q3")
        msg = str(exc.value)
        assert "VOO: prices end 2026-07-20, missing 2026-07-21 to 2026-09-30" in msg
        assert "Nothing was locked" in msg
        assert get_quarter_snapshot("2026Q3") == (None, None)
    finally:
        unpin_leftovers()


@pytest.mark.parametrize("days_short, refused", [(5, False), (6, True)])
def test_coverage_allows_the_frontier_caps_weekend_tolerance_and_no_more(days_short, refused):
    """Two-sided: a close within QUARTER_END_COVERAGE_DAYS locks (a quarter ending on
    a weekend or holiday), one day beyond refuses."""
    from src.asof import QUARTER_END_COVERAGE_DAYS
    from src.cache import _short_coverage
    from src.coverage import TickerStatus
    assert QUARTER_END_COVERAGE_DAYS == 5
    end = date(2026, 9, 30)
    served = (end - timedelta(days=days_short)).isoformat()
    statuses = [TickerStatus("VOO", True, served_through=end.isoformat()),
                TickerStatus("VTV", True, served_through=served)]
    lines = _short_coverage(statuses, end)
    assert bool(lines) is refused
    if refused:
        assert lines == [f"VTV: prices end {served}, missing "
                         f"{(end - timedelta(days=days_short - 1)).isoformat()} to 2026-09-30"]


def test_a_persisted_lock_is_served_as_written(book):
    """The rule governs NEW locks. Once persisted, a lock is read back unchanged,
    whatever the cache takes afterwards."""
    from src.cache import capture_quarter_snapshot, get_quarter_snapshot
    snap, _ = capture_quarter_snapshot("2026Q2")
    c = sqlite3.connect(book)
    c.execute("INSERT OR REPLACE INTO dividends VALUES ('VOO', '2026-05-15', 9.99)")
    c.commit()
    c.close()
    back, _ = get_quarter_snapshot("2026Q2")
    pd.testing.assert_frame_equal(back.adj_close, snap.adj_close, check_freq=False)


# ── the correction, pinned, and the note that discloses it ────────────────────

def test_q2_locked_headline_figures_golden(book):
    """Q2 2026's locked headline figures on the frozen book, under the rule.

    THE CORRECTION THIS PINS: alpha vs S&P read -289 bps before the rule and reads
    -290 bps under it; the return (12.23%) and alpha vs the blend (+52 bps) did not
    move at this precision. The whole Q2 report moves in 31 formatted figures, by at
    most 2 bp on a relative return (demo book, #368). A change here is a change to
    the lock rule or to what it reads: decide it, then update this."""
    from src import reports
    from src.cache import capture_quarter_snapshot, snapshot_price_context
    snap, _ = capture_quarter_snapshot("2026Q2")
    with snapshot_price_context(snap):
        ex = reports._build_executive_summary(*Q2)
    assert (ex["portfolio_return_pct"], ex["alpha_sp_str"], ex["alpha_bl_str"]) == (
        "12.23%", "-290 bps", "+52 bps")


def test_a_lock_records_its_rule_and_the_record_survives_the_blob(book):
    from src.cache import QUARTER_END_RULE, capture_quarter_snapshot, get_quarter_snapshot
    snap, _ = capture_quarter_snapshot("2026Q2")
    back, _ = get_quarter_snapshot("2026Q2")
    assert snap.rule == back.rule == QUARTER_END_RULE


def test_the_restatement_note_is_for_restated_quarters_only():
    """Every quarter that closed before the rule took effect was reported under the
    old reads, so each carries the note (five demo quarters moved, not only Q2). A
    quarter closing after it, and a lock persisted without the rule, carry none."""
    from src.cache import QUARTER_END_RULE, SnapshotFrames, restatement_note
    ruled = SnapshotFrames(adj_close=pd.DataFrame(), rule=QUARTER_END_RULE)
    unruled = SnapshotFrames(adj_close=pd.DataFrame())
    note = restatement_note("2026Q2", ruled)
    assert note.startswith("Restated September 24, 2026: this quarter now locks at its "
                           "close on June 30, 2026, using only prices and dividends")
    assert restatement_note("2025Q3", ruled) is not None
    assert restatement_note("2026Q3", ruled) is None, "closed after the rule: never restated"
    assert restatement_note("2026Q2", unruled) is None, "a persisted lock stays as written"
    assert restatement_note(None, ruled) is None, "a custom range is not a locked quarter"


def test_the_generated_q2_report_reads_the_lock_and_says_it_was_restated(book, monkeypatch):
    """End to end through generate_quarterly_report_bytes, the entry point the page
    calls: the cover carries the note, and the methodology describes the rule."""
    from src import reports
    html = {}
    monkeypatch.setattr(reports, "_render_pdf", lambda h: html.setdefault("h", h).encode())
    reports.generate_quarterly_report_bytes(*Q2, is_demo=True)
    assert "Restated September 24, 2026: this quarter now locks at its close on June 30" in html["h"]
    flat = " ".join(html["h"].split())       # the template wraps the methodology text
    assert "using only prices and dividends through the quarter's last day" in flat
