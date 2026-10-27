"""#687 — an external flow is taken in the value series' own units: the trade's own dollars,
shares × its price, at the ticker's adjustment factor on the trade date, with fees kept as
a cost.

The TWR nets each day's flow out of that day's change in value,
r_t = (V_t − V_{t−1} − CF_t) / V_{t−1}. The value series counts a share at its
total-return level, which is back-adjusted for every stored dividend after the date, and
the flow used to count it at the trade's raw price. Wherever the ticker paid a dividend
later, a buy added less to V_t than it took off it, so the flow day's step was low by
(CF_t / V_{t−1}) × (1 − f), f being the ticker's adjustment factor on the trade date (its
level over its close); a withdrawal read high by the same amount.

The factor goes on the trade's price, not on its close (the owner's choice, October 27,
2026): a ledger's trades carry their execution price, and the gap between it and the
close is that day's gain or loss, because the book paid the price. So a trade at the
close earns nothing on its own day, and one away from it keeps
shares × (price − close) × f in that day's step.

The committed demo book holds no flow after its first day, so this module plants one on a
scratch copy, offline, as #686 and #687 reproduced it: VOO on 2026-07-01. VOO went ex on
2026-09-28 ($1.823), so its factor on July 1 is below one. At the close the day's step on
the staged book is the committed book's own step, −0.007267, whichever way the flow goes;
1% above it, a buy loses the gap that day and a sale gains it.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import src.db as db
import src.holdings as holdings
import src.prices as prices
from src.coverage import TickerStatus
from src.holdings import (
    get_external_cashflow_series,
    get_inception_date,
    get_portfolio_account_id,
    get_portfolio_value_series,
)
from src.returns import twr_index
from tests.conftest import block_network, pin_today, unpin_leftovers
from tests.test_flow_netted_regressions import _returns_each_path_takes

ROOT = Path(__file__).resolve().parent.parent
END = "2026-09-30"
FLOW_DAY = pd.Timestamp("2026-07-01")
DAY = FLOW_DAY.date().isoformat()
# The committed book's own return on FLOW_DAY, as #687 measured it.
OWN_STEP = -0.007267
# VOO's adjustment factor on FLOW_DAY, as #687 measured it: one dividend after it.
VOO_FACTOR = 0.997435
FEE = 5.0
# A trade 1% above FLOW_DAY's close: execution away from the close, as a ledger's are.
ABOVE = 1.01


def _copy(tmp_path_factory, name: str) -> Path:
    copy = tmp_path_factory.mktemp(name) / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    return copy


def _plant(book: Path, action: str, shares: float, fees: float = 0.0,
           over_close: float = 1.0) -> "tuple[float, float]":
    """One VOO trade on FLOW_DAY at ``over_close`` × that day's close; returns the close
    and the trade's price."""
    con = sqlite3.connect(book)
    with con:
        close, = con.execute("SELECT close FROM prices WHERE ticker = 'VOO' AND price_date = ?",
                             (DAY,)).fetchone()
        price = close * over_close
        n = con.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, fees, "
            "notes, lot_source) VALUES (1, 'VOO', ?, ?, ?, ?, ?, '#687 staged flow', 'Manual')",
            (DAY, action, shares, price, fees)).rowcount
    con.close()
    assert n == 1
    return close, price


def _value_and_flows(book: Path) -> "tuple[pd.Series, pd.Series]":
    """The value series and the flow series on ``book``, as the Performance page takes
    them (from inception), offline."""
    with pytest.MonkeyPatch.context() as mp:
        pin_today(mp, date(2026, 10, 1))
        block_network(mp)
        mp.setattr(db, "DB_PATH", book)
        mp.setattr(db, "_migrated_paths", set())
        mp.setattr(db, "_RUNTIME_CACHE", None)
        prices._reset_trailing_memo()
        try:
            account_id = get_portfolio_account_id()
            inception = get_inception_date(account_id=account_id)
            pv = get_portfolio_value_series(inception, END, account_id=account_id)
            cf = get_external_cashflow_series(inception, END, account_id=account_id)
        finally:
            unpin_leftovers()
    return pv, cf.reindex(pv.index).fillna(0.0)


def _step(pv: pd.Series, cf: pd.Series) -> float:
    """The TWR's step on FLOW_DAY: twr_index's chain, which the Performance page's chart,
    twr_daily_linked and period_return all take."""
    index = twr_index(pv, cf)
    return float(index[FLOW_DAY] / index.shift(1)[FLOW_DAY] - 1)


def _staged(book: Path, shares: float, close: float, price: float) -> dict:
    return {"shares": shares, "close": close, "price": price,
            **_returns_each_path_takes(book)}


