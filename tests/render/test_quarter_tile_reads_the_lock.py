"""The Performance page's "(locked)" quarter tiles read the lock (#383).

They computed the quarter from the page's live series under a "locked" label, so a
revision of a close inside the quarter after it locked moved them and not the PDF.
They also disagreed with the PDF on the demo (blended 11.83% against 11.71%); that
gap was construction, not the lock, and #392's one blended series closed it. The
tiles read the lock anyway: a tile labelled locked reads the lock even when the
numbers agree. They print the PDF executive summary's figures, built inside the
same lock. Frozen book, offline, today pinned.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent.parent
Q2 = ("Q2 2026", "2026-03-31", "2026-06-30")


@pytest.fixture
def book(tmp_path, monkeypatch):
    import streamlit as st
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    st.cache_data.clear()
    yield path
    st.cache_data.clear()
    unpin_leftovers()


def _tiles():
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    # The benchmark returns moved from the tiles' deltas to captions (audit item 7), so
    # the tiles are read with the captions that carry them.
    tiles = {m.label: (m.value, m.delta) for m in at.metric if "Q2 2026" in m.label}
    # The since-inception row's captions (" SI") are live and move with a revision.
    caps = tuple(str(c.value) for c in at.caption
                 if str(c.value).startswith(("S&P 500: ", "Blended: "))
                 and not str(c.value).endswith(" SI"))
    return tiles, caps


def _pdf():
    """The PDF's figures, as generate_quarterly_report_bytes builds them."""
    from src import reports
    from src.cache import get_quarter_snapshot, snapshot_price_context
    snap = get_quarter_snapshot("2026Q2")[0]
    assert snap is not None, "the page took the lock the report reads"
    with snapshot_price_context(snap):
        return reports._build_executive_summary(Q2[1], Q2[2])


def _assert_agree(shown, pdf):
    tiles, caps = shown
    assert {k: v for k, (v, _d) in tiles.items()} == {
        "Q2 2026 return": pdf["portfolio_return_pct"],
        "vs. S&P 500 — Q2 2026": pdf["alpha_sp_str"],
        "vs. Custom Blended — Q2 2026": pdf["alpha_bl_str"],
    }
    assert not [d for _v, d in tiles.values() if d], "a quarter tile carries a delta again"
    assert f"S&P 500: {pdf['sp500_return_pct']}" in caps, caps
    assert f"Blended: {pdf['blended_return_pct']}" in caps, caps


def test_the_page_and_the_pdf_agree_on_every_q2_figure(book):
    _assert_agree(_tiles(), _pdf())


def test_a_revised_q2_close_moves_neither(book):
    """A provider revising a close inside the quarter after it locked: the live
    computation moves, the locked tiles and the PDF do not."""
    from src import reports
    before = _tiles()
    unlocked_before = reports._build_executive_summary(Q2[1], Q2[2])
    c = sqlite3.connect(book)
    n = c.execute("UPDATE prices SET close = close * 1.05 WHERE ticker = 'SPY' "
                  "AND price_date = '2026-06-30'").rowcount
    c.commit()
    c.close()
    assert n == 1, "fixture premise: the book prices SPY on the quarter's last day"
    after = _tiles()
    assert after == before, "the locked tiles moved with a revised Q2 close"
    _assert_agree(after, _pdf())
    unlocked_after = reports._build_executive_summary(Q2[1], Q2[2])
    assert unlocked_after["sp500_return_pct"] != unlocked_before["sp500_return_pct"], (
        "control: the same revision moves an unlocked computation, so the check is live")
