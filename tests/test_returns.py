"""Tests for src/returns.py TWR calculations."""
import sys
import pathlib

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.returns import twr_daily_linked, twr_modified_dietz, annualize, period_return


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
    Day 1: V=160, CF=50  ($50 deposit at start of day; market ends at 160)
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

def test_gips_multiperiod_chain_link():
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
