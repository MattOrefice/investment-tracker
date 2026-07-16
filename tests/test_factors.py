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
    _parse_momentum_csv_text,
    build_benchmark_methodology,
    build_benchmark_prose,
    build_factor_methodology_notes,
    build_factor_prose,
    regress_fi_sleeve,
    run_benchmark_attribution_regression,
    run_sleeve_regressions,
    run_sleeve_regressions_mom,
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


# ── Benchmark attribution regression ─────────────────────────────────────────

def test_run_benchmark_attribution_regression_returns_expected_structure():
    """
    With mocked portfolio/benchmark series and FF factors,
    run_benchmark_attribution_regression must return a dict with all required keys
    including betas for all four model factors.
    """
    rng = np.random.default_rng(11)
    T = 100
    bdays = pd.bdate_range("2025-05-01", periods=T)

    pv_series = pd.Series(
        np.cumprod(1 + rng.standard_normal(T) * 0.008) * 10000,
        index=bdays,
    )
    bl_series = pd.Series(
        np.cumprod(1 + rng.standard_normal(T) * 0.008),
        index=bdays,
    )
    mock_ff = _make_mock_ff(T, rng, bdays)

    with patch("src.factors.get_portfolio_value_series", return_value=pv_series), \
         patch("src.factors.get_custom_blended_series", return_value=bl_series), \
         patch("src.factors.load_factors", return_value=mock_ff):
        result = run_benchmark_attribution_regression("2025-05-01", "2025-09-30")

    assert result is not None, "Expected non-None result with sufficient data"
    required = {
        "alpha_daily", "alpha_annual", "alpha_annual_bps",
        "t_alpha", "p_alpha",
        "betas", "t_stats", "p_values",
        "r_squared", "adj_r_squared",
        "T", "nw_lags", "sample_start", "sample_end",
    }
    assert required.issubset(result.keys()), f"Missing keys: {required - result.keys()}"
    for f in ["Bench-RF", "HML", "SMB", "RMW"]:
        assert f in result["betas"],   f"Missing beta for factor '{f}'"
        assert f in result["t_stats"], f"Missing t-stat for factor '{f}'"
        assert f in result["p_values"],f"Missing p-value for factor '{f}'"


def test_benchmark_regression_recovers_synthetic_bench_beta():
    """
    When R_p = R_b + small noise (β_bench ≈ 1.0, alpha ≈ 0), the regression
    must recover β_bench close to 1.0 and a near-zero daily alpha.
    """
    rng = np.random.default_rng(22)
    T = 200
    bdays = pd.bdate_range("2025-01-01", periods=T)

    mock_ff = _make_mock_ff(T, rng, bdays)
    mock_ff["RF"] = 0.0001  # near-zero RF so excess ≈ raw return

    # R_b tracks the Mkt-RF factor; R_p = R_b + small noise → β_bench ≈ 1, α ≈ 0
    R_b_raw = mock_ff["Mkt-RF"].values.copy()
    R_p_raw = R_b_raw + rng.standard_normal(T) * 0.001

    pv_series = pd.Series(np.cumprod(1 + R_p_raw) * 10000, index=bdays)
    bl_series = pd.Series(np.cumprod(1 + R_b_raw),         index=bdays)

    with patch("src.factors.get_portfolio_value_series", return_value=pv_series), \
         patch("src.factors.get_custom_blended_series", return_value=bl_series), \
         patch("src.factors.load_factors", return_value=mock_ff):
        result = run_benchmark_attribution_regression("2025-01-01", "2025-10-31")

    assert result is not None
    assert abs(result["betas"]["Bench-RF"] - 1.0) < 0.15, (
        f"Expected β_bench ≈ 1.0, got {result['betas']['Bench-RF']:.3f}"
    )
    assert abs(result["alpha_daily"]) < 0.0005, (
        f"Expected near-zero alpha, got {result['alpha_daily']:.6f}"
    )


