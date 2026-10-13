"""The PDF's Asset Evaluation section names a close the book lacks (#521).

The page has named one since #518. The PDF's section is built from the same series,
live at each render and outside the quarter's lock, with the close before a missing day
carried forward, and it said nothing. It now carries the page's sentence, worded for a
report, under the line that says through when its data runs.

On copies of the frozen book, offline, today pinned to its next day.
"""
from __future__ import annotations

import html as html_lib
import re
import sqlite3

import pandas as pd
import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src import asset_evaluation as ae
from src import price_gaps
from src import reports
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

JULY_8 = ("No close is stored for BTC-USD on July 8, 2026. The figures in this section "
          "that read it carry the close before forward. Each price refresh asks for it "
          "again, and a report generated once it is stored carries no such note.")


def _delete(book, ticker: str, day: str) -> None:
    con = sqlite3.connect(book)
    try:
        with con:
            assert con.execute("DELETE FROM prices WHERE ticker = ? AND price_date = ?",
                               (ticker, day)).rowcount == 1, (ticker, day)
    finally:
        con.close()


@pytest.fixture
def book(tmp_path, monkeypatch):
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    yield path
    unpin_leftovers()


def _section() -> dict:
    section = reports._build_asset_eval_section()
    assert section["disposition"] not in ("failed", "no_data"), section.get("failure_reason")
    assert section["uni_rows"], "premise: the section still builds its figures"
    return section


def _last_common_close() -> str:
    """The last day all three of the section's series hold, read from the book."""
    ends = [ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START).index.max(),
            ae.get_sleeve_returns(ae.SAMPLE_START).index.max(),
            ae.get_candidate_returns("SPY", ae.SAMPLE_START).index.max()]
    return min(pd.Timestamp(e) for e in ends).date().isoformat()


# ── the owner's proof, in the section the PDF prints ──────────────────────────

def test_the_book_missing_july_8_names_it_in_the_section(book):
    _delete(book, "BTC-USD", "2026-07-08")
    assert _section()["gap_note"] == JULY_8


def test_the_filled_book_names_nothing(book):
    assert _section()["gap_note"] is None


def test_the_section_states_the_date_its_data_runs_through_either_way(book):
    """The line the note sits under. Its date is the last day all three of the
    section's series hold, read from the book here, with the gap and without it. On
    the frozen book that is July 16, where BTC-USD ends; the funds run to July 20."""
    through = _last_common_close()
    assert through == "2026-07-16", "premise: the frozen book's BTC-USD ends July 16"
    expected = (f"Live, not locked to the report's quarter: returns from {ae.SAMPLE_START} "
                f"through {through}.")
    assert _section()["as_of"] == expected
    _delete(book, "BTC-USD", "2026-07-08")
    assert _section()["as_of"] == expected


def test_the_note_covers_every_ticker_the_section_asks_the_price_layer_for(
        book, monkeypatch):
    """The note can only name a gap in a ticker it was computed over. Every ticker the
    build read is one of them. The candidate screen is the page's alone, so its ticker
    is neither read nor covered here."""
    asked, covered = set(), set()
    series, gaps_for = ae._price_series, price_gaps.gaps_for

    def _recorded(ticker, start, end):
        asked.add(ticker)
        return series(ticker, start, end)

    def _covering(tickers):
        covered.update(tickers)
        return gaps_for(tickers)

    monkeypatch.setattr(ae, "_price_series", _recorded)
    monkeypatch.setattr(price_gaps, "gaps_for", _covering)
    _section()
    assert {"BTC-USD", "SPY"} <= asked, "premise: the build read the case study"
    assert asked - covered == set(), "the section reads a ticker its note cannot name"
    assert "QQQ" not in asked and "QQQ" not in covered


def test_a_gap_in_the_pages_screen_ticker_is_not_the_sections(book):
    _delete(book, "QQQ", "2026-07-07")
    assert ae.price_gap_note(screened="QQQ") is not None, "premise: the page names it"
    assert _section()["gap_note"] is None


def test_several_gaps_are_each_named_with_their_dates(book):
    _delete(book, "BTC-USD", "2026-07-08")
    _delete(book, "GLD", "2026-07-07")
    _delete(book, "GLD", "2026-07-09")
    assert _section()["gap_note"] == (
        "No close is stored for BTC-USD on July 8, 2026; GLD on July 7, 2026, July 9, 2026. "
        "The figures in this section that read them carry the close before forward. Each "
        "price refresh asks for them again, and a report generated once they are stored "
        "carries no such note.")


def test_the_pages_sentence_is_unchanged(book):
    """The page keeps its own wording: a page's note goes; a report's cannot."""
    _delete(book, "BTC-USD", "2026-07-08")
    assert ae.price_gap_note() == (
        "No close is stored for BTC-USD on July 8, 2026. The figures on this page that "
        "read it carry the close before forward. Each price refresh asks for it again, "
        "and this note goes once it is stored.")
    assert ae.price_gap_note(in_report=True) == JULY_8


def test_a_section_that_shows_no_figures_names_no_gap(book, monkeypatch):
    """The sentence is about figures. The exit that shows none carries no note, though
    the book has the gap."""
    _delete(book, "BTC-USD", "2026-07-08")
    monkeypatch.setattr(ae, "get_candidate_returns",
                        lambda *a, **k: pd.Series(dtype=float))
    section = reports._build_asset_eval_section()
    assert section["disposition"] == "no_data"
    assert section["gap_note"] is None and "as_of" not in section


# ── what the PDF prints ───────────────────────────────────────────────────────

class _Blank(ChainableUndefined):
    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def _printed(section: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(reports.TEMPLATES_DIR)),
                      autoescape=True, undefined=_Blank)
    html = env.get_template("quarterly_report.html").render(asset_eval=section,
                                                            css_content="")
    m = re.search(r"<section>\s*<h2>Asset Evaluation.*?</section>", html, re.S)
    assert m, "the Asset Evaluation section was not found in the template"
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(0)))).strip()


def test_the_pdf_prints_the_note_under_the_line_that_says_through_when(book):
    _delete(book, "BTC-USD", "2026-07-08")
    section = _section()
    text = _printed(section)
    assert f"{section['as_of']} {JULY_8} {section['rf_note']}" in text


def test_the_pdf_prints_no_note_on_the_filled_book(book):
    section = _section()
    text = _printed(section)
    assert "No close is stored" not in text
    assert f"{section['as_of']} {section['rf_note']}" in text
