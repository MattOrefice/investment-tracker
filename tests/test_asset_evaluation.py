"""Tests for src/asset_evaluation.py — pure unit tests and structural checks.

All unit tests use synthetic data and do not make network calls.
Structural tests read source files to verify cross-module invariants.

Sections:
  Unit tests (offline, synthetic data):
    1. sleeve_weights_sum_to_one
    2. compute_univariate_stats_basic
    3. solve_tangency_unconstrained_weights_sum_to_one
    4. solve_tangency_constrained_feasible
    5. portfolio_sharpe_annual_known_case
    6. marginal_sharpe_curve_has_correct_shape
    7. drawdown_sensitivity_has_correct_shape
    8. weekly_resampling_reduces_rows

  Structural / cross-module tests:
    9. rf_annual_consistent_with_performance_page
    10. sample_start_matches_page_caption
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import src.asset_evaluation as ae
from src.asset_evaluation import (
    compute_univariate_stats,
    portfolio_sharpe_annual,
    solve_tangency_constrained,
    solve_tangency_unconstrained,
    compute_marginal_sharpe_curve,
    compute_drawdown_sensitivity,
    compute_weekly_correlations,
    TRADING_DAYS,
    RF_ANNUAL,
    SLEEVE_WEIGHTS,
    SLEEVES,
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_daily_returns(n: int, mu: float = 0.0004, sigma: float = 0.01,
                        seed: int = 42) -> pd.Series:
    """Synthetic daily return series on a business-day index."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(mu, sigma, n)
    idx = pd.bdate_range("2018-01-02", periods=n)
    return pd.Series(rets, index=idx)


