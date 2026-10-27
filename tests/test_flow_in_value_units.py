"""#687 — an external flow is taken in the value series' own units: shares × the ticker's
total-return level on the trade date, with fees kept as a cost.

The TWR nets each day's flow out of that day's change in value,
r_t = (V_t − V_{t−1} − CF_t) / V_{t−1}. The value series counts a share at its
total-return level, which is back-adjusted for every stored dividend after the date, and
the flow used to count it at the trade's raw price. Wherever the ticker paid a dividend
later, a buy added less to V_t than it took off it, so the flow day's step was low by
(CF_t / V_{t−1}) × (1 − f), f being the ticker's adjustment factor on the trade date; a
withdrawal read high by the same amount.

The committed demo book holds no flow after its first day, so this module plants one on a
scratch copy, offline, as #686 and #687 reproduced it: VOO on 2026-07-01, at that day's
close. VOO went ex on 2026-09-28 ($1.823), so its factor on July 1 is below one. Under
end-of-day timing the flow earns nothing on its own day, so the day's step on the staged
book is the committed book's own step, −0.007267, whichever way the flow goes.
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
import src.prices as prices
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
FEE = 5.0


def _copy(tmp_path_factory, name: str) -> Path:
    copy = tmp_path_factory.mktemp(name) / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    return copy


def _plant(book: Path, action: str, shares: float, fees: float = 0.0) -> float:
    """One VOO trade on FLOW_DAY at that day's close; returns the close."""
    con = sqlite3.connect(book)
    with con:
        close, = con.execute("SELECT close FROM prices WHERE ticker = 'VOO' AND price_date = ?",
                             (DAY,)).fetchone()
        n = con.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, fees, "
            "notes, lot_source) VALUES (1, 'VOO', ?, ?, ?, ?, ?, '#687 staged flow', 'Manual')",
            (DAY, action, shares, close, fees)).rowcount
    con.close()
    assert n == 1
    return close


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
def bought(committed, tmp_path_factory):
    """A contribution worth the portfolio's value on FLOW_DAY, as #686 and #687 staged it."""
    pv, _ = committed
    book = _copy(tmp_path_factory, "flow_units_bought")
    close = _plant(book, "Buy", float(pv[FLOW_DAY]) / _close_on_flow_day(), 0.0)
    return {"shares": float(pv[FLOW_DAY]) / close, "close": close,
            **_returns_each_path_takes(book)}


@pytest.fixture(scope="module")
def sold(voo_held, tmp_path_factory):
    """A withdrawal: half the VOO the book holds, sold on FLOW_DAY at its close."""
    book = _copy(tmp_path_factory, "flow_units_sold")
    close = _plant(book, "Sell", voo_held / 2)
    return {"shares": voo_held / 2, "close": close, **_returns_each_path_takes(book)}


@pytest.fixture(scope="module")
def bought_with_fee(committed, tmp_path_factory):
    pv, _ = committed
    book = _copy(tmp_path_factory, "flow_units_fee")
    _plant(book, "Buy", float(pv[FLOW_DAY]) / _close_on_flow_day(), FEE)
    return _value_and_flows(book)


def _close_on_flow_day() -> float:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'demo.db'}?mode=ro", uri=True)
    close, = con.execute("SELECT close FROM prices WHERE ticker = 'VOO' AND price_date = ?",
                         (DAY,)).fetchone()
    con.close()
    return float(close)


def test_the_committed_books_own_step_is_the_one_687_measured(committed):
    """PREMISE. The committed book holds no flow after its first day, and its own return on
    FLOW_DAY is #687's −0.007267: the step a flow on that day must leave alone."""
    pv, cf = committed
    assert (cf.iloc[1:] == 0.0).all()
    assert round(float(pv.pct_change()[FLOW_DAY]), 6) == OWN_STEP


def test_a_buy_is_taken_at_what_it_adds_to_the_value_series(committed, bought):
    """The flow is what the trade adds to V on its day: shares × VOO's total-return level.
    VOO's later dividend puts that level below the close, so the flow is below the raw
    shares × close it used to be, by VOO's factor on the day (0.997435, #687)."""
    pv_c, _ = committed
    added = float(bought["pv"][FLOW_DAY] - pv_c[FLOW_DAY])
    assert float(bought["cf"][FLOW_DAY]) == pytest.approx(added, rel=1e-12)
    raw = bought["shares"] * bought["close"]
    assert added / raw == pytest.approx(0.997435, abs=5e-7)


def test_a_buy_earns_nothing_on_its_own_day(committed, bought):
    """The flow day's step is the book's own step, in the TWR and in every path that takes
    the flow-netted return: the risk window function and both regressions (#686)."""
    pv_c, _ = committed
    own = float(pv_c.pct_change()[FLOW_DAY])
    step = _step(bought["pv"], bought["cf"])
    assert step == pytest.approx(own, abs=1e-12), (
        f"the TWR reads {step:+.6f} on {DAY}, the book's own step is {own:+.6f}")
    differ = [f"{name}: {r_p[FLOW_DAY]:+.6f}" for name, r_p in bought["returns"].items()
              if abs(r_p[FLOW_DAY] - own) >= 1e-12]
    assert not differ, f"the book's own step is {own:+.6f}; " + "; ".join(differ)


def test_a_withdrawal_is_taken_at_what_it_removes_from_the_value_series(committed, sold):
    """A sale's flow is what it takes out of V on its day, at the same level as a buy."""
    pv_c, _ = committed
    removed = float(sold["pv"][FLOW_DAY] - pv_c[FLOW_DAY])
    assert removed < 0
    assert float(sold["cf"][FLOW_DAY]) == pytest.approx(removed, rel=1e-12)
    raw = -sold["shares"] * sold["close"]
    assert removed / raw == pytest.approx(0.997435, abs=5e-7)


def test_a_withdrawal_is_symmetric(committed, sold):
    """So the flow day's step is the book's own step here too, in every path, not high by
    VOO's factor."""
    pv_c, _ = committed
    own = float(pv_c.pct_change()[FLOW_DAY])
    step = _step(sold["pv"], sold["cf"])
    assert step == pytest.approx(own, abs=1e-12), (
        f"the TWR reads {step:+.6f} on {DAY}, the book's own step is {own:+.6f}")
    differ = [f"{name}: {r_p[FLOW_DAY]:+.6f}" for name, r_p in sold["returns"].items()
              if abs(r_p[FLOW_DAY] - own) >= 1e-12]
    assert not differ, f"the book's own step is {own:+.6f}; " + "; ".join(differ)


def test_a_fee_stays_a_cost(committed, bought_with_fee):
    """Fees are a cost in dollars, not converted: the flow day's step is the book's own step
    less the fee over the prior day's value."""
    pv_c, _ = committed
    pv, cf = bought_with_fee
    own = float(pv_c.pct_change()[FLOW_DAY])
    expected = own - FEE / float(pv_c.shift(1)[FLOW_DAY])
    assert _step(pv, cf) == pytest.approx(expected, abs=1e-12)
