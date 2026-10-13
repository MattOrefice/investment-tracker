"""Asset Evaluation names a close the book lacks in a ticker it reads (#501).

Every series on the page is built from a ticker's stored closes with the close before a
missing day carried forward, so a gap changes its figures and nothing on the page said
so. A ticker a quarter lock covers is named elsewhere: the Performance page steps back
and prints the close. No lock covers BTC-USD, the ticker this page is about.

The owner's proof, on copies of the frozen book, offline, today pinned to its next day:
the book missing BTC-USD's July 8 close shows the note, with the date; the filled book
shows none.
"""
from __future__ import annotations

import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from src import asset_evaluation as ae
from src import price_gaps
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

JULY_8 = ("No close is stored for BTC-USD on July 8, 2026. The figures on this page that "
          "read it carry the close before forward. Each price refresh asks for it again, "
          "and this note goes once it is stored.")


def _delete(book, ticker: str, day: str) -> None:
    con = sqlite3.connect(book)
    try:
        with con:
            assert con.execute("DELETE FROM prices WHERE ticker = ? AND price_date = ?",
                               (ticker, day)).rowcount == 1, (ticker, day)
    finally:
        con.close()


def _render(tmp_path_factory, name: str, missing=()):
    """(what the page showed, every ticker it asked the price layer for, every ticker
    its gap note was computed over)."""
    import streamlit as st
    asked, covered = set(), set()
    with pytest.MonkeyPatch.context() as mp:
        book = point_at_frozen_book(mp, tmp_path_factory.mktemp(name))
        for ticker, day in missing:
            _delete(book, ticker, day)
        pin_today(mp)
        series = ae._price_series

        def _recorded(ticker, start, end):
            asked.add(ticker)
            return series(ticker, start, end)

        mp.setattr(ae, "_price_series", _recorded)
        gaps_for = price_gaps.gaps_for

        def _covering(tickers):
            covered.update(tickers)
            return gaps_for(tickers)

        mp.setattr(price_gaps, "gaps_for", _covering)
        st.cache_data.clear()
        at = AppTest.from_file("pages/5_Asset_Evaluation.py", default_timeout=300)
        at.run()
        shown = {"exception": list(at.exception), "info": [str(i.value) for i in at.info],
                 "metrics": len(at.metric)}
        st.cache_data.clear()
    unpin_leftovers()
    return shown, asked, covered


@pytest.fixture(scope="module")
def filled(tmp_path_factory):
    return _render(tmp_path_factory, "ae-filled")


@pytest.fixture(scope="module")
def missing_july_8(tmp_path_factory):
    return _render(tmp_path_factory, "ae-gap", missing=[("BTC-USD", "2026-07-08")])


def test_the_book_missing_july_8_shows_the_note_with_its_date(missing_july_8):
    shown, _asked, _covered = missing_july_8
    assert not shown["exception"], shown["exception"]
    assert [i for i in shown["info"] if "No close is stored" in i] == [JULY_8]
    assert shown["metrics"] > 0, "premise: the page still renders its figures"


def test_the_filled_book_shows_none(filled):
    shown, _asked, _covered = filled
    assert not shown["exception"], shown["exception"]
    assert [i for i in shown["info"] if "No close is stored" in i] == []
    assert shown["metrics"] > 0


def test_the_note_covers_every_ticker_the_page_asks_the_price_layer_for(filled):
    """The note can only name a gap in a ticker it was computed over. Every ticker the
    render read is one of them: the candidate, and QQQ, which sits in the candidate
    screen's box when the page loads and was missing from the first list written."""
    _shown, asked, covered = filled
    assert {"BTC-USD", "QQQ"} <= asked, "premise: the render read the case study and the screen"
    assert asked - covered == set(), "the page reads a ticker its note cannot name"


# ── the note itself ───────────────────────────────────────────────────────────

