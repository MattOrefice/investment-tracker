"""External cash-flow threading for TWR and risk metrics (CODE-GAP 5+6).

The TWR functions and compute_risk_metrics were always flow-CAPABLE, but every
production caller hardcoded cashflows = 0, so a recorded contribution (Deploy
New Cash) was silently counted as market return. These tests pin the corrected
behavior:

  - the flow builder reconstructs signed external flows from the trades ledger,
    excluding DRIP lots (internal income, already embedded in adj_close);
  - risk metrics computed WITH flows equal those of a flowless twin portfolio
    with identical daily returns (consistency with the corrected TWR);
  - the old flow-blind behavior demonstrably differs (the bug proof);
  - windows with NO flows are numerically UNCHANGED (invariance) — including
    the demo portfolio, whose only external flow is the first-date seed.
"""
import math
import os
import pathlib
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# The two DB-reading tests below pin the demo book via the use_demo_db fixture
# (tests/conftest.py). A module-level os.environ.setdefault("TRACKER_MODE",...)
# used to sit here — inert, because .env=personal is already resolved at import,
# so it silently read the personal book instead. The fixture overrides that.

from src.performance import compute_risk_metrics
from src.returns import period_return


# ── fixture: deposit-bearing series and its flowless twin ────────────────────

def _flow_fixture(flow_index: int = 30):
    """60 daily points with known returns and one $500 deposit at flow_index.

    V_t = V_{t-1} × (1 + r_t) + CF_t  (end-of-day: the flow lands after the
    day's move), alongside a flowless twin with the SAME daily returns. Every
    return-based risk metric must agree between (deposit series + flow series)
    and the twin. The benchmark has small nonzero daily moves so the
    trading-day filter keeps all rows.
    """
    idx = pd.date_range("2025-01-01", periods=60, freq="D")
    pattern = [0.004, -0.002, 0.003, -0.001, 0.005, -0.003]
    rets = [0.0] + [pattern[i % 6] for i in range(1, 60)]

    cf = pd.Series(0.0, index=idx)
    cf.iloc[flow_index] = 500.0

    pv_vals, twin_vals = [1000.0], [1000.0]
    for i in range(1, 60):
        pv_vals.append(pv_vals[-1] * (1 + rets[i]) + cf.iloc[i])
        twin_vals.append(twin_vals[-1] * (1 + rets[i]))
    pv = pd.Series(pv_vals, index=idx)
    twin = pd.Series(twin_vals, index=idx)

    bench = pd.Series(
        [1.001 + 0.001 * ((i * 7) % 3) for i in range(60)], index=idx
    ).cumprod()
    return pv, twin, cf, bench


# ── flow builder on the demo ledger ──────────────────────────────────────────

def test_builder_demo_flows_are_inception_seed_only(use_demo_db):
    """demo.db's only external flow is the ~$1k inception seed on 2025-05-01.
    The 49 DRIP reinvestment rows are internal income (already embedded in
    adj_close) and must be excluded — any nonzero mid-history value here means
    the DRIP filter broke and income would be double-counted as a flow."""
    from src.holdings import get_external_cashflow_series

    inception = "2025-05-01"
    cf = get_external_cashflow_series(inception, date.today().isoformat())
    if float(cf.abs().sum()) == 0.0:
        pytest.skip("trades table empty — skipped in empty-DB mode")
    assert float(cf.loc[pd.Timestamp(inception)]) == pytest.approx(1000.0, abs=1.0)
    # Post-inception flow must be ZERO to the DOLLAR, not to the bit. demo.db now
    # holds a genuinely cash-neutral same-date rebalance (Phase 39 VEA trim funding
    # the three tilt buys). Its net flow sums the signed shares*price legs, and
    # IEEE754 addition is not associative — SQLite sums them in a different order
    # than Python, leaving ~7e-15 of machine epsilon no matter how the shares are
    # sized. A DRIP row misclassified as a flow would be DOLLAR-scale (~1e0); 1e-9
    # sits nine orders above that noise and six below a real flow, so it still
    # catches the double-count this guards against. Do NOT tighten back to == 0.0:
    # that asserts float-summation determinism, which does not hold for real sums.
    assert float(cf.iloc[1:].abs().sum()) < 1e-9


# ── risk-metrics consistency with the corrected TWR ──────────────────────────

