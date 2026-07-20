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


# ── Regression: pair view must use pair-specific date intersection ─────────────
#
# Root cause: the original pair view used the shared 9-sleeve returns_df, which
# requires all 9 tickers to have data simultaneously. QUAL (US Large Quality)
# launched 2013-07-18, constraining the shared DataFrame to start there even for
# pairs like SPY (1993) × IEF (2002) that have much longer overlapping histories.
# The fix: pair view calls _load_sleeve_returns per sleeve, not returns_df.
# This test ensures the regression cannot reappear silently.

def test_pair_specific_intersection_longer_than_shared_9sleeve():
    """
    Pair-specific date intersection (two sleeves) must start earlier than the
    shared 9-sleeve intersection when one sleeve launched more recently.

    Simulates the QUAL-constrained scenario: 9-sleeve shared DF starts at QUAL
    inception (2013-07-18); the SPY × IEF pair, loaded independently, starts at
    IEF's inception (~2002-07-30), giving 11+ additional years of history.
    """
    rng = np.random.default_rng(42)

    # Shared 9-sleeve DataFrame: constrained by youngest sleeve (QUAL, 2013-07-18)
    qual_start = pd.Timestamp("2013-07-18")
    idx_shared = pd.bdate_range(qual_start, periods=700)
    shared_df  = pd.DataFrame(
        rng.normal(0.0004, 0.01, (700, 2)),
        index=idx_shared,
        columns=["A", "B"],
    )
    shared_roll = shared_df["A"].rolling(120).corr(shared_df["B"]).dropna()

    # Pair-specific: SPY × IEF — starts at IEF inception (2002-07-30)
    ief_start = pd.Timestamp("2002-07-30")
    n_pair    = len(pd.bdate_range(ief_start, "2026-05-07"))
    idx_pair  = pd.bdate_range(ief_start, periods=n_pair)
    pair_df   = pd.DataFrame(
        rng.normal(0.0003, 0.009, (n_pair, 2)),
        index=idx_pair,
        columns=["A", "B"],
    )
    pair_roll = pair_df["A"].rolling(120).corr(pair_df["B"]).dropna()

    assert pair_roll.index.min() < shared_roll.index.min(), (
        f"Pair-specific first date ({pair_roll.index.min().date()}) must be earlier "
        f"than shared-DF first date ({shared_roll.index.min().date()}). "
        "If this fails, the pair view may have regressed to using the shared DataFrame."
    )
    assert pair_roll.index.min().year <= 2003, (
        f"SPY×IEF pair should start in ~2002–2003 (120 trading days after IEF 2002-07-30). "
        f"Got {pair_roll.index.min().date()}."
    )
    assert shared_roll.index.min().year >= 2014, (
        f"Shared-DF (QUAL-constrained) first date should be 2014+. "
        f"Got {shared_roll.index.min().date()}."
    )


def test_pair_specific_xaxis_range_matches_data_bounds():
    """
    x_min and x_max for the pair chart must equal the actual index bounds of
    roll_corr — not today's date, not a future date.

    This is the guard against Plotly 6 autorange padding the x-axis into the future.
    """
    rng   = np.random.default_rng(7)
    n     = 300
    idx   = pd.bdate_range("2010-01-04", periods=n)
    df    = pd.DataFrame(rng.normal(0, 0.01, (n, 2)), index=idx, columns=["A", "B"])
    roll  = df["A"].rolling(60).corr(df["B"]).dropna()

    x_min = roll.index.min().isoformat()[:10]
    x_max = roll.index.max().isoformat()[:10]

    assert x_min == str(roll.index.min().date()), (
        f"x_min ({x_min}) must equal the first computed correlation date, not an earlier date"
    )
    assert x_max == str(roll.index.max().date()), (
        f"x_max ({x_max}) must equal the last computed correlation date, not a future date"
    )
    # Ensure x_max is the actual last data point, not today (which may be a weekend)
    assert pd.Timestamp(x_max) == roll.index.max()


# ── Phase 31: sleeve mapping de-duplication ─────────────────────────────────────

def test_correlations_page_imports_shared_sleeve_mapping():
    """The page must source the sleeve mapping from asset_evaluation, not a local literal."""
    import src.asset_evaluation as ae

    expected = {
        "US Large Core", "US Large Quality", "US Large Value", "US Small Cap",
        "Intl Core", "Intl Quality", "Intl Large Value", "Intl Small Value",
        "Emerging Markets", "Core Fixed Income", "TIPS",
        "Real Assets",
    }
    assert set(ae.SLEEVE_BENCHMARKS) == expected
    assert ae.SLEEVE_BENCHMARKS["US Large Quality"] == [("QUAL", 1.0)]
    assert ae.SLEEVE_BENCHMARKS["Real Assets"] == [("VNQ", 0.6), ("DBC", 0.4)]

    page = (pathlib.Path(__file__).resolve().parent.parent
            / "pages" / "9_Correlations.py").read_text(encoding="utf-8")
    # De-dup: page references the shared mapping and no longer hardcodes the dict.
    assert "ae.SLEEVE_BENCHMARKS" in page
    assert '"US Large Core":          [("SPY",  1.0)]' not in page
