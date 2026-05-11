"""Tests for src/macro.py — CAPE implied return formula, ECY, FRED retry, window_pctile."""
import math
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.macro import (
    compute_cape_implied_return, compute_ecy, percentile, window_pctile,
    classify_regime, _REGIME_LABELS, format_ur_delta,
)


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


# ── window_pctile unit tests ───────────────────────────────────────────────────

def _synthetic_series(n: int = 300, seed: int = 0) -> pd.Series:
    rng  = np.random.default_rng(seed)
    vals = rng.normal(0, 1, n)
    idx  = pd.date_range("2015-01-01", periods=n, freq="ME")
    return pd.Series(vals, index=idx)


def test_window_pctile_max_uses_full_series():
    """'1800-01-01' sentinel must use the entire series, same as percentile()."""
    s   = _synthetic_series()
    val = float(s.iloc[-1])
    assert abs(window_pctile(s, val, "1800-01-01") - percentile(s, val)) < 1e-9


def test_window_pctile_5y_differs_from_full():
    """5Y window percentile must differ from full-history percentile for a trending series."""
    # Monotonically increasing series: last 5 years cluster at the top
    idx = pd.date_range("2010-01-01", periods=180, freq="ME")
    s   = pd.Series(range(180), index=idx, dtype=float)
    val = float(s.iloc[-1])          # maximum value

    full_pctile = percentile(s, val)          # should be ~100
    w5_start    = (idx[-1] - pd.DateOffset(years=5)).isoformat()[:10]
    w5_pctile   = window_pctile(s, val, w5_start)   # also ~100 since val is still max

    # Both should be 100 for the absolute maximum
    assert full_pctile == pytest.approx(100.0, abs=1.0)
    assert w5_pctile   == pytest.approx(100.0, abs=1.0)

    # Now test a mid-range value — windowed pctile should differ from full
    mid_val     = float(s.iloc[150])          # 150/180 = 83rd percentile full history
    full_mid    = percentile(s, mid_val)
    w5_mid      = window_pctile(s, mid_val, w5_start)
    # The 5Y window only has values 120..179, so mid_val (150) is below the median
    # of the 5Y window (median ~149.5) — roughly the 50th percentile of that window
    assert w5_mid < full_mid, (
        f"5Y window pctile ({w5_mid:.1f}) should be lower than full ({full_mid:.1f}) "
        "for a value that's mid-history in a monotonically increasing series"
    )


def test_window_pctile_10y_uses_only_windowed_data():
    """10Y window percentile is computed strictly on data from w_start onward."""
    idx = pd.date_range("2005-01-01", periods=240, freq="ME")
    s   = pd.Series(range(240), index=idx, dtype=float)

    w10_start = (idx[-1] - pd.DateOffset(years=10)).isoformat()[:10]
    val       = float(s.loc[w10_start:].median())
    w10_pctile = window_pctile(s, val, w10_start)

    # Median of the 10Y window = 50th percentile of that window
    assert 48.0 < w10_pctile < 52.0, f"Median should be near 50th pctile; got {w10_pctile:.1f}"


def test_window_pctile_empty_window_falls_back_to_full():
    """If window start is after all data, falls back to full-series percentile."""
    idx = pd.date_range("2010-01-01", periods=60, freq="ME")
    s   = pd.Series(np.linspace(1, 60, 60), index=idx)
    val = float(s.iloc[30])

    # Window starting after end of data
    future_start = "2030-01-01"
    assert window_pctile(s, val, future_start) == pytest.approx(percentile(s, val), abs=1e-9)


def test_window_pctile_5y_matches_manual_slice():
    """window_pctile must exactly equal percentile(series[w_start:].dropna(), val)."""
    s         = _synthetic_series(n=120, seed=7)
    w5_start  = (s.index[-1] - pd.DateOffset(years=5)).isoformat()[:10]
    val       = float(s.iloc[-1])
    expected  = percentile(s.loc[w5_start:].dropna(), val)
    assert window_pctile(s, val, w5_start) == pytest.approx(expected, abs=1e-9)


def test_window_pctile_10y_matches_manual_slice():
    """window_pctile 10Y window must equal percentile(series[w_start:].dropna(), val)."""
    s         = _synthetic_series(n=240, seed=13)
    w10_start = (s.index[-1] - pd.DateOffset(years=10)).isoformat()[:10]
    val       = float(s.quantile(0.75))
    expected  = percentile(s.loc[w10_start:].dropna(), val)
    assert window_pctile(s, val, w10_start) == pytest.approx(expected, abs=1e-9)