def test_benchmark_regression_returns_none_on_insufficient_data():
    """Fewer than 30 aligned observations → result must be None."""
    T = 5
    short_bdays = pd.bdate_range("2025-05-01", periods=T)

    short_pv = pd.Series(np.ones(T) * 10000, index=short_bdays)
    short_bl = pd.Series(np.ones(T),          index=short_bdays)
    short_ff = pd.DataFrame({
        "Mkt-RF": np.zeros(T), "SMB": np.zeros(T), "HML": np.zeros(T),
        "RMW":    np.zeros(T), "CMA": np.zeros(T), "RF":  np.zeros(T),
    }, index=short_bdays)

    with patch("src.factors.get_portfolio_value_series", return_value=short_pv), \
         patch("src.factors.get_custom_blended_series", return_value=short_bl), \
         patch("src.factors.load_factors", return_value=short_ff):
        result = run_benchmark_attribution_regression("2025-05-01", "2025-05-10")

    assert result is None


def test_benchmark_regression_returns_none_on_total_blended_gap():
    """A totally-unpriceable custom blended benchmark (PR A: get_custom_blended_series
    now yields an explicit NaN sentinel on a total reference-benchmark data gap,
    src/benchmarks.py) must degrade this regression to None (same as insufficient
    data) via the existing .dropna() alignment — not raise or silently regress
    against garbage.

    This is the intentionally-inert path: run_benchmark_attribution_regression
    needed NO code change for PR A — R_b = bl.pct_change() is already dropped
    from `merged` by the existing .dropna(), so a NaN bl_series just shrinks the
    aligned sample to 0 rows and this pins that None-not-crash behavior explicitly.
    """
    T = 100
    bdays = pd.bdate_range("2025-05-01", periods=T)

    pv_series  = pd.Series(np.full(T, 10000.0), index=bdays)
    nan_bl     = pd.Series(np.nan, index=bdays)   # total reference-benchmark gap sentinel
    rng = np.random.default_rng(33)
    mock_ff = _make_mock_ff(T, rng, bdays)

    with patch("src.factors.get_portfolio_value_series", return_value=pv_series), \
         patch("src.factors.get_custom_blended_series", return_value=nan_bl), \
         patch("src.factors.load_factors", return_value=mock_ff):
        result = run_benchmark_attribution_regression("2025-05-01", "2025-09-30")

    assert result is None, (
        "a totally NaN-sentineled blended benchmark must degrade to None "
        f"(insufficient aligned data), got: {result!r}"
    )


def test_benchmark_prose_contains_alpha_interpretation():
    """
    build_benchmark_prose must:
    - describe the intercept as 'active return'
    - use the four-tier significance label correctly:
      t = 3.65 (|t| ≥ 2.58) → 'at the 1% level', NOT 'marginally significant'
    """
    mock_result = {
        "betas":   {"Bench-RF": 0.990, "HML": 0.052, "SMB": -0.007, "RMW": 0.012},
        "t_stats": {"Bench-RF": 98.46, "HML": 6.00,  "SMB": -0.82,  "RMW": 1.17},
        "p_values":{"Bench-RF": 0.0,   "HML": 0.0,   "SMB": 0.41,   "RMW": 0.24},
        "alpha_daily":      0.001706,
        "alpha_annual":     0.04299,
        "alpha_annual_bps": 429.0,
        "t_alpha":          3.65,
        "p_alpha":          0.0003,
        "r_squared":        0.986,
        "adj_r_squared":    0.986,
        "T":                207,
        "nw_lags":          4,
        "sample_start":     "2025-05-02",
        "sample_end":       "2026-03-28",
    }
    prose = build_benchmark_prose(mock_result)
    full_text = " ".join(prose).lower()

    assert "active return" in full_text, (
        "Prose must frame the intercept as 'active return after controlling for...'"
    )
    assert "at the 1% level" in full_text, (
        "t = 3.65 (|t| ≥ 2.58) must trigger '1% level' significance label, not 'marginally significant'"
    )
    assert "marginally significant" not in full_text, (
        "t = 3.65 must not be labeled 'marginally significant' — that label is for 1.65 ≤ |t| < 1.96"
    )


