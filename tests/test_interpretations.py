"""Tests for dynamic interpretation functions in src/macro.py and src/factors.py."""
import re
import sys
import pathlib

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.macro import (
    EXCESS_CAPE_CHEAP,
    EXCESS_CAPE_FAIR,
    EXCESS_CAPE_RICH,
    EXCESS_CAPE_RICH_THRESHOLD,
    ECY_EXTREME_LOW_PCT,
    ECY_LOW_PCT,
    ECY_HIGH_PCT,
    ECY_EXTREME_HIGH_PCT,
    CURVE_FLAT,
    CURVE_INVERTED,
    CURVE_NORMAL,
    HY_SPREAD_NORMAL,
    HY_SPREAD_RECESSIONARY,
    HY_SPREAD_TIGHT,
    HY_SPREAD_WIDE,
    GDP_TREND,
    GDP_ABOVE_TREND,
    interpret_curve_spread,
    interpret_excess_cape,
    interpret_gdp_growth,
    interpret_hy_spread,
    interpret_us_vs_intl_spread,
)
from src.factors import (
    interpret_benchmark_attribution,
    interpret_correlations,
    interpret_sleeve_regression,
)


# ── Placeholder guard utility ────────────────────────────────────────────────
# Matches {NAME}, {NAME:fmt}, [NAME], [NAME:fmt], %(name)s, ${NAME} patterns.
_PLACEHOLDER_PATTERNS = [
    r"\{[A-Z_][A-Z_0-9]*(?::[^}]+)?\}",       # {NAME} or {NAME:fmt}
    r"\[[A-Z_][A-Z_0-9]*(?::[^\]]+)?\]",       # [NAME] or [NAME:fmt]
    r"%\([A-Za-z_][A-Za-z_0-9]*\)[sdif]",      # %(name)s style
    r"\$\{[A-Za-z_][A-Za-z_0-9]*\}",           # ${NAME} style
]

def _assert_no_placeholders(text: str) -> None:
    for pattern in _PLACEHOLDER_PATTERNS:
        matches = re.findall(pattern, text)
        assert not matches, f"Unrendered placeholder found in output: {matches!r}\nFull text: {text!r}"


# ── 1. interpret_excess_cape ─────────────────────────────────────────────────

class TestInterpretExcessCape:
    def test_extreme_low_percentile(self):
        # Bottom decile → extreme compression branch
        text = interpret_excess_cape(0.48, ECY_EXTREME_LOW_PCT - 0.05)
        assert "extreme" in text.lower() or "compression" in text.lower()
        assert "bond" in text.lower()

    def test_low_percentile(self):
        # Between ECY_EXTREME_LOW_PCT and ECY_LOW_PCT → below-average branch
        text = interpret_excess_cape(1.0, (ECY_EXTREME_LOW_PCT + ECY_LOW_PCT) / 2)
        assert "below" in text.lower() or "thin" in text.lower()

    def test_median_percentile(self):
        # Between ECY_LOW_PCT and ECY_HIGH_PCT → near median branch
        text = interpret_excess_cape(2.5, (ECY_LOW_PCT + ECY_HIGH_PCT) / 2)
        assert "median" in text.lower() or "moderate" in text.lower() or "near" in text.lower()

    def test_high_percentile(self):
        # Between ECY_HIGH_PCT and ECY_EXTREME_HIGH_PCT → above-average branch
        text = interpret_excess_cape(3.5, (ECY_HIGH_PCT + ECY_EXTREME_HIGH_PCT) / 2)
        assert "premium" in text.lower() or "meaningful" in text.lower() or "above" in text.lower()

    def test_extreme_high_percentile(self):
        # Top decile → unusually wide branch
        text = interpret_excess_cape(5.0, ECY_EXTREME_HIGH_PCT + 0.05)
        assert "unusually" in text.lower() or "large" in text.lower() or "wide" in text.lower()

    def test_returns_f_string_with_value(self):
        text = interpret_excess_cape(1.73, 0.50)
        assert "1.73" in text

    def test_production_scenario_not_fair_range(self):
        # ECY=0.48% at 0th percentile must NOT return "fair range" text
        text = interpret_excess_cape(0.48, 0.00)
        assert "fair" not in text.lower()
        assert "extreme" in text.lower() or "compression" in text.lower()

    def test_runtime_assertion_fires_on_percentage_units(self):
        # percentile=50.0 is a units bug (should be 0.50); assert must fire
        with pytest.raises(AssertionError):
            interpret_excess_cape(0.48, 50.0)

    def test_no_unrendered_placeholders(self):
        for pct in [0.00, 0.05, 0.20, 0.50, 0.80, 0.95, 1.00]:
            text = interpret_excess_cape(2.0, pct)
            _assert_no_placeholders(text)