@pytest.fixture(scope="module")
def committed(tmp_path_factory):
    return _value_and_flows(_copy(tmp_path_factory, "flow_units_committed"))


@pytest.fixture(scope="module")
def voo_held(committed):
    """VOO's non-DRIP shares on FLOW_DAY in the committed book, the shares its value
    series counts."""
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'demo.db'}?mode=ro", uri=True)
    held, = con.execute(
        "SELECT SUM(CASE WHEN LOWER(action) = 'buy' THEN shares ELSE -shares END) FROM trades "
        "WHERE ticker = 'VOO' AND account_id = 1 AND trade_date <= ? "
        "AND (lot_source IS NULL OR lot_source != 'drip')", (DAY,)).fetchone()
    con.close()
    assert held and held > 0
    return float(held)


@pytest.fixture(scope="module")
def contribution(committed):
    """Shares worth the portfolio's value on FLOW_DAY at its close, as #686 and #687
    staged the contribution."""
    pv, _ = committed
    return float(pv[FLOW_DAY]) / _close_on_flow_day()


@pytest.fixture(scope="module")
def bought(contribution, tmp_path_factory):
    book = _copy(tmp_path_factory, "flow_units_bought")
    close, price = _plant(book, "Buy", contribution)
    return _staged(book, contribution, close, price)


@pytest.fixture(scope="module")
def bought_above(contribution, tmp_path_factory):
    book = _copy(tmp_path_factory, "flow_units_bought_above")
    close, price = _plant(book, "Buy", contribution, over_close=ABOVE)
    return _staged(book, contribution, close, price)


@pytest.fixture(scope="module")
def sold(voo_held, tmp_path_factory):
    """A withdrawal: half the VOO the book holds, sold on FLOW_DAY at its close."""
    book = _copy(tmp_path_factory, "flow_units_sold")
    close, price = _plant(book, "Sell", voo_held / 2)
    return _staged(book, voo_held / 2, close, price)


@pytest.fixture(scope="module")
def sold_above(voo_held, tmp_path_factory):
    book = _copy(tmp_path_factory, "flow_units_sold_above")
    close, price = _plant(book, "Sell", voo_held / 2, over_close=ABOVE)
    return _staged(book, voo_held / 2, close, price)


@pytest.fixture(scope="module")
def bought_with_fee(contribution, tmp_path_factory):
    book = _copy(tmp_path_factory, "flow_units_fee")
    _plant(book, "Buy", contribution, FEE)
    return _value_and_flows(book)


def _close_on_flow_day() -> float:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'demo.db'}?mode=ro", uri=True)
    close, = con.execute("SELECT close FROM prices WHERE ticker = 'VOO' AND price_date = ?",
                         (DAY,)).fetchone()
    con.close()
    return float(close)


def _factor(committed, bought) -> float:
    """VOO's factor on FLOW_DAY as the value series reads it: what the at-close buy added
    to V, over its raw dollars. Read off the value series, which #687 leaves alone, so it
    is the same number on the code before the fix."""
    pv_c, _ = committed
    added = float(bought["pv"][FLOW_DAY] - pv_c[FLOW_DAY])
    return added / (bought["shares"] * bought["close"])


def _every_path_reads(staged: dict, expected: float) -> "list[str]":
    """The flow day's step in the TWR and in every path that takes the flow-netted return
    (the risk window function and both regressions, #686) that is not ``expected``."""
    step = _step(staged["pv"], staged["cf"])
    reads = {"TWR": step, **{name: float(r_p[FLOW_DAY])
                             for name, r_p in staged["returns"].items()}}
    return [f"{name}: {r:+.6f}" for name, r in reads.items() if abs(r - expected) >= 1e-12]


def test_the_committed_books_own_step_is_the_one_687_measured(committed):
    """PREMISE. The committed book holds no flow after its first day, and its own return on
    FLOW_DAY is #687's −0.007267: the step a flow at the close on that day must leave
    alone."""
    pv, cf = committed
    assert (cf.iloc[1:] == 0.0).all()
    assert round(float(pv.pct_change()[FLOW_DAY]), 6) == OWN_STEP


def test_a_buy_is_its_own_dollars_at_voos_factor(committed, bought):
    """The flow is the trade's dollars, shares × price, at VOO's factor on the day
    (0.997435, #687). At the close that is exactly what the buy adds to V."""
    f = _factor(committed, bought)
    assert f == pytest.approx(VOO_FACTOR, abs=5e-7)
    dollars = bought["shares"] * bought["price"]
    assert float(bought["cf"][FLOW_DAY]) == pytest.approx(dollars * f, rel=1e-12)


