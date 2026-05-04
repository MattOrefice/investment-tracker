"""
Tests for src/factors.py — FF5 data ingestion and regression engine.
"""
from __future__ import annotations

import sys
import pathlib
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.factors import (
    _nw_lags,
    _ols_ff5,
    _parse_ff_csv_text,
    build_factor_methodology_notes,
    build_factor_prose,
    run_ff5_regression,
    sig_marker,
)


# ── Parser ────────────────────────────────────────────────────────────────────

_SAMPLE_FF_TEXT = """\
F-F Research Data 5 Factors (2x3) -- Daily
Breakpoints: NYSE; Medians used for Size and each of the Value factors.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20250101,   0.50,   0.09,   0.26,   0.09,   0.18,  0.01
20250102,   0.73,  -0.07,   0.26,  -0.04,  -0.21,  0.01
20250103,  -0.12,   0.15,  -0.09,   0.22,   0.11,  0.01

Annual Factors: Value-Weighted Returns -- Breakpoints: NYSE
2024,   0.40,   0.10,   0.05,   0.08,   0.06,  0.00
"""


def test_ff_parser_returns_three_rows():
    df = _parse_ff_csv_text(_SAMPLE_FF_TEXT)
    assert len(df) == 3, f"Expected 3 rows, got {len(df)}"


def test_ff_parser_column_names():
    df = _parse_ff_csv_text(_SAMPLE_FF_TEXT)
    assert set(df.columns) == {"Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"}


def test_ff_parser_date_index():
    df = _parse_ff_csv_text(_SAMPLE_FF_TEXT)
    assert df.index[0].date().isoformat() == "2025-01-01"
    assert df.index[2].date().isoformat() == "2025-01-03"


def test_ff_parser_unit_conversion():
    """Source values are in percent; parser must divide by 100."""
    df = _parse_ff_csv_text(_SAMPLE_FF_TEXT)
    # First row Mkt-RF: 0.50% → 0.005
    assert abs(df["Mkt-RF"].iloc[0] - 0.005) < 1e-10
    # First row RF: 0.01% → 0.0001
    assert abs(df["RF"].iloc[0] - 0.0001) < 1e-10


def test_ff_parser_excludes_annual_footer():
    """Annual rows (4-digit years) must not appear in the output."""
    df = _parse_ff_csv_text(_SAMPLE_FF_TEXT)
    assert len(df) == 3, "Annual footer rows must be excluded"


def test_ff_parser_handles_missing_column_header_line():
    """Data rows are found even when no explicit column-header line precedes them."""
    text = (
        "Some arbitrary header text\n\n"
        "19630701,   0.50,   0.09,   0.26,   0.09,   0.18,  0.01\n"
        "19630702,   0.73,  -0.07,   0.26,  -0.04,  -0.21,  0.01\n"
    )
    df = _parse_ff_csv_text(text)
    assert len(df) == 2


# ── Newey-West lag formula ─────────────────────────────────────────────────────

@pytest.mark.parametrize("T,expected", [
    (100, 4),  # 4 * (100/100)^(2/9) = 4.0
    (200, 4),  # 4 * 2.0^(2/9) ≈ 4.60 → 4
    (250, 4),  # 4 * 2.5^(2/9) ≈ 4.90 → 4
    (350, 5),  # 4 * 3.5^(2/9) ≈ 5.19 → 5
])
def test_nw_lags_formula(T, expected):
    assert _nw_lags(T) == expected


# ── Significance marker ───────────────────────────────────────────────────────

def test_sig_marker_three_stars():
    assert sig_marker(0.005) == "***"

def test_sig_marker_two_stars():
    assert sig_marker(0.04) == "**"

def test_sig_marker_one_star():
    assert sig_marker(0.08) == "*"

def test_sig_marker_none():
    assert sig_marker(0.20) == ""


# ── Regression engine: coefficient recovery ───────────────────────────────────

