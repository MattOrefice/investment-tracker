"""#686 — the benchmark attribution regression and the Risk page's factor regression take
the portfolio's daily return as the Performance page's risk windows take it: flow-netted.

Both regressions used to take R_p as the value series' pct_change, so a contribution
entered R_p as return on the day it landed. The committed demo book has no external
flow after its inception day (every later trade is a DRIP lot), so no test on it could
see that. This module plants one, as #686 reproduced it: a VOO buy on 2026-07-01 worth
the portfolio's value that day, on a scratch copy of demo.db, offline.

Each regression's R_p is read where it enters the fit (the dependent variable handed
to its OLS, plus the RF it was net of), so the test sees what the regression used, not
what a helper returns.
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
import src.factors as factors
import src.prices as prices
import src.risk as risk
from src.benchmarks import get_custom_blended_series
from src.holdings import (
    get_external_cashflow_series,
    get_inception_date,
    get_portfolio_account_id,
    get_portfolio_value_series,
)
from src.performance import _trading_day_returns
from src.returns import twr_index
from tests.conftest import block_network, pin_today, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
END = "2026-09-30"
FLOW_DAY = pd.Timestamp("2026-07-01")


def _returns_each_path_takes(book: Path) -> dict:
    """{name: daily portfolio return} for the three paths on ``book``, with the inputs
    each takes in production: the window function with the series and flows the
    Performance page passes it (SI), and each regression's R_p as fitted."""
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
            cf = cf.reindex(pv.index).fillna(0.0)
            bl = get_custom_blended_series(inception, END)
            window, _ = _trading_day_returns(pv, bl / float(bl.iloc[0]), "SI", cf)

            fitted = {}
            real_bench, real_risk = factors._ols_benchmark, risk._ols_five_factor

            def bench_fit(y, X):
                fitted["benchmark regression"] = y
                return real_bench(y, X)

            def risk_fit(y, X):
                fitted["risk regression"] = y
                return real_risk(y, X)

            mp.setattr(factors, "_ols_benchmark", bench_fit)
            mp.setattr(risk, "_ols_five_factor", risk_fit)
            assert factors.run_benchmark_attribution_regression(inception, END) is not None
            assert risk.run_portfolio_factor_regression(inception, END)["status"] == "ok"
            rf = factors.load_factors("us")["RF"]
            rf.index = pd.to_datetime(rf.index)
        finally:
            unpin_leftovers()
    out = {"window function": window}
    for name, excess in fitted.items():
        out[name] = excess + rf.reindex(excess.index)
    return {"returns": out, "fitted": fitted, "rf": rf, "pv": pv, "cf": cf}


@pytest.fixture(scope="module")
def committed(tmp_path_factory):
    copy = tmp_path_factory.mktemp("flow_netted") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    return copy, _returns_each_path_takes(copy)


@pytest.fixture(scope="module")
def staged(committed, tmp_path_factory):
    """demo.db plus one contribution: VOO bought on FLOW_DAY at its close, worth the
    portfolio's value that day."""
    _, before = committed
    copy = tmp_path_factory.mktemp("flow_netted_staged") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    value = float(before["pv"][FLOW_DAY])
    con = sqlite3.connect(copy)
    with con:
        close, = con.execute("SELECT close FROM prices WHERE ticker = 'VOO' AND price_date = ?",
                             (FLOW_DAY.date().isoformat(),)).fetchone()
        n = con.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, fees, "
            "notes, lot_source) VALUES (1, 'VOO', ?, 'Buy', ?, ?, 0, '#686 staged contribution', "
            "'Manual')", (FLOW_DAY.date().isoformat(), value / close, close)).rowcount
    con.close()
    assert n == 1
    return value, _returns_each_path_takes(copy)


def test_the_staged_contribution_is_one_a_value_ratio_reads_as_a_gain(committed, staged):
    """The premise: the flow series holds the staged buy, at what it adds to the value
    series that day (#687), the committed book holds no flow after its first day, and
    pct_change reads the staged day as about +98%."""
    _, before = committed
    _, after = staged
    added = after["pv"][FLOW_DAY] - before["pv"][FLOW_DAY]
    assert after["cf"][FLOW_DAY] == pytest.approx(added, rel=1e-12)
    assert (before["cf"].iloc[1:] == 0.0).all()
    assert after["pv"].pct_change()[FLOW_DAY] > 0.9
    assert abs(before["pv"].pct_change()[FLOW_DAY]) < 0.05


def test_the_three_take_the_same_return_on_every_day(staged):
    """The window function and both regressions hold the same days and the same return
    on each of them, the contribution day included."""
    _, after = staged
    returns = after["returns"]
    window = returns["window function"]
    differ = []
    for name in ("benchmark regression", "risk regression"):
        r_p = returns[name]
        assert list(r_p.index) == list(window.index), f"{name}: not the window's days"
        gap = (r_p - window).abs()
        differ += [f"{name} on {day.date()}: {r_p[day]:+.6f}, the window function "
                   f"{window[day]:+.6f}" for day in gap.index[gap >= 1e-12]]
    assert not differ, "\n".join(differ)


def test_the_contribution_days_return_is_the_twrs(staged):
    """On the flow day each path's return is the TWR's step, not the +98% the deposit
    reads as in pct_change."""
    _, after = staged
    index = twr_index(after["pv"], after["cf"])
    step = index[FLOW_DAY] / index.shift(1)[FLOW_DAY] - 1
    assert abs(step) < 0.05
    differ = [f"{name}: {r_p[FLOW_DAY]:+.6f} on {FLOW_DAY.date()}, the TWR's step is {step:+.6f}"
              for name, r_p in after["returns"].items() if abs(r_p[FLOW_DAY] - step) >= 1e-12]
    assert not differ, "\n".join(differ)


def test_a_book_with_no_flow_after_inception_is_fitted_on_pct_change_exactly(committed):
    """The control: where no flow falls after the first day (the committed demo book),
    each path takes the value series' pct_change bit for bit, and each regression fits
    exactly pct_change - RF, so no figure a committed lock prints moves
    (tests/test_locked_figures_golden.py)."""
    _, before = committed
    pv, rf = before["pv"], before["rf"]
    window = before["returns"]["window function"]
    assert window.equals(pv.pct_change().reindex(window.index))
    plain = pv.reindex(pd.date_range(pv.index.min(), pv.index.max(), freq="D")).ffill().pct_change()
    for name, excess in before["fitted"].items():
        assert excess.equals(plain.reindex(excess.index) - rf.reindex(excess.index)), name
