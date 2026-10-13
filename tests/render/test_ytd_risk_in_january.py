"""Early in a year the Performance page says why its year-to-date risk figures are missing.

performance.compute_risk_metrics returns nothing for a window of fewer than 20 rows. That
floor is a parameter of the method and is kept (#496). So each January the year-to-date
window is empty for a while, and the page must say so in words: no blank tiles, no error.

Rendered on a copy of the frozen book with every close carried forward through Monday,
January 4, 2027, and today pinned to that day: the year holds four days. The window
selector is set to YTD.

(The sentence names 20 trading days. The page's series has a row for every calendar day,
so the figures come back on the year's 20th day: #502.)
"""
from __future__ import annotations

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
from tests.test_cape_read_dates import _extend_prices_through

JANUARY_4 = date(2027, 1, 4)
SAYS = "Insufficient data for the YTD window: it needs at least 20 trading days."
# Every tile the Risk-Adjusted Metrics section draws for a window that has figures.
RISK_TILES = {"Std Dev (ann.)", "Sharpe", "Sortino", "Max DD", "VaR (95%)", "CVaR (95%)",
              "Track. Err", "Info Ratio", "Beta", "Active Ret (ann.)"}


def _window(at: AppTest):
    return next(r for r in at.radio if r.key == "risk_metrics_window")


@pytest.fixture(scope="module")
def january(tmp_path_factory):
    """(the page as it first renders, its risk tiles then, the page with YTD selected)."""
    import streamlit as st
    with pytest.MonkeyPatch.context() as mp:
        book = point_at_frozen_book(mp, tmp_path_factory.mktemp("january"))
        _extend_prices_through(book, JANUARY_4.isoformat())
        pin_today(mp, JANUARY_4)
        st.cache_data.clear()
        at = AppTest.from_file("pages/2_Performance.py", default_timeout=300)
        at.run()
        first = {"exception": list(at.exception), "window": _window(at).value,
                 "tiles": {m.label for m in at.metric} & RISK_TILES}
        _window(at).set_value("YTD")
        at.run()
        yield first, at
        st.cache_data.clear()
    unpin_leftovers()


def test_the_premise_the_year_is_four_days_old(january):
    """The series the page measures ends in 2027, so its year-to-date window is short,
    and Since Inception, the window the page opens on, has every tile."""
    import src.db as db
    first, _at = january
    with db.get_connection() as conn:
        newest, in_year = conn.execute(
            "SELECT MAX(price_date), SUM(price_date >= '2027-01-01') FROM prices "
            "WHERE ticker = 'SPY'").fetchone()
    assert newest == "2027-01-04" and in_year == 2, "January 1 (a Friday) and January 4"
    assert not first["exception"], first["exception"]
    assert first["window"] == "Since Inception" and first["tiles"] == RISK_TILES


def test_the_page_says_why_the_ytd_figures_are_missing(january):
    _first, at = january
    assert not at.exception, at.exception
    assert _window(at).value == "YTD"
    assert [c.value for c in at.caption if "Insufficient data" in c.value] == [SAYS]


def test_it_shows_no_blank_tile_and_no_error_for_the_window(january):
    _first, at = january
    assert {m.label for m in at.metric} & RISK_TILES == set(), "no tile, so none blank"
    assert [e.value for e in at.error] == []
    assert [w.value for w in at.warning if "YTD" in w.value] == []