def test_risk_metrics_flow_adjusted_match_flowless_twin():
    """A $500 deposit embedded in the value series, netted via cashflows=,
    must yield the SAME metrics as a flowless portfolio with identical daily
    returns — Sharpe/Sortino/vol/VaR/max-DD are then computed off flow-corrected
    returns, consistent with the corrected TWR."""
    pv, twin, cf, bench = _flow_fixture()
    m_flow = compute_risk_metrics(pv, bench, window="SI", cashflows=cf)
    m_twin = compute_risk_metrics(twin, bench, window="SI")
    assert m_flow and m_twin
    for key, expected in m_twin.items():
        actual = m_flow[key]
        if isinstance(expected, float) and math.isnan(expected):
            assert math.isnan(actual), key
        else:
            assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), key


def test_risk_metrics_flow_blind_counted_deposit_as_return():
    """THE CODE-GAP 6 BUG PROOF: without the flow series (the only pre-fix
    behavior — the cashflows parameter did not exist, so this test fails on
    pre-fix code with a TypeError), the $500 deposit into a ~$1,100 portfolio
    is booked as a ~+45% day: annualized return and volatility are wildly
    inflated relative to the flow-corrected truth."""
    pv, twin, cf, bench = _flow_fixture()
    blind = compute_risk_metrics(pv, bench, window="SI")
    aware = compute_risk_metrics(pv, bench, window="SI", cashflows=cf)
    assert blind["ann_port_return_pct"] > aware["ann_port_return_pct"] + 100.0
    assert blind["annualized_vol_pct"] > aware["annualized_vol_pct"] * 5


# ── no-flow invariance ────────────────────────────────────────────────────────

def _assert_metrics_bit_identical(a: dict, b: dict):
    """Exact (not approx) equality per key; NaN==NaN counts as identical
    (the rising fixture benchmark has no negative excess days, so its
    sortino is NaN in BOTH dicts — plain dict equality would false-fail)."""
    assert a.keys() == b.keys()
    for key in a:
        va, vb = a[key], b[key]
        if isinstance(va, float) and math.isnan(va):
            assert isinstance(vb, float) and math.isnan(vb), key
        else:
            assert va == vb, key


def test_risk_metrics_no_flow_invariance_exact():
    """An all-zero flow series must produce BIT-IDENTICAL metrics to
    cashflows=None: only days with a nonzero flow are ever adjusted, so the
    fix cannot perturb a flowless portfolio."""
    _, twin, _, bench = _flow_fixture()
    zeros = pd.Series(0.0, index=twin.index)
    _assert_metrics_bit_identical(
        compute_risk_metrics(twin, bench, window="SI"),
        compute_risk_metrics(twin, bench, window="SI", cashflows=zeros),
    )


def test_risk_metrics_flow_outside_window_leaves_window_unchanged():
    """A deposit BEFORE the trailing window's start must not perturb that
    window's metrics at all (bit-identical to the no-flow call)."""
    pv, _, cf, bench = _flow_fixture(flow_index=10)  # 1M window covers days 29+
    _assert_metrics_bit_identical(
        compute_risk_metrics(pv, bench, window="1M"),
        compute_risk_metrics(pv, bench, window="1M", cashflows=cf),
    )


def test_demo_period_returns_unchanged_by_flow_threading(use_demo_db):
    """demo.db has no mid-history external flow — its only flow is the
    first-date inception seed, which both TWR methods treat as starting NAV.
    The real flow series must therefore reproduce the old hardcoded-zeros
    numbers EXACTLY, for every period and both methods: on this data the fix
    is inert, and any drift here means a flow was misclassified."""
    from src.holdings import get_external_cashflow_series, get_portfolio_value_series

    inception = "2025-05-01"
    today = date.today().isoformat()
    pv = get_portfolio_value_series(inception, today)
    if pv.dropna().empty or float(pv.max()) == 0.0:
        pytest.skip("Portfolio data empty — skipped in empty-DB mode")
    cf_real = get_external_cashflow_series(inception, today).reindex(pv.index).fillna(0.0)
    cf_zeros = pd.Series(0.0, index=pv.index)

    # Returns must match to well within a basis point, not to the bit. The real
    # flow series carries ~7e-15 of machine epsilon on the cash-neutral Phase 39
    # rebalance date (IEEE754 non-associativity — see test_builder_demo_flows),
    # so threading it vs threading exact zeros differs at ~1e-18 in the return.
    # 1e-12 is ~1e-10 bps: inert to any decision, while a misclassified flow would
    # move the return by basis points. Do NOT tighten back to ==: float-summation
    # equality does not hold across the two summation orders.
    for p in ["1M", "3M", "YTD", "1Y", "SI"]:
        for method in ["daily", "modified_dietz"]:
            assert period_return(method, pv, cf_real, p) == \
                pytest.approx(period_return(method, pv, cf_zeros, p), abs=1e-12), (method, p)