# ── 2. interpret_curve_spread ────────────────────────────────────────────────

class TestInterpretCurveSpread:
    def test_inverted(self):
        text = interpret_curve_spread(-50.0)
        assert "inverted" in text.lower()

    def test_flat(self):
        text = interpret_curve_spread(30.0)
        assert "flat" in text.lower() or "compressed" in text.lower()

    def test_normal(self):
        text = interpret_curve_spread(100.0)
        assert "normal" in text.lower() or "positive" in text.lower() or "upward" in text.lower()

    def test_steep(self):
        text = interpret_curve_spread(200.0)
        assert "steep" in text.lower()

    def test_returns_f_string_with_value(self):
        text = interpret_curve_spread(-25.0)
        assert "-25" in text or "−25" in text or "25" in text

    def test_no_unrendered_placeholders(self):
        for val in [-100.0, -10.0, 0.0, 30.0, 100.0, 200.0]:
            _assert_no_placeholders(interpret_curve_spread(val))


# ── 3. interpret_hy_spread ───────────────────────────────────────────────────

class TestInterpretHySpread:
    def test_tight(self):
        text = interpret_hy_spread(HY_SPREAD_TIGHT - 50)  # 250 bps
        assert "tight" in text.lower() or "complacency" in text.lower()

    def test_normal(self):
        text = interpret_hy_spread((HY_SPREAD_TIGHT + HY_SPREAD_NORMAL) / 2)  # 375 bps
        assert "normal" in text.lower() or "neutral" in text.lower() or "mid-cycle" in text.lower()

    def test_wide(self):
        text = interpret_hy_spread((HY_SPREAD_NORMAL + HY_SPREAD_WIDE) / 2)  # 525 bps
        assert "elevated" in text.lower() or "stress" in text.lower() or "recessionary" in text.lower()

    def test_stress(self):
        text = interpret_hy_spread((HY_SPREAD_WIDE + HY_SPREAD_RECESSIONARY) / 2)  # 700 bps
        assert "stress" in text.lower() or "recession" in text.lower()

    def test_recessionary(self):
        text = interpret_hy_spread(HY_SPREAD_RECESSIONARY + 100)  # 900 bps
        assert "recessionary" in text.lower() or "severe" in text.lower()

    def test_returns_f_string_with_value(self):
        text = interpret_hy_spread(350.0)
        assert "350" in text

    def test_tight_branch_renders_threshold_value(self):
        # The tight branch must render the HY_SPREAD_TIGHT constant (300), not leave a placeholder
        text = interpret_hy_spread(250.0)
        assert "300" in text, f"HY_SPREAD_TIGHT value not rendered — possible f-string bug: {text!r}"

    def test_tight_branch_no_unrendered_placeholders(self):
        # Specifically tests the branch that had the f-string bug
        _assert_no_placeholders(interpret_hy_spread(250.0))

    def test_no_unrendered_placeholders(self):
        for val in [200.0, 250.0, 375.0, 525.0, 700.0, 900.0]:
            _assert_no_placeholders(interpret_hy_spread(val))


# ── 4. interpret_gdp_growth ──────────────────────────────────────────────────

class TestInterpretGdpGrowth:
    def test_negative(self):
        text = interpret_gdp_growth(-1.0)
        assert "negative" in text.lower()

    def test_below_trend(self):
        text = interpret_gdp_growth(GDP_TREND - 0.5)  # 2.0%
        assert "below" in text.lower()
        assert str(GDP_TREND) in text

    def test_near_trend(self):
        text = interpret_gdp_growth((GDP_TREND + GDP_ABOVE_TREND) / 2)  # 3.0%
        assert "near" in text.lower() or "mid-cycle" in text.lower() or "goldilocks" in text.lower()

    def test_above_trend(self):
        text = interpret_gdp_growth(GDP_ABOVE_TREND + 0.5)  # 4.0%
        assert "above" in text.lower()
        assert str(GDP_TREND) in text

    def test_returns_f_string_with_value(self):
        text = interpret_gdp_growth(2.8)
        assert "2.8" in text

    def test_no_unrendered_placeholders(self):
        for val in [-2.0, 0.0, 1.5, 2.5, 3.0, 4.5]:
            _assert_no_placeholders(interpret_gdp_growth(val))


# ── 5. interpret_us_vs_intl_spread ──────────────────────────────────────────

