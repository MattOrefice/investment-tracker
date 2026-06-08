"""Tests for src/attribution.py — Brinson-Fachler and two-stage decomposition."""
import math
import sys
import pathlib

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.attribution import brinson_fachler, compute_two_stage_attribution


def test_two_sleeve_hand_calculation():
    """
    Hand-constructed two-sleeve scenario:

    Sleeve A: w_p=0.60, w_b=0.50, r_p=0.12, r_b=0.10
    Sleeve B: w_p=0.40, w_b=0.50, r_p=0.05, r_b=0.08

    r_b_total = 0.50*0.10 + 0.50*0.08 = 0.09
    r_p_total = 0.60*0.12 + 0.40*0.05 = 0.072 + 0.020 = 0.092

    Sleeve A:
      alloc = (0.60 - 0.50) * (0.10 - 0.09) = 0.10 * 0.01 = 0.001
      sel   = 0.60 * (0.12 - 0.10) = 0.60 * 0.02 = 0.012
      total = 0.013

    Sleeve B:
      alloc = (0.40 - 0.50) * (0.08 - 0.09) = (-0.10) * (-0.01) = 0.001
      sel   = 0.40 * (0.05 - 0.08) = 0.40 * (-0.03) = -0.012
      total = -0.011

    Sum of effects = 0.013 + (-0.011) = 0.002
    Active return  = 0.092 - 0.090   = 0.002  ✓
    """
    pw = {"A": 0.60, "B": 0.40}
    bw = {"A": 0.50, "B": 0.50}
    pr = {"A": 0.12, "B": 0.05}
    br = {"A": 0.10, "B": 0.08}

    df = brinson_fachler(pw, bw, pr, br)

    row_a = df[df["sleeve"] == "A"].iloc[0]
    row_b = df[df["sleeve"] == "B"].iloc[0]

    assert row_a["allocation_effect"] == pytest.approx(0.001, abs=1e-10)
    assert row_a["selection_effect"]  == pytest.approx(0.012, abs=1e-10)
    assert row_a["total_effect"]      == pytest.approx(0.013, abs=1e-10)

    assert row_b["allocation_effect"] == pytest.approx(0.001, abs=1e-10)
    assert row_b["selection_effect"]  == pytest.approx(-0.012, abs=1e-10)
    assert row_b["total_effect"]      == pytest.approx(-0.011, abs=1e-10)


def test_identity_bf_effects_sum_to_active_return():
    """Sum of BF effects must equal portfolio − benchmark return within 1 bp."""
    pw = {"Equity": 0.70, "Bonds": 0.30}
    bw = {"Equity": 0.60, "Bonds": 0.40}
    pr = {"Equity": 0.15, "Bonds": 0.03}
    br = {"Equity": 0.12, "Bonds": 0.04}

    df = brinson_fachler(pw, bw, pr, br)

    r_p = 0.70 * 0.15 + 0.30 * 0.03
    r_b = 0.60 * 0.12 + 0.40 * 0.04
    active = r_p - r_b

    assert df["total_effect"].sum() == pytest.approx(active, abs=0.0001)


def test_equal_weights_no_allocation_effect():
    """When portfolio weights equal benchmark weights, allocation effect = 0."""
    pw = {"X": 0.50, "Y": 0.50}
    bw = {"X": 0.50, "Y": 0.50}
    pr = {"X": 0.20, "Y": 0.10}
    br = {"X": 0.15, "Y": 0.05}

    df = brinson_fachler(pw, bw, pr, br)

    for _, row in df.iterrows():
        assert row["allocation_effect"] == pytest.approx(0.0, abs=1e-10)


def test_equal_returns_no_selection_effect():
    """When portfolio return equals benchmark return per sleeve, selection = 0."""
    pw = {"X": 0.60, "Y": 0.40}
    bw = {"X": 0.50, "Y": 0.50}
    pr = {"X": 0.10, "Y": 0.08}   # same as br
    br = {"X": 0.10, "Y": 0.08}

    df = brinson_fachler(pw, bw, pr, br)

    for _, row in df.iterrows():
        assert row["selection_effect"] == pytest.approx(0.0, abs=1e-10)


# ── Two-stage attribution unit tests ─────────────────────────────────────────