def test_benchmark_prose_significance_thresholds():
    """
    Verify all four significance-label tiers are applied correctly.
    """
    def _prose_for_t(t_val: float) -> str:
        mock = {
            "betas":   {"Bench-RF": 0.99, "HML": 0.0, "SMB": 0.0, "RMW": 0.0},
            "t_stats": {"Bench-RF": 50.0, "HML": 0.0, "SMB": 0.0, "RMW": 0.0},
            "p_values":{"Bench-RF": 0.0,  "HML": 1.0, "SMB": 1.0, "RMW": 1.0},
            "alpha_daily": 0.0, "alpha_annual": 0.0, "alpha_annual_bps": 0.0,
            "t_alpha": t_val, "p_alpha": 0.5,
            "r_squared": 0.99, "adj_r_squared": 0.99,
            "T": 200, "nw_lags": 4,
            "sample_start": "2025-05-01", "sample_end": "2026-01-01",
        }
        return " ".join(build_benchmark_prose(mock)).lower()

    assert "at the 1% level"    in _prose_for_t(3.0),  "t=3.0 → 1% level"
    assert "at the 5% level"    in _prose_for_t(2.1),  "t=2.1 → 5% level"
    assert "marginally significant" in _prose_for_t(1.8), "t=1.8 → marginally significant (10%)"
    assert "not statistically distinguishable" in _prose_for_t(1.0), "t=1.0 → not significant"


# ── EM disclosure constant ─────────────────────────────────────────────────────

# ── Carhart momentum parser ───────────────────────────────────────────────────

_SAMPLE_UMD_TEXT = """\
F-F Momentum Factor (Daily)

,Mom
20250101,   2.34
20250102,  -0.15
20250103,   0.88

Annual Factors: Value-Weighted Returns
2024,   5.20
"""


def test_parse_momentum_returns_correct_count():
    s = _parse_momentum_csv_text(_SAMPLE_UMD_TEXT)
    assert len(s) == 3


def test_parse_momentum_unit_conversion():
    """Momentum values should be divided by 100 (percent → decimal)."""
    s = _parse_momentum_csv_text(_SAMPLE_UMD_TEXT)
    assert abs(s.iloc[0] - 0.0234) < 1e-9


def test_parse_momentum_date_index():
    s = _parse_momentum_csv_text(_SAMPLE_UMD_TEXT)
    assert s.index[0].date().isoformat() == "2025-01-01"


def test_parse_momentum_excludes_annual_footer():
    s = _parse_momentum_csv_text(_SAMPLE_UMD_TEXT)
    assert len(s) == 3


def test_run_sleeve_regressions_mom_returns_expected_structure():
    """
    With mocked _get_sleeve_return_series, load_factors, and load_umd_factor,
    run_sleeve_regressions_mom must return a dict with 'us' and 'developed_exus' keys
    containing all required keys including 'Mom' in betas.
    """
    rng = np.random.default_rng(77)
    T = 120
    dates = pd.bdate_range("2025-05-01", periods=T)

    mock_ret = pd.Series(rng.standard_normal(T) * 0.008, index=dates)
    mock_ff  = _make_mock_ff(T, rng, dates)
    mock_umd = pd.Series(rng.standard_normal(T) * 0.006, index=dates, name="Mom")

    with patch("src.factors._get_sleeve_return_series", return_value=mock_ret), \
         patch("src.factors.load_factors", return_value=mock_ff), \
         patch("src.factors.load_umd_factor", return_value=mock_umd):
        results_mom = run_sleeve_regressions_mom("2025-05-01", "2025-10-31")

    assert "us" in results_mom
    assert "developed_exus" in results_mom
    for key in ("us", "developed_exus"):
        r = results_mom[key]
        assert r is not None
        assert "Mom" in r["betas"]
        assert "Mom" in r["t_stats"]
        assert "Mom" in r["p_values"]


def test_global_region_in_factor_config():
    """'global' must be a valid region key in _FACTOR_CONFIG."""
    from src.factors import _FACTOR_CONFIG
    assert "global" in _FACTOR_CONFIG
    assert "url" in _FACTOR_CONFIG["global"]
    assert "cache" in _FACTOR_CONFIG["global"]
    assert "Global" in _FACTOR_CONFIG["global"]["url"]


# ── FI TERM/CREDIT regression ─────────────────────────────────────────────────

