"""Tests for src/returns.py TWR calculations."""
import sys
import pathlib

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.returns import (
    annualize,
    flow_netted_daily_returns,
    period_return,
    twr_daily_linked,
    twr_index,
    twr_modified_dietz,
)


def _series(values, cfs, start="2025-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx), pd.Series(cfs, index=idx)


# ── Test 1: no cash flows ────────────────────────────────────────────────────

def test_no_cashflows_daily_linked():
    """$100 → $110 with no flows: daily-linked returns exactly 0.10."""
    v, cf = _series([100.0, 110.0], [0.0, 0.0])
    assert twr_daily_linked(v, cf) == pytest.approx(0.10, abs=1e-10)


def test_no_cashflows_modified_dietz():
    """$100 → $110 with no flows: Modified Dietz returns exactly 0.10."""
    v, cf = _series([100.0, 110.0], [0.0, 0.0])
    assert twr_modified_dietz(v, cf) == pytest.approx(0.10, abs=1e-10)


# ── Test 2: mid-period deposit ───────────────────────────────────────────────

def test_midperiod_deposit_daily_linked():
    """
    Day 0: V=100, CF=0   (starting value)
    Day 1: V=160, CF=50  ($50 deposit at END of day 1, after the market move)
    Day 2: V=165, CF=0

    r_1 = (160 - 100 - 50) / 100 = 0.10
    r_2 = (165 - 160 -  0) / 160 = 0.03125
    TWR = 1.10 × 1.03125 − 1 = 0.134375
    """
    v, cf = _series([100.0, 160.0, 165.0], [0.0, 50.0, 0.0])
    assert twr_daily_linked(v, cf) == pytest.approx(0.134375, abs=1e-8)


def test_midperiod_deposit_modified_dietz():
    """
    Same scenario as above:
      V_start=100, V_end=165, total_CF=50, total_days=2
      CF at day 1: days_remaining=1, w=0.5 → weighted_CF=25
      Return = (165 − 100 − 50) / (100 + 25) = 15/125 = 0.12
    """
    v, cf = _series([100.0, 160.0, 165.0], [0.0, 50.0, 0.0])
    assert twr_modified_dietz(v, cf) == pytest.approx(0.12, abs=1e-10)


# ── Test 3: multi-sub-period GIPS chain-link ────────────────────────────────

def test_identity_gips_multiperiod_chain_link():
    """
    Three sub-periods, no cash flows (textbook GIPS example):
      R_1 = +10%: 1000 → 1100
      R_2 =  -5%: 1100 → 1045
      R_3 =  +5%: 1045 → 1097.25
    TWR = (1.10)(0.95)(1.05) − 1 = 0.09725
    """
    v, cf = _series(
        [1000.0, 1100.0, 1045.0, 1097.25],
        [0.0,    0.0,    0.0,    0.0],
    )
    assert twr_daily_linked(v, cf) == pytest.approx(0.09725, abs=1e-8)


# ── Test 4: annualize ────────────────────────────────────────────────────────

def test_annualize_one_year():
    """10% over exactly 365 days annualizes to 10%."""
    assert annualize(0.10, 365) == pytest.approx(0.10, abs=1e-10)


def test_annualize_two_years():
    """20% over 730 days annualizes to (1.20)^(365/730) − 1 ≈ 9.544%."""
    expected = (1.20 ** (365 / 730)) - 1.0
    assert annualize(0.20, 730) == pytest.approx(expected, abs=1e-10)


# ── Test 5: period_return slicing ────────────────────────────────────────────

def test_period_return_si_matches_full():
    """period_return('SI') on a no-CF series should equal simple return."""
    v, cf = _series([100.0, 105.0, 108.0, 112.0], [0.0, 0.0, 0.0, 0.0])
    expected = twr_daily_linked(v, cf)
    assert period_return("daily", v, cf, "SI") == pytest.approx(expected, abs=1e-10)


def test_period_return_insufficient_data():
    """period_return returns 0.0 when fewer than 2 data points after slicing."""
    v, cf = _series([100.0], [0.0])
    assert period_return("daily", v, cf, "SI") == 0.0


# ── Test 6: flow-timing pins — END-of-day is the implemented convention ──────

def test_daily_linked_flow_timing_is_end_of_day():
    """
    Timing discriminator: $100 → $160 with a $50 deposit on day 1.
      End-of-day (implemented):   r = (160 − 100 − 50) / 100        = 0.10
      Beginning-of-day (NOT it):  r = (160 − 100 − 50) / (100 + 50) ≈ 0.0667
    The deposit earns nothing on the day it lands, so the denominator is the
    bare prior value. Pins the formula the docstring now documents.
    """
    v, cf = _series([100.0, 160.0], [0.0, 50.0])
    r = twr_daily_linked(v, cf)
    assert r == pytest.approx(0.10, abs=1e-12)
    assert r != pytest.approx(10.0 / 150.0, abs=1e-4)   # beginning-of-day value


def test_modified_dietz_hand_computed_end_of_day_weight():
    """
    Hand-computed Modified Dietz with an asymmetric flow date (11 daily points,
    total_days = 10; Dietz reads only the endpoints and the flow):
      V_start = 1000, V_end = 1300, one +$200 flow on day 3.
      End-of-day weight: w = (10 − 3)/10 = 0.7 → denom = 1000 + 0.7×200 = 1140
      Return = (1300 − 1000 − 200) / 1140 = 100/1140 ≈ 0.0877193
    Beginning-of-day (w = 0.8, denom = 1160 → 100/1160 ≈ 0.0862069) would fail.
    """
    values = [1000.0, 1005.0, 1010.0, 1215.0, 1220.0, 1230.0,
              1240.0, 1250.0, 1270.0, 1290.0, 1300.0]
    cfs = [0.0] * 11
    cfs[3] = 200.0
    v, cf = _series(values, cfs)
    r = twr_modified_dietz(v, cf)
    assert r == pytest.approx(100.0 / 1140.0, abs=1e-12)
    assert r != pytest.approx(100.0 / 1160.0, abs=1e-4)  # beginning-of-day value


def test_flow_blind_zeros_overstate_return():
    """
    THE CODE-GAP 5 BUG, at the function boundary: production callers used to
    pass hardcoded zeros, so a $50 deposit was credited as market gain. Same
    value path, flow-blind vs flow-aware:
      flow-blind SI TWR  = 165/100 − 1 = 0.65   (deposit booked as return)
      flow-aware SI TWR  = 0.134375              (deposit netted out)
    The fix threads the real flow series into every caller; this pins the
    difference so the two can never silently re-converge.
    """
    v, cf_real = _series([100.0, 160.0, 165.0], [0.0, 50.0, 0.0])
    cf_zeros = pd.Series(0.0, index=v.index)
    blind = twr_daily_linked(v, cf_zeros)
    aware = twr_daily_linked(v, cf_real)
    assert blind == pytest.approx(0.65, abs=1e-10)
    assert aware == pytest.approx(0.134375, abs=1e-10)
    assert blind > aware  # the miscounted contribution inflated the return


def test_twr_index_is_twr_daily_linked_kept_as_a_series():
    """twr_index (the chart's portfolio line, #349) is twr_daily_linked's chain kept as
    a series: it starts at 1.0, ends at 1 + twr_daily_linked, and a deposit is netted
    rather than drawn as a gain. The zero prior value mirrors twr_daily_linked's skip."""
    v, cf = _series([0.0, 100.0, 110.0, 260.0, 247.0], [100.0, 0.0, 0.0, 150.0, 0.0])
    idx = twr_index(v, cf)
    assert list(idx.index) == list(v.index)
    assert idx.iloc[0] == 1.0
    assert idx.iloc[-1] - 1 == pytest.approx(twr_daily_linked(v, cf), abs=1e-12)
    # By hand: day 2 +10%, day 3 (260 - 110 - 150) / 110 = 0, day 4 -5%.
    assert idx.iloc[-1] == pytest.approx(1.10 * 1.0 * 0.95, abs=1e-12)
    # A value ratio would call the 150 deposit a gain.
    assert v.iloc[-1] / v.iloc[1] - 1 == pytest.approx(1.47, abs=1e-12)

# ── flow_netted_daily_returns: the one daily return the risk windows and both
# ── regressions take (#686) ──────────────────────────────────────────────────

def test_flow_netted_returns_take_the_twrs_step_on_a_flow_day():
    """A deposit is netted out of its day's return by twr_daily_linked's step, so the
    returns chain to the TWR; pct_change would read the deposit as a gain."""
    v, cf = _series([0.0, 100.0, 110.0, 260.0, 247.0], [100.0, 0.0, 0.0, 150.0, 0.0])
    r = flow_netted_daily_returns(v, cf)
    assert list(r.index) == list(v.index)
    assert r.iloc[3] == pytest.approx((260.0 - 110.0 - 150.0) / 110.0, abs=1e-15)
    assert v.pct_change().iloc[3] == pytest.approx(150.0 / 110.0, abs=1e-12)
    steps = twr_index(v, cf) / twr_index(v, cf).shift(1) - 1
    assert r.iloc[2:].tolist() == pytest.approx(steps.iloc[2:].tolist(), abs=1e-15)
    assert (1 + r.iloc[2:]).prod() - 1 == pytest.approx(twr_daily_linked(v, cf), abs=1e-12)


def test_flow_netted_returns_leave_every_day_without_a_flow_as_pct_change():
    """Bit-identical, not approximately equal: a series with no flow, or no flow series,
    gives pct_change exactly, and on a flow day's neighbours nothing moves."""
    v, cf = _series([100.0, 101.3, 99.7, 260.0, 247.0], [0.0, 0.0, 0.0, 150.0, 0.0])
    assert flow_netted_daily_returns(v, None).equals(v.pct_change())
    assert flow_netted_daily_returns(v, cf * 0.0).equals(v.pct_change())
    r = flow_netted_daily_returns(v, cf)
    others = [0, 1, 2, 4]
    assert r.iloc[others].equals(v.pct_change().iloc[others])
    assert r.iloc[3] != v.pct_change().iloc[3]


def test_flow_netted_returns_take_a_first_day_flow_as_the_starting_nav():
    """A flow on the first date is the starting NAV, as in the TWR; a day after a zero
    value keeps pct_change's reading rather than dividing by zero."""
    v, cf = _series([100.0, 100.0, 0.0, 50.0], [100.0, 0.0, 0.0, 50.0])
    r = flow_netted_daily_returns(v, cf)
    assert pd.isna(r.iloc[0])
    assert r.iloc[1] == 0.0
    assert r.iloc[3] == v.pct_change().iloc[3]