def test_identity_stage1_plus_stage2_equals_total():
    """Stage1 + Stage2 = Total = port_return - naive_return within floating-point precision.

    Regression pin: Phase 9. Tests the algebraic identity that holds by construction
    in compute_two_stage_attribution.
    """
    sleeve_weights  = {"Equity": 0.72, "Fixed Income": 0.15, "Real Assets": 0.10, "Cash": 0.03}
    sleeve_returns  = {"Equity": 0.30, "Fixed Income": 0.04, "Real Assets": 0.08, "Cash": 0.05}
    saa_return      = sum(sleeve_weights[s] * sleeve_returns[s] for s in sleeve_weights)
    naive_return    = 0.60 * 0.27 + 0.40 * 0.04   # 60% SPY at 27%, 40% AGG at 4%
    port_return     = saa_return + 0.0050           # 50 bps implementation outperformance

    result = compute_two_stage_attribution(
        port_return              = port_return,
        saa_return               = saa_return,
        naive_return             = naive_return,
        sleeve_saa_weights       = sleeve_weights,
        sleeve_benchmark_returns = sleeve_returns,
    )

    # Stage1 + Stage2 = Total by construction
    assert result["algebra_residual"] < 1e-10, (
        f"Stage1 + Stage2 != Total: residual = {result['algebra_residual']:.2e}"
    )
    assert result["total"] == pytest.approx(port_return - naive_return, abs=1e-12)
    assert result["stage1"] == pytest.approx(saa_return - naive_return, abs=1e-12)
    assert result["stage2"] == pytest.approx(port_return - saa_return, abs=1e-12)

    # ±0.5 bps tolerance check (as specified)
    assert result["algebra_residual"] * 10_000 < 0.5, (
        f"Algebra residual {result['algebra_residual'] * 10_000:.4f} bps exceeds 0.5 bps"
    )


def test_identity_stage1_sleeve_contributions_sum_to_total():
    """Sum of per-sleeve Stage 1 contributions = Stage 1 total.

    Stage1_i = w_b_i * (r_b_i - naive). Sum = sum(w_b_i * r_b_i) - naive * sum(w_b_i).
    When sum(w_b_i) = 1, this equals saa_return - naive = Stage1. Regression pin: Phase 9.
    """
    sleeve_weights  = {"A": 0.60, "B": 0.30, "C": 0.10}
    sleeve_returns  = {"A": 0.18, "B": 0.04, "C": 0.09}
    saa_return      = sum(sleeve_weights[s] * sleeve_returns[s] for s in sleeve_weights)
    naive_return    = 0.10
    port_return     = 0.13

    result = compute_two_stage_attribution(
        port_return              = port_return,
        saa_return               = saa_return,
        naive_return             = naive_return,
        sleeve_saa_weights       = sleeve_weights,
        sleeve_benchmark_returns = sleeve_returns,
    )

    sleeve_sum = sum(result["per_sleeve"].values())
    assert result["sleeve_sum_residual"] < 1e-10, (
        f"Sleeve sum {sleeve_sum:.8f} != Stage1 {result['stage1']:.8f}: "
        f"residual = {result['sleeve_sum_residual']:.2e}"
    )

    # Manual check for sleeve A:
    # contribution_A = 0.60 * (0.18 - 0.10) = 0.048
    assert result["per_sleeve"]["A"] == pytest.approx(0.060 * 0.8, abs=1e-12)  # 0.60*(0.18-0.10)=0.048
    assert result["per_sleeve"]["A"] == pytest.approx(0.60 * (0.18 - 0.10), abs=1e-12)
    assert result["per_sleeve"]["B"] == pytest.approx(0.30 * (0.04 - 0.10), abs=1e-12)
    assert result["per_sleeve"]["C"] == pytest.approx(0.10 * (0.09 - 0.10), abs=1e-12)