def test_a_buy_at_the_close_earns_nothing_on_its_own_day(committed, bought):
    """#687's staged VOO buy, at the close: the flow day's step reads the book's own
    −0.007267, in the TWR and in every path that takes the flow-netted return."""
    pv_c, _ = committed
    own = float(pv_c.pct_change()[FLOW_DAY])
    assert round(_step(bought["pv"], bought["cf"]), 6) == OWN_STEP
    differ = _every_path_reads(bought, own)
    assert not differ, f"the book's own step is {own:+.6f}; " + "; ".join(differ)


def test_a_buy_above_the_close_keeps_its_gap_in_its_days_step(committed, bought, bought_above):
    """A buy 1% above the close paid shares × (price − close) more than its shares were
    worth that evening. The book paid it, so that day's step is the book's own step less
    the gap in the value series' units, over the prior day's value: not the book's own
    step, which is what valuing the flow at the close would read."""
    pv_c, _ = committed
    own = float(pv_c.pct_change()[FLOW_DAY])
    s = bought_above
    gap = s["shares"] * (s["price"] - s["close"]) * _factor(committed, bought)
    expected = own - gap / float(pv_c.shift(1)[FLOW_DAY])
    assert expected < own - 0.005
    differ = _every_path_reads(s, expected)
    assert not differ, (f"the book's own step {own:+.6f} less the gap is {expected:+.6f}; "
                        + "; ".join(differ))


def test_a_withdrawal_is_symmetric(committed, bought, sold):
    """A sale's flow is its own dollars at the same factor, so at the close it takes out
    exactly what it removes from V, and the flow day's step is the book's own step in
    every path, not high by VOO's factor."""
    pv_c, _ = committed
    own = float(pv_c.pct_change()[FLOW_DAY])
    assert round(_step(sold["pv"], sold["cf"]), 6) == OWN_STEP
    differ = _every_path_reads(sold, own)
    assert not differ, f"the book's own step is {own:+.6f}; " + "; ".join(differ)
    removed = float(sold["pv"][FLOW_DAY] - pv_c[FLOW_DAY])
    assert removed < 0
    dollars = -sold["shares"] * sold["price"]
    assert float(sold["cf"][FLOW_DAY]) == pytest.approx(
        dollars * _factor(committed, bought), rel=1e-12)
    assert float(sold["cf"][FLOW_DAY]) == pytest.approx(removed, rel=1e-12)


def test_a_sale_above_the_close_gains_its_gap(committed, bought, sold_above):
    """And a sale 1% above the close is that day's gain, by the same gap."""
    pv_c, _ = committed
    own = float(pv_c.pct_change()[FLOW_DAY])
    s = sold_above
    gap = s["shares"] * (s["price"] - s["close"]) * _factor(committed, bought)
    expected = own + gap / float(pv_c.shift(1)[FLOW_DAY])
    assert expected > own + 0.0005
    differ = _every_path_reads(s, expected)
    assert not differ, (f"the book's own step {own:+.6f} plus the gap is {expected:+.6f}; "
                        + "; ".join(differ))


def test_a_fee_stays_a_cost(committed, bought_with_fee):
    """Fees are a cost in dollars, not converted: the flow day's step is the book's own step
    less the fee over the prior day's value."""
    pv_c, _ = committed
    pv, cf = bought_with_fee
    own = float(pv_c.pct_change()[FLOW_DAY])
    expected = own - FEE / float(pv_c.shift(1)[FLOW_DAY])
    assert _step(pv, cf) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("serves_close", [True, False], ids=["close", "no-close"])
def test_the_factor_is_the_level_over_the_close_from_one_frame(monkeypatch, serves_close):
    """The factor is read from the frame the level comes from. A frame that serves no close,
    which only a legacy single-basis lock does (#193), has a factor of 1, so its flows stay
    the ledger's dollars rather than going NaN, which every reader fills with zero."""
    days = pd.to_datetime(["2026-07-01", "2026-07-02"]).date
    frame = pd.DataFrame({"adj_close": [99.0, 99.5]}, index=days)
    if serves_close:
        frame["close"] = [100.0, 100.0]
    monkeypatch.setattr(holdings, "_price_and_status",
                        lambda t, s, e, **kw: (frame.copy(), TickerStatus(t, True)))
    levels, factors, _ = holdings._valuation_levels(
        ["XYZ"], "2026-07-01", "2026-07-03", account_id=1)
    assert list(levels["XYZ"]) == [99.0, 99.5, 99.5]
    want = [0.99, 0.995, 0.995] if serves_close else [1.0, 1.0, 1.0]
    assert list(factors["XYZ"]) == pytest.approx(want, rel=1e-15)