def test_fi_term_credit_regression_recovers_known_betas():
    """
    Synthetic FI series with known TERM/CREDIT loadings: regression must
    recover betas within ±0.10 of truth at T=250.
    """
    rng = np.random.default_rng(42)
    T = 250
    dates = pd.bdate_range("2025-01-01", periods=T)

    term_factor   = rng.normal(0, 0.0015, T)
    credit_factor = rng.normal(0, 0.0008, T)
    noise         = rng.normal(0, 0.0002, T)

    true_term, true_credit = 0.85, 0.15
    R_excess = true_term * term_factor + true_credit * credit_factor + noise

    factors_df = pd.DataFrame(
        {"TERM": term_factor, "CREDIT": credit_factor}, index=dates
    )
    X = add_constant(factors_df)
    L = _nw_lags(T)
    res = OLS(pd.Series(R_excess, index=dates), X).fit(
        cov_type="HAC", cov_kwds={"maxlags": L}
    )

    assert abs(res.params["TERM"]   - true_term)   < 0.10, f"TERM: {res.params['TERM']:.3f}"
    assert abs(res.params["CREDIT"] - true_credit) < 0.10, f"CREDIT: {res.params['CREDIT']:.3f}"


def test_regress_fi_sleeve_returns_expected_structure():
    """
    With mocked get_prices and load_factors, regress_fi_sleeve must return a
    dict with all required keys and TERM / CREDIT entries in betas / t_stats / p_values.
    """
    rng = np.random.default_rng(99)
    T = 250
    dates_full = pd.date_range("2025-05-01", periods=T + 1, freq="D")

    px = pd.DataFrame({
        "adj_close": 100.0 * np.cumprod(1 + rng.standard_normal(T + 1) * 0.003),
        "close":     100.0 * np.cumprod(1 + rng.standard_normal(T + 1) * 0.003),
    }, index=dates_full)

    mock_ff = _make_mock_ff(T, rng, pd.bdate_range("2025-05-01", periods=T))
    mock_ff["RF"] = 0.0001

    with patch("src.factors.get_prices", return_value=px), \
         patch("src.factors.load_factors", return_value=mock_ff):
        result = regress_fi_sleeve("2025-05-01", "2026-04-30")

    assert result is not None
    required = {
        "alpha_daily", "alpha_annual", "alpha_annual_bps",
        "t_alpha", "p_alpha", "betas", "t_stats", "p_values",
        "r_squared", "adj_r_squared", "T", "nw_lags",
        "sample_start", "sample_end", "sleeve_label", "tickers",
    }
    assert required.issubset(result.keys()), f"Missing: {required - result.keys()}"
    for f in ["TERM", "CREDIT"]:
        assert f in result["betas"],    f"betas missing {f}"
        assert f in result["t_stats"],  f"t_stats missing {f}"
        assert f in result["p_values"], f"p_values missing {f}"


def test_regress_fi_sleeve_returns_none_on_insufficient_data():
    """Fewer than 30 aligned observations → None."""
    short_dates = pd.date_range("2025-05-01", periods=5, freq="D")
    short_px = pd.DataFrame({
        "adj_close": np.ones(5), "close": np.ones(5),
    }, index=short_dates)

    short_ff = pd.DataFrame({
        "Mkt-RF": np.zeros(5), "SMB": np.zeros(5), "HML": np.zeros(5),
        "RMW":    np.zeros(5), "CMA": np.zeros(5), "RF":  np.zeros(5),
    }, index=pd.bdate_range("2025-05-01", periods=5))

    with patch("src.factors.get_prices", return_value=short_px), \
         patch("src.factors.load_factors", return_value=short_ff):
        result = regress_fi_sleeve("2025-05-01", "2025-05-07")

    assert result is None


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
         patch("src.reports.get_inception_date", return_value="2025-05-01"):
        section = _build_factor_section("2025-09-30")

    assert section is not None
    assert "sleeves" in section

    # EXACT count, not >= 1. Both regressions are mocked to succeed here, so every
    # sleeve in _SLEEVES must come back. `>= 1` passed even if a sleeve silently
    # dropped out — which is exactly what happens when a sleeve's spec breaks:
    # _build_factor_section tolerates partial results by design (it returns None
    # only if BOTH fail), so a dropped sleeve is invisible to a >= check. Counted
    # against _SLEEVES rather than a literal so the assertion tracks the config.
    from src.factors import _SLEEVES

    assert len(section["sleeves"]) == len(_SLEEVES), (
        f"Expected one section entry per configured factor sleeve "
        f"({len(_SLEEVES)}: {sorted(_SLEEVES)}), got {len(section['sleeves'])}. "
        f"With both regressions mocked to succeed, a shortfall means a sleeve was "
        f"silently dropped — do NOT relax this back to >=."
    )

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


