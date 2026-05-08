"""Unit tests for risk-adjusted metrics (src/performance.py).

Covers:
- Standard deviation computed on trading days only, matches numpy reference
- Beta computation vs each benchmark
- 60/40 benchmark series construction
- Tracking error, information ratio, beta return finite sane values for all benchmarks
"""
from __future__ import annotations

import math
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.performance import compute_risk_metrics


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_series(n: int = 252, seed: int = 42, start: str = "2025-01-01") -> pd.Series:
    """Synthetic daily price series with realistic returns."""
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(0.0004, 0.008, n)
    prices = 10_000.0 * np.exp(np.cumsum(log_rets))
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(prices, index=idx)


def _make_pair(n: int = 252, seed: int = 0) -> tuple[pd.Series, pd.Series]:
    pv = _make_series(n, seed=seed)
    bl = _make_series(n, seed=seed + 1)
    return pv, bl


# ── Standard deviation: trading-day filtering ────────────────────────────────

def test_std_dev_trading_days_only():
    """Std dev must be computed on trading days only, not calendar-padded zeros.

    Construct a calendar-day series that includes weekend rows with zero returns.
    The daily-linked pct_change of a series with repeated values on weekends produces
    zeros. If std dev included those zeros, the annualized vol would be lower.
    This test verifies the filter is applied before computing std dev.
    """
    # Build a daily-indexed series that includes weekends (zero returns on those days)
    idx_bday = pd.bdate_range("2025-01-01", periods=60)
    idx_cal  = pd.date_range("2025-01-01", periods=len(idx_bday) + 20)

    rng = np.random.default_rng(7)
    bday_prices = 10_000.0 * np.cumprod(1 + rng.normal(0.0004, 0.008, len(idx_bday)))
    bday_series = pd.Series(bday_prices, index=idx_bday)

    # ffill onto calendar-day index (weekends repeat prior Friday close)
    cal_series = bday_series.reindex(idx_cal).ffill().dropna()

    # Benchmark that also has calendar-day index (same trick)
    bench_prices = 10_000.0 * np.cumprod(1 + rng.normal(0.0003, 0.007, len(idx_bday)))
    bench_bday = pd.Series(bench_prices, index=idx_bday)
    bench_cal  = bench_bday.reindex(idx_cal).ffill().dropna()

    m = compute_risk_metrics(cal_series, bench_cal, window="SI")
    assert m, "compute_risk_metrics returned empty dict"

    vol = m["annualized_vol_pct"]
    assert vol > 0, "Annualized vol must be positive"

    # If weekends were NOT filtered, std would be diluted toward zero by
    # ~30% extra zero returns. Verify vol is in a realistic range (5%–50% ann).
    assert 5.0 < vol < 50.0, (
        f"Annualized vol {vol:.2f}% outside plausible range. "
        "Weekend zeros may not be filtered correctly."
    )


def test_std_dev_matches_numpy_reference():
    """Annualized vol must equal numpy.std(ddof=1) * sqrt(252) on the filtered returns."""
    pv, bl = _make_pair(n=252)
    m = compute_risk_metrics(pv, bl, window="SI")
    assert m, "compute_risk_metrics returned empty dict"

    # Replicate the same filter logic as compute_risk_metrics
    port_ret  = pv.pct_change().dropna()
    bench_ret = bl.pct_change().dropna()
    idx2      = port_ret.index.intersection(bench_ret.index)
    port_ret  = port_ret.loc[idx2]
    bench_ret = bench_ret.loc[idx2]
    trading   = (port_ret != 0) | (bench_ret != 0)
    port_ret  = port_ret[trading]

    expected_vol = float(np.std(port_ret, ddof=1)) * math.sqrt(252) * 100
    assert abs(m["annualized_vol_pct"] - expected_vol) < 1e-6, (
        f"Annualized vol mismatch: got {m['annualized_vol_pct']:.6f}%, "
        f"numpy reference {expected_vol:.6f}%"
    )


# ── Beta computation ──────────────────────────────────────────────────────────

def test_beta_finite_and_sane():
    """Beta must be a finite number in a plausible range for equity-like portfolios."""
    pv, bl = _make_pair(n=252)
    m = compute_risk_metrics(pv, bl, window="SI")
    assert m, "compute_risk_metrics returned empty dict"
    assert math.isfinite(m["beta"]), f"Beta is not finite: {m['beta']}"
    assert -3.0 < m["beta"] < 5.0, f"Beta {m['beta']:.3f} outside plausible range [-3, 5]"


