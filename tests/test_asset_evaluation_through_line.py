"""The PDF's Asset Evaluation line states one through-date only when every series the
section reads ends on it (#523).

The section opened "Live, not locked to the report's quarter: returns from 2018-01-01
through <date>", where the date was the last day three joined series shared. The
univariate table runs each asset through its own last close, so on a book whose tickers
end on different days some rows read later closes than the line said (#478 I34: "2 days
short for 4 rows"). The line now gives one date only when every series ends on it, and
otherwise names each series' last date.

On copies of the frozen book and of the committed demo book, offline.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from src import asset_evaluation as ae
from src import reports
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
HEAD = "Live, not locked to the report's quarter: returns from 2018-01-01"
TAIL = " A figure over the whole sample runs through the last day the series it reads share."
FROZEN = (HEAD + ". Its series do not all end on one day: BTC-USD and GLD through 2026-07-16; "
          "AGG, DBC, EEM, EFA, EFV, IEF, IQLT, IWD, IWM, QUAL, SCZ, SPY, TIP, VNQ and VOO "
          "through 2026-07-20." + TAIL)


# ── the line ─────────────────────────────────────────────────────────────────

def test_one_date_when_every_series_ends_on_it():
    assert ae.through_line({"BTC-USD": "2026-09-30", "SPY": "2026-09-30",
                            "AGG": "2026-09-30"}) == HEAD + " through 2026-09-30."


def test_each_series_last_date_when_they_differ():
    assert ae.through_line({"SPY": "2026-07-20", "BTC-USD": "2026-07-16", "VOO": "2026-07-20",
                            "GLD": "2026-07-16", "AGG": "2026-07-20"}) == (
        HEAD + ". Its series do not all end on one day: BTC-USD and GLD through 2026-07-16; "
        "AGG, SPY and VOO through 2026-07-20." + TAIL)
    # One day apart is apart: a weekend close for bitcoin after the funds' Friday.
    assert ae.through_line({"SPY": "2026-10-02", "BTC-USD": "2026-10-04"}) == (
        HEAD + ". Its series do not all end on one day: SPY through 2026-10-02; BTC-USD "
        "through 2026-10-04." + TAIL)


def test_a_series_with_no_close_is_named_as_that_and_never_given_a_date():
    line = ae.through_line({"SPY": "2026-09-30", "AGG": "2026-09-30", "DBC": None})
    assert line == (HEAD + ". Its series do not all end on one day: AGG and SPY through "
                    "2026-09-30; DBC with no close stored." + TAIL)
    assert ae.through_line({}) == HEAD + "."


# ── the series it is read from ───────────────────────────────────────────────

@pytest.fixture
def frozen(tmp_path, monkeypatch):
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    yield path
    unpin_leftovers()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """A copy of the committed demo book, offline, today pinned to the day after its
    last close."""
    import socket
    from datetime import date
    import src.db as db
    import src.macro as macro
    import src.prices as prices

    def _offline(*_a, **_k):
        raise OSError("network blocked")

    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(macro, "fetch_fred_series", _offline)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    prices._reset_trailing_memo()
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch, date(2026, 10, 1))
    yield copy
    unpin_leftovers()


def _last_closes(book, tickers) -> dict:
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return {t: con.execute("SELECT MAX(price_date) FROM prices WHERE ticker = ?",
                               (t,)).fetchone()[0] for t in tickers}
    finally:
        con.close()


def test_the_ends_are_each_tickers_last_stored_close(frozen):
    ends = ae.series_ends()
    assert sorted(ends) == ae.tickers_read()
    assert ends == _last_closes(frozen, ae.tickers_read())
    assert sorted(set(ends.values())) == ["2026-07-16", "2026-07-20"]


def test_a_ticker_with_no_stored_close_has_no_end_and_the_line_says_so(frozen):
    """Never today's date, and never left out: the line names it as having no close."""
    con = sqlite3.connect(frozen)
    with con:
        assert con.execute("DELETE FROM prices WHERE ticker = 'SCZ'").rowcount > 100
    con.close()
    import src.prices as prices
    prices._reset_trailing_memo()
    ends = ae.series_ends()
    assert ends["SCZ"] is None
    assert all(day for ticker, day in ends.items() if ticker != "SCZ")
    line = ae.through_line(ends)
    assert "; SCZ with no close stored." in line and "SCZ through" not in line, line


def test_the_ends_cover_every_ticker_the_section_asks_the_price_layer_for(frozen, monkeypatch):
    asked, series = set(), ae._price_series

    def _recorded(ticker, start, end):
        asked.add(ticker)
        return series(ticker, start, end)

    monkeypatch.setattr(ae, "_price_series", _recorded)
    section = reports._build_asset_eval_section()
    assert section["uni_rows"], "premise: the section builds its figures"
    assert {"BTC-USD", "SPY", "GLD"} <= asked
    assert asked == set(ae.series_ends()), "the line is read over the series the section reads"


def test_on_the_frozen_book_the_line_names_both_days(frozen):
    """What #523 found. The line read "through 2026-07-16"; SPY's, AGG's and VOO's rows
    of the univariate table, and the Real Assets row, read closes through July 20."""
    section = reports._build_asset_eval_section()
    assert section["as_of"] == FROZEN
    from datetime import date
    spy = ae._price_series("SPY", ae.SAMPLE_START, date.today().isoformat())
    assert spy.index.max().date().isoformat() == "2026-07-20", "the row the old line was short for"


def test_on_the_committed_demo_book_every_series_ends_one_day_and_the_line_says_it(demo):
    assert set(ae.series_ends().values()) == {"2026-09-30"}
    assert reports._build_asset_eval_section()["as_of"] == HEAD + " through 2026-09-30."


def test_one_series_a_day_short_changes_the_line(demo):
    """The contrast on the same book: bitcoin's last close removed."""
    con = sqlite3.connect(demo)
    with con:
        assert con.execute("DELETE FROM prices WHERE ticker = 'BTC-USD' AND price_date = "
                           "'2026-09-30'").rowcount == 1
    con.close()
    import src.prices as prices
    prices._reset_trailing_memo()
    line = reports._build_asset_eval_section()["as_of"]
    assert line.startswith(HEAD + ". Its series do not all end on one day: BTC-USD through "
                           "2026-09-29; AGG, ")
    assert line.endswith(" and VOO through 2026-09-30." + TAIL)
