"""A benchmark window starts from the last close on or before its first day (#391).

A held benchmark basket, and the sleeve benchmark returns Brinson-Fachler reads, priced
a window's base at the first close ON OR AFTER its first day. A window starting on a
weekend or market holiday therefore skipped its first trading day: the page's YTD
window (January 1) missed January 2, 80 of the 111.5 bps between its two benchmarks.
The portfolio side always used the prior close. Now the benchmark side does too,
found in the data, so holidays, weekends and gaps resolve the same way. Only windows
whose first day has no close move; the locked reports it moves are restated.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
END = "2026-07-20"


@pytest.fixture
def book(tmp_path, monkeypatch):
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


def _close(ticker, day):
    from src.prices import get_prices, total_return_series
    p = get_prices(ticker, day, day)
    assert not p.empty, f"premise: {ticker} has a close on {day}"
    return float(total_return_series(p).iloc[0])


def _ret(series):
    return float(series.iloc[-1] / series.iloc[0] - 1)


def test_a_window_starting_on_january_1_starts_from_december_31(book):
    """The decision's mutation target: the skip restored is killed here."""
    from src import benchmarks as bm
    con = sqlite3.connect(book)
    no_close = con.execute("SELECT COUNT(*) FROM prices WHERE ticker = 'SPY' "
                           "AND price_date = '2026-01-01'").fetchone()[0] == 0
    con.close()
    assert no_close, "premise: no close on January 1"
    s, gaps = bm._component_series("SPY", "2026-01-01", END)
    assert not gaps and float(s.iloc[0]) == _close("SPY", "2025-12-31")
    # The held basket and the BF sleeve returns read the same base.
    assert _ret(bm._fresh_basket_series("2026-01-01", END)) == pytest.approx(
        _ret(bm._fresh_basket_series("2025-12-31", END)), abs=1e-12)
    jan1 = bm.get_sleeve_benchmark_returns("2026-01-01", END).iloc[-1]
    dec31 = bm.get_sleeve_benchmark_returns("2025-12-31", END).iloc[-1]
    pd.testing.assert_series_equal(jan1, dec31, check_names=False, atol=1e-12, rtol=0)


def test_brinson_fachlers_benchmark_is_the_basket_from_the_prior_close(book):
    from src import benchmarks as bm
    from src.attribution import brinson_fachler_period
    bf = brinson_fachler_period("2026-01-01", END, account_id=1)
    assert float((bf["w_b"] * bf["r_b"]).sum()) == pytest.approx(
        _ret(bm._fresh_basket_series("2025-12-31", END)), abs=1e-9)


@pytest.mark.parametrize("start", ["2025-12-31", "2026-01-02", "2026-03-31", "2026-06-30"])
def test_a_window_starting_on_a_trading_day_does_not_move(book, start):
    """The old rule, first close on or after day 1, and the new one agree whenever day 1
    has a close: only windows whose first day has none move."""
    from src import benchmarks as bm
    s, _ = bm._component_series("SPY", start, END)
    assert float(s.iloc[0]) == _close("SPY", start)


def test_a_weekend_start_uses_the_fridays_close(book):
    from src import benchmarks as bm
    s, _ = bm._component_series("SPY", "2026-05-31", END)        # a Sunday
    assert float(s.iloc[0]) == _close("SPY", "2026-05-29")


def test_a_component_whose_history_begins_inside_the_window_keeps_its_first_close(book):
    """Nothing earlier to base on, so the forward tolerance stays, with no gap flagged."""
    from src import benchmarks as bm
    con = sqlite3.connect(book)
    with con:
        rows = con.execute("SELECT price_date, close FROM prices WHERE ticker = 'SPY' "
                           "AND price_date >= '2026-01-05' AND price_date <= ?", (END,)).fetchall()
        con.executemany("INSERT INTO prices (ticker, price_date, close, adj_close) "
                        "VALUES ('NEWX', ?, ?, NULL)", rows)
    con.close()
    s, gaps = bm._component_series("NEWX", "2026-01-02", END)
    assert gaps == [] and float(s.iloc[0]) == _close("NEWX", "2026-01-05")


# ── the locked reports it moves are restated ───────────────────────────────────

@pytest.fixture
def demo(tmp_path, monkeypatch):
    import socket
    import src.db as db
    import src.prices as prices
    from src import reports
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)

    def _offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch)
    yield copy
    unpin_leftovers()


QUARTERS = {"2025Q2": "2025-06-30", "2025Q3": "2025-09-30", "2025Q4": "2025-12-31",
            "2026Q1": "2026-03-31", "2026Q2": "2026-06-30"}


def test_the_legacy_locks_it_moves_say_so_and_the_other_does_not(demo):
    from src.cache import get_quarter_snapshot
    from src.reports import window_base_note
    notes = {q: window_base_note(get_quarter_snapshot(q)[0], "2025-05-01", end)
             for q, end in QUARTERS.items()}
    assert notes["2025Q4"] is None, "every Q4 2025 window starts on a trading day"
    assert all(notes[q] for q in ("2025Q2", "2025Q3", "2026Q1", "2026Q2"))
    assert notes["2026Q2"] == (
        "Restated September 26, 2026 to correct an error. The 1-month Custom Blended "
        "period began on May 31, 2026, a day with no close; its return was measured from "
        "the next close, June 1, 2026, and left out that day's move. It is now measured "
        "from the last close before the period, May 29, 2026.")
    assert "September 2, 2025" in notes["2025Q3"], "Labor Day: the next close, from the data"


def test_the_q2_2026_report_states_the_correction(demo):
    from markupsafe import escape
    from src import reports
    from src.cache import get_quarter_snapshot
    note = reports.window_base_note(get_quarter_snapshot("2026Q2")[0], "2025-05-01", "2026-06-30")
    html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                   is_demo=True).decode("utf-8")
    assert str(escape(note)) in html


def test_a_lock_under_the_quarterly_rule_is_never_restated(book):
    from src.cache import capture_quarter_snapshot, get_quarter_snapshot
    from src.reports import window_base_note
    capture_quarter_snapshot("2026Q2")
    assert window_base_note(get_quarter_snapshot("2026Q2")[0], "2025-05-01", "2026-06-30") is None
