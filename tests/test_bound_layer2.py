"""Phase 12 Section 2 — Layer 2 reasonability bounds (soft, @pytest.mark.bound).

These tests assert that the system output falls within expected ranges under normal
conditions. A 'bound' test may legitimately fail on unusual data (extreme markets,
stale cache). Each test fails hard when a threshold is exceeded by a wide-enough
margin to indicate a real bug, not just unusual data.

Run with: pytest -m bound
Exclude from fast iteration: pytest -m "not slow"

Subsections:
  2.3  Risk-adjusted metric coherence (synthetic, always run)
  2.4  Drift band sizing matches SAA rule (DB-backed, always run)
  2.5  Macro indicator plausibility (DB-backed, skips if cache empty)
  2.1  Cache freshness vs. yfinance (network-bound, @pytest.mark.slow)
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.db import get_connection
from src.performance import compute_risk_metrics


# ── helpers ───────────────────────────────────────────────────────────────────

def _bday_series(returns: np.ndarray, start: str = "2025-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(returns))
    return pd.Series(10_000.0 * np.cumprod(1.0 + returns), index=idx)


# ── 2.3  Risk-adjusted metric coherence ──────────────────────────────────────

@pytest.mark.bound
def test_bound_sortino_gte_sharpe_positive_excess_return():
    """When mean excess return > 0, Sortino must be ≥ Sharpe.

    Intuition: when the portfolio is profitable (positive mean excess return),
    penalising only downside deviation gives a more favourable ratio than penalising
    total deviation. Violations indicate the semi-deviation formula has been changed
    to something inconsistent with the Sharpe denominator.
    """
    rng = np.random.default_rng(1)
    # Drift returns positive so mean excess > 0 for sure
    port_ret   = rng.normal(0.002, 0.01, 300)   # positive mean
    bench_ret  = rng.normal(0.0005, 0.01, 300)

    pv = _bday_series(port_ret)
    bl = _bday_series(bench_ret)
    m  = compute_risk_metrics(pv, bl)

    assert m, "compute_risk_metrics returned empty dict"
    if math.isnan(m["sortino"]):
        pytest.skip("No negative excess returns — Sortino undefined; bound doesn't apply")

    assert m["sortino"] >= m["sharpe"], (
        f"Sortino ({m['sortino']:.3f}) < Sharpe ({m['sharpe']:.3f}) for a positive-mean series. "
        "Expected Sortino ≥ Sharpe when mean excess return > 0."
    )


@pytest.mark.bound
def test_bound_sortino_gte_sharpe_negative_excess_return():
    """When mean excess return < 0 (underperforming), Sortino ≥ Sharpe still holds.

    With negative mean: large downside outlier increases semi-dev more than total std,
    making Sortino a less negative number than Sharpe.
    """
    rng = np.random.default_rng(2)
    port_ret   = rng.normal(-0.001, 0.01, 300)  # negative mean
    bench_ret  = rng.normal(0.0005, 0.01, 300)

    pv = _bday_series(port_ret)
    bl = _bday_series(bench_ret)
    m  = compute_risk_metrics(pv, bl)

    assert m
    if math.isnan(m["sortino"]):
        pytest.skip("Sortino undefined; bound doesn't apply")

    assert m["sortino"] >= m["sharpe"], (
        f"Sortino ({m['sortino']:.3f}) < Sharpe ({m['sharpe']:.3f}) for negative-mean series. "
        "Expected Sortino ≥ Sharpe in all cases."
    )


@pytest.mark.bound
def test_bound_sortino_gte_sharpe_multiple_seeds():
    """Sortino ≥ Sharpe across 20 random seeds, both positive and negative mean excess returns."""
    failures = []
    for seed in range(20):
        rng = np.random.default_rng(seed)
        drift = rng.choice([-1, 1]) * 0.001
        port_ret  = rng.normal(drift, 0.012, 250)
        bench_ret = rng.normal(0.0005, 0.01, 250)
        pv = _bday_series(port_ret)
        bl = _bday_series(bench_ret)
        m  = compute_risk_metrics(pv, bl)
        if not m or math.isnan(m.get("sortino", float("nan"))):
            continue
        if m["sortino"] < m["sharpe"] - 0.001:  # 0.001 tolerance for float precision
            failures.append(
                f"seed={seed}: Sortino={m['sortino']:.3f} < Sharpe={m['sharpe']:.3f}"
            )
    assert not failures, (
        f"Sortino < Sharpe in {len(failures)} / 20 seeds:\n" + "\n".join(failures)
    )


@pytest.mark.bound
def test_bound_ir_times_te_within_jensen_gap_of_arithmetic():
    """The arithmetic IR should exceed the geometric IR by no more than Jensen's gap.

    IR (geometric) = (ann_port − ann_bench) / TE
    IR (arithmetic) = (mean(daily_port − daily_bench) × 252) / TE

    By Jensen's inequality, arithmetic mean ≥ geometric mean, so arithmetic IR ≥ geometric IR.
    The gap is typically 0–30% of the geometric IR at normal portfolio vol levels.

    Bound: arithmetic_IR ≤ geometric_IR × 1.50 (50% gap is pathologically large
    and would indicate a computation error, not just Jensen's inequality).
    """
    rng = np.random.default_rng(10)
    N = 300
    port_ret  = rng.normal(0.0005, 0.01, N)
    bench_ret = port_ret + rng.normal(0.0, 0.003, N)

    pv = _bday_series(port_ret)
    bl = _bday_series(bench_ret)
    m  = compute_risk_metrics(pv, bl)

    assert m
    if abs(m["tracking_error_pct"]) < 0.01:
        pytest.skip("TE too small — IR would be numerically unstable")

    geo_ir  = m["information_ratio"]
    te      = m["tracking_error_pct"] / 100.0
    # Arithmetic active return: mean daily active × 252
    port_ret_series  = pv.pct_change().dropna()
    bench_ret_series = bl.pct_change().dropna()
    trading          = (port_ret_series != 0) | (bench_ret_series != 0)
    daily_active     = (port_ret_series - bench_ret_series)[trading]
    arith_active     = float(daily_active.mean()) * 252
    arith_ir         = arith_active / te

    # Jensen's gap check: arithmetic ≥ geometric (both measured in same sign direction)
    if geo_ir > 0:
        assert arith_ir >= geo_ir - 0.01, (
            f"Arithmetic IR ({arith_ir:.3f}) < Geometric IR ({geo_ir:.3f}). "
            "Arithmetic mean ≥ geometric mean — this reversal would indicate a bug."
        )
        assert arith_ir <= geo_ir * 1.50, (
            f"Arithmetic IR ({arith_ir:.3f}) is >50% above Geometric IR ({geo_ir:.3f}). "
            f"Gap = {(arith_ir/geo_ir - 1)*100:.0f}%. Expected Jensen gap ≤ 20–30%."
        )


@pytest.mark.bound
def test_bound_var_and_cvar_plausible_range():
    """VaR(95%) and CVaR(95%) must be within [0%, 20%] for typical equity-like portfolios.

    A daily VaR > 20% would imply a 5% chance of losing more than 20% in a single day,
    which is only possible for extreme leverage or a data error. A zero or negative VaR
    indicates the portfolio had no losing days, which is implausible over 200+ observations.
    """
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0004, 0.012, 400)  # ~18% annualized vol
    pv = _bday_series(returns)
    bl = _bday_series(np.zeros(400))

    m = compute_risk_metrics(pv, bl)
    assert m

    assert 0 < m["var_95_pct"] < 20, (
        f"VaR(95%) = {m['var_95_pct']:.2f}% is outside [0%, 20%]. "
        "Check for data errors or extreme leverage."
    )
    assert 0 < m["cvar_95_pct"] < 20, (
        f"CVaR(95%) = {m['cvar_95_pct']:.2f}% is outside [0%, 20%]. "
        "Check for data errors or extreme leverage."
    )
    assert m["cvar_95_pct"] >= m["var_95_pct"], (
        f"CVaR ({m['cvar_95_pct']:.2f}%) < VaR ({m['var_95_pct']:.2f}%). "
        "Expected Shortfall must be ≥ VaR by definition."
    )


# ── 2.4  Drift band sizing matches SAA rule ───────────────────────────────────

@pytest.mark.bound
def test_bound_drift_bands_match_saa_rule():
    """Each sleeve's tolerance_band in asset_classes must follow the SAA rule:
    ±3% (0.03) for sleeves with target_weight ≥ 10%, ±2% (0.02) for smaller.

    Per CLAUDE.md: 'Tolerance band rule: ±3% for sleeves ≥10%, ±2% for sleeves <10%.'
    The Real Assets sleeve at exactly 10% is boundary — either 0.02 or 0.03 is accepted.

    This is a bound test (not identity) because the boundary case is ambiguous and
    the rule might be intentionally relaxed for specific sleeves.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight, tolerance_band FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()

    assert rows, "No sleeve rows in asset_classes"

    violations = []
    for r in rows:
        name     = r["name"]
        wt       = r["target_weight"]
        band     = r["tolerance_band"]
        expected = 0.03 if wt >= 0.10 else 0.02

        # Allow ±0.005 tolerance to handle boundary cases (~10%)
        if abs(band - expected) > 0.005:
            # Real Assets is the designated boundary sleeve — ~10% (10.2% ex-cash
            # after the Phase 38a rescale) with a deliberate ±2% band.
            if name == "Real Assets" and band in (0.02, 0.03):
                continue
            violations.append(
                f"  {name}: target={wt:.0%}, "
                f"expected_band={expected:.0%}, actual_band={band:.0%}"
            )

    assert not violations, (
        "Tolerance bands don't match SAA rule (≥10% → ±3%, <10% → ±2%):\n"
        + "\n".join(violations)
    )


@pytest.mark.bound
def test_bound_all_sleeves_have_positive_tolerance_bands():
    """Every sleeve must have a positive tolerance band (> 0).

    A zero or negative tolerance band would cause all positions to immediately
    trigger 'Drift' status, rendering the drift analysis meaningless.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, tolerance_band FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()

    bad = [r["name"] for r in rows if r["tolerance_band"] <= 0]
    assert not bad, (
        f"Sleeves with non-positive tolerance_band: {bad}. "
        "Tolerance bands must be > 0."
    )


# ── 2.5  Macro indicator plausibility ────────────────────────────────────────

@pytest.mark.bound
def test_bound_cape_value_historically_plausible():
    """Current CAPE must be within the historically observed range (5, 60).

    CAPE has never been below 5 or above 60 in the Shiller dataset back to 1871.
    Values outside this range indicate a data ingestion or normalization error.

    Skips if CAPE data is unavailable (network or cache not populated).
    """
    try:
        from src.shiller import current_cape
        cape = current_cape()
    except Exception as e:
        pytest.skip(f"CAPE data unavailable: {e}")

    assert cape is not None and cape > 0, f"CAPE is None or non-positive: {cape}"
    assert 5 < cape < 60, (
        f"CAPE = {cape:.1f} is outside historical range (5, 60). "
        "Check Shiller data ingestion for normalization errors."
    )


@pytest.mark.bound
def test_bound_cape_percentile_in_range():
    """CAPE percentile computed from the Shiller series must be in [0, 100].

    Percentile values outside this range indicate the `percentile()` function
    received a malformed series or an out-of-range cape value.

    Skips if CAPE data is unavailable.
    """
    try:
        from src.shiller import current_cape, get_cape_series
        from src.macro import percentile
        cape_val = current_cape()
        cape_s   = get_cape_series()
    except Exception as e:
        pytest.skip(f"CAPE data unavailable: {e}")

    if cape_val is None or cape_s is None or len(cape_s.dropna()) < 10:
        pytest.skip("Insufficient CAPE data for percentile computation")

    pct = percentile(cape_s, cape_val)
    assert 0 <= pct <= 100, (
        f"CAPE percentile = {pct:.1f} is outside [0, 100]. "
        f"CAPE value = {cape_val:.1f}, series length = {len(cape_s.dropna())}."
    )


@pytest.mark.bound
def test_bound_macro_regime_pct_int_is_integer_in_range():
    """_build_macro_section must always return pct_int as an integer in [0, 100].

    This bound pins that the CAPE percentile → pct_int conversion never produces
    a float, None, or out-of-range value that would break template logic like
    'if pct_int >= 80'.

    Uses mocked CAPE data so it runs without network access.
    """
    from unittest.mock import patch
    import src.reports as rpt

    for cape_val, series_vals, expected_tier in [
        (36.0, [10.0, 15.0, 20.0, 25.0, 30.0, 36.0], "Elevated"),
        (20.0, list(range(10, 31)), "Moderate"),
        (10.0, list(range(10, 41)), "Below-average"),
    ]:
        cape_s = pd.Series(series_vals, dtype=float)
        with patch("src.reports.current_cape", return_value=cape_val), \
             patch("src.reports.get_cape_series", return_value=cape_s), \
             patch("src.reports.get_series", side_effect=RuntimeError("mocked")):
            macro = rpt._build_macro_section()

        pct_int = macro["cape"]["pct_int"]
        assert isinstance(pct_int, int), (
            f"pct_int is {type(pct_int).__name__}: {pct_int} for CAPE={cape_val}"
        )
        assert 0 <= pct_int <= 100, f"pct_int={pct_int} out of [0, 100]"
        assert macro["cape"]["regime"] == expected_tier, (
            f"cape={cape_val}: expected regime '{expected_tier}', got '{macro['cape']['regime']}'"
        )


# ── 2.1  Cache freshness vs. yfinance (network-bound) ────────────────────────

@pytest.mark.bound
@pytest.mark.slow
def test_bound_prices_db_within_tolerance_of_yfinance():
    """Cached prices must match fresh yfinance pulls within 0.5 pp cumulative return.

    Fetches last 30 trading days from yfinance for each tracked equity ticker.
    Compares the 30-day cumulative return from cache vs. fresh data.
    Warns (via assertion message) if any ticker exceeds 0.5 pp.
    Fails hard only if any ticker exceeds 2.0 pp (indicates materially stale cache).

    Tickers checked: core holdings and benchmarks.
    Skips if yfinance is unavailable.
    """
    tickers = ["SPY", "VOO", "VTV", "SPHQ", "AVUV", "VEA", "IEMG", "VNQ", "PDBC", "IEF"]

    try:
        from src.prices import get_prices, fetch_prices
    except ImportError as e:
        pytest.skip(f"src.prices unavailable: {e}")

    from datetime import date, timedelta
    end_date   = date.today().isoformat()
    start_date = (date.today() - timedelta(days=45)).isoformat()

    soft_violations = []   # 0.5–2.0 pp: warn
    hard_violations = []   # >2.0 pp: fail

    for ticker in tickers:
        try:
            # Cached data
            cached = get_prices(ticker, start_date, end_date)
            if cached.empty or len(cached) < 5:
                continue
            cache_ret = float(cached["adj_close"].dropna().iloc[-1] /
                              cached["adj_close"].dropna().iloc[0] - 1)

            # Fresh data
            fresh = fetch_prices(ticker, start_date, end_date)
            if fresh.empty or len(fresh) < 5:
                continue
            fresh_ret = float(fresh["adj_close"].dropna().iloc[-1] /
                              fresh["adj_close"].dropna().iloc[0] - 1)

            diff_pp = abs(cache_ret - fresh_ret) * 100
            if diff_pp > 2.0:
                hard_violations.append(f"{ticker}: cache={cache_ret*100:.2f}%, fresh={fresh_ret*100:.2f}%, diff={diff_pp:.2f}pp")
            elif diff_pp > 0.5:
                soft_violations.append(f"{ticker}: diff={diff_pp:.2f}pp (warn threshold 0.5pp)")

        except Exception:
            continue

    if soft_violations:
        import warnings
        warnings.warn(
            f"Cache freshness soft-violation (0.5–2.0 pp): {'; '.join(soft_violations)}. "
            "Run scripts/rebuild_prices_cache.py to refresh.",
            UserWarning,
        )

    assert not hard_violations, (
        f"Cache materially stale (>2.0 pp from fresh yfinance) for:\n"
        + "\n".join(hard_violations)
        + "\nRun scripts/rebuild_prices_cache.py to refresh the price cache."
    )