def test_stage1_hand_calculation():
    """Verify Stage 1 against a fully hand-computed two-sleeve example.

    Weights: Equity 70%, FI 30%. Returns: Equity 15%, FI 3%.
    SAA blend = 0.70*0.15 + 0.30*0.03 = 0.105 + 0.009 = 0.114.
    Naive (60/40): 0.60*0.12 + 0.40*0.04 = 0.072 + 0.016 = 0.088.
    Stage 1 = 0.114 - 0.088 = 0.026  (260 bps).
    Stage 2 = 0.120 - 0.114 = 0.006   (60 bps).
    Total   = 0.120 - 0.088 = 0.032  (320 bps).
    """
    result = compute_two_stage_attribution(
        port_return              = 0.120,
        saa_return               = 0.114,
        naive_return             = 0.088,
        sleeve_saa_weights       = {"Equity": 0.70, "FI": 0.30},
        sleeve_benchmark_returns = {"Equity": 0.15, "FI": 0.03},
    )

    assert result["stage1"] == pytest.approx(0.026, abs=1e-10)
    assert result["stage2"] == pytest.approx(0.006, abs=1e-10)
    assert result["total"]  == pytest.approx(0.032, abs=1e-10)
    assert result["algebra_residual"] < 1e-12

    # Sleeve contributions: Equity = 0.70*(0.15-0.088)=0.0434, FI=0.30*(0.03-0.088)=-0.0174
    assert result["per_sleeve"]["Equity"] == pytest.approx(0.70 * (0.15 - 0.088), abs=1e-10)
    assert result["per_sleeve"]["FI"]     == pytest.approx(0.30 * (0.03 - 0.088), abs=1e-10)
    # Sum = 0.0434 - 0.0174 = 0.0260 = Stage 1
    assert result["sleeve_sum_residual"] < 1e-10


def test_naive_benchmark_60_40_composition():
    """60/40 series = 0.6 * daily_SPY_return + 0.4 * daily_AGG_return to floating-point tolerance.

    Regression pin: Phase 9. Uses actual price data from the cache (requires demo.db with AGG prices).
    Skips gracefully if AGG data is absent.
    """
    from src.benchmarks import get_naive_60_40_series, _get_price_series

    start = "2025-11-03"
    end   = "2025-12-31"

    try:
        naive = get_naive_60_40_series(start, end)
        spy   = _get_price_series("SPY", start, end)
        agg   = _get_price_series("AGG", start, end)
    except Exception as exc:
        pytest.skip(f"Price data unavailable: {exc}")

    if spy.isnull().all() or agg.isnull().all():
        pytest.skip("SPY or AGG price series empty — prices not in cache")

    spy_ret      = spy.pct_change().fillna(0.0)
    agg_ret      = agg.pct_change().fillna(0.0)
    expected_ret = 0.6 * spy_ret + 0.4 * agg_ret
    expected     = (1 + expected_ret).cumprod()
    expected     = expected / float(expected.iloc[0])

    common = naive.dropna().index.intersection(expected.dropna().index)
    assert len(common) >= 10, f"Fewer than 10 common dates: {len(common)}"

    np.testing.assert_allclose(
        naive.loc[common].values,
        expected.loc[common].values,
        rtol=1e-8,
        err_msg="Naive 60/40 series does not match 0.6*SPY_ret + 0.4*AGG_ret",
    )