@pytest.fixture
def book(tmp_path, monkeypatch):
    return point_at_frozen_book(monkeypatch, tmp_path)


def test_one_gap_is_named_in_the_singular(book):
    assert ae.price_gap_note() is None
    _delete(book, "BTC-USD", "2026-07-08")
    assert price_gaps.gaps_for(ae.tickers_read()) == [("BTC-USD", "2026-07-08")]
    assert ae.price_gap_note() == JULY_8


def test_a_daily_series_is_held_to_every_calendar_day(book):
    """July 11, 2026 is a Saturday. BTC-USD trades it, so its close there can be missing."""
    _delete(book, "BTC-USD", "2026-07-11")
    assert ae.price_gap_note().startswith("No close is stored for BTC-USD on July 11, 2026.")


def test_several_gaps_are_each_named_with_their_dates(book):
    _delete(book, "BTC-USD", "2026-07-08")
    _delete(book, "GLD", "2026-07-07")
    _delete(book, "GLD", "2026-07-09")
    assert ae.price_gap_note() == (
        "No close is stored for BTC-USD on July 8, 2026; GLD on July 7, 2026, July 9, 2026. "
        "The figures on this page that read them carry the close before forward. Each "
        "price refresh asks for them again, and this note goes once they are stored.")


def test_the_screens_ticker_is_named_when_it_is_the_one_with_the_gap(book):
    _delete(book, "QQQ", "2026-07-07")
    assert ae.price_gap_note() is None, "premise: QQQ is not in the case study"
    assert ae.price_gap_note(screened="QQQ").startswith(
        "No close is stored for QQQ on July 7, 2026.")
    assert ae.price_gap_note(screened="not a ticker!") is None


def test_a_gap_in_a_ticker_the_page_does_not_read_is_not_named(book):
    """XLK is in the book and not on the page. Its gap is the gap check's to name."""
    assert "XLK" not in ae.tickers_read()
    _delete(book, "XLK", "2026-06-02")
    assert ("XLK", "2026-06-02", price_gaps.MISSING) in price_gaps.book_gaps()
    assert price_gaps.gaps_for(ae.tickers_read()) == []
    assert ae.price_gap_note() is None


def test_a_book_with_no_reference_names_nothing(book):
    con = sqlite3.connect(book)
    with con:
        con.execute("DELETE FROM prices WHERE ticker = 'SPY'")
    con.close()
    _delete(book, "GLD", "2026-07-07")
    assert price_gaps.gaps_for(["GLD", "BTC-USD"]) == []


def test_the_query_names_only_missing_closes_of_the_tickers_asked(book):
    """Same rule as the gap check, on fewer rows: an off-session close is the check's
    other finding and no close the page lacks."""
    con = sqlite3.connect(book)
    with con:
        con.execute("INSERT INTO prices (ticker, price_date, close) VALUES ('GLD', '2026-07-11', 1.0)")
    con.close()
    _delete(book, "GLD", "2026-07-07")
    _delete(book, "VOO", "2026-07-07")
    assert ("GLD", "2026-07-11", price_gaps.OFF_SESSION) in price_gaps.book_gaps()
    assert price_gaps.gaps_for(["GLD"]) == [("GLD", "2026-07-07")]
    assert price_gaps.gaps_for(["GLD", "VOO"]) == [("GLD", "2026-07-07"), ("VOO", "2026-07-07")]


def test_the_references_own_gap_is_returned_only_when_the_reference_is_asked_for(book):
    """SPY is read to find the sessions, whatever is asked. A session SPY itself lacks is
    its own finding, and belongs to a caller only if SPY is one of its tickers."""
    _delete(book, "SPY", "2026-07-07")
    assert ("SPY", "2026-07-07", price_gaps.MISSING) in price_gaps.book_gaps()
    assert price_gaps.gaps_for(["GLD"]) == []
    assert price_gaps.gaps_for(["GLD", "SPY"]) == [("SPY", "2026-07-07")]