# ── Ken French fetch retry tests ──────────────────────────────────────────────

def test_fetch_factors_retries_on_transient_failure(monkeypatch):
    """_fetch_factors retries on transient HTTP failure and succeeds on second attempt."""
    import io as _io
    import zipfile as _zf
    import requests as _req
    import src.factors as _f

    call_count = [0]

    def _make_zip(csv_text: str) -> bytes:
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as z:
            z.writestr("factors.CSV", csv_text)
        return buf.getvalue()

    _csv = (
        "Some header\n\n"
        ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
        "20200102,   0.50,   0.10,  -0.20,   0.30,   0.10,   0.01\n"
        "20200103,   0.30,  -0.05,   0.15,   0.20,  -0.05,   0.01\n\n"
        "Annual Factors\n"
    )

    class _MockResponse:
        status_code = 200
        content = _make_zip(_csv)
        def raise_for_status(self): pass

    def _mock_get(url, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            raise _req.exceptions.ConnectionError("transient network error")
        return _MockResponse()

    monkeypatch.setattr(_req, "get", _mock_get)
    monkeypatch.setattr(_f, "_FF_RETRY_DELAYS", (0, 0, 0))

    df = _f._fetch_factors("https://example.com/fake.zip")
    assert call_count[0] == 2
    assert "Mkt-RF" in df.columns
    assert len(df) == 2


def test_fetch_factors_raises_after_all_retries(monkeypatch):
    """_fetch_factors raises RuntimeError after all retry attempts are exhausted."""
    import requests as _req
    import src.factors as _f

    monkeypatch.setattr(_req, "get", lambda *a, **kw: (_ for _ in ()).throw(
        _req.exceptions.ConnectionError("always fails")
    ))
    monkeypatch.setattr(_f, "_FF_RETRY_DELAYS", (0, 0, 0))

    with pytest.raises(RuntimeError, match="Ken French factor download failed"):
        _f._fetch_factors("https://example.com/fake.zip")


# ── Factor-sleeve config is tied to live DB data ──────────────────────────────
# _SLEEVES (src/factors.py) is a hand-maintained config with no DB backing and,
# until now, no test asserting its shape at all. Nothing stopped it from drifting
# out of step with the SAA: a sleeve could be added to asset_classes and the factor
# page would silently keep regressing the old ticker set. These tie the config to
# the DB so the drift is loud. Both sides are read live.

def test_factor_sleeve_tickers_all_exist_in_securities():
    """Every ticker named in _SLEEVES must be a real security in the DB.

    Catches a renamed/retired holding leaving a factor sleeve regressing a ticker
    the portfolio no longer defines — the regression would still run and still
    render, against the wrong instrument.
    """
    import pandas as pd
    from src.factors import _SLEEVES
    from src.db import get_connection

    with get_connection() as conn:
        known = set(pd.read_sql_query("SELECT ticker FROM securities", conn)["ticker"])

    declared = {t for spec in _SLEEVES.values() for t in spec["tickers"]}
    missing = declared - known
    assert not missing, (
        f"_SLEEVES names tickers absent from securities: {sorted(missing)}. "
        f"A factor sleeve is regressing an instrument the portfolio does not define."
    )


def test_factor_sleeve_weights_cover_their_tickers_exactly():
    """A weighted sleeve's weight keys must match its ticker list exactly.

    A ticker present in `tickers` but missing from `weights` would be silently
    dropped from the regression's synthetic series; a weight key with no ticker
    would be dead config. Neither raises today.
    """
    from src.factors import _SLEEVES

    for key, spec in _SLEEVES.items():
        if "weights" not in spec:
            continue
        assert set(spec["weights"]) == set(spec["tickers"]), (
            f"_SLEEVES['{key}'] weights {sorted(spec['weights'])} do not match its "
            f"tickers {sorted(spec['tickers'])}."
        )


def test_every_factor_sleeve_declares_at_least_one_ticker():
    """A sleeve with an empty ticker list would regress nothing and report nothing,
    without failing — the shape of the silent-coverage-loss bug."""
    from src.factors import _SLEEVES

    empty = [k for k, spec in _SLEEVES.items() if not spec.get("tickers")]
    assert not empty, f"_SLEEVES entries declare no tickers: {empty}"