@pytest.fixture(scope="module")
def synthetic_regression_result():
    """
    Synthetic series: R_p = 0.0002 + 1.0 * Mkt-RF + 0.3 * HML + iid noise.
    Expected: beta_mkt ≈ 1.0, beta_hml ≈ 0.3, alpha_daily ≈ 0.0002.
    """
    rng = np.random.default_rng(42)
    T = 300
    dates = pd.bdate_range("2024-01-01", periods=T)

    Mkt = rng.standard_normal(T) * 0.010
    HML = rng.standard_normal(T) * 0.005
    SMB = rng.standard_normal(T) * 0.004
    RMW = rng.standard_normal(T) * 0.003
    CMA = rng.standard_normal(T) * 0.003
    RF  = np.full(T, 0.0001)
    noise = rng.standard_normal(T) * 0.003

    R_p = 0.0002 + 1.0 * Mkt + 0.3 * HML + noise

    factors = pd.DataFrame(
        {"Mkt-RF": Mkt, "SMB": SMB, "HML": HML, "RMW": RMW, "CMA": CMA, "RF": RF},
        index=dates,
    )
    R_excess = pd.Series(R_p - RF, index=dates)
    return _ols_ff5(R_excess, factors)


def test_beta_mkt_approx_one(synthetic_regression_result):
    assert abs(synthetic_regression_result["betas"]["Mkt-RF"] - 1.0) < 0.1


def test_beta_hml_approx_point3(synthetic_regression_result):
    assert abs(synthetic_regression_result["betas"]["HML"] - 0.3) < 0.1


def test_alpha_approx_zero(synthetic_regression_result):
    alpha_daily = synthetic_regression_result["alpha_daily"]
    assert abs(alpha_daily) < 0.001, f"Alpha too large: {alpha_daily:.6f}"


def test_mkt_tstat_large(synthetic_regression_result):
    """Market factor should dominate with |t| > 10 in a T=300 clean synthetic."""
    assert abs(synthetic_regression_result["t_stats"]["Mkt-RF"]) > 10


def test_r_squared_high(synthetic_regression_result):
    """Synthetic constructed from Mkt+HML; R² should be well above 0.5."""
    assert synthetic_regression_result["r_squared"] > 0.5


# ── Newey-West SEs differ from OLS SEs on autocorrelated residuals ────────────

def test_newey_west_se_exceeds_ols_se_under_ar1():
    """
    When residuals follow an AR(1) process, HAC SEs should exceed OLS SEs
    for at least one coefficient (OLS underestimates variance under autocorrelation).
    """
    rng = np.random.default_rng(99)
    T = 300
    dates = pd.bdate_range("2024-01-01", periods=T)

    X_raw = rng.standard_normal(T) * 0.01
    # AR(1) errors with rho=0.5
    eps = np.zeros(T)
    eps[0] = rng.standard_normal() * 0.005
    for t in range(1, T):
        eps[t] = 0.5 * eps[t - 1] + rng.standard_normal() * 0.004

    y = 0.0001 + 0.8 * X_raw + eps

    factors = pd.DataFrame(
        {"Mkt-RF": X_raw, "SMB": np.zeros(T), "HML": np.zeros(T),
         "RMW": np.zeros(T), "CMA": np.zeros(T), "RF": np.zeros(T)},
        index=dates,
    )
    R_excess = pd.Series(y, index=dates)
    result = _ols_ff5(R_excess, factors)

    hac_bse = result["_hac_bse"]
    ols_bse = result["_ols_bse"]

    # At least one coefficient should have HAC SE > OLS SE
    any_larger = any(
        hac_bse[k] > ols_bse[k]
        for k in hac_bse
        if k in ols_bse
    )
    assert any_larger, (
        "Expected HAC SEs to exceed OLS SEs on AR(1) residuals, "
        f"but got HAC={hac_bse}, OLS={ols_bse}"
    )


# ── Alignment: observation count within expected range ────────────────────────

def test_alignment_produces_trading_days_only():
    """
    When portfolio returns are a daily series and FF factors are business-days only,
    the inner join must drop weekend rows.
    """
    # Build a 10-calendar-day portfolio return series (includes weekends)
    dates_cal = pd.date_range("2025-01-01", periods=10, freq="D")
    pv = pd.Series(
        np.cumprod(1 + np.random.default_rng(0).standard_normal(10) * 0.005),
        index=dates_cal,
    )
    daily_ret = pv.pct_change().dropna()

    # FF factors only for business days in same range
    bdays = pd.bdate_range("2025-01-01", "2025-01-10")
    ff = pd.DataFrame(
        {"Mkt-RF": np.zeros(len(bdays)), "SMB": np.zeros(len(bdays)),
         "HML": np.zeros(len(bdays)), "RMW": np.zeros(len(bdays)),
         "CMA": np.zeros(len(bdays)), "RF": np.zeros(len(bdays))},
        index=bdays,
    )

    merged = daily_ret.to_frame(name="R_p").join(ff, how="inner").dropna()

    # Only business days overlap — no weekend rows
    assert all(d.weekday() < 5 for d in merged.index)
    assert len(merged) == len(bdays) - 1  # one fewer because pct_change drops first