class TestInterpretUsVsIntlSpread:
    def test_us_leading_well_above_mean(self):
        text = interpret_us_vs_intl_spread(20.0, 5.0)   # 15 pp above mean
        assert "outperformed" in text.lower()
        assert "mean" in text.lower() or "extended" in text.lower()

    def test_near_mean(self):
        text = interpret_us_vs_intl_spread(5.0, 5.0)    # at the mean
        assert "near" in text.lower() or "norm" in text.lower()

    def test_us_lagging_well_below_mean(self):
        text = interpret_us_vs_intl_spread(-5.0, 5.0)   # 10 pp below mean
        assert "underperformed" in text.lower()

    def test_value_in_output(self):
        text = interpret_us_vs_intl_spread(12.3, 4.0)
        assert "12.3" in text

    def test_handles_negative_current_spread(self):
        text = interpret_us_vs_intl_spread(-8.5, 3.0)
        assert "8.5" in text
        assert "underperformed" in text.lower()

    def test_uses_percent_not_pp(self):
        # Units convention: interpretation must use "%" not " pp"
        text = interpret_us_vs_intl_spread(6.7, 4.9)
        assert "%" in text
        assert " pp" not in text

    def test_no_unrendered_placeholders(self):
        for spread, mean in [(20.0, 5.0), (8.0, 5.0), (5.0, 5.0), (-5.0, 5.0), (-15.0, 5.0)]:
            _assert_no_placeholders(interpret_us_vs_intl_spread(spread, mean))


# ── 6. interpret_sleeve_regression ──────────────────────────────────────────

def _make_res(
    betas: dict,
    t_stats: dict,
    alpha_bps: float = 0.0,
    t_alpha: float = 0.5,
    r2: float = 0.92,
    T: int = 250,
) -> dict:
    p_values = {f: 0.05 if abs(t) >= 2.0 else 0.30 for f, t in t_stats.items()}
    return {
        "betas":            betas,
        "t_stats":          t_stats,
        "p_values":         p_values,
        "alpha_annual_bps": alpha_bps,
        "t_alpha":          t_alpha,
        "p_alpha":          0.06 if abs(t_alpha) >= 1.65 else 0.30,
        "r_squared":        r2,
        "adj_r_squared":    r2 - 0.01,
        "T":                T,
        "nw_lags":          4,
        "sleeve_label":     "Test Sleeve",
        "sample_start":     "2025-05-01",
        "sample_end":       "2026-05-01",
        "tickers":          ["TST"],
    }


class TestInterpretSleeveRegression:
    def test_significant_hml_appears(self):
        res = _make_res(
            betas={"Mkt-RF": 1.0, "HML": 0.30, "SMB": 0.05, "RMW": -0.10, "CMA": 0.02},
            t_stats={"Mkt-RF": 20.0, "HML": 3.5, "SMB": 0.8, "RMW": -2.5, "CMA": 0.4},
        )
        text = interpret_sleeve_regression(res, ["Mkt-RF", "HML", "SMB", "RMW", "CMA"])
        assert "HML" in text
        assert "+0.300" in text or "0.300" in text

    def test_alpha_insignificant_says_so(self):
        res = _make_res(
            betas={"Mkt-RF": 1.0, "HML": 0.2},
            t_stats={"Mkt-RF": 18.0, "HML": 1.0},
            alpha_bps=25.0, t_alpha=0.9,
        )
        text = interpret_sleeve_regression(res, ["Mkt-RF", "HML"])
        assert "not statistically significant" in text.lower() or "statistically" in text.lower()

    def test_significant_alpha_flagged(self):
        res = _make_res(
            betas={"Mkt-RF": 1.0, "HML": 0.1},
            t_stats={"Mkt-RF": 15.0, "HML": 0.5},
            alpha_bps=800.0, t_alpha=2.8,
        )
        text = interpret_sleeve_regression(res, ["Mkt-RF", "HML"])
        assert "800" in text
        assert "significant" in text.lower()

    def test_low_r2_flagged(self):
        res = _make_res(
            betas={"Mkt-RF": 0.5},
            t_stats={"Mkt-RF": 5.0},
            r2=0.45,
        )
        text = interpret_sleeve_regression(res, ["Mkt-RF"])
        assert "0.45" in text or "low" in text.lower() or "R²" in text

    def test_none_result_returns_unavailable(self):
        text = interpret_sleeve_regression(None)
        assert "unavailable" in text.lower()

    def test_no_unrendered_placeholders(self):
        res = _make_res(
            betas={"Mkt-RF": 1.0, "HML": 0.30, "SMB": 0.05, "RMW": -0.10, "CMA": 0.02},
            t_stats={"Mkt-RF": 20.0, "HML": 3.5, "SMB": 0.8, "RMW": -2.5, "CMA": 0.4},
        )
        text = interpret_sleeve_regression(res, ["Mkt-RF", "HML", "SMB", "RMW", "CMA"])
        _assert_no_placeholders(text)


# ── 7. interpret_benchmark_attribution ──────────────────────────────────────