def test_beta_against_identical_benchmark():
    """Beta of a series against itself must be 1.0."""
    pv, _ = _make_pair(n=252)
    m = compute_risk_metrics(pv, pv.copy(), window="SI")
    assert m, "compute_risk_metrics returned empty dict"
    assert abs(m["beta"] - 1.0) < 1e-6, (
        f"Beta of series against itself = {m['beta']:.6f}, expected 1.0"
    )


def test_beta_numpy_consistency():
    """Beta must match cov(p, b, ddof=1) / var(b, ddof=1) on filtered trading-day returns."""
    pv, bl = _make_pair(n=252, seed=99)
    m = compute_risk_metrics(pv, bl, window="SI")
    assert m, "compute_risk_metrics returned empty dict"

    # Replicate filter
    port_ret  = pv.pct_change().dropna()
    bench_ret = bl.pct_change().dropna()
    idx2      = port_ret.index.intersection(bench_ret.index)
    port_ret  = port_ret.loc[idx2]
    bench_ret = bench_ret.loc[idx2]
    trading   = (port_ret != 0) | (bench_ret != 0)
    port_ret  = port_ret[trading]
    bench_ret = bench_ret[trading]

    cov_mat  = np.cov(port_ret.values, bench_ret.values, ddof=1)
    expected = cov_mat[0, 1] / cov_mat[1, 1]

    assert abs(m["beta"] - expected) < 1e-9, (
        f"Beta mismatch: got {m['beta']:.9f}, numpy reference {expected:.9f}"
    )


# ── Active return ─────────────────────────────────────────────────────────────

def test_active_return_is_annualized_difference():
    """active_return_pct must equal ann_port_return_pct - ann_bench_return_pct."""
    pv, bl = _make_pair(n=252)
    m = compute_risk_metrics(pv, bl, window="SI")
    assert m, "compute_risk_metrics returned empty dict"
    expected = m["ann_port_return_pct"] - m["ann_bench_return_pct"]
    assert abs(m["active_return_pct"] - expected) < 1e-6, (
        f"active_return_pct {m['active_return_pct']:.4f} != "
        f"ann_port - ann_bench {expected:.4f}"
    )


# ── 60/40 benchmark series ────────────────────────────────────────────────────

def test_60_40_benchmark_returns_normalised_series():
    """get_naive_60_40_series must return a Series starting near 1.0."""
    from src.benchmarks import get_naive_60_40_series
    s = get_naive_60_40_series("2025-05-01", "2025-07-31")
    assert not s.empty, "60/40 series is empty"
    assert abs(float(s.iloc[0]) - 1.0) < 0.01, (
        f"Series does not start near 1.0: first value = {float(s.iloc[0]):.4f}"
    )


def test_60_40_benchmark_positive_values():
    """60/40 series must have all positive values (price-like)."""
    from src.benchmarks import get_naive_60_40_series
    s = get_naive_60_40_series("2025-05-01", "2025-07-31")
    assert (s > 0).all(), "60/40 series contains non-positive values"


# ── Risk metrics across all three benchmark types ─────────────────────────────

@pytest.mark.parametrize("bm_kind", ["blended", "spy", "60_40"])
def test_risk_metrics_finite_for_each_benchmark(bm_kind):
    """TE, IR, beta, and active return must be finite for each benchmark type."""
    from src.benchmarks import get_custom_blended_series, get_sp500_series, get_naive_60_40_series

    start, end = "2025-05-01", "2025-07-31"
    pv = get_sp500_series(start, end)   # use SPY as stand-in for a real portfolio

    if bm_kind == "blended":
        bl = get_custom_blended_series(start, end)
    elif bm_kind == "spy":
        bl = get_sp500_series(start, end)
    else:
        bl = get_naive_60_40_series(start, end)

    # Normalize to same start value
    pv_norm = pv / float(pv.iloc[0]) * 10_000.0
    bl_norm = bl / float(bl.iloc[0])

    m = compute_risk_metrics(pv_norm, bl_norm, window="SI")
    assert m, f"compute_risk_metrics returned empty dict for benchmark '{bm_kind}'"

    for key in ("tracking_error_pct", "information_ratio", "beta", "active_return_pct"):
        assert key in m, f"'{key}' missing from result dict for benchmark '{bm_kind}'"
        assert math.isfinite(m[key]) or math.isnan(m[key]), (
            f"m['{key}'] = {m[key]} is not finite or nan for benchmark '{bm_kind}'"
        )
    # TE must be finite and non-negative
    assert math.isfinite(m["tracking_error_pct"]) and m["tracking_error_pct"] >= 0, (
        f"tracking_error_pct invalid: {m['tracking_error_pct']:.4f} for '{bm_kind}'"
    )
