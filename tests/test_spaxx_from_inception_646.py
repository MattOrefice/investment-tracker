"""SPAXX's BIL growth is measured from the inception of the book being valued (#646).

Decided by the owner on October 18, 2026:
  * attribution's BIL base follows #645's rule: the close of the first session on or
    after inception, or a gap;
  * an unresolved SPAXX is valued at its $1.00 NAV where it is valued now, and its
    marker says exactly that: valued at its $1.00 NAV, without the income BIL would
    model, and why. Not "counted as zero";
  * SPAXX's growth is measured from the book's inception everywhere. The literal
    2025-05-01 goes, and with it the Performance page assertion's failure path.

On copies of the frozen book, offline, on July 21, 2026. Its inception is 2025-05-01, as
demo.db's is, so a book whose inception is later is made by moving its inception trades
to June 2, 2025, and a BIL history that starts later by deleting BIL's earlier closes.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent

DAY = "2026-07-20"                       # the frozen book's last close
LATER = "2025-06-02"                     # a later inception, a Monday


def _sql(path, sql: str, *args) -> int:
    con = sqlite3.connect(path)
    try:
        with con:
            return con.execute(sql, args).rowcount
    finally:
        con.close()


def _cut_bil(path, before: str) -> None:
    assert _sql(path, "DELETE FROM prices WHERE ticker = 'BIL' AND price_date < ?", before) > 0


@pytest.fixture
def book(tmp_path, monkeypatch):
    path = point_at_frozen_book(monkeypatch, tmp_path)
    pin_today(monkeypatch)
    yield path
    unpin_leftovers()


@pytest.fixture
def later_book(book):
    """Inception June 2, 2025, and BIL stored from May 20: after 2025-05-01, on or before
    inception."""
    assert _sql(book, "UPDATE trades SET trade_date = ? WHERE trade_date = '2025-05-01'",
                LATER) > 0
    _cut_bil(book, "2025-05-20")
    return book


# ── growth from the book's inception, everywhere ─────────────────────────────

def test_every_site_measures_spaxx_from_the_books_inception(later_book):
    """The current market value and the sleeve weights measured from 2025-05-01, so here
    they had SPAXX unresolved while the value series, from inception, had it resolved."""
    from src.holdings import (_spaxx_price, current_market_value_with_coverage,
                              get_inception_date, get_portfolio_account_id,
                              portfolio_value_series_with_coverage, sleeve_weights_with_coverage)
    from src.prices import get_prices, total_return_series
    acct = get_portfolio_account_id()
    assert get_inception_date(account_id=acct) == LATER, "premise: the later inception"
    mv = current_market_value_with_coverage(DAY).coverage
    pv = portfolio_value_series_with_coverage(LATER, DAY, account_id=acct).coverage
    sw = sleeve_weights_with_coverage(DAY).coverage
    for cov in (mv, pv, sw):
        assert "SPAXX" in cov.requested and "SPAXX" not in cov.unresolved_tickers()
    assert mv.unresolved_tickers() == pv.unresolved_tickers()
    bil = total_return_series(get_prices("BIL", LATER, DAY))
    price, st = _spaxx_price(DAY)
    assert st.resolved
    assert price == pytest.approx(float(bil.iloc[-1]) / float(bil.loc[date(2025, 6, 2)]))


def test_the_performance_page_renders_where_its_assertion_used_to_fail(later_book):
    """pages/2_Performance.py asserts the market value's and the value series' unresolved
    sets are equal. On this book it stopped on that AssertionError."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_data.clear()
    st.cache_resource.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=300).run()
    st.cache_data.clear()
    assert not at.exception, at.exception


def test_the_committed_inception_reads_as_before(book):
    """demo.db's and the frozen book's inception is 2025-05-01: nothing moves there."""
    from src.holdings import _spaxx_price, get_inception_date, get_portfolio_account_id
    from src.prices import get_prices, total_return_series
    assert get_inception_date(account_id=get_portfolio_account_id()) == "2025-05-01"
    bil = total_return_series(get_prices("BIL", "2025-05-01", DAY))
    price, st = _spaxx_price(DAY)
    assert st.resolved
    assert price == pytest.approx(float(bil.iloc[-1]) / float(bil.loc[date(2025, 5, 1)]))


# ── attribution's BIL base ───────────────────────────────────────────────────

def _bil_gaps(start: str, end: str) -> list:
    from src.attribution import brinson_fachler_period
    from src.holdings import get_portfolio_account_id
    bf = brinson_fachler_period(start, end, account_id=get_portfolio_account_id())
    return [g for g in bf.attrs["price_gaps"] if g[0] == "BIL"]


