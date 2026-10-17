"""A series that starts later than asked is never read as covering the window (#639).

Decided by the owner on October 17, 2026, for the parts that do not depend on how a
figure is withheld:

  * SPAXX's income is modeled at BIL's growth from a start. BIL covers that start, or
    SPAXX is unresolved; it is never measured from a later base.
  * The multi-fund factor regressions (the US equity sleeve, the FI sleeve) no longer
    fill a leg's missing stretch with 0%. They use the dates every leg covers, and their
    sample window, printed from those dates, says where it starts.
  * A quarter lock is refused while a holding has no price on a date it was held in the
    quarter. A ticker the portfolio does not hold may start late, and a market holiday
    is never a gap.

On copies of the frozen book, offline. A short series is made by deleting a ticker's
closes before a date.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

DAY = "2026-07-20"               # the frozen book's last close


def _cut(path, ticker: str, before: str) -> None:
    """Delete ``ticker``'s closes before ``before``: its series now starts there."""
    con = sqlite3.connect(path)
    try:
        with con:
            assert con.execute("DELETE FROM prices WHERE ticker = ? AND price_date < ?",
                               (ticker, before)).rowcount > 0
    finally:
        con.close()


def _sessions(path, start: str, end: str) -> "list[str]":
    """The days SPY closed in [start, end]: the sessions the book's frame holds."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT price_date FROM prices WHERE ticker = 'SPY' AND price_date >= ? "
            "AND price_date <= ? ORDER BY price_date", (start, end))]
    finally:
        con.close()


@pytest.fixture
def book(tmp_path, monkeypatch):
    return point_at_frozen_book(monkeypatch, tmp_path)


# ── SPAXX's BIL growth ────────────────────────────────────────────────────────

def test_spaxx_is_unresolved_when_bil_starts_after_the_start(book, monkeypatch):
    """BIL from May 12, 2025, against SPAXX's May 1 start: not measured from May 12.
    Offline, the fetch for the missing stretch fails, and the reason says so."""
    from src import holdings
    _cut(book, "BIL", "2025-05-12")
    price, st = holdings._spaxx_price(DAY)
    assert price == 1.0
    assert (st.ticker, st.resolved, st.reason) == ("SPAXX", False, "fetch_failed")


def test_with_the_gap_fetch_off_the_stretch_is_not_stored(book, monkeypatch):
    import src.prices as prices
    from src import holdings
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    _cut(book, "BIL", "2025-05-12")
    _price, st = holdings._spaxx_price(DAY)
    assert (st.resolved, st.reason) == (False, "no_cached_rows")


def test_bil_covering_the_start_still_models_the_income(book):
    """The control: the book as committed, and a start on a Saturday that BIL covers
    from the Monday after, its first session."""
    from src import holdings
    price, st = holdings._spaxx_price(DAY)
    assert st.resolved and price > 1.0
    _cut(book, "BIL", "2025-05-05")
    frame, st = holdings._bil_for_spaxx("2025-05-03", DAY)
    assert st.resolved and str(min(frame.index)) == "2025-05-05"


def test_every_spaxx_valuation_marks_it_unresolved(book):
    """The sleeve weights, the current market value and the value series each read
    SPAXX through BIL; each reports it unresolved rather than a later base."""
    from src import holdings
    _cut(book, "BIL", "2025-05-12")
    acct = holdings.get_portfolio_account_id()
    _sw, sw = holdings.sleeve_weights_with_coverage(DAY)
    _mv, mv = holdings.current_market_value_with_coverage(DAY)
    _pv, pv = holdings.portfolio_value_series_with_coverage("2025-05-01", DAY, account_id=acct)
    for cov in (sw, mv, pv):
        assert cov.unresolved_tickers() == {"SPAXX"}, cov


def test_the_value_series_values_it_as_an_unresolved_spaxx(book):
    """A short BIL is valued exactly as a missing one already is, so no base is
    taken from the date BIL happens to start."""
    from src import holdings
    acct = holdings.get_portfolio_account_id()
    _cut(book, "BIL", "2025-05-12")
    short = holdings.get_portfolio_value_series("2025-05-01", DAY, account_id=acct)
    _cut(book, "BIL", "2099-01-01")
    missing = holdings.get_portfolio_value_series("2025-05-01", DAY, account_id=acct)
    pd.testing.assert_series_equal(short, missing)


# ── the multi-fund factor regressions ──────────────────────────────────────────

US_WEIGHTS = {"VOO": 0.6, "VTV": 0.4}
INCEPTION, FACTOR_END = "2025-05-01", "2026-03-31"     # the factor files end in March


def _first_factor_day_on_or_after(day: str) -> str:
    from src.factors import load_factors
    ff = load_factors("us")
    idx = pd.to_datetime(ff.index)
    return idx[idx >= pd.Timestamp(day)][0].date().isoformat()


def test_a_legs_missing_stretch_stays_missing(book):
    """VTV from September 2, 2025: no sleeve return before its first return, where
    0% used to stand, and the weighted return after it."""
    from src import factors
    _cut(book, "VTV", "2025-09-02")
    ret = factors._get_sleeve_return_series(["VOO", "VTV"], INCEPTION, FACTOR_END,
                                            weights=US_WEIGHTS)
    assert ret[:"2025-09-02"].isna().all()
    assert not (ret[:"2025-09-02"] == 0.0).any()
    voo = factors.get_prices("VOO", "2025-09-02", "2025-09-03")["adj_close"]
    vtv = factors.get_prices("VTV", "2025-09-02", "2025-09-03")["adj_close"]
    want = 0.6 * (voo.iloc[1] / voo.iloc[0] - 1) + 0.4 * (vtv.iloc[1] / vtv.iloc[0] - 1)
    assert ret.loc["2025-09-03"] == pytest.approx(want, rel=1e-12)


def test_the_us_sleeve_window_starts_where_every_leg_covers(book):
    from src import factors
    whole = factors.run_sleeve_regression("US", ["VOO", "VTV"], "us", INCEPTION,
                                          FACTOR_END, weights=US_WEIGHTS)
    assert whole["sample_start"] == _first_factor_day_on_or_after("2025-05-02")
    _cut(book, "VTV", "2025-09-02")
    short = factors.run_sleeve_regression("US", ["VOO", "VTV"], "us", INCEPTION,
                                          FACTOR_END, weights=US_WEIGHTS)
    assert short["sample_start"] == _first_factor_day_on_or_after("2025-09-03")
    assert short["T"] < whole["T"]


def test_the_fi_sleeve_window_starts_where_both_funds_cover(book):
    from src import factors
    whole = factors.regress_fi_sleeve(INCEPTION, FACTOR_END)
    assert whole["sample_start"] == _first_factor_day_on_or_after("2025-05-02")
    _cut(book, "SCHP", "2025-09-02")
    short = factors.regress_fi_sleeve(INCEPTION, FACTOR_END)
    assert short["sample_start"] == _first_factor_day_on_or_after("2025-09-03")
    assert short["T"] < whole["T"]


# ── the quarter lock's leading edge ───────────────────────────────────────────

@pytest.fixture
def unlocked_q2(book, monkeypatch):
    """The frozen book on the day after Q2 2026 ended, with no Q2 lock."""
    pin_today(monkeypatch, date(2026, 7, 1))
    con = sqlite3.connect(book)
    try:
        with con:
            assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                               ).rowcount == 1
    finally:
        con.close()
    yield book
    unpin_leftovers()


def _locked(path) -> "list[str]":
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT quarter_id FROM quarter_snapshots")]
    finally:
        con.close()


def _refusal(dates: "list[str]", names: str) -> str:
    return ("Cannot lock 2026Q2: a holding has no price on a date it was held in the "
            "quarter, so a lock now would freeze it unpriced. Nothing was locked. No close "
            f"on {', '.join(dates)} for {names}.")


def test_a_holding_unpriced_on_days_it_was_held_refuses_the_lock(unlocked_q2):
    """VOO, held all quarter, from May 15: every session before it is named."""
    from src.cache import LockCoverageError, capture_quarter_snapshot
    _cut(unlocked_q2, "VOO", "2026-05-15")
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value) == _refusal(_sessions(unlocked_q2, "2026-04-01", "2026-05-14"),
                                      "VOO")
    assert "2026Q2" not in _locked(unlocked_q2), "nothing was persisted"


def test_spaxx_is_checked_through_bil(unlocked_q2):
    from src.cache import LockCoverageError, capture_quarter_snapshot
    _cut(unlocked_q2, "BIL", "2026-05-15")
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value) == _refusal(_sessions(unlocked_q2, "2026-04-01", "2026-05-14"),
                                      "SPAXX (BIL)")


def test_a_market_holiday_is_not_a_gap(unlocked_q2):
    """VOO from Monday April 6, 2026: April 1 and 2 are named, Good Friday is not."""
    from src.cache import LockCoverageError, capture_quarter_snapshot
    _cut(unlocked_q2, "VOO", "2026-04-06")
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value) == _refusal(["2026-04-01", "2026-04-02"], "VOO")


def test_a_ticker_the_portfolio_does_not_hold_may_start_late(unlocked_q2):
    """SPY, a benchmark the lock covers and the portfolio does not hold, from May 15:
    a late listing is not a gap, and the lock is taken."""
    from src.cache import capture_quarter_snapshot
    _cut(unlocked_q2, "SPY", "2026-05-15")
    capture_quarter_snapshot("2026Q2")
    assert "2026Q2" in _locked(unlocked_q2)


def test_a_book_without_a_trade_ledger_holds_nothing(tmp_path, monkeypatch):
    import src.db as db
    from src.cache import _held_without_price
    path = tmp_path / "prices_only.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, "
                "adj_close REAL, PRIMARY KEY (ticker, price_date))")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated_paths", {str(path)})
    frame = pd.DataFrame({"VOO": [None, 1.0]}, index=[date(2026, 4, 1), date(2026, 4, 2)])
    assert _held_without_price(frame, date(2026, 4, 1), date(2026, 6, 30)) == []