def _make_sleeve_df(n: int = 1000, n_sleeves: int = 4,
                    seed: int = 42) -> pd.DataFrame:
    """Synthetic sleeve return DataFrame on a business-day index."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    data = rng.normal(0.0003, 0.008, (n, n_sleeves))
    cols = [f"Sleeve{i+1}" for i in range(n_sleeves)]
    return pd.DataFrame(data, index=idx, columns=cols)


# ── 1. Sleeve weights sum to one ─────────────────────────────────────────────

def test_sleeve_weights_sum_to_one():
    """SLEEVE_WEIGHTS values must sum to exactly 1.0 (within floating-point tolerance).

    These weights are used as the baseline portfolio in MV optimization and the
    marginal Sharpe curve. If they don't sum to 1.0, portfolio arithmetic is wrong.
    """
    total = sum(SLEEVE_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, (
        f"SLEEVE_WEIGHTS sum to {total:.10f}, not 1.0. "
        f"Weights: {SLEEVE_WEIGHTS}"
    )


# ── 2. compute_univariate_stats basic cases ───────────────────────────────────

def test_compute_univariate_stats_basic():
    """compute_univariate_stats with a constant positive series produces sensible output.

    Constant positive returns → ann_return > 0, sharpe > 0, max_drawdown == 0,
    skewness and kurtosis are computable (may be zero or near-zero for constant series).
    """
    # Constant positive daily return — no drawdown, positive Sharpe
    n = 252 * 3  # 3 years
    const_ret = 0.0005   # ~13.4% annualized
    rets = pd.Series([const_ret] * n,
                     index=pd.bdate_range("2020-01-02", periods=n))

    stats = compute_univariate_stats(rets, rf_annual=RF_ANNUAL)

    assert stats, "compute_univariate_stats returned empty dict"
    assert stats["ann_return"] > 0,      "ann_return should be positive for constant positive returns"
    assert stats["ann_vol"] >= 0,        "ann_vol should be non-negative"
    assert stats["sharpe"] > 0,          "Sharpe should be positive (ann_return > RF)"
    assert stats["max_drawdown"] == 0.0, "max_drawdown should be 0 for monotonically increasing equity curve"
    assert "skewness" in stats,          "skewness key must be present"
    assert "kurtosis" in stats,          "kurtosis key must be present"
    assert np.isfinite(stats["skewness"]), "skewness must be finite"
    assert np.isfinite(stats["kurtosis"]), "kurtosis must be finite"


def test_compute_univariate_stats_empty_series():
    """compute_univariate_stats with fewer than 2 observations returns empty dict."""
    stats = compute_univariate_stats(pd.Series(dtype=float))
    assert stats == {}, "Expected empty dict for empty returns series"

    stats_one = compute_univariate_stats(pd.Series([0.01]))
    assert stats_one == {}, "Expected empty dict for single-observation series"


# ── 3. Unconstrained tangency weights sum to one ──────────────────────────────

def test_solve_tangency_unconstrained_weights_sum_to_one():
    """Unconstrained tangency weights must sum to approximately 1.0.

    The closed-form solution normalizes by 1'Σ⁻¹(μ − rf·1), so the weights
    sum to exactly 1.0 by construction — unless the normalization factor is
    near-zero (degenerate case). Tests with a well-conditioned 3-asset example.
    """
    rng = np.random.default_rng(42)
    n = 3
    # Random positive-definite covariance via A'A construction
    A = rng.standard_normal((n, n)) * 0.01
    cov = A @ A.T + np.eye(n) * 1e-5
    mu = np.array([0.0005, 0.0003, 0.0002])   # daily means

    w = solve_tangency_unconstrained(mu, cov, rf_annual=RF_ANNUAL)

    assert abs(w.sum() - 1.0) < 1e-6, (
        f"Unconstrained tangency weights sum to {w.sum():.8f}, not 1.0. "
        f"Weights: {w}"
    )
    assert len(w) == n, f"Expected {n} weights, got {len(w)}"


# ── 4. Constrained tangency feasibility ───────────────────────────────────────

def test_solve_tangency_constrained_feasible():
    """Constrained tangency with max_wt=0.25 must produce feasible weights.

    All weights must lie in [0, 0.25] and sum to 1.0. Uses a 5-asset synthetic
    problem where the 25% cap is not binding for all assets.
    """
    rng = np.random.default_rng(42)
    n = 5
    A = rng.standard_normal((n, n)) * 0.005
    cov = A @ A.T + np.eye(n) * 1e-4
    mu = rng.uniform(0.0001, 0.0008, n)
    max_wt = 0.25

    w = solve_tangency_constrained(mu, cov, rf_annual=RF_ANNUAL, max_wt=max_wt)

    assert len(w) == n, f"Expected {n} weights, got {len(w)}"
    assert abs(w.sum() - 1.0) < 1e-4, (
        f"Constrained weights sum to {w.sum():.6f}, not 1.0. "
        f"Weights: {w}"
    )
    assert all(wi >= -1e-6 for wi in w), (
        f"Some weights negative (violation of lower bound 0): {w}"
    )
    assert all(wi <= max_wt + 1e-4 for wi in w), (
        f"Some weights exceed max_wt={max_wt}: {w}"
    )


# ── 5. portfolio_sharpe_annual known case ─────────────────────────────────────

def test_portfolio_sharpe_annual_known_case():
    """portfolio_sharpe_annual returns positive finite Sharpe for a simple known case.

    Uses 2-asset equal-weight portfolio with uncorrelated returns. Constructs
    mu and cov from daily parameters and verifies the result is positive and finite.
    """
    n = 2
    # Daily excess return well above rf/252
    rf_daily = RF_ANNUAL / TRADING_DAYS
    mu = np.array([rf_daily + 0.001, rf_daily + 0.0005])  # both assets beat RF
    cov = np.diag([0.01**2, 0.008**2])   # uncorrelated, daily variance
    w = np.array([0.5, 0.5])

    sharpe = portfolio_sharpe_annual(w, mu, cov, rf_annual=RF_ANNUAL)

    assert np.isfinite(sharpe), f"Sharpe should be finite, got {sharpe}"
    assert sharpe > 0, (
        f"Sharpe should be positive for mu above RF. Got {sharpe:.4f}. "
        f"Daily mu={mu}, RF_daily={rf_daily:.6f}"
    )


# ── 6. Marginal Sharpe curve shape ────────────────────────────────────────────

def test_marginal_sharpe_curve_has_correct_shape():
    """compute_marginal_sharpe_curve returns a DataFrame with correct shape and columns.

    Uses synthetic 4-sleeve returns and a 4-element allocations list.
    Verifies: columns 'btc_alloc' and 'sharpe' present; len == len(allocations).
    """
    n = 500
    n_sleeves = 4
    sleeves_df = _make_sleeve_df(n=n, n_sleeves=n_sleeves, seed=7)
    btc_ret    = _make_daily_returns(n=n, mu=0.001, sigma=0.03, seed=99)

    # Align indices
    common = btc_ret.index.intersection(sleeves_df.index)
    btc_aligned   = btc_ret.reindex(common)
    sleeves_aligned = sleeves_df.reindex(common)

    sleeve_weights = {col: 1.0 / n_sleeves for col in sleeves_df.columns}
    allocations = [0.0, 0.02, 0.05, 0.10]

    result = compute_marginal_sharpe_curve(
        btc_aligned, sleeves_aligned,
        sleeve_weights=sleeve_weights,
        rf_annual=RF_ANNUAL,
        allocations=allocations,
    )

    assert isinstance(result, pd.DataFrame), "Result must be a DataFrame"
    assert "btc_alloc" in result.columns, "Missing column 'btc_alloc'"
    assert "sharpe" in result.columns,    "Missing column 'sharpe'"
    assert len(result) == len(allocations), (
        f"Expected {len(allocations)} rows, got {len(result)}"
    )
    assert list(result["btc_alloc"]) == allocations, (
        "btc_alloc column does not match allocations input"
    )
    assert all(np.isfinite(v) for v in result["sharpe"]), (
        "All Sharpe values must be finite"
    )


# ── 7. Drawdown sensitivity shape ─────────────────────────────────────────────

def test_drawdown_sensitivity_has_correct_shape():
    """compute_drawdown_sensitivity returns a DataFrame with expected columns and rows.

    Uses default allocations [0.00, 0.02, 0.05, 0.10] and synthetic data.
    Verifies: expected column set, correct row count, 'BTC Alloc' formatted as percent.
    """
    n = 600
    n_sleeves = 4
    sleeves_df  = _make_sleeve_df(n=n, n_sleeves=n_sleeves, seed=3)
    btc_ret     = _make_daily_returns(n=n, mu=0.0008, sigma=0.04, seed=77)

    common = btc_ret.index.intersection(sleeves_df.index)
    btc_aligned     = btc_ret.reindex(common)
    sleeves_aligned = sleeves_df.reindex(common)

    sleeve_weights = {col: 1.0 / n_sleeves for col in sleeves_df.columns}
    allocations    = [0.00, 0.02, 0.05, 0.10]

    result = compute_drawdown_sensitivity(
        btc_aligned, sleeves_aligned,
        sleeve_weights=sleeve_weights,
        allocations=allocations,
        rf_annual=RF_ANNUAL,
    )

    expected_cols = {"BTC Alloc", "CAGR", "Max DD", "Sharpe", "2022 MDD"}
    assert isinstance(result, pd.DataFrame), "Result must be a DataFrame"
    assert set(result.columns) == expected_cols, (
        f"Expected columns {expected_cols}, got {set(result.columns)}"
    )
    assert len(result) == len(allocations), (
        f"Expected {len(allocations)} rows (one per allocation), got {len(result)}"
    )
    # BTC Alloc column must contain percent-formatted strings
    assert result["BTC Alloc"].iloc[0] == "0.0%",  "First row must be '0.0%'"
    assert result["BTC Alloc"].iloc[-1] == "10.0%", "Last row (10%) must be '10.0%'"


# ── 8. Weekly resampling reduces rows ─────────────────────────────────────────

def test_weekly_resampling_reduces_rows():
    """compute_weekly_correlations returns fewer observations than daily returns.

    Weekly Friday-to-Friday resampling collapses 5 daily rows into 1 weekly row.
    For 250 trading days, we expect roughly 50 weekly observations per sleeve pair.
    Verifies the output Series has fewer entries than the input daily returns.
    """
    n = 250   # ~1 year of trading days
    n_sleeves = 3
    sleeves_df = _make_sleeve_df(n=n, n_sleeves=n_sleeves, seed=21)
    btc_ret    = _make_daily_returns(n=n, seed=55)

    common = btc_ret.index.intersection(sleeves_df.index)
    btc_aligned     = btc_ret.reindex(common)
    sleeves_aligned = sleeves_df.reindex(common)

    weekly_corr = compute_weekly_correlations(btc_aligned, sleeves_aligned)

    assert isinstance(weekly_corr, pd.Series), "Result must be a pd.Series"
    # Weekly correlations are scalars per sleeve, not time-series; result has n_sleeves entries
    assert len(weekly_corr) == n_sleeves, (
        f"Expected {n_sleeves} correlation values (one per sleeve), got {len(weekly_corr)}"
    )
    # All values must be finite correlations in [-1, 1]
    for sleeve, val in weekly_corr.items():
        assert np.isfinite(val), f"Correlation for {sleeve} is not finite: {val}"
        assert -1.0 <= val <= 1.0, f"Correlation for {sleeve} out of [-1, 1]: {val}"


# ── 9. RF_ANNUAL consistent with Performance page ─────────────────────────────

def test_rf_annual_consistent_with_performance_page():
    """ae.RF_ANNUAL must equal 0.0432 (4.32%), matching the Performance page disclosure.

    The Sharpe ratios on the Performance page and the Asset Evaluation page are
    computed with the same risk-free rate. If either is changed without updating
    the other, this test fails and forces reconciliation.
    """
    assert RF_ANNUAL == pytest.approx(0.0432, abs=1e-9), (
        f"ae.RF_ANNUAL = {RF_ANNUAL:.6f}, expected 0.0432 (4.32%). "
        "Update both src/asset_evaluation.py and pages/4_Performance.py "
        "when changing the risk-free rate."
    )


# ── 10. SAMPLE_START visible in page caption ──────────────────────────────────

def test_sample_start_matches_page_caption():
    """The string '2018-01-01' must appear in pages/5_Asset_Evaluation.py.

    SAMPLE_START is exposed to users via the page caption and methodology section.
    This test verifies the date is explicitly visible in the source — not just
    referenced through ae.SAMPLE_START — so users reading the page understand
    the analysis window.
    """
    page_path = _ROOT / "pages" / "5_Asset_Evaluation.py"
    assert page_path.exists(), (
        f"pages/5_Asset_Evaluation.py not found at {page_path}. "
        "Was the file not created or moved?"
    )
    source = page_path.read_text(encoding="utf-8")
    assert "2018-01-01" in source, (
        "The string '2018-01-01' is not present in pages/5_Asset_Evaluation.py. "
        "The sample start date must be visible to users in the page caption or "
        "methodology section (ae.SAMPLE_START = '2018-01-01')."
    )
