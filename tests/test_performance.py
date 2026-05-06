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

def test_compute_risk_metrics_short_windows():
    """compute_risk_metrics must support 1M, 3M, YTD, and 1Y windows.

    Regression pin: Phase 8u. Extended from SI+1Y to five windows matching the
    BF Attribution period set. Uses a synthetic 400-day series so every window
    has ≥ 20 observations; asserts all seven keys are present and values are finite.
    """
    import math as _math
    import pandas as pd
    from datetime import date as _date
    rng = np.random.default_rng(99)
    N400 = 400
    returns = rng.normal(0.0004, 0.01, N400)
    # Anchor end to today so all window cutoffs fall within the series
    start400 = (pd.Timestamp(_date.today()) - pd.tseries.offsets.BDay(N400)).date().isoformat()
    pv = _make_series(returns, start=start400)
    bl = _make_series(np.zeros(N400), start=start400)

    expected_keys = {
        "sharpe", "sortino", "max_drawdown_pct", "tracking_error_pct",
        "information_ratio", "var_95_pct", "cvar_95_pct",
    }
    for w in ("1M", "3M", "YTD", "1Y", "SI"):
        m = compute_risk_metrics(pv, bl, window=w)
        assert m, f"window={w!r} returned empty dict — check observation threshold"
        missing = expected_keys - m.keys()
        assert not missing, f"window={w!r} missing keys: {missing}"
        for k in expected_keys:
            assert _math.isfinite(m[k]), (
                f"window={w!r} key {k!r} is not finite: {m[k]}"
            )


def test_risk_metrics_distinguish_across_windows():
    """Window filtering must produce monotonically shorter obs counts and distinct Sharpe.

    Regression pin: Phase 8u.1. Previous tests only asserted non-NaN; this test
    pins that each window produces a distinct observation count (chain assertion)
    and that Sharpe values clearly differ between short and full windows.

    Uses a step-down return series: strongly positive returns for the first 300
    business days, then negative for the last 100 business days. This guarantees:
      - 1M/3M Sharpe is negative (window falls in the negative period)
      - SI Sharpe is positive  (300 positive days dominate 100 negative days)

    If window filtering collapses (all windows return the SI series), 1M Sharpe
    would be positive like SI — and the sign assertion would fail.
    """
    import pandas as pd
    from datetime import date

    rng = np.random.default_rng(77)
    N = 400
    returns = np.concatenate([
        rng.normal(0.003, 0.006, 300),   # first 300 days: strongly positive
        rng.normal(-0.005, 0.004, 100),  # last 100 days: strongly negative, low vol
        # Low vol in the negative period ensures the 1M sample mean (21 obs)
        # is reliably negative regardless of seed: P(mean>0) < 1e-6.
    ])
    # Anchor the series END to today so all window cutoffs (1M/3M/YTD/1Y) fall
    # within the series. BDay(N) counts N business days back from today; the
    # resulting series spans N business days ending at the most recent business day.
    start = (pd.Timestamp(date.today()) - pd.tseries.offsets.BDay(N)).date().isoformat()
    pv = _make_series(returns, start=start)
    bl = _make_series(np.zeros(N), start=start)

    results = {w: compute_risk_metrics(pv, bl, window=w) for w in ("1M", "3M", "YTD", "1Y", "SI")}

    for w in ("1M", "3M", "YTD", "1Y", "SI"):
        assert results[w], f"window={w!r} returned empty dict — observation threshold not met"

    # Obs-count chain: shorter calendar window = fewer return observations.
    # This is the strongest possible pin: if any window returns SI obs count,
    # the cutoff filter collapsed regardless of what metric values came out.
    n = {w: results[w]["n_days"] for w in ("1M", "3M", "YTD", "1Y", "SI")}
    assert n["1M"] < n["3M"],  f"1M ({n['1M']} obs) must be < 3M ({n['3M']} obs)"
    assert n["3M"] < n["1Y"],  f"3M ({n['3M']} obs) must be < 1Y ({n['1Y']} obs)"
    assert n["1Y"] < n["SI"],  f"1Y ({n['1Y']} obs) must be < SI ({n['SI']} obs)"
    # YTD is somewhere between the extremes (exact position varies by day of year)
    assert 0 < n["YTD"] < n["SI"], (
        f"YTD ({n['YTD']} obs) must be > 0 and < SI ({n['SI']} obs)"
    )

    # Sharpe must clearly distinguish short vs. long windows.
    # Step-down series: first 300 days strongly positive, last 100 days strongly
    # negative (low vol so the 1M sample mean is deterministically negative).
    # If window filtering collapsed, 1M would report SI values → difference ≈ 0.
    assert results["1M"]["sharpe"] < 0, (
        f"1M Sharpe ({results['1M']['sharpe']:.3f}) should be negative — "
        f"1M window falls in the strongly-negative tail of the test series. "
        f"Positive 1M Sharpe means the cutoff is not filtering correctly."
    )
    assert results["SI"]["sharpe"] > 0, (
        f"SI Sharpe ({results['SI']['sharpe']:.3f}) should be positive — "
        f"300 positive days dominate 100 negative days over the full series."
    )
    sharpe_gap = abs(results["1M"]["sharpe"] - results["SI"]["sharpe"])
    assert sharpe_gap > 1.0, (
        f"1M Sharpe ({results['1M']['sharpe']:.3f}) and SI Sharpe ({results['SI']['sharpe']:.3f}) "
        f"differ by only {sharpe_gap:.3f}. Expected > 1.0 — if gap is small, window "
        f"filtering collapsed and all windows are returning the full SI series. "
        f"Fix: restore .copy() after cutoff slice in src/performance.py."
    )


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
