"""Tests for src/macro.py — CAPE implied return formula, ECY, and FRED retry."""
import math
import sys
import pathlib

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.macro import compute_cape_implied_return, compute_ecy


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


# ── ECY tests ─────────────────────────────────────────────────────────────────

def test_ecy_positive():
    """CAPE=25, DGS10=3.0%, T10YIE=2.0% → ECY = 100/25 − (3.0−2.0) = 4.0 − 1.0 = 3.0%"""
    assert abs(compute_ecy(25.0, 3.0, 2.0) - 3.0) < 1e-9


def test_ecy_negative():
    """CAPE=40, DGS10=6.0%, T10YIE=2.0% → ECY = 100/40 − (6.0−2.0) = 2.5 − 4.0 = −1.5%"""
    assert abs(compute_ecy(40.0, 6.0, 2.0) - (-1.5)) < 1e-9


def test_ecy_zero():
    """ECY = 0 when CAPE earnings yield exactly equals real bond rate."""
    # CAPE=25 → earnings yield 4.0%; T10Y=6.0%, T10YIE=2.0% → real rate 4.0%
    assert abs(compute_ecy(25.0, 6.0, 2.0) - 0.0) < 1e-9


def test_ecy_higher_cape_lower_value():
    """Higher CAPE reduces the earnings yield, lowering ECY all else equal."""
    ecy_low_cape  = compute_ecy(20.0, 4.0, 2.0)   # earnings yield 5% → ECY 3%
    ecy_high_cape = compute_ecy(40.0, 4.0, 2.0)   # earnings yield 2.5% → ECY 0.5%
    assert ecy_low_cape > ecy_high_cape


# ── FRED retry tests ──────────────────────────────────────────────────────────

def test_fetch_fred_retries_on_transient_failure(monkeypatch):
    """fetch_fred_series retries on transient failure and succeeds on second attempt."""
    import src.macro as _m

    call_count = [0]

    class MockFred:
        def get_series(self, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Internal Server Error")
            return pd.Series([1.0], index=pd.to_datetime(["2020-01-01"]))

    monkeypatch.setattr(_m, "_get_fred", lambda: MockFred())
    monkeypatch.setattr(_m, "_FRED_RETRY_DELAYS", (0, 0, 0))

    result = _m.fetch_fred_series("DFF", "2020-01-01")
    assert call_count[0] == 2
    assert isinstance(result, pd.Series)
    assert len(result) == 1


def test_fetch_fred_raises_after_all_retries(monkeypatch):
    """fetch_fred_series raises FREDFetchError after all attempts are exhausted."""
    import src.macro as _m
    from src.macro import FREDFetchError

    class MockFred:
        def get_series(self, *args, **kwargs):
            raise RuntimeError("Internal Server Error")

    monkeypatch.setattr(_m, "_get_fred", lambda: MockFred())
    monkeypatch.setattr(_m, "_FRED_RETRY_DELAYS", (0, 0, 0))

    with pytest.raises(FREDFetchError) as exc_info:
        _m.fetch_fred_series("T10Y2Y", "2020-01-01")
    assert "T10Y2Y" in str(exc_info.value)


def test_fredetcherror_carries_series_id():
    """FREDFetchError.series_id attribute must equal the requested series."""
    from src.macro import FREDFetchError
    cause = ValueError("network timeout")
    err   = FREDFetchError("DGS10", cause)
    assert err.series_id == "DGS10"
    assert err.cause is cause
    assert "DGS10" in str(err)