def _make_bench_res(
    b_bench: float = 1.00,
    t_bench: float = 50.0,
    b_hml: float = 0.10,
    t_hml: float = 1.5,
    b_smb: float = 0.08,
    t_smb: float = 1.2,
    b_rmw: float = 0.12,
    t_rmw: float = 2.5,
    alpha_bps: float = 80.0,
    t_alpha: float = 1.2,
    r2: float = 0.98,
    T: int = 250,
) -> dict:
    return {
        "betas":            {"Bench-RF": b_bench, "HML": b_hml, "SMB": b_smb, "RMW": b_rmw},
        "t_stats":          {"Bench-RF": t_bench, "HML": t_hml, "SMB": t_smb, "RMW": t_rmw},
        "p_values":         {"Bench-RF": 0.001, "HML": 0.15, "SMB": 0.20, "RMW": 0.03},
        "alpha_annual_bps": alpha_bps,
        "t_alpha":          t_alpha,
        "p_alpha":          0.10,
        "r_squared":        r2,
        "adj_r_squared":    r2 - 0.01,
        "T":                T,
        "nw_lags":          4,
        "sample_start":     "2025-05-01",
        "sample_end":       "2026-05-01",
    }


class TestInterpretBenchmarkAttribution:
    def test_benchmark_beta_close_to_1_says_tracks(self):
        """Gray-box conveys tracking qualitatively; specific β values live in the table. Phase 40."""
        res = _make_bench_res(b_bench=1.02, t_bench=50.0)
        text = interpret_benchmark_attribution(res)
        assert "tracks" in text.lower()
        assert "benchmark" in text.lower()
        assert "1.020" not in text  # numbers cut from gray-box (Phase 40 Item 2)

    def test_significant_rmw_appears(self):
        res = _make_bench_res(b_rmw=0.20, t_rmw=3.0)
        text = interpret_benchmark_attribution(res)
        assert "RMW" in text

    def test_no_significant_style_when_all_low_t(self):
        res = _make_bench_res(t_hml=0.8, t_smb=0.5, t_rmw=1.0)
        text = interpret_benchmark_attribution(res)
        assert "no statistically significant" in text.lower() or "statistically" in text.lower()

    def test_significant_alpha_flagged(self):
        """Gray-box flags alpha significance; bps value lives in regression table. Phase 40."""
        res = _make_bench_res(alpha_bps=250.0, t_alpha=2.5)
        text = interpret_benchmark_attribution(res)
        assert "significant" in text.lower()
        assert "250" not in text  # numbers cut from gray-box (Phase 40 Item 2)

    def test_none_returns_unavailable(self):
        text = interpret_benchmark_attribution(None)
        assert "unavailable" in text.lower()

    def test_no_unrendered_placeholders(self):
        _assert_no_placeholders(interpret_benchmark_attribution(_make_bench_res()))


# ── 8. interpret_correlations ────────────────────────────────────────────────

def _make_corr() -> pd.DataFrame:
    sleeves = [
        "US Large Core", "US Large Quality", "US Small Cap",
        "Core Fixed Income", "TIPS",
    ]
    vals = {
        "US Large Core":     [1.00, 0.92, 0.85, 0.10, 0.15],
        "US Large Quality":  [0.92, 1.00, 0.80, 0.08, 0.12],
        "US Small Cap":      [0.85, 0.80, 1.00, 0.05, 0.10],
        "Core Fixed Income": [0.10, 0.08, 0.05, 1.00, 0.75],
        "TIPS":              [0.15, 0.12, 0.10, 0.75, 1.00],
    }
    return pd.DataFrame(vals, index=sleeves)


class TestInterpretCorrelations:
    def test_returns_non_empty_string(self):
        text = interpret_correlations(_make_corr())
        assert len(text) > 50

    def test_identifies_high_equity_pair(self):
        text = interpret_correlations(_make_corr())
        assert "US Large Core" in text or "US Large Quality" in text or "equity" in text.lower()

    def test_identifies_diversifying_pair(self):
        text = interpret_correlations(_make_corr())
        assert "Core Fixed Income" in text or "diversif" in text.lower() or "ballast" in text.lower()

    def test_bond_equity_cross_asset_noted(self):
        text = interpret_correlations(_make_corr())
        assert "bond" in text.lower() or "equity" in text.lower()

    def test_empty_returns_empty_string(self):
        assert interpret_correlations(pd.DataFrame()) == ""

    def test_small_matrix_returns_empty_string(self):
        tiny = pd.DataFrame(
            [[1.0, 0.5], [0.5, 1.0]],
            index=["A", "B"], columns=["A", "B"]
        )
        assert interpret_correlations(tiny) == ""

    def test_no_unrendered_placeholders(self):
        _assert_no_placeholders(interpret_correlations(_make_corr()))
