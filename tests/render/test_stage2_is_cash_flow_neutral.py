"""#349 — Stage 2's portfolio side is the book's own TWR, not an end-over-start value ratio.

The two-stage tiles took the portfolio's return from _benchmark_period_return(pv, ...),
which divides the value series' end by its start. pv is a dollar series that steps up
on every deposit, so new money was reported as return: on the owner's book, one deposit
inside the default window put Stage 2 at +10295 bps beside a 3.16% TWR.

The demo and frozen books have no deposit after inception, where a value ratio and a
TWR agree exactly, so no render test could see it. This module plants one: a buy with
no matching sell inside the default 3M window, the shape a real deployment has in the
ledger. Then the tile must agree with the TWR the page itself renders for that window.
"""
from __future__ import annotations

import re
import sqlite3

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.usefixtures("frozen_book_module")

# Inside the default 3M window, which on the frozen book (frontier 2026-07-20) runs
# 2026-04-21 to 2026-07-20. ~$300 into a ~$1.4k book: a value ratio would read the
# deposit as a ~2000 bps gain, far outside any rounding tolerance below.
DEPOSIT_DATE = "2026-06-01"
DEPOSIT_TICKER = "VOO"
DEPOSIT_DOLLARS = 300.0


def _bps(text: str) -> int:
    m = re.fullmatch(r"([+-]?\d+) bps", str(text).strip())
    assert m, f"not a bps tile: {text!r}"
    return int(m.group(1))


def _pct(text: str) -> float:
    m = re.fullmatch(r"(-?\d+\.\d+)%", str(text).strip())
    assert m, f"not a percent cell: {text!r}"
    return float(m.group(1)) / 100


@pytest.fixture(scope="module")
def book_with_deposit(frozen_book_module):
    """The module's frozen-book copy, plus one external deposit mid-window."""
    con = sqlite3.connect(frozen_book_module)
    with con:
        close = con.execute(
            "SELECT close FROM prices WHERE ticker = ? AND price_date = ?",
            (DEPOSIT_TICKER, DEPOSIT_DATE),
        ).fetchone()[0]
        n = con.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
            "fees, notes, lot_source) VALUES (1, ?, ?, 'Buy', ?, ?, 0, ?, 'Fidelity CSV')",
            (DEPOSIT_TICKER, DEPOSIT_DATE, DEPOSIT_DOLLARS / close, close,
             "#349 test deposit"),
        ).rowcount
    con.close()
    assert n == 1
    return frozen_book_module


@pytest.fixture(scope="module")
def page(book_with_deposit):
    """Page 2 rendered on the deposit book. st.cache_data is cleared on both sides:
    _load_portfolio is keyed on its arguments, not on the DB behind them, so a render
    from another module would otherwise serve a deposit-free series here, and this
    module's series would leak into the next."""
    import streamlit as st
    st.cache_data.clear()
    at = AppTest.from_file("pages/2_Performance.py", default_timeout=180).run()
    assert not at.exception, f"page raised: {at.exception}"
    yield at
    st.cache_data.clear()


def test_the_deposit_would_move_a_value_ratio(book_with_deposit):
    """PREMISE. Without a deposit inside the window a value ratio equals the TWR, and
    the test below could not tell the right expression from the wrong one."""
    from src.holdings import (get_external_cashflow_series, get_inception_date,
                              get_portfolio_value_series, last_settled_price_date)
    from src.returns import period_bounds, period_return

    inc = get_inception_date(account_id=1)
    anchor = last_settled_price_date(inc)
    start, end = period_bounds("3M", anchor, inc)
    assert start < DEPOSIT_DATE <= end, (start, end)
    pv = get_portfolio_value_series(inc, anchor, account_id=1)
    cf = get_external_cashflow_series(inc, anchor, account_id=1).reindex(pv.index).fillna(0.0)
    assert cf[pd.Timestamp(DEPOSIT_DATE)] == pytest.approx(DEPOSIT_DOLLARS)
    ratio = period_return("daily", pv, pd.Series(0.0, index=pv.index), "3M")
    twr = period_return("daily", pv, cf, "3M")
    assert ratio - twr > 0.10, (ratio, twr)


def test_stage2_agrees_with_the_books_own_twr_on_the_default_window(page):
    """Stage 2 = portfolio return − SAA blend. The portfolio return must be the TWR the
    page renders in its returns table for the same window, not V_end / V_start − 1."""
    assert [r.value for r in page.radio if r.key == "bf_period"] == ["3M"]
    metrics = {m.label: m.value for m in page.metric}
    stage2 = _bps(metrics["Stage 2: Implementation"])

    returns = next(d.value for d in page.dataframe if "Portfolio" in d.value.columns)
    twr_3m = _pct(returns.loc["3 Months", "Portfolio"])

    # The SAA blend is the one series the returns table reads (#383): rebalanced each
    # calendar quarter. It was the BF table's Σ Bench Wt × Bench Ret, one basket held
    # from the window's start, until #383 made the stages read the series.
    saa = _pct(returns.loc["3 Months", "Custom Blended"])

    expected = (twr_3m - saa) * 10_000
    # The tile rounds to 1 bp and each table cell to 0.01%: at most 1.5 bp between them.
    assert abs(stage2 - expected) <= 1.5, (
        f"Stage 2 tile {stage2:+d} bps, but the book's 3M TWR ({twr_3m:.4%}) less the "
        f"SAA blend ({saa:.4%}) is {expected:+.1f} bps. A gap of the deposit's size "
        "means the portfolio side is counting new money as return again (#349)."
    )