def test_stage1_distinguishes_across_windows():
    """Stage 1 values must not be identical across windows (tests price-series sensitivity).

    Regression pin: Phase 9. Uses actual benchmark series from the cache. If all windows
    collapse to the same return (e.g., because the naive or SAA series is flat), this pin
    fails and diagnoses the collapse. Skips if benchmark data is unavailable.
    """
    from datetime import date, timedelta
    import pandas as pd
    from src.benchmarks import get_naive_60_40_series, get_custom_blended_series

    INCEPTION = "2025-05-01"
    TODAY     = date.today().isoformat()

    try:
        naive = get_naive_60_40_series(INCEPTION, TODAY)
        bl    = get_custom_blended_series(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Benchmark data unavailable: {exc}")

    if naive.isnull().all() or bl.isnull().all():
        pytest.skip("Benchmark series empty")

    def _bpr(series: "pd.Series", period: str) -> float:
        last_date = series.index[-1].date() if hasattr(series.index[-1], "date") else series.index[-1]
        if period == "SI":
            start_ts = series.index[0]
        elif period == "1Y":
            start_ts = pd.Timestamp(last_date - timedelta(days=365))
        elif period == "3M":
            start_ts = pd.Timestamp(last_date - timedelta(days=90))
        elif period == "1M":
            start_ts = pd.Timestamp(last_date - timedelta(days=30))
        else:
            return 0.0
        sliced = series[series.index >= start_ts]
        if len(sliced) < 2:
            return 0.0
        return float(sliced.iloc[-1] / sliced.iloc[0] - 1)

    stage1_by_window = {
        p: _bpr(bl, p) - _bpr(naive, p)
        for p in ("1M", "3M", "1Y", "SI")
    }

    unique_vals = set(round(v, 8) for v in stage1_by_window.values())
    assert len(unique_vals) > 1, (
        f"Stage 1 values are identical across all windows: {stage1_by_window}. "
        "If naive and SAA series both have data, different windows must produce "
        "different start-of-window prices and thus different returns."
    )


# ── Phase 10 Section 0 — naive benchmark toggle ───────────────────────────────

def test_two_stage_reconciles_against_spy():
    """Stage1 + Stage2 = Total holds when naive baseline is SPY (not 60/40).

    Regression pin: Phase 10 Section 0. The algebra is purely arithmetic and
    must hold regardless of which naive baseline is selected. This test uses a
    hand-constructed scenario with a pure-SPY naive return.
    """
    spy_return  = 0.25   # hypothetical SPY return
    saa_return  = 0.30   # SAA-blended benchmark
    port_return = 0.31   # portfolio outperforms SAA by 1%

    result = compute_two_stage_attribution(
        port_return              = port_return,
        saa_return               = saa_return,
        naive_return             = spy_return,
        sleeve_saa_weights       = {"Equity": 0.72, "Income": 0.15, "Real Assets": 0.10, "Cash": 0.03},
        sleeve_benchmark_returns = {"Equity": 0.35, "Income": 0.05, "Real Assets": 0.12, "Cash": 0.05},
    )

    assert result["algebra_residual"] < 1e-12
    assert result["stage1"] == pytest.approx(saa_return - spy_return, abs=1e-12)
    assert result["stage2"] == pytest.approx(port_return - saa_return, abs=1e-12)
    assert result["total"]  == pytest.approx(port_return - spy_return, abs=1e-12)
    assert result["algebra_residual"] * 10_000 < 0.5


def test_stage1_distinguishes_across_naive_benchmarks():
    """Stage 1 vs. 60/40 must differ from Stage 1 vs. SPY for the same period.

    Regression pin: Phase 10 Section 0. If both naive series return the same
    value (e.g., because price data is absent), Stage 1 becomes identical
    regardless of selection and the toggle has no effect.
    Skips when benchmark data is unavailable.
    """
    from src.benchmarks import get_naive_series, get_custom_blended_series

    INCEPTION = "2025-05-01"
    import datetime
    TODAY = datetime.date.today().isoformat()

    try:
        naive_6040 = get_naive_series("60_40", INCEPTION, TODAY)
        naive_spy  = get_naive_series("spy",   INCEPTION, TODAY)
        bl         = get_custom_blended_series(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Benchmark data unavailable: {exc}")

    if naive_6040.isnull().all() or naive_spy.isnull().all() or bl.isnull().all():
        pytest.skip("One or more benchmark series is empty")

    # SI return for each naive baseline and the SAA blend
    bl_si     = float(bl.iloc[-1] / bl.iloc[0] - 1)
    naive_6040_si = float(naive_6040.iloc[-1] / naive_6040.iloc[0] - 1)
    naive_spy_si  = float(naive_spy.iloc[-1]  / naive_spy.iloc[0]  - 1)

    stage1_6040 = bl_si - naive_6040_si
    stage1_spy  = bl_si - naive_spy_si

    assert abs(stage1_6040 - stage1_spy) > 0.001, (
        f"Stage 1 is indistinguishable across naive benchmarks: "
        f"60/40={stage1_6040:.4f}, SPY={stage1_spy:.4f}. "
        "The 60/40 and SPY series must have diverged over the inception period."
    )


# ── Phase 10.1 — price-series two-stage algebra regression pins ───────────────

def _bpr_helper(series: "pd.Series", period: str) -> float:
    """Slice series to period and return end/start - 1. Mirrors _benchmark_period_return."""
    from datetime import timedelta, date as _date
    import pandas as pd

    last_ts   = series.index[-1]
    last_date = last_ts.date() if hasattr(last_ts, "date") else last_ts
    if period == "SI":
        start_ts = series.index[0]
    elif period == "1Y":
        start_ts = pd.Timestamp(last_date - timedelta(days=365))
    elif period == "YTD":
        start_ts = pd.Timestamp(_date(last_date.year, 1, 1))
    elif period == "3M":
        start_ts = pd.Timestamp(last_date - timedelta(days=90))
    elif period == "1M":
        start_ts = pd.Timestamp(last_date - timedelta(days=30))
    else:
        return 0.0
    sliced = series[series.index >= start_ts]
    if len(sliced) < 2:
        return 0.0
    return float(sliced.iloc[-1] / sliced.iloc[0] - 1)


def test_identity_ps_two_stage_si_60_40():
    """Price-series Stage1+Stage2=Total within 0.05 bps for SI period vs 60/40 naive.

    Phase 10.1 regression pin. Pre-fix code used _r_p_bf (BF-internal,
    price-appreciation only) for Stage 2. With price-series inputs, algebra
    residual must be < 1e-10 (exact by construction).
    Skips when price data is unavailable (local empty-DB mode).
    """
    import datetime
    from src.attribution import compute_two_stage_attribution, brinson_fachler_period
    from src.benchmarks import get_custom_blended_series, get_naive_series
    from src.holdings import get_portfolio_value_series

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()

    try:
        pv = get_portfolio_value_series(INCEPTION, TODAY)
        bl = get_custom_blended_series(INCEPTION, TODAY)
        naive = get_naive_series("60_40", INCEPTION, TODAY)
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")

    if pv.dropna().empty or bl.dropna().empty or naive.dropna().empty or bf_df.empty:
        pytest.skip("One or more series is empty — skipped in local/empty-DB mode")

    r_p_ps  = _bpr_helper(pv, "SI")
    r_b_ps  = _bpr_helper(bl, "SI")
    naive_r = _bpr_helper(naive, "SI")

    result = compute_two_stage_attribution(
        port_return              = r_p_ps,
        saa_return               = r_b_ps,
        naive_return             = naive_r,
        sleeve_saa_weights       = dict(zip(bf_df["sleeve"], bf_df["w_b"])),
        sleeve_benchmark_returns = dict(zip(bf_df["sleeve"], bf_df["r_b"])),
    )

    resid_bps = result["algebra_residual"] * 10_000
    assert resid_bps < 0.05, (
        f"Price-series Stage1+Stage2 algebra residual {resid_bps:.4f} bps exceeds 0.05 bps "
        f"for SI/60_40. stage1={result['stage1']*10000:.1f} bps, "
        f"stage2={result['stage2']*10000:.1f} bps, total={result['total']*10000:.1f} bps."
    )


def test_identity_ps_two_stage_1y_60_40():
    """Price-series Stage1+Stage2=Total within 0.05 bps for 1Y period vs 60/40 naive.

    Phase 10.1 regression pin. Verifies the price-series algebra at the 1Y window,
    which had the largest BF-internal divergence (~316 bps).
    Skips when price data is unavailable (local empty-DB mode).
    """
    import datetime
    from src.attribution import compute_two_stage_attribution, brinson_fachler_period
    from src.benchmarks import get_custom_blended_series, get_naive_series
    from src.holdings import get_portfolio_value_series

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()

    try:
        pv = get_portfolio_value_series(INCEPTION, TODAY)
        bl = get_custom_blended_series(INCEPTION, TODAY)
        naive = get_naive_series("60_40", INCEPTION, TODAY)
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")

    if pv.dropna().empty or bl.dropna().empty or naive.dropna().empty or bf_df.empty:
        pytest.skip("One or more series is empty — skipped in local/empty-DB mode")

    r_p_ps  = _bpr_helper(pv, "1Y")
    r_b_ps  = _bpr_helper(bl, "1Y")
    naive_r = _bpr_helper(naive, "1Y")

    if r_p_ps == 0.0:
        pytest.skip("1Y portfolio price-series slice too short — insufficient data")

    result = compute_two_stage_attribution(
        port_return              = r_p_ps,
        saa_return               = r_b_ps,
        naive_return             = naive_r,
        sleeve_saa_weights       = dict(zip(bf_df["sleeve"], bf_df["w_b"])),
        sleeve_benchmark_returns = dict(zip(bf_df["sleeve"], bf_df["r_b"])),
    )

    resid_bps = result["algebra_residual"] * 10_000
    assert resid_bps < 0.05, (
        f"Price-series Stage1+Stage2 algebra residual {resid_bps:.4f} bps exceeds 0.05 bps "
        f"for 1Y/60_40. stage1={result['stage1']*10000:.1f} bps, "
        f"stage2={result['stage2']*10000:.1f} bps, total={result['total']*10000:.1f} bps."
    )


def test_identity_ps_two_stage_si_spy():
    """Price-series Stage1+Stage2=Total within 0.05 bps for SI period vs SPY naive.

    Phase 10.1 regression pin. Verifies the price-series algebra holds for the SPY
    naive baseline (Stage 1 is negative when SAA blend underperforms S&P 500).
    Skips when price data is unavailable (local empty-DB mode).
    """
    import datetime
    from src.attribution import compute_two_stage_attribution, brinson_fachler_period
    from src.benchmarks import get_custom_blended_series, get_naive_series
    from src.holdings import get_portfolio_value_series

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()

    try:
        pv = get_portfolio_value_series(INCEPTION, TODAY)
        bl = get_custom_blended_series(INCEPTION, TODAY)
        naive = get_naive_series("spy", INCEPTION, TODAY)
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")

    if pv.dropna().empty or bl.dropna().empty or naive.dropna().empty or bf_df.empty:
        pytest.skip("One or more series is empty — skipped in local/empty-DB mode")

    r_p_ps  = _bpr_helper(pv, "SI")
    r_b_ps  = _bpr_helper(bl, "SI")
    naive_r = _bpr_helper(naive, "SI")

    result = compute_two_stage_attribution(
        port_return              = r_p_ps,
        saa_return               = r_b_ps,
        naive_return             = naive_r,
        sleeve_saa_weights       = dict(zip(bf_df["sleeve"], bf_df["w_b"])),
        sleeve_benchmark_returns = dict(zip(bf_df["sleeve"], bf_df["r_b"])),
    )

    resid_bps = result["algebra_residual"] * 10_000
    assert resid_bps < 0.05, (
        f"Price-series Stage1+Stage2 algebra residual {resid_bps:.4f} bps exceeds 0.05 bps "
        f"for SI/SPY. stage1={result['stage1']*10000:.1f} bps, "
        f"stage2={result['stage2']*10000:.1f} bps, total={result['total']*10000:.1f} bps."
    )


def test_identity_bf_sum_reconciles_to_stage2():
    """Ex-cash BF portfolio return + cash drag must reconcile with the actual TWR
    within 0.5 bps for all windows (Phase 38b-2 bridge).

    Phase 10.2 regression pin, updated for Phase 38b-2. BF weights are now ex-cash
    (operational SPAXX float excluded), so the BF portfolio return is the INVESTED
    (strategic) return. The operational cash drag is exposed on bf_df.attrs. The
    bridge that keeps the Performance page reconciliation ✓:
      bf_r_p_excash + cash_drag  ==  r_p_ps   (actual incl-cash portfolio return)
    because cash_drag = r_p_total_incl − r_p_total_excash and bf_r_p_excash =
    r_p_total_excash, so the sum is r_p_total_incl ≈ r_p_ps within the same tolerance
    that held pre-38b-2.

    r_p_ps uses inception-based portfolio value series sliced to the period, matching
    the Performance page's _benchmark_period_return(pv, bf_period) call.
    """
    import datetime
    from src.attribution import brinson_fachler_period
    from src.holdings import get_portfolio_value_series

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()
    today = datetime.date.today()

    windows = [
        ("SI",  INCEPTION),
        ("1Y",  (today - datetime.timedelta(days=365)).isoformat()),
        ("YTD", f"{today.year}-01-01"),
        ("3M",  (today - datetime.timedelta(days=90)).isoformat()),
        ("1M",  (today - datetime.timedelta(days=30)).isoformat()),
    ]

    try:
        pv_full = get_portfolio_value_series(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Portfolio data unavailable: {exc}")

    if pv_full.dropna().empty:
        pytest.skip("Portfolio data empty — skipped in local/empty-DB mode")

    for label, start in windows:
        try:
            bf_df = brinson_fachler_period(start, TODAY)
        except Exception as exc:
            pytest.skip(f"BF data unavailable for {label}: {exc}")

        if bf_df.empty:
            pytest.skip(f"BF result empty for {label} — skipped in local/empty-DB mode")

        r_p_ps        = _bpr_helper(pv_full, label)
        bf_r_p_excash = float((bf_df["w_p"] * bf_df["r_p"]).sum())
        cash_drag     = float(bf_df.attrs.get("cash_drag", 0.0))
        bridged       = bf_r_p_excash + cash_drag
        gap_bps = abs(bridged - r_p_ps) * 10_000

        assert gap_bps < 0.5, (
            f"Ex-cash BF return + cash drag ({bridged*10000:.1f} bps = "
            f"{bf_r_p_excash*10000:.1f} strategic {cash_drag*10000:+.1f} drag) diverges from "
            f"price series ({r_p_ps*10000:.1f} bps) by {gap_bps:.2f} bps for {label} window. "
            f"The cash-drag bridge in brinson_fachler_period() may have failed — check "
            f"the cash_drag attr and the ex-cash weight normalization."
        )


def test_bf_per_sleeve_returns_are_total_return():
    """BF chart International Developed Port Ret must exceed price-only by >= 100 bps.

    Phase 10.2 regression pin. Pre-fix: brinson_fachler_period() returned
    price-only sleeve returns (stale inception adj_close, no dividends).
    Post-fix: DRIP shares added in the holdings loop → r_p includes dividend
    income. VEA pays ~3% annual yield, so SI total-return must exceed the
    raw adj_close ratio by at least 100 bps.  If this test fails, check
    _drip_shares() in src/attribution.py and confirm get_dividends() returns
    non-empty for VEA over the SI window.
    """
    import datetime
    from src.attribution import brinson_fachler_period, _first_adj_price, _last_adj_price

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()

    try:
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")

    if bf_df.empty:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")

    intl_rows = bf_df[bf_df["sleeve"] == "International Developed"]
    if intl_rows.empty:
        pytest.skip("International Developed sleeve not in BF result")

    r_p_total_return = float(intl_rows["r_p"].iloc[0])

    p_start = _first_adj_price("VEA", INCEPTION)
    p_end   = _last_adj_price("VEA", TODAY)
    if p_start <= 0 or p_end <= 0:
        pytest.skip("VEA price data unavailable")

    r_p_price_only = p_end / p_start - 1
    dividend_premium_bps = (r_p_total_return - r_p_price_only) * 10_000

    assert dividend_premium_bps >= 100, (
        f"International Developed r_p ({r_p_total_return*100:.2f}%) not materially "
        f"higher than price-only ({r_p_price_only*100:.2f}%). "
        f"Dividend premium: {dividend_premium_bps:.0f} bps (expected >= 100 bps for ~1yr). "
        f"DRIP alignment in brinson_fachler_period() may not be applied — check "
        f"_drip_shares() returns non-zero for VEA."
    )


def test_bf_ex_cash_weights_and_cash_drag_attr():
    """Phase 38b-2: brinson_fachler_period returns ex-cash strategic weights (no cash
    row, w_p sums to 1.0) and exposes the operational cash drag on .attrs, such that
    ex-cash strategic active + cash drag = the incl-cash active (total unchanged).
    """
    import datetime
    from src.attribution import brinson_fachler_period

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()

    try:
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")
    if bf_df.empty:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")

    # No cash row; the 9 strategic weights sum to 1.0 (match the ex-cash benchmark).
    assert not bf_df["sleeve"].str.contains("Cash").any(), "cash must be excluded from BF rows"
    assert abs(bf_df["w_p"].sum() - 1.0) < 1e-6, "ex-cash portfolio weights must sum to 1.0"
    assert abs(bf_df["w_b"].sum() - 1.0) < 1e-6, "benchmark weights must sum to 1.0"

    # Cash-drag plumbing present on .attrs.
    for key in ("cash_drag", "cash_weight", "r_p_total_excash", "r_p_total_incl"):
        assert key in bf_df.attrs, f"missing .attrs['{key}']"

    cash_drag        = bf_df.attrs["cash_drag"]
    r_p_total_excash = bf_df.attrs["r_p_total_excash"]
    r_p_total_incl   = bf_df.attrs["r_p_total_incl"]

    # cash_drag is defined as incl − ex-cash, and bf_r_p (ex-cash) == r_p_total_excash.
    assert abs((r_p_total_incl - r_p_total_excash) - cash_drag) < 1e-9
    assert abs(float((bf_df["w_p"] * bf_df["r_p"]).sum()) - r_p_total_excash) < 1e-9

    # ex-cash strategic active + cash drag == incl-cash active (total unchanged).
    r_b_total     = float((bf_df["w_b"] * bf_df["r_b"]).sum())
    ex_cash_active = r_p_total_excash - r_b_total
    incl_active    = r_p_total_incl  - r_b_total
    assert abs((ex_cash_active + cash_drag) - incl_active) < 1e-9, (
        "strategic active (ex-cash) + cash drag must equal the incl-cash active "
        "(the total is unchanged; only the split is new)"
    )