# ── classify_regime unit tests (Layer 1: no None gaps) ───────────────────────

@pytest.mark.parametrize("usrec,t10y2y,unrate,expected", [
    # Rule 1: Recession when USREC = 1
    (1.0,  1.0,  6.0, "Recession"),
    (1.0, -0.5,  4.0, "Recession"),
    (1.0,  None, None, "Recession"),
    # Rule 2: Early-cycle — USREC=0, UNRATE > 5.5, curve not deeply inverted
    (0.0,  0.5,  7.0, "Early-cycle"),
    (0.0,  0.0,  6.0, "Early-cycle"),
    (0.0,  None, 8.0, "Early-cycle"),
    # Rule 3a: Late-cycle — curve inverted
    (0.0, -0.5,  4.5, "Late-cycle"),
    (0.0, -0.3,  5.0, "Late-cycle"),
    # Rule 3b: Late-cycle — labor very tight
    (0.0,  0.5,  3.9, "Late-cycle"),
    (0.0,  None, 4.0, "Late-cycle"),
    # Rule 4: Mid-cycle default
    (0.0,  1.0,  4.5, "Mid-cycle"),
    (0.0,  0.0,  5.0, "Mid-cycle"),
    (None, None, None, "Mid-cycle"),   # all signals missing → default
])
def test_classify_regime_rules(usrec, t10y2y, unrate, expected):
    """classify_regime must return the correct label for each signal combination."""
    assert classify_regime(usrec, t10y2y, unrate) == expected


def test_classify_regime_always_returns_valid_label():
    """classify_regime must always return one of the four valid labels."""
    import itertools
    usrec_vals  = [None, 0.0, 1.0]
    t10y2y_vals = [None, -1.0, -0.3, -0.1, 0.0, 0.5, 2.0]
    unrate_vals = [None, 3.5, 4.0, 4.2, 5.0, 5.5, 6.0, 8.0]
    for u, t, r in itertools.product(usrec_vals, t10y2y_vals, unrate_vals):
        label = classify_regime(u, t, r)
        assert label in _REGIME_LABELS, (
            f"classify_regime({u}, {t}, {r}) = {label!r} not in {_REGIME_LABELS}"
        )


def test_classify_regime_recession_overrides_all():
    """USREC = 1 must produce 'Recession' regardless of other signal values."""
    combos = [
        (1.0,  2.0, 3.5),   # would otherwise be Late-cycle (tight labor)
        (1.0, -1.0, 7.0),   # would otherwise be Late-cycle (curve inverted)
        (1.0,  0.0, 5.2),   # would otherwise be Mid-cycle
        (1.0,  1.0, 8.0),   # would otherwise be Early-cycle
    ]
    for u, t, r in combos:
        assert classify_regime(u, t, r) == "Recession", (
            f"Expected 'Recession' for usrec={u}, t10y2y={t}, unrate={r}"
        )


def test_classify_regime_backtest_has_no_gaps():
    """
    Layer 1: synthetic monthly backtest grid must produce a valid label for every row.
    Uses a synthetic 40-year grid of plausible signal combinations — no FRED calls.
    """
    rng = np.random.default_rng(42)
    n   = 480   # 40 years of monthly data
    usrec_sim  = rng.choice([0.0, 1.0], n, p=[0.85, 0.15])
    t10y2y_sim = rng.normal(0.5, 1.2, n)         # mean slightly positive
    unrate_sim = rng.uniform(3.0, 10.0, n)

    labels = [
        classify_regime(float(usrec_sim[i]), float(t10y2y_sim[i]), float(unrate_sim[i]))
        for i in range(n)
    ]

    assert len(labels) == n, "Backtest produced fewer labels than input rows"
    assert all(lbl in _REGIME_LABELS for lbl in labels), (
        f"Backtest produced invalid label(s): {set(labels) - set(_REGIME_LABELS)}"
    )
    assert None not in labels, "Backtest produced None labels (gaps)"


# ── format_ur_delta ────────────────────────────────────────────────────────

def test_format_ur_delta_zero_is_flat():
    assert format_ur_delta(0.0) == "Flat vs one year ago"

def test_format_ur_delta_near_zero_rounds_flat():
    # 0.4 bps rounds to 0 → still Flat
    assert format_ur_delta(0.4) == "Flat vs one year ago"
    assert format_ur_delta(-0.4) == "Flat vs one year ago"

def test_format_ur_delta_positive():
    assert format_ur_delta(20.0) == "+20 bps from one year ago"

def test_format_ur_delta_negative():
    assert format_ur_delta(-30.0) == "-30 bps from one year ago"
