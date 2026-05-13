"""Snapshot tests pinning exact output of dynamic interpretation functions.

If output formatting changes intentionally, update the expected string here.
If it changes unintentionally, the test will fail and force review.

These complement the existing substring/structure tests in test_interpretations.py —
those check what the output should contain; these check what it should literally be.

Expected strings were captured by running each function with canonical inputs and
copying the repr() output — never transcribed from memory or this file's spec.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.macro import (
    interpret_excess_cape,
    interpret_curve_spread,
    interpret_hy_spread,
    interpret_gdp_growth,
    interpret_us_vs_intl_spread,
)
from src.factors import (
    interpret_benchmark_attribution,
    interpret_correlations,
    interpret_sleeve_regression,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_res(
    betas: dict,
    t_stats: dict,
    alpha_bps: float = 0.0,
    t_alpha: float = 0.5,
    r2: float = 0.92,
    T: int = 250,
    sleeve_label: str = "Test Sleeve",
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
        "sleeve_label":     sleeve_label,
        "sample_start":     "2025-05-01",
        "sample_end":       "2026-05-01",
        "tickers":          ["TST"],
    }


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


# ── 1. interpret_excess_cape ─────────────────────────────────────────────────

class TestExcessCapeSnapshot:
    """Snapshot tests — pins exact output for two extreme percentile branches.

    Extreme-low fires when ECY is deeply compressed (equities expensive vs bonds).
    Extreme-high fires when ECY is unusually wide (equities cheap vs bonds).
    Both branches fire regularly as macro regimes rotate; the middle branch
    is pinned via the median test.
    """

    def test_extreme_low_branch(self):
        """Bottom-decile ECY — extreme compression, muted forward return signal."""
        result = interpret_excess_cape(0.48, 0.05)
        expected = (
            "ECY of 0.48% is at extreme compression — at the 5% percentile of "
            "the Jan 2003+ history, equities offer essentially no real-yield premium "
            "over bonds. This historically precedes muted forward equity returns; the "
            "Core Fixed Income and TIPS sleeves are competitively priced relative to "
            "equities on a real-yield basis."
        )
        assert result == expected

    def test_median_branch(self):
        """Mid-distribution ECY — near historical median, balanced equity/bond tradeoff."""
        result = interpret_excess_cape(2.50, 0.50)
        expected = (
            "ECY of 2.50% is near the historical median (at the 50% percentile). "
            "Equities offer a moderate real-yield premium over bonds — neither a "
            "strong valuation tailwind nor headwind for forward returns."
        )
        assert result == expected

    def test_extreme_high_branch(self):
        """Top-decile ECY — unusually wide premium, historically undervalued signal."""
        result = interpret_excess_cape(5.00, 0.95)
        expected = (
            "ECY of 5.00% is unusually wide — at the 95% percentile of the "
            "Jan 2003+ history, equities offer a large real-yield premium over bonds. "
            "This has historically signalled materially undervalued equity markets."
        )
        assert result == expected


# ── 2. interpret_curve_spread ─────────────────────────────────────────────────

class TestCurveSpreadSnapshot:
    """Snapshot tests — pins exact output for inverted and steep yield curve branches.

    Inverted fires during recession-risk windows; steep fires in early-cycle recovery.
    Both branches appear in live production as the yield curve cycles.
    """

    def test_inverted_branch(self):
        """Deeply inverted curve — recession lead-time signal."""
        result = interpret_curve_spread(-100.0)
        expected = (
            "The yield curve is inverted at -100 bps — short-term rates exceed "
            "long-term rates. Persistent inversion has preceded each of the last seven "
            "US recessions with a 12–18 month lead time. Allocators watch the "
            "un-inversion (curve steepening back above zero) as the signal that a "
            "cutting cycle is underway, not the inversion itself."
        )
        assert result == expected

    def test_flat_branch(self):
        """Flat-but-positive curve — late-cycle caution, re-inversion risk."""
        result = interpret_curve_spread(25.0)
        expected = (
            "The yield curve is flat at +25 bps — the 10Y−2Y spread is "
            "positive but compressed. A flat curve reflects limited term premium and "
            "implies bond markets expect short rates to remain near current levels; "
            "it warrants monitoring for re-inversion."
        )
        assert result == expected

    def test_steep_branch(self):
        """Steep curve — early-cycle recovery, high term premium."""
        result = interpret_curve_spread(200.0)
        expected = (
            "The yield curve is steep at +200 bps. A steep curve historically reflects "
            "high term premium and often emerges in early-cycle recoveries as short "
            "rates are cut while long-end inflation expectations remain elevated — "
            "historically favorable for duration and early-cycle equity returns."
        )
        assert result == expected


# ── 3. interpret_hy_spread ────────────────────────────────────────────────────

class TestHySpreadSnapshot:
    """Snapshot tests — pins exact output for tight and recessionary HY spread branches.

    Tight fires during late-cycle complacency; recessionary fires during crisis episodes.
    """

    def test_tight_branch(self):
        """Below-300-bps spreads — late-cycle credit complacency."""
        result = interpret_hy_spread(250.0)
        expected = (
            "HY spreads at 250 bps are historically tight (below 300 bps) — "
            "late-cycle credit market complacency. Tight spreads limit the cushion "
            "for further compression; historical episodes of sub-300 bps spreads have "
            "preceded equity peaks and subsequent spread blowouts."
        )
        assert result == expected

    def test_normal_branch(self):
        """Mid-cycle normal range — healthy credit environment."""
        result = interpret_hy_spread(375.0)
        expected = (
            "HY spreads at 375 bps are in the normal mid-cycle range "
            "(300–450 bps), consistent with moderate default risk and a healthy "
            "credit environment."
        )
        assert result == expected

    def test_recessionary_branch(self):
        """Above-800-bps spreads — pricing severe recession and elevated defaults."""
        result = interpret_hy_spread(900.0)
        expected = (
            "HY spreads at 900 bps are at recessionary levels (above 800 bps). "
            "Credit markets are pricing severe recession and elevated default rates "
            "— levels that have historically coincided with the most severe "
            "equity drawdowns."
        )
        assert result == expected


# ── 4. interpret_gdp_growth ───────────────────────────────────────────────────

class TestGdpGrowthSnapshot:
    """Snapshot tests — negative growth and near-trend growth branches."""

    def test_negative_branch(self):
        """Negative real GDP — informal recession definition met."""
        result = interpret_gdp_growth(-1.0)
        expected = (
            "Real GDP growth of -1.0% is negative. Two consecutive quarters of "
            "negative growth satisfies the informal recession definition (NBER uses "
            "a broader indicator set). Negative growth is associated with rising "
            "unemployment, falling earnings, and widening credit spreads — "
            "conditions where duration and quality equity historically outperform."
        )
        assert result == expected

    def test_near_trend_branch(self):
        """Near-trend growth (3.0%) — Goldilocks mid-cycle range."""
        result = interpret_gdp_growth(3.0)
        expected = (
            "Real GDP growth of 3.0% is near the long-run potential output trend of "
            "~2.5% — a mid-cycle Goldilocks range. On-trend growth is associated "
            "with stable corporate earnings and balanced equity risk premiums; "
            "the SAA is calibrated for this baseline environment."
        )
        assert result == expected


# ── 5. interpret_us_vs_intl_spread ────────────────────────────────────────────

class TestUsVsIntlSpreadSnapshot:
    """Snapshot tests — pins the two extreme spread branches.

    Far-above-mean fires during extended US leadership (current market).
    Far-below-mean fires if international strongly reverses the gap.
    """

    def test_far_above_mean_branch(self):
        """15 pp above 5-year rolling average — extended US leadership territory."""
        result = interpret_us_vs_intl_spread(20.0, 5.0)
        expected = (
            "Over the trailing 12 months, US equities (SPY) have outperformed "
            "international developed (EFA) by 20.0%. On a 5-year rolling basis, "
            "the spread is 15.0% above its 5-year rolling average of 5.0% — "
            "well into extended US-leadership territory. Historically such extremes "
            "have mean-reverted via valuation convergence and dollar cycle turns, "
            "supporting the case for the International Developed sleeve."
        )
        assert result == expected

    def test_far_below_mean_branch(self):
        """15 pp below 5-year rolling average — strong international reversal."""
        result = interpret_us_vs_intl_spread(-5.0, 10.0)
        expected = (
            "Over the trailing 12 months, US equities (SPY) have underperformed "
            "international developed (EFA) by 5.0%. On a 5-year rolling basis, "
            "the spread is 15.0% below its 5-year rolling average of 10.0% — "
            "a strong reversal in international's favor, consistent with the "
            "valuation mean-reversion thesis underlying the 19% International "
            "Developed sleeve weight."
        )
        assert result == expected


# ── 6. interpret_sleeve_regression ───────────────────────────────────────────

class TestSleeveRegressionSnapshot:
    """Snapshot tests — three branches covering production-relevant scenarios."""

    def test_significant_factors_insignificant_alpha(self):
        """Three significant loadings, alpha not significant — typical production case."""
        res = _make_res(
            betas={"Mkt-RF": 1.02, "HML": 0.28, "SMB": 0.04, "RMW": -0.18, "CMA": 0.01},
            t_stats={"Mkt-RF": 22.0, "HML": 3.1, "SMB": 0.7, "RMW": -2.3, "CMA": 0.2},
            alpha_bps=15.0, t_alpha=0.4, r2=0.94,
        )
        result = interpret_sleeve_regression(res, ["Mkt-RF", "HML", "SMB", "RMW", "CMA"])
        expected = (
            "Test Sleeve shows significant loadings on: "
            "Mkt-RF loading: +1.020 (t = 22.00, positive, significant); "
            "HML loading: +0.280 (t = 3.10, positive, significant); "
            "RMW loading: -0.180 (t = -2.30, negative, significant). "
            "Annualized alpha of +15 bps (t = 0.40) is not statistically significant "
            "— the sleeve's returns are consistent with factor exposures alone."
        )
        assert result == expected

    def test_no_significant_factors_short_sample(self):
        """120-day sample — no |t|>2 loadings; short-sample caveat appears in output."""
        res = _make_res(
            betas={"Mkt-RF": 0.95, "HML": 0.10},
            t_stats={"Mkt-RF": 1.5, "HML": 0.9},
            alpha_bps=50.0, t_alpha=0.8, T=120,
        )
        result = interpret_sleeve_regression(res, ["Mkt-RF", "HML"])
        expected = (
            "Test Sleeve has no statistically significant factor loadings "
            "at the |t| > 2 threshold across the 120 observed trading days. "
            "This may reflect a short sample period, not an absence of factor exposure. "
            "Annualized alpha of +50 bps (t = 0.80) is not statistically significant "
            "— the sleeve's returns are consistent with factor exposures alone."
        )
        assert result == expected

    def test_significant_alpha(self):
        """Statistically significant positive alpha — short-sample caveat included."""
        res = _make_res(
            betas={"Mkt-RF": 1.05, "HML": 0.15},
            t_stats={"Mkt-RF": 18.0, "HML": 1.0},
            alpha_bps=420.0, t_alpha=2.5,
        )
        result = interpret_sleeve_regression(res, ["Mkt-RF", "HML"])
        expected = (
            "Test Sleeve shows significant loadings on: "
            "Mkt-RF loading: +1.050 (t = 18.00, positive, significant). "
            "Annualized alpha is +420 bps (t = 2.50), statistically significant "
            "— a positive active return not explained by the factor model. "
            "Given the short sample, this estimate carries wide uncertainty and "
            "should not be read as evidence of persistent skill."
        )
        assert result == expected


# ── 7. interpret_benchmark_attribution ───────────────────────────────────────

class TestBenchmarkAttributionSnapshot:
    """Snapshot tests — three benchmark attribution scenarios."""

    def test_close_to_1_no_style_tilts_insig_alpha(self):
        """Beta near 1, no significant style tilts, insignificant alpha — typical early period."""
        res = _make_bench_res(
            b_bench=1.01, t_bench=55.0,
            t_hml=1.0, t_smb=0.5, t_rmw=0.8,
            alpha_bps=35.0, t_alpha=0.6,
        )
        result = interpret_benchmark_attribution(res)
        expected = (
            "The portfolio's benchmark beta of 1.010 (t = 55.00) indicates it tracks "
            "its SAA policy benchmark closely, as expected for a fully-invested "
            "passive/semi-passive implementation. "
            "No statistically significant residual style tilts beyond the SAA benchmark "
            "are detected at the current sample length. "
            " The active return intercept of +35 bps (t = 0.60) is not statistically "
            "significant at the current sample length — the portfolio's return is "
            "consistent with its SAA benchmark exposure and residual style tilts alone."
        )
        assert result == expected

    def test_significant_rmw_insig_alpha(self):
        """Beta near 1, significant profitability (RMW) tilt, insignificant alpha."""
        res = _make_bench_res(
            b_bench=1.00, t_bench=52.0,
            b_rmw=0.18, t_rmw=2.8,
            t_hml=1.2, t_smb=0.7,
            alpha_bps=80.0, t_alpha=1.2,
        )
        result = interpret_benchmark_attribution(res)
        expected = (
            "The portfolio's benchmark beta of 1.000 (t = 52.00) indicates it tracks "
            "its SAA policy benchmark closely, as expected for a fully-invested "
            "passive/semi-passive implementation. "
            "Residual style tilts beyond the SAA benchmark: "
            "RMW β = +0.180 (t = 2.80, positive — profitability tilt (SPHQ, AVUV)). "
            "These represent the portfolio's active factor exposures relative to "
            "the SAA policy benchmark, not explained by the target weights. "
            "The active return intercept of +80 bps (t = 1.20) is not statistically "
            "significant at the current sample length — the portfolio's return is "
            "consistent with its SAA benchmark exposure and residual style tilts alone."
        )
        assert result == expected

    def test_beta_departs_significant_alpha(self):
        """Beta departs from 1.0, significant positive alpha — flags cross-sleeve dispersion."""
        res = _make_bench_res(
            b_bench=0.85, t_bench=40.0,
            t_hml=0.8, t_smb=0.4, t_rmw=1.1,
            alpha_bps=350.0, t_alpha=2.2,
        )
        result = interpret_benchmark_attribution(res)
        expected = (
            "The portfolio's benchmark beta of 0.850 (t = 40.00) departs from the "
            "expected 1.0, reflecting cross-sleeve return dispersion or cash drag. "
            "No statistically significant residual style tilts beyond the SAA benchmark "
            "are detected at the current sample length. "
            " The active return intercept is +350 bps (t = 2.20), statistically "
            "significant — a positive return after accounting for both the SAA "
            "benchmark and residual style exposures. The Brinson-Fachler attribution "
            "on the Performance page decomposes this into allocation and selection "
            "effects at the sleeve level."
        )
        assert result == expected


# ── 8. interpret_correlations ─────────────────────────────────────────────────

class TestCorrelationsSnapshot:
    """Snapshot test — standard 5-sleeve correlation matrix (3 equity + 2 bond)."""

    def test_standard_correlation_matrix(self):
        """Pins the exact 3-sentence output for the canonical sleeve correlation matrix."""
        result = interpret_correlations(_make_corr())
        expected = (
            "The strongest cross-sleeve correlations are "
            "US Large Core × US Large Quality (ρ = 0.92), "
            "US Large Core × US Small Cap (ρ = 0.85), "
            "US Large Quality × US Small Cap (ρ = 0.80), "
            "all within the broad equity bucket — expected co-movement driven "
            "by common global market risk.  \n"
            "The most diversifying pairs are "
            "US Small Cap × Core Fixed Income (ρ = 0.05) and "
            "US Large Quality × Core Fixed Income (ρ = 0.08), "
            "providing meaningful return offsets in normal market conditions.  \n"
            "Bond sleeves are weakly correlated with equities on average "
            "(ρ ≈ 0.10), consistent with their role as portfolio ballast."
        )
        assert result == expected
