"""Tests for src/performance.py — VaR, CVaR, and risk metrics."""
import sys
import pathlib
import math

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.performance import compute_risk_metrics


def _make_series(returns: np.ndarray, start: str = "2025-01-01") -> pd.Series:
    """Build a portfolio value series from a daily return array."""
    dates = pd.bdate_range(start, periods=len(returns))
    values = 10_000 * np.cumprod(1 + returns)
    return pd.Series(values, index=dates)


# ── VaR and CVaR unit tests ───────────────────────────────────────────────────

def test_var_95_positive_loss():
    """VaR(95%) must be a positive number (loss, not return)."""
    rng = np.random.default_rng(1)
    returns = rng.normal(0.0005, 0.01, 100)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(100))

    m = compute_risk_metrics(pv, bl)
    assert "var_95_pct" in m
    assert m["var_95_pct"] > 0, "VaR must be a positive loss magnitude"


def test_cvar_ge_var():
    """CVaR(95%) must be ≥ VaR(95%) — CVaR is the average of worst tail losses."""
    rng = np.random.default_rng(2)
    returns = rng.normal(0.0003, 0.015, 200)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(200))

    m = compute_risk_metrics(pv, bl)
    assert m["cvar_95_pct"] >= m["var_95_pct"], (
        f"CVaR ({m['cvar_95_pct']:.3f}%) must be >= VaR ({m['var_95_pct']:.3f}%)"
    )


def test_var_known_normal_distribution():
    """
    For a large iid N(μ=0, σ=0.01) sample:
    VaR(95%) ≈ 1.645 × σ = 1.645% (daily).
    Allow ±0.3% tolerance for finite-sample noise.
    """
    rng = np.random.default_rng(42)
    N = 2000
    returns = rng.normal(0.0, 0.01, N)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(N))

    m = compute_risk_metrics(pv, bl)
    # 1.645 * 1.0% = 1.645% daily VaR
    assert abs(m["var_95_pct"] - 1.645) < 0.30, (
        f"VaR(95%) for N(0, 0.01) should be ~1.645%; got {m['var_95_pct']:.3f}%"
    )


def test_cvar_known_normal_distribution():
    """
    For large iid N(μ=0, σ=0.01):
    CVaR(95%) ≈ σ × φ(1.645) / 0.05 = 1.0% × 0.1031 / 0.05 ≈ 2.063%.
    Allow ±0.4% tolerance for finite-sample noise.
    """
    rng = np.random.default_rng(42)
    N = 2000
    returns = rng.normal(0.0, 0.01, N)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(N))

    m = compute_risk_metrics(pv, bl)
    # φ(1.645) ≈ 0.1031
    expected_cvar = 0.01 * 0.1031 / 0.05 * 100   # ≈ 2.063%
    assert abs(m["cvar_95_pct"] - expected_cvar) < 0.40, (
        f"CVaR(95%) for N(0, 0.01) should be ~{expected_cvar:.2f}%; got {m['cvar_95_pct']:.3f}%"
    )


def test_risk_metrics_returns_var_cvar_keys():
    """compute_risk_metrics must include 'var_95_pct' and 'cvar_95_pct' in output."""
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0003, 0.01, 50)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(50))

    m = compute_risk_metrics(pv, bl)
    assert "var_95_pct" in m,  "var_95_pct missing from compute_risk_metrics output"
    assert "cvar_95_pct" in m, "cvar_95_pct missing from compute_risk_metrics output"


# ── Alpha CI unit tests ───────────────────────────────────────────────────────

def test_alpha_ci_str_format():
    """alpha_ci_str must produce '+N bps/yr [±lo, ±hi]' format."""
    from src.factors import alpha_ci_str
    mock_result = {
        "alpha_annual_bps": 127.0,
        "_hac_bse": {"const": 0.0005},  # daily SE in decimal
    }
    s = alpha_ci_str(mock_result)
    assert "bps/yr" in s
    assert "[" in s and "]" in s


def test_alpha_ci_str_known_values():
    """
    alpha = 127 bps/yr, SE_daily = 0.0005 decimal
    SE_bps = 0.0005 × 252 × 10000 = 1260 bps
    95% CI: 127 ± 1.96 × 1260 = 127 ± 2469.6
    lo ≈ −2342, hi ≈ +2597
    """
    from src.factors import alpha_ci_str
    se_daily = 0.0005
    a_bps    = 127.0
    se_bps   = se_daily * 252 * 10_000  # = 1260
    lo = a_bps - 1.96 * se_bps           # ≈ −2342
    hi = a_bps + 1.96 * se_bps           # ≈ +2597

    mock_result = {
        "alpha_annual_bps": a_bps,
        "_hac_bse": {"const": se_daily},
    }
    s = alpha_ci_str(mock_result)
    assert "+127 bps/yr" in s
    assert str(int(round(abs(lo)))) in s
    assert str(int(round(hi))) in s


def test_alpha_ci_str_no_hac_bse():
    """When _hac_bse is missing, alpha_ci_str falls back to plain bps string."""
    from src.factors import alpha_ci_str
    mock_result = {"alpha_annual_bps": -94.0, "_hac_bse": {}}
    s = alpha_ci_str(mock_result)
    assert "bps/yr" in s
    assert "[" not in s   # no CI when SE is unavailable
