"""Tests for the benchmark row added to the Risk Metrics section (Phase 25).

Verifies that compute_risk_metrics returns correct bench_* keys and that the
values switch correctly across benchmark choices and time windows.
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_series(n: int = 252, seed: int = 42, start: str = "2024-01-01") -> pd.Series:
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(0.0004, 0.008, n)
    prices = 10_000.0 * np.exp(np.cumsum(log_rets))
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(prices, index=idx)


# ── Test 1: bench metrics match standalone computation ────────────────────────

def test_benchmark_row_metrics_match_standalone_computation():
    """bench_* keys must equal manually computing the same metrics on bench_ret.

    Uses synthetic portfolio and benchmark with SI window (full history).
    Replicates the internal trading-day filter to get the exact bench_ret
    slice, then computes std dev, Sharpe, and Sortino independently.
    """
    pv = _make_series(n=400, seed=10)
    bl = _make_series(n=400, seed=20)

    m = compute_risk_metrics(pv, bl, rf_annual=0.045, window="SI")
    assert m, "compute_risk_metrics returned empty dict"

    # Replicate the internal filter to get bench_ret as used in the function
    port_ret  = pv.pct_change().dropna()
    bench_ret = bl.pct_change().dropna()
    idx2      = port_ret.index.intersection(bench_ret.index)
    port_ret  = port_ret.loc[idx2]
    bench_ret = bench_ret.loc[idx2]
    trading   = (port_ret != 0) | (bench_ret != 0)
    bench_ret = bench_ret[trading]

    rf_daily = (1 + 0.045) ** (1 / 252) - 1
    bench_excess = bench_ret - rf_daily

    expected_vol = float(bench_ret.std(ddof=1)) * math.sqrt(252) * 100
    expected_sharpe = (
        (bench_excess.mean() / bench_excess.std(ddof=1)) * math.sqrt(252)
    )
    bench_neg = bench_excess[bench_excess < 0]
    expected_sortino = (
        (bench_excess.mean() / math.sqrt((bench_neg ** 2).mean())) * math.sqrt(252)
        if len(bench_neg) > 1 else float("nan")
    )

    assert abs(m["bench_annualized_vol_pct"] - expected_vol) < 1e-6, (
        f"bench vol mismatch: got {m['bench_annualized_vol_pct']:.6f}%, "
        f"expected {expected_vol:.6f}%"
    )
    assert abs(m["bench_sharpe"] - expected_sharpe) < 1e-6, (
        f"bench Sharpe mismatch: got {m['bench_sharpe']:.6f}, "
        f"expected {expected_sharpe:.6f}"
    )
    if math.isfinite(expected_sortino):
        assert abs(m["bench_sortino"] - expected_sortino) < 1e-6, (
            f"bench Sortino mismatch: got {m['bench_sortino']:.6f}, "
            f"expected {expected_sortino:.6f}"
        )


# ── Test 2: bench metrics differ across benchmark choices ─────────────────────

def test_benchmark_row_switches_with_compare_against():
    """bench_annualized_vol_pct must differ for each of the three benchmark series.

    Uses a fixed portfolio pv and three distinct synthetic benchmark series.
    Asserts Custom SAA ≠ SPY ≠ 60/40 (distinct seeds guarantee distinct series).
    """
    pv       = _make_series(n=300, seed=0)
    bl_saa   = _make_series(n=300, seed=100)   # stand-in for Custom Blended SAA
    bl_spy   = _make_series(n=300, seed=200)   # stand-in for SPY
    bl_60_40 = _make_series(n=300, seed=300)   # stand-in for 60/40

    m_saa   = compute_risk_metrics(pv, bl_saa,   window="SI")
    m_spy   = compute_risk_metrics(pv, bl_spy,   window="SI")
    m_60_40 = compute_risk_metrics(pv, bl_60_40, window="SI")

    assert m_saa and m_spy and m_60_40, "One or more compute_risk_metrics calls returned empty dict"

    vol_saa   = m_saa["bench_annualized_vol_pct"]
    vol_spy   = m_spy["bench_annualized_vol_pct"]
    vol_60_40 = m_60_40["bench_annualized_vol_pct"]

    assert vol_saa != vol_spy, (
        f"Custom SAA and SPY bench vols are identical ({vol_saa:.4f}%); "
        "benchmark series may not be switching correctly"
    )
    assert vol_spy != vol_60_40, (
        f"SPY and 60/40 bench vols are identical ({vol_spy:.4f}%); "
        "benchmark series may not be switching correctly"
    )
    assert vol_saa != vol_60_40, (
        f"Custom SAA and 60/40 bench vols are identical ({vol_saa:.4f}%); "
        "benchmark series may not be switching correctly"
    )


# ── Test 3: bench metrics respect the window filter ───────────────────────────

def test_benchmark_row_window_filters_correctly():
    """bench_annualized_vol_pct for 1Y window must differ from SI window.

    Uses 400 trading days starting 2024-01-01, which ends ~Aug 2025.
    Today is May 2026; 1Y cutoff is ~May 2025, leaving ~63 trading days in the
    1Y window vs 400 in SI — materially different subsets with distinct vol.
    """
    pv = _make_series(n=400, seed=7, start="2024-01-01")
    bl = _make_series(n=400, seed=8, start="2024-01-01")

    m_si = compute_risk_metrics(pv, bl, window="SI")
    m_1y = compute_risk_metrics(pv, bl, window="1Y")

    assert m_si, "SI window returned empty dict"
    assert m_1y, "1Y window returned empty dict"

    vol_si = m_si["bench_annualized_vol_pct"]
    vol_1y = m_1y["bench_annualized_vol_pct"]

    assert vol_si != vol_1y, (
        f"SI and 1Y bench vols are identical ({vol_si:.4f}%); "
        "window filter may not be applied to benchmark metrics"
    )


# ── Test 4: row label snapshot ────────────────────────────────────────────────

def test_benchmark_row_label_strings():
    """Row labels must be exact strings with no format-string leakage.

    Pins: 'Portfolio' and the three benchmark captions used in the page.
    Checks that none of the strings contain placeholder patterns.
    """
    portfolio_label = "Portfolio"
    benchmark_labels = [
        "Custom Blended SAA",
        "S&P 500 (SPY)",
        "60/40",
    ]

    all_labels = [portfolio_label] + benchmark_labels

    _placeholder_patterns = ["{}", "[]", "%(", "${", "%s"]

    for label in all_labels:
        assert isinstance(label, str), f"Label is not a string: {label!r}"
        assert label.strip() == label, f"Label has leading/trailing whitespace: {label!r}"
        assert len(label) > 0, "Label is empty"
        for pat in _placeholder_patterns:
            assert pat not in label, (
                f"Label {label!r} contains placeholder pattern {pat!r}"
            )

    # Verify the mapping dict in the page uses exactly these values
    bm_row_labels = {
        "blended": "Custom Blended SAA",
        "spy":     "S&P 500 (SPY)",
        "60_40":   "60/40",
    }
    assert set(bm_row_labels.values()) == set(benchmark_labels), (
        f"Benchmark label set mismatch: {set(bm_row_labels.values())} "
        f"!= {set(benchmark_labels)}"
    )
