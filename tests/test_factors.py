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
    EM_DISCLOSURE,
    _nw_lags,
    _ols_ff5,
    _parse_ff_csv_text,
    build_factor_methodology_notes,
    build_factor_prose,
    run_sleeve_regressions,
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


def test_ff_parser_replaces_missing_sentinel():
    """-99.99 percent → -0.9999 decimal must become NaN."""
    text = (
        "19900101,  -99.99,   0.09,   0.26,   0.09,   0.18,  0.01\n"
        "19900102,   0.50,   0.09,   0.26,   0.09,   0.18,  0.01\n"
    )
    df = _parse_ff_csv_text(text)
    assert np.isnan(df["Mkt-RF"].iloc[0])
    assert not np.isnan(df["Mkt-RF"].iloc[1])


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


# ── run_sleeve_regressions: structure and required keys ───────────────────────

def _make_mock_ff(T: int, rng: np.random.Generator, dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({
        "Mkt-RF": rng.standard_normal(T) * 0.01,
        "SMB":    rng.standard_normal(T) * 0.005,
        "HML":    rng.standard_normal(T) * 0.005,
        "RMW":    rng.standard_normal(T) * 0.003,
        "CMA":    rng.standard_normal(T) * 0.003,
        "RF":     np.full(T, 0.0001),
    }, index=dates)


def test_run_sleeve_regressions_returns_expected_structure():
    """
    With mocked _get_sleeve_return_series and load_factors, run_sleeve_regressions
    must return a dict with 'us' and 'developed_exus' keys, each containing all
    required result keys.
    """
    rng = np.random.default_rng(7)
    T = 100
    dates = pd.bdate_range("2025-05-01", periods=T)

    mock_ret = pd.Series(rng.standard_normal(T) * 0.008, index=dates)
    mock_ff  = _make_mock_ff(T, rng, dates)

    with patch("src.factors._get_sleeve_return_series", return_value=mock_ret), \
         patch("src.factors.load_factors", return_value=mock_ff):
        results = run_sleeve_regressions("2025-05-01", "2025-09-30")

    assert "us" in results
    assert "developed_exus" in results

    required = {
        "alpha_daily", "alpha_annual", "alpha_annual_bps",
        "t_alpha", "p_alpha",
        "betas", "t_stats", "p_values",
        "r_squared", "adj_r_squared",
        "T", "nw_lags", "sample_start", "sample_end",
        "sleeve_label", "tickers", "region",
    }
    for key in ("us", "developed_exus"):
        res = results[key]
        assert res is not None, f"Expected non-None result for '{key}'"
        assert required.issubset(res.keys()), (
            f"Missing keys in '{key}': {required - res.keys()}"
        )


def test_run_sleeve_regressions_returns_none_on_insufficient_data():
    """When fewer than 30 observations are aligned, both sleeve results must be None."""
    short_dates = pd.bdate_range("2025-05-01", periods=5)
    short_ret = pd.Series(np.zeros(5), index=short_dates)
    short_ff  = pd.DataFrame({
        "Mkt-RF": np.zeros(5), "SMB": np.zeros(5), "HML": np.zeros(5),
        "RMW":    np.zeros(5), "CMA": np.zeros(5), "RF":  np.zeros(5),
    }, index=short_dates)

    with patch("src.factors._get_sleeve_return_series", return_value=short_ret), \
         patch("src.factors.load_factors", return_value=short_ff):
        results = run_sleeve_regressions("2025-05-01", "2025-05-10")

    assert results["us"] is None
    assert results["developed_exus"] is None


# ── SAA weights embedded in sleeve config ─────────────────────────────────────

def test_us_sleeve_has_embedded_weights_summing_to_one():
    """
    _SLEEVES['us'] must carry embedded SAA-proportional weights so that
    _get_sleeve_return_series never needs to call get_holdings_on_date.
    """
    from src.factors import _SLEEVES
    us = _SLEEVES["us"]
    assert "weights" in us, "_SLEEVES['us'] must have a 'weights' key"
    w = us["weights"]
    assert set(w.keys()) == set(us["tickers"]), "Weight keys must match tickers"
    assert abs(sum(w.values()) - 1.0) < 1e-10, f"Weights sum to {sum(w.values())}, not 1.0"
    assert all(v > 0 for v in w.values()), "All weights must be positive"


def test_us_sleeve_non_none_when_holdings_db_empty():
    """
    US sleeve must return a regression result even when get_holdings_on_date
    returns an empty DataFrame (e.g., personal mode with no trades, or fresh
    Cloud deployment before TRACKER_MODE=demo is configured in st.secrets).

    The fix: SAA weights embedded in _SLEEVES bypass the DB lookup entirely.
    This is the regression test for the production N/A bug.
    """
    rng = np.random.default_rng(77)
    T = 100
    dates_range = pd.date_range("2025-05-01", periods=T + 1, freq="D")

    # Realistic price DataFrame: DatetimeIndex, adj_close + close columns
    mock_prices = pd.DataFrame({
        "adj_close": np.cumprod(1 + rng.standard_normal(T + 1) * 0.01),
        "close":     np.cumprod(1 + rng.standard_normal(T + 1) * 0.01),
    }, index=dates_range)

    mock_ff = _make_mock_ff(T, rng, pd.bdate_range("2025-05-01", periods=T))

    empty_holdings = pd.DataFrame(columns=["net_shares"])

    with patch("src.factors.get_prices", return_value=mock_prices), \
         patch("src.factors.get_holdings_on_date", return_value=empty_holdings), \
         patch("src.factors.load_factors", return_value=mock_ff):
        results = run_sleeve_regressions("2025-05-01", "2025-09-30")

    assert results["us"] is not None, (
        "US sleeve returned None despite SAA weights being embedded in _SLEEVES. "
        "The regression must not require get_holdings_on_date when weights are provided."
    )
    assert results["us"]["T"] > 0


# ── Developed sleeve prose contains Korea universe-mismatch disclosure ─────────

def test_developed_sleeve_prose_contains_korea_disclosure():
    """
    The Developed sleeve prose paragraph must name the universe mismatch
    (Korea excluded from Ken French Developed, included in VEA/FTSE) as the
    source of the elevated alpha. This is the credibility-defining sentence.
    """
    rng = np.random.default_rng(55)
    T = 100
    dates = pd.bdate_range("2025-05-01", periods=T)
    mock_ret = pd.Series(rng.standard_normal(T) * 0.008, index=dates)
    mock_ff  = _make_mock_ff(T, rng, dates)

    with patch("src.factors._get_sleeve_return_series", return_value=mock_ret), \
         patch("src.factors.load_factors", return_value=mock_ff):
        results = run_sleeve_regressions("2025-05-01", "2025-09-30")

    prose = build_factor_prose(results)
    full_text = " ".join(prose)

    assert "Korean equities" in full_text, (
        "Developed sleeve prose must name 'Korean equities' as the universe-mismatch source"
    )
    assert "FTSE Developed All Cap ex US" in full_text, (
        "Developed sleeve prose must name the FTSE index to disclose the classification gap"
    )
    assert "universe mismatch" in full_text, (
        "Developed sleeve prose must frame the alpha as a universe-mismatch artifact"
    )


# ── EM disclosure constant ─────────────────────────────────────────────────────

def test_em_disclosure_is_non_empty_string():
    assert isinstance(EM_DISCLOSURE, str) and len(EM_DISCLOSURE) > 50


# ── Factor section values match regression object ─────────────────────────────

def test_factor_section_values_match_raw_result():
    """
    _build_factor_section must format values from run_sleeve_regressions results
    without recomputing or silently transforming them.
    Checks the new two-sleeve structure: section["sleeves"][0] is the US sleeve.
    """
    from src.reports import _build_factor_section

    rng = np.random.default_rng(3)
    T = 80
    dates = pd.bdate_range("2025-05-01", periods=T)
    mock_ret = pd.Series(rng.standard_normal(T) * 0.008, index=dates)
    mock_ff  = _make_mock_ff(T, rng, dates)

    with patch("src.factors._get_sleeve_return_series", return_value=mock_ret), \
         patch("src.factors.load_factors", return_value=mock_ff), \
         patch("src.reports._inception_date", return_value="2025-05-01"):
        section = _build_factor_section("2025-09-30")

    assert section is not None
    assert "sleeves" in section
    assert len(section["sleeves"]) >= 1

    # Verify EM disclosure is passed through
    assert section["em_note"] == EM_DISCLOSURE

    # Check US sleeve (first entry)
    us_sleeve = section["sleeves"][0]
    raw = us_sleeve["_raw"]

    # R² must match exactly (not transformed)
    assert us_sleeve["r_squared"] == f"{raw['r_squared']:.3f}"

    # T must match exactly
    assert us_sleeve["T"] == raw["T"]

    # Mkt-RF beta string must match raw beta rounded to 3 dp
    mkt_row = next(r for r in us_sleeve["rows"] if r["factor"] == "Mkt-RF")
    assert mkt_row["beta"] == f"{raw['betas']['Mkt-RF']:.3f}"
