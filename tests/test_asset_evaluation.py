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
    9. rf_annual_value
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

def test_raw_weights_match_db_targets_and_scale(use_demo_db):
    """The derived ex-cash MV weights must equal the DB targets, normalised.

    Replaces a vacuous assertion. The old test summed SLEEVE_WEIGHTS and checked
    it equalled 1.0, but src/asset_evaluation.py:62 *defines* SLEEVE_WEIGHTS as
    `{k: v / _raw_total}` — so the sum is 1.0 by construction and the assertion
    only proved that division works. It passed even if every raw weight were
    doubled, or if all nine were wrong in the same proportion.

    This checks the two things that assertion could not:
      SHAPE — each raw weight, normalized, equals the DB's strategic target.
      SCALE — the raw weights sum to the seed's documented ex-cash normalizer,
              which is what makes them *raw SAA weights* rather than any
              proportional set. A uniform rescale is invisible downstream but
              means _RAW_WEIGHTS no longer says what its comment claims.

    Both sides read live. The name map is derived from _RAW_WEIGHTS' own keys, so
    a new sleeve is carried automatically and an unmappable one fails loudly —
    rather than being quietly skipped by a hand-maintained list.
    """
    import src.asset_evaluation as ae
    from src.db import get_connection

    # asset_evaluation no longer hardcodes _RAW_WEIGHTS — the ex-cash MV weights
    # are DERIVED from asset_classes at import (mode-frozen), so re-derive under
    # the pinned demo DB. "shape == DB" is now largely structural (both read the
    # same table); this still guards the abbreviation bridge and the ex-cash
    # normalisation (weights sum to 1.0, every DB sleeve present, none dropped).
    _, weights = ae._derive_sleeve_maps()

    # The benchmark-proxy subsystem abbreviates exactly one family of sleeve names.
    _AE_ONLY_ALIAS = {
        "Intl Core":        "International Core",
        "Intl Quality":     "International Quality",
        "Intl Large Value": "International Large Value",
        "Intl Small Value": "International Small Value",
    }
    ae_to_db = {a: _AE_ONLY_ALIAS.get(a, a) for a in weights}

    with get_connection() as conn:
        db = {
            r["name"]: r["target_weight"]
            for r in conn.execute(
                "SELECT name, target_weight FROM asset_classes "
                "WHERE parent_id IS NOT NULL AND target_weight > 0"
            ).fetchall()
        }
    db_total = sum(db.values())

    # SCALE — the ex-cash MV weights normalise to 1.0.
    assert abs(sum(weights.values()) - 1.0) < 1e-9, (
        f"derived ex-cash weights sum to {sum(weights.values()):.10f}, not 1.0"
    )

    # SHAPE — every AE sleeve exists in the DB and equals its normalised target.
    for ae_name, db_name in ae_to_db.items():
        assert db_name in db, (
            f"derived weights name '{ae_name}' (-> DB '{db_name}'), which is not a "
            f"strategic sleeve in asset_classes. DB has: {sorted(db)}"
        )
        assert abs(weights[ae_name] - db[db_name] / db_total) < 1e-6, (
            f"weights['{ae_name}']={weights[ae_name]:.6f} != DB '{db_name}' "
            f"normalised {db[db_name] / db_total:.6f}. The two SAA sources diverged."
        )

    # ...and the DB must not carry a strategic sleeve the derivation omits.
    unmapped = set(db) - set(ae_to_db.values())
    assert not unmapped, (
        f"asset_classes has strategic sleeves absent from the derived weights: "
        f"{sorted(unmapped)}. The MV baseline would silently exclude them."
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


# ── 9. RF_ANNUAL cross-source consistency ───────────────────────────────────
#
# Corrected 2026-07-07: asset_evaluation.RF_ANNUAL previously pinned its own
# static 0.0432 (4.32%, arithmetic daily conversion), a different rate and
# convention than src/performance.py's compute_risk_metrics default (0.045,
# geometric daily conversion) — despite this module's own comment once
# claiming the two were "consistent". The two are now standardized on the
# same rate (4.5%) and the same geometric daily-compounding convention. This
# test asserts CROSS-SOURCE equality against performance.py's own default
# (not a literal pin of either value) so a future edit to either side that
# breaks the match fails CI immediately, instead of silently drifting the
# way the arithmetic/4.32% mismatch did.

def test_rf_annual_matches_performance_rf():
    """ae.RF_ANNUAL must equal compute_risk_metrics' default rf_annual — the
    same rate disclosed in the Performance page's Sharpe/Sortino caption
    (see test_identity_layer1.py's test_identity_rf_default_matches_caption_disclosure).
    """
    import inspect

    from src.performance import compute_risk_metrics

    perf_rf = inspect.signature(compute_risk_metrics).parameters["rf_annual"].default
    assert RF_ANNUAL == pytest.approx(perf_rf, abs=1e-10), (
        f"ae.RF_ANNUAL = {RF_ANNUAL:.6f} but compute_risk_metrics' default "
        f"rf_annual = {perf_rf:.6f} — the Asset Evaluation and Performance pages "
        "must use the same risk-free rate. If one changed intentionally, update "
        "the other to match (and its caption disclosure)."
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


# ── Phase 31: aggregate rolling correlation helpers ─────────────────────────────

def _corr_frame(n=120):
    """3-column frame: B = A (ρ=+1), C = -A (ρ=-1)."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    a = pd.Series(np.linspace(0.0, 1.0, n) + np.sin(np.arange(n)) * 0.01, index=idx)
    return pd.DataFrame({"A": a, "B": a.copy(), "C": -a})


def test_rolling_pairwise_mean_known_value():
    df = _corr_frame(120)
    m = ae.rolling_pairwise_mean(df, window=120)
    # Pairs: A×B=+1, A×C=-1, B×C=-1 → mean = (1 - 1 - 1)/3 = -1/3.
    assert len(m) == 1
    assert m.iloc[-1] == pytest.approx(-1.0 / 3.0, abs=1e-6)


def test_rolling_pairwise_band_min_max():
    df = _corr_frame(120)
    band = ae.rolling_pairwise_band(df, window=120)
    assert band["max"].iloc[-1] == pytest.approx(1.0, abs=1e-6)
    assert band["min"].iloc[-1] == pytest.approx(-1.0, abs=1e-6)


def test_rolling_pairwise_mean_is_generic_over_n():
    # 2 columns → single pair → mean equals that pair's correlation (+1 here).
    df = _corr_frame(120)
    two = ae.rolling_pairwise_mean(df[["A", "B"]], window=120)
    assert two.iloc[-1] == pytest.approx(1.0, abs=1e-6)
    # 5 columns (guards Phase 32 reuse with an extra candidate column).
    five = pd.concat([df, df[["A", "B"]].add_suffix("2")], axis=1)
    assert not ae.rolling_pairwise_mean(five, window=120).empty


def test_rolling_equity_vs_bondequity_classification():
    n = 120
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    e = pd.Series(np.linspace(0.0, 1.0, n) + np.sin(np.arange(n)) * 0.01, index=idx)
    df = pd.DataFrame({
        "US Large Core":     e,
        "US Small Cap":      e.copy(),     # intra-equity ρ = +1
        "Core Fixed Income": -e,           # bond–equity ρ = -1
        "Real Assets":       e * 0.0 + np.arange(n),  # excluded from both groups
    })
    dec = ae.rolling_equity_vs_bondequity(df, window=120)
    assert dec["intra_equity"].iloc[-1] == pytest.approx(1.0, abs=1e-6)
    assert dec["bond_equity"].iloc[-1] == pytest.approx(-1.0, abs=1e-6)


def test_interpret_rolling_correlation_branches():
    from src.macro import percentile as macro_percentile
    hist = pd.Series(
        [0.1, 0.2, 0.3, 0.4, 0.5],
        index=pd.date_range("2013-01-01", periods=5, freq="YE"),
    )
    high = ae.interpret_rolling_correlation(0.80, hist)
    assert "compressed" in high
    # Precise stress framing: equity sleeves converge to +1; bonds decouple.
    assert "equity sleeves converge toward" in high
    assert "bond sleeves decouple" in high
    # The blended-average line itself must NOT be the thing claimed to hit +1.
    assert "spikes toward +1" not in high
    assert "100th percentile" in high    # macro.percentile: all 5 obs <= 0.80
    assert _ord(macro_percentile(hist, 0.80)) + " percentile" in high

    low = ae.interpret_rolling_correlation(0.15, hist)
    assert "strong" in low
    assert "diversif" in low
    assert "20th percentile" in low      # only 0.1 <= 0.15 → 1/5


def _ord(n):
    n = int(round(n))
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


# ── Phase 31: live-fetch (deselected by default) ────────────────────────────────

@pytest.mark.live_data
def test_average_pairwise_rolling_correlation_live():
    df = ae.average_pairwise_rolling_correlation(window=60, start_date="2007-01-01")
    assert not df.empty
    assert {"mean", "min", "max"}.issubset(df.columns)
    cur = float(df["mean"].dropna().iloc[-1])
    assert -1.0 <= cur <= 1.0


@pytest.mark.live_data
def test_extended_history_reaches_2008_live():
    default = ae.average_pairwise_rolling_correlation(window=60, start_date="2007-01-01")
    extended = ae.average_pairwise_rolling_correlation(
        window=60, start_date="2007-01-01", exclude=["US Large Quality"],
    )
    # Default 9-sleeve set is QUAL-constrained (~2013); extended reaches ~2007.
    assert default.index.min().year >= 2013
    assert extended.index.min().year <= 2008


# ── Phase 32: candidate-to-sleeves correlation ──────────────────────────────────

def test_rolling_correlation_to_set_known_value():
    idx = pd.date_range("2020-01-01", periods=80, freq="D")
    a = pd.Series(np.linspace(0.0, 1.0, 80) + np.sin(np.arange(80)) * 0.01, index=idx)
    refs = pd.DataFrame({"r1": a.copy(), "r2": -a, "r3": a.copy()})  # ρ = +1, -1, +1
    s = ae.rolling_correlation_to_set(a, refs, window=80)
    # Mean of [+1, -1, +1] = +1/3.
    assert len(s) == 1
    assert s.iloc[-1] == pytest.approx(1.0 / 3.0, abs=1e-6)


def test_rolling_correlation_to_set_generic_over_n():
    idx = pd.date_range("2020-01-01", periods=80, freq="D")
    a = pd.Series(np.linspace(0.0, 1.0, 80) + np.sin(np.arange(80)) * 0.01, index=idx)
    one = ae.rolling_correlation_to_set(a, pd.DataFrame({"r": a.copy()}), window=80)
    assert one.iloc[-1] == pytest.approx(1.0, abs=1e-6)
    five = pd.DataFrame({f"r{i}": a.copy() for i in range(5)})
    assert not ae.rolling_correlation_to_set(a, five, window=80).empty


def test_full_sample_average_is_mean_of_correlation_row():
    idx = pd.date_range("2020-01-01", periods=80, freq="D")
    a = pd.Series(np.linspace(0.0, 1.0, 80) + np.sin(np.arange(80)) * 0.01, index=idx)
    sleeves = pd.DataFrame({"s1": a.copy(), "s2": -a})  # ρ = +1, -1 → mean 0
    per = ae.compute_full_sample_correlations(a, sleeves)
    assert float(per.mean()) == pytest.approx(0.0, abs=1e-6)


def _per_sleeve(d):
    return pd.Series(d)


def test_interpret_candidate_doubles_down_with_offset():
    per = _per_sleeve({
        "US Large Core": 0.94, "US Large Quality": 0.92, "US Large Value": 0.76,
        "US Small Cap": 0.77, "Intl Core": 0.76, "Emerging Markets": 0.73,
        "Core Fixed Income": -0.07, "TIPS": 0.08, "Real Assets": 0.59,
    })
    txt = ae.interpret_candidate_diversification(float(per.mean()), per, "QQQ")
    assert "doubles down" in txt
    assert "offset" in txt
    assert "US Large Core" in txt   # names the top-correlation sleeve
    assert txt.startswith("QQQ")


def test_interpret_candidate_genuine_diversifier():
    per = _per_sleeve({k: 0.10 for k in ae.SLEEVES})
    txt = ae.interpret_candidate_diversification(float(per.mean()), per, "GLD")
    assert "genuine diversifier" in txt


def test_interpret_candidate_redundant_including_bonds():
    per = _per_sleeve({k: 0.70 for k in ae.SLEEVES})
    txt = ae.interpret_candidate_diversification(float(per.mean()), per, "XYZ")
    assert "redundant" in txt


# ── Phase 32: live-fetch (deselected by default) ────────────────────────────────

@pytest.mark.live_data
def test_candidate_screen_qqq_doubles_down_live():
    cand = ae.get_candidate_returns("QQQ", ae.SAMPLE_START)
    slv = ae.get_sleeve_returns(ae.SAMPLE_START)
    per = ae.compute_full_sample_correlations(cand, slv)
    # QQQ is US large-growth: very high to US large-cap sleeves, low/neg to bonds.
    assert per["US Large Core"] > 0.8
    assert per["Core Fixed Income"] < 0.2
    assert float(per.mean()) > 0.4
    roll = ae.rolling_correlation_to_set(cand, slv, window=60)
    assert not roll.empty and -1.0 <= float(roll.iloc[-1]) <= 1.0


# ── EQUITY_SLEEVES derives from the book (Phase: derived-prose batch) ─────────

def test_equity_sleeves_derived_from_book():
    """EQUITY_SLEEVES must cover every equity sleeve of the CURRENT book.

    The old hardcoded list froze the personal names ('Intl Developed'), so on
    the demo book the four international sleeves silently vanished from the
    intra-equity decomposition (the `c in cols` filter dropped them without
    error). Property-tested against SLEEVES so it holds in both modes.
    """
    intl = [s for s in ae.SLEEVES if s.startswith("Intl")]
    assert intl, "expected at least one international sleeve in the book"
    missing = [s for s in intl if s not in ae.EQUITY_SLEEVES]
    assert not missing, f"international sleeves missing from EQUITY_SLEEVES: {missing}"
    assert ae.EQUITY_SLEEVES == [
        s for s in ae.SLEEVES if s not in ae.BOND_SLEEVES and s != "Real Assets"
    ], "EQUITY_SLEEVES diverged from the SLEEVES-derived definition"