# ── run_ff5_regression integration (mocked DB + factor data) ─────────────────

def test_run_ff5_regression_returns_required_keys():
    """
    With mocked portfolio value series and FF factor data, run_ff5_regression
    must return a dict containing all expected keys.
    """
    rng = np.random.default_rng(7)
    T = 100
    dates = pd.bdate_range("2025-05-01", periods=T + 1)

    pv_vals = np.cumprod(1 + rng.standard_normal(T + 1) * 0.008) * 10_000
    mock_pv = pd.Series(pv_vals, index=dates)

    ff_dates = pd.bdate_range("2025-05-01", periods=T)
    mock_ff = pd.DataFrame({
        "Mkt-RF": rng.standard_normal(T) * 0.01,
        "SMB":    rng.standard_normal(T) * 0.005,
        "HML":    rng.standard_normal(T) * 0.005,
        "RMW":    rng.standard_normal(T) * 0.003,
        "CMA":    rng.standard_normal(T) * 0.003,
        "RF":     np.full(T, 0.0001),
    }, index=ff_dates)

    with patch("src.factors.get_portfolio_value_series", return_value=mock_pv), \
         patch("src.factors.get_ff_factors", return_value=mock_ff):
        res = run_ff5_regression("2025-05-01", "2025-09-30")

    assert res is not None
    required = {
        "alpha_daily", "alpha_annual", "alpha_annual_bps",
        "t_alpha", "p_alpha",
        "betas", "t_stats", "p_values",
        "r_squared", "adj_r_squared",
        "T", "nw_lags", "sample_start", "sample_end",
    }
    assert required.issubset(res.keys()), f"Missing keys: {required - res.keys()}"


def test_run_ff5_regression_returns_none_on_empty_portfolio():
    empty_pv = pd.Series(0.0, index=pd.date_range("2025-05-01", periods=5))
    with patch("src.factors.get_portfolio_value_series", return_value=empty_pv):
        res = run_ff5_regression("2025-05-01", "2025-05-10")
    assert res is None


# ── Factor section values match regression object ─────────────────────────────

def test_factor_section_values_match_raw_result():
    """
    _build_factor_section must format values from the raw run_ff5_regression result
    without recomputing or silently transforming them.
    The formatted beta strings, r_squared, and T must be derivable from the raw dict.
    """
    from src.reports import _build_factor_section

    rng = np.random.default_rng(3)
    T = 80
    dates = pd.bdate_range("2025-05-01", periods=T + 1)
    pv_vals = np.cumprod(1 + rng.standard_normal(T + 1) * 0.008) * 10_000
    mock_pv = pd.Series(pv_vals, index=dates)

    ff_dates = pd.bdate_range("2025-05-01", periods=T)
    mock_ff = pd.DataFrame({
        "Mkt-RF": rng.standard_normal(T) * 0.01,
        "SMB":    rng.standard_normal(T) * 0.005,
        "HML":    rng.standard_normal(T) * 0.005,
        "RMW":    rng.standard_normal(T) * 0.003,
        "CMA":    rng.standard_normal(T) * 0.003,
        "RF":     np.full(T, 0.0001),
    }, index=ff_dates)

    with patch("src.factors.get_portfolio_value_series", return_value=mock_pv), \
         patch("src.factors.get_ff_factors", return_value=mock_ff), \
         patch("src.reports._inception_date", return_value="2025-05-01"):
        section = _build_factor_section("2025-09-30")

    assert section is not None

    raw = section["_raw"]

    # R² must match exactly (it is not transformed)
    assert section["r_squared"] == f"{raw['r_squared']:.3f}"

    # T must match exactly
    assert section["T"] == raw["T"]

    # Mkt-RF beta string must match the raw beta rounded to 3 dp
    mkt_row = next(r for r in section["rows"] if r["factor"] == "Mkt-RF")
    assert mkt_row["beta"] == f"{raw['betas']['Mkt-RF']:.3f}"
