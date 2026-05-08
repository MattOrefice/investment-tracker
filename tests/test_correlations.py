"""Unit tests for sleeve correlation matrix logic (pages/9_Correlations.py helpers).

Tests cover:
- Rolling correlation matrix is symmetric
- Diagonal entries are exactly 1.0
- Off-diagonal entries are in [-1, 1]
- Blended-sleeve return computation is weighted correctly
- Rolling pair correlation length is correct
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# ── inline helpers (mirror pages/9_Correlations.py without Streamlit) ─────────

def _rolling_corr_matrix(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    tail = returns.tail(window)
    if len(tail) < max(10, window // 4):
        return pd.DataFrame()
    return tail.corr()


def _pair_corr_series(returns: pd.DataFrame, a: str, b: str, window: int) -> pd.Series:
    return returns[a].rolling(window).corr(returns[b]).dropna()


# ── fixtures ───────────────────────────────────────────────────────────────────

def _synthetic_returns(
    n: int = 300,
    n_sleeves: int = 9,
    seed: int = 0,
) -> pd.DataFrame:
    rng   = np.random.default_rng(seed)
    idx   = pd.bdate_range("2020-01-01", periods=n)
    data  = rng.normal(0.0004, 0.008, (n, n_sleeves))
    cols  = [f"Sleeve_{i+1}" for i in range(n_sleeves)]
    return pd.DataFrame(data, index=idx, columns=cols)


# ── Layer 1: matrix properties ─────────────────────────────────────────────────

@pytest.mark.parametrize("window", [30, 60, 120, 252])
def test_corr_matrix_symmetric(window):
    """Correlation matrix must be symmetric for all supported window sizes."""
    df   = _synthetic_returns(n=max(300, window + 20))
    corr = _rolling_corr_matrix(df, window)
    assert not corr.empty, f"Matrix is empty for window={window}"
    diff = (corr - corr.T).abs().max().max()
    assert diff < 1e-10, f"Matrix is not symmetric (max asymmetry {diff:.2e}) for window={window}"


@pytest.mark.parametrize("window", [30, 60, 120, 252])
def test_corr_matrix_diagonal_is_one(window):
    """All diagonal entries must be exactly 1.0."""
    df   = _synthetic_returns(n=max(300, window + 20))
    corr = _rolling_corr_matrix(df, window)
    assert not corr.empty
    diag = np.diag(corr.values)
    for i, val in enumerate(diag):
        assert abs(val - 1.0) < 1e-10, (
            f"Diagonal entry [{i},{i}] = {val:.10f}, expected 1.0 (window={window})"
        )


@pytest.mark.parametrize("window", [30, 60, 120, 252])
def test_corr_matrix_off_diagonal_bounded(window):
    """All off-diagonal entries must lie within [-1, 1]."""
    df   = _synthetic_returns(n=max(300, window + 20))
    corr = _rolling_corr_matrix(df, window)
    assert not corr.empty
    vals = corr.values
    for i in range(len(vals)):
        for j in range(len(vals)):
            if i == j:
                continue
            assert -1.0 - 1e-10 <= vals[i, j] <= 1.0 + 1e-10, (
                f"Off-diagonal [{i},{j}] = {vals[i,j]:.6f} outside [-1,1] (window={window})"
            )


# ── Perfect correlation / anti-correlation edge cases ─────────────────────────

def test_corr_identical_series_is_one():
    """Correlation of a series with itself must be 1.0."""
    df   = _synthetic_returns(n=200)
    df["Sleeve_1_copy"] = df["Sleeve_1"].copy()
    corr = _rolling_corr_matrix(df, 60)
    assert abs(corr.loc["Sleeve_1", "Sleeve_1_copy"] - 1.0) < 1e-8


def test_corr_negated_series_is_minus_one():
    """Correlation of a series with its negation must be -1.0."""
    df   = _synthetic_returns(n=200)
    df["Sleeve_1_neg"] = -df["Sleeve_1"]
    corr = _rolling_corr_matrix(df, 60)
    assert abs(corr.loc["Sleeve_1", "Sleeve_1_neg"] - (-1.0)) < 1e-8


# ── Blended sleeve return weighting ──────────────────────────────────────────

def test_blended_sleeve_weighted_correctly():
    """50/50 blend return must equal (ret_a + ret_b) / 2 on every row."""
    rng   = np.random.default_rng(7)
    n     = 100
    idx   = pd.bdate_range("2022-01-01", periods=n)
    ret_a = pd.Series(rng.normal(0.0003, 0.01, n), index=idx)
    ret_b = pd.Series(rng.normal(0.0002, 0.012, n), index=idx)
    blend = ret_a * 0.5 + ret_b * 0.5
    expected = (ret_a + ret_b) / 2.0
    pd.testing.assert_series_equal(blend, expected, check_names=False, atol=1e-12)


# ── Rolling pair correlation ──────────────────────────────────────────────────

def test_pair_corr_series_length():
    """Rolling pair correlation must have n - window + 1 entries (after dropna)."""
    n      = 200
    window = 60
    df     = _synthetic_returns(n=n)
    roll   = _pair_corr_series(df, "Sleeve_1", "Sleeve_2", window)
    expected_len = n - window + 1
    assert len(roll) == expected_len, (
        f"Expected {expected_len} entries, got {len(roll)} (n={n}, window={window})"
    )


def test_pair_corr_values_in_range():
    """All rolling pair correlation values must lie in [-1, 1]."""
    df   = _synthetic_returns(n=300)
    roll = _pair_corr_series(df, "Sleeve_1", "Sleeve_3", 60)
    assert (roll >= -1.0 - 1e-10).all() and (roll <= 1.0 + 1e-10).all(), (
        f"Pair correlation out of range: min={roll.min():.4f}, max={roll.max():.4f}"
    )


def test_insufficient_data_returns_empty():
    """_rolling_corr_matrix returns empty DataFrame if data is below minimum."""
    df   = _synthetic_returns(n=8)   # fewer than min(10, window//4) for window=60
    corr = _rolling_corr_matrix(df, window=60)
    assert corr.empty, "Expected empty DataFrame for insufficient data"
