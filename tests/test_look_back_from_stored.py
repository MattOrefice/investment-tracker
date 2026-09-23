"""#302 (product half) — the price look-back runs from the newest STORED price, not
from the calendar, so offline use does not degrade just because the calendar moved.

From the calendar alone, a cache more than a week behind left every look-back window
empty whenever the gap could not be fetched: every holding unresolved, and the page
rendering "No committed price data" for a book that HAS prices, just older ones.
Network blocked throughout: this is the offline case.
"""
from datetime import date, timedelta

import pytest

import src.db as db
import src.prices as prices


@pytest.fixture
def offline(monkeypatch):
    def boom(*a, **k):
        raise OSError("network blocked")
    monkeypatch.setattr(prices._SESSION, "get", boom)
    monkeypatch.setattr("socket.getaddrinfo", boom)
    prices._reset_trailing_memo()


def _newest_stored() -> str:
    # Through get_connection, the SAME connection path the code under test uses: the
    # suite redirects write-mode opens of data/*.db to a per-session copy, which an
    # earlier test's fetch can advance (in CI, with the network up), so a direct
    # read-only open of the tracked file can see an older book than the code does.
    with db.get_connection() as c:
        return c.execute("SELECT MAX(price_date) FROM prices").fetchone()[0]


def test_look_back_start_anchors_on_the_newest_stored_price(use_demo_db):
    from src.holdings import look_back_start
    newest = _newest_stored()
    far = (date.fromisoformat(newest) + timedelta(days=60)).isoformat()
    with db.get_connection() as c:
        ticker, t_newest = c.execute(
            "SELECT ticker, MAX(price_date) FROM prices GROUP BY ticker LIMIT 1").fetchone()
    far_t = (date.fromisoformat(t_newest) + timedelta(days=60)).isoformat()
    assert look_back_start(ticker, far_t) == (
        date.fromisoformat(t_newest) - timedelta(days=7)).isoformat()
    # A historical date inside the cache is unchanged: 7 days before the date itself.
    assert look_back_start(ticker, t_newest) == (
        date.fromisoformat(t_newest) - timedelta(days=7)).isoformat()
    assert far  # (newest across the book, for the next test)


def test_a_stale_cache_offline_still_prices_the_book(use_demo_db, offline):
    """Two months past the newest stored price, offline: holdings resolve at their
    stored prices, and the coverage record says how old what was served is."""
    from src.holdings import sleeve_weights_with_coverage
    newest = _newest_stored()
    far = (date.fromisoformat(newest) + timedelta(days=60)).isoformat()
    frame, cov = sleeve_weights_with_coverage(far)
    assert cov.resolved, f"nothing resolved offline on a stale cache: {cov.unresolved}"
    assert not frame.empty
    assert cov.frontier_served is not None and cov.frontier_served <= newest, (
        "the served frontier must be the stored price's date, so the banner shows the lag")


def test_a_stale_cache_offline_still_values_positioning(use_demo_db, offline):
    """The positioning page's market values: the same look-back, the same stale case."""
    from src.positioning import _portfolio_market_values
    far = (date.fromisoformat(_newest_stored()) + timedelta(days=60)).isoformat()
    mv, total = _portfolio_market_values(far)
    priced = {t: v for t, v in mv.items() if t != "SPAXX"}
    assert priced and all(v > 0 for v in priced.values()), (
        f"positions valued at zero offline on a stale cache: {mv}")


def test_a_stale_cache_offline_still_prices_the_lots(use_demo_db, offline):
    """The tax-lot inventory's current prices: the same look-back, the same stale case."""
    from src.tax_lots import get_lot_inventory
    far = (date.fromisoformat(_newest_stored()) + timedelta(days=60)).isoformat()
    lots = get_lot_inventory(as_of=far)
    etf = lots[lots["ticker"] != "SPAXX"]
    assert not etf.empty and (etf["current_price"] > 0).all(), (
        "lots priced at zero offline on a stale cache: "
        f"{sorted(etf.loc[etf['current_price'] <= 0, 'ticker'].unique())}")