def test_attributions_bil_base_is_the_first_sessions_close_or_a_gap(book):
    """BIL from Monday May 5, 2025, two sessions after the Thursday inception: the base
    used to be May 5's close, within five days, with no gap recorded."""
    _cut_bil(book, "2025-05-05")
    assert _bil_gaps("2025-07-01", "2025-09-30") == [("BIL", "2025-05-01")]


def test_bil_covering_inception_records_no_gap(book):
    assert _bil_gaps("2025-07-01", "2025-09-30") == []


# ── an unresolved SPAXX is valued at its NAV, and the marker says so ─────────

def _cov(*statuses):
    from src.coverage import coverage_from_statuses
    return coverage_from_statuses(list(statuses), DAY)


def _st(ticker, reason=None):
    from src.coverage import TickerStatus
    if reason:
        return TickerStatus(ticker, False, reason=reason)
    return TickerStatus(ticker, True, served_through=DAY)


NAV = "SPAXX is valued at its $1.00 NAV, without the income BIL would model, because "
NOT_STORED = "BIL's stored prices do not reach back to the book's inception"
FAILED = "the fetch of BIL's prices back to the book's inception failed"


@pytest.mark.parametrize("statuses, marker", [
    pytest.param((_st("SPAXX", "no_cached_rows"), _st("VOO"), _st("VTV")),
                 f"1 of 3 holdings have no committed price (SPAXX): {NAV}{NOT_STORED}. "
                 f"Prices served through {DAY}.", id="SPAXX alone"),
    pytest.param((_st("SPAXX", "fetch_failed"), _st("VOO", "fetch_failed"), _st("VTV")),
                 "2 of 3 holdings have no committed price (SPAXX, VOO): VOO's market value "
                 f"counted as zero, and {NAV}{FAILED}. Prices served through {DAY}.",
                 id="SPAXX and one more"),
    pytest.param((_st("SPAXX", "no_cached_rows"), _st("VOO", "no_cached_rows"),
                  _st("VTV", "no_cached_rows"), _st("SPY")),
                 "3 of 4 holdings have no committed price (SPAXX, VOO, VTV): the market value "
                 f"of VOO and VTV counted as zero, and {NAV}{NOT_STORED}. Prices served "
                 f"through {DAY}.", id="SPAXX and two more"),
    pytest.param((_st("VOO", "no_cached_rows"), _st("SPAXX")),
                 "1 of 2 holdings have no committed price (VOO), so their market value counted "
                 f"as zero. Prices served through {DAY}.", id="control: SPAXX resolved"),
])
def test_the_sleeve_weights_marker_says_what_was_done_with_spaxx(statuses, marker):
    from src.coverage import unresolved_marker
    assert unresolved_marker(_cov(*statuses)) == marker


def test_a_page_that_withholds_says_it_in_its_own_sentence():
    """The Performance page passes no consequence: it withholds, and values nothing."""
    from src.coverage import unresolved_marker
    assert unresolved_marker(_cov(_st("SPAXX", "fetch_failed"), _st("VOO")),
                             consequence=None) == (
        f"1 of 2 holdings have no committed price (SPAXX). Prices served through {DAY}.")


@pytest.mark.parametrize("gap_fetch, why", [(False, NOT_STORED), (True, FAILED)])
def test_an_unresolved_spaxx_is_valued_at_its_nav_where_it_is_valued_now(book, monkeypatch,
                                                                        gap_fetch, why):
    """BIL from May 20, 2025, after the May 1 inception: SPAXX is unresolved, and the
    sleeve weights and the current market value count its 20 shares at $1.00."""
    import src.prices as prices
    from src.coverage import unresolved_marker
    from src.holdings import current_market_value_with_coverage, sleeve_weights_with_coverage
    monkeypatch.setattr(prices, "_GAP_FETCH", gap_fetch)
    _cut_bil(book, "2025-05-20")
    sw = sleeve_weights_with_coverage(DAY)
    assert "SPAXX" in sw.coverage.unresolved_tickers()
    assert sw.frame.attrs["cash_mv"] == pytest.approx(20.0)
    marker = unresolved_marker(sw.coverage)
    assert f"{NAV}{why}." in marker and "counted as zero" not in marker
    mv = current_market_value_with_coverage(DAY)
    assert "SPAXX" in mv.coverage.unresolved_tickers()
