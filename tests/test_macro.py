"""Tests for src/macro.py — CAPE implied return formula."""
import math
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.macro import compute_cape_implied_return


def test_cape_16_anchor():
    """CAPE=16 is the formula's calibration anchor — implied return = exactly 6.6%."""
    result = compute_cape_implied_return(16.0)
    assert abs(result - 0.066) < 0.001, f"Expected ~6.6%, got {result:.4%}"


def test_cape_25_midrange():
    """CAPE=25 is mid-range; result must match the log-linear formula exactly."""
    expected = -0.070 * math.log(25 / 16) + 0.066
    result   = compute_cape_implied_return(25.0)
    assert abs(result - expected) < 1e-9


def test_cape_30_8_current_is_positive():
    """CAPE=30.8 (current market) must give a POSITIVE return (~+2.0%), not -2.0%."""
    result = compute_cape_implied_return(30.8)
    assert result > 0, (
        f"Implied return at CAPE=30.8 must be positive; got {result:.4%}. "
        "A negative value indicates a sign error in the formula."
    )
    assert abs(result - 0.020) < 0.005, f"Expected ~+2.0%, got {result:.4%}"


def test_cape_44_elevated_is_negative():
    """CAPE=44 (2000-era level) must give a NEGATIVE implied return."""
    result = compute_cape_implied_return(44.0)
    assert result < 0, f"Implied return at CAPE=44 must be negative; got {result:.4%}"


def test_cape_monotonically_decreasing():
    """Higher CAPE must always produce lower implied forward return."""
    capes   = [10, 16, 20, 25, 30, 35, 40, 50]
    returns = [compute_cape_implied_return(c) for c in capes]
    for i in range(len(returns) - 1):
        assert returns[i] > returns[i + 1], (
            f"Monotonicity failed: CAPE={capes[i]} gives {returns[i]:.4%} "
            f"but CAPE={capes[i+1]} gives {returns[i+1]:.4%}"
        )
