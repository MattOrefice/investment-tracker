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


# ── Completeness precondition on the sink defaults ───────────────────────────
# brinson_fachler()'s per-sleeve .get(s, 0.0) lookups exist only to keep the
# dict lookups total, not to paper over a missing return. A sleeve missing
# from a WEIGHT dict is a legitimate 0 weight (standard BF); a sleeve missing
# from a RETURNS dict is fabrication the algebra check cannot catch (it
# cancels out only when w_b == 0, and is silently wrong otherwise). These
# tests prove the precondition fails loud on the fabrication case and never
# fires on the normal, complete-dict case every other test in this file uses.

def test_brinson_fachler_fails_loud_on_missing_portfolio_return():
    """A sleeve carrying weight but absent from portfolio_sleeve_returns must
    raise, not fall through to the r_p=0.0 sink default.

    PROVES the pre-fix bug: pre-fix, this call returns a DataFrame with sleeve
    "B" priced at a fabricated r_p=0.0 instead of raising.
    """
    pw = {"A": 0.60, "B": 0.40}
    bw = {"A": 0.60, "B": 0.40}
    pr = {"A": 0.10}                       # "B" missing
    br = {"A": 0.08, "B": 0.04}

    with pytest.raises(AssertionError, match="B"):
        brinson_fachler(pw, bw, pr, br)


def test_brinson_fachler_fails_loud_on_missing_benchmark_return():
    """A sleeve carrying weight but absent from benchmark_sleeve_returns must
    raise, not fall through to the r_b=0.0 sink default.

    PROVES the pre-fix bug: pre-fix, this call returns a DataFrame with sleeve
    "B" priced at a fabricated r_b=0.0 instead of raising.
    """
    pw = {"A": 0.60, "B": 0.40}
    bw = {"A": 0.60, "B": 0.40}
    pr = {"A": 0.10, "B": 0.05}
    br = {"A": 0.08}                       # "B" missing

    with pytest.raises(AssertionError, match="B"):
        brinson_fachler(pw, bw, pr, br)


def test_brinson_fachler_passes_on_complete_returns():
    """The completeness precondition must not fire when every weighted sleeve
    has an explicit return in both returns dicts — the normal case."""
    pw = {"A": 0.60, "B": 0.40}
    bw = {"A": 0.60, "B": 0.40}
    pr = {"A": 0.10, "B": 0.05}
    br = {"A": 0.08, "B": 0.04}

    df = brinson_fachler(pw, bw, pr, br)   # must not raise
    assert len(df) == 2


def test_brinson_fachler_allows_weight_sparsity():
    """A sleeve absent from a WEIGHT dict (genuine 0 weight) must still be
    accepted, as long as it has an explicit return in both returns dicts —
    only return-dict sparsity is fabrication."""
    pw = {"A": 1.0}                         # "B" carries 0 portfolio weight
    bw = {"A": 0.60, "B": 0.40}
    pr = {"A": 0.10, "B": 0.05}
    br = {"A": 0.08, "B": 0.04}

    df = brinson_fachler(pw, bw, pr, br)   # must not raise
    assert set(df["sleeve"]) == {"A", "B"}


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


def _holdings_common_frontier() -> str:
    """Latest committed date on which EVERY portfolio holding has a REAL price — the
    holdings' COMMON frontier = MIN over holdings of each holding's
    MAX(price_date < today). Read directly from the DB, NO network fetch.

    The reconciliation consumes only the portfolio holdings, so the anchor must be
    scoped to them. The GLOBAL MAX(price_date) across all cached tickers OVER-PROMISES:
    the shared cache also holds non-holding tickers (weekend-trading benchmarks such as
    BTC-USD / GLD, sleeve benchmarks) and tickers a sibling test's fetch has advanced,
    any of which can reach a LATER date than the holdings. A global anchor then lands a
    trading day past the holdings' real frontier, so the 1M window's two endpoints
    diverge by that day's return — the residual that the strictly-before-today floor
    (#39) could NOT catch, because it is a SCOPE axis (global vs holdings), not a
    today-contamination axis. Taking the MIN over holdings — not the max, which is what
    last_real_price_date returns — guarantees every consumed holding has real data AT
    the anchor. Per-holding `price_date < today` also floors out any same-day partial
    bar (#39). No fetch.
    """
    import datetime
    from src.db import get_connection
    from src.holdings import get_holdings_on_date
    today = datetime.date.today().isoformat()
    holdings = get_holdings_on_date(today)
    frontiers: list[str] = []
    with get_connection() as conn:
        for ticker in holdings.index:
            price_ticker = "BIL" if ticker == "SPAXX" else ticker  # SPAXX proxied via BIL
            row = conn.execute(
                "SELECT MAX(price_date) FROM prices WHERE ticker = ? AND price_date < ?",
                (price_ticker, today),
            ).fetchone()
            if row and row[0]:
                frontiers.append(row[0])
    return min(frontiers) if frontiers else today


# Captured ONCE at import (pytest collection). Scoped to the holdings' common frontier
# (min over holdings), not the GLOBAL cache MAX: a non-holding ticker that reaches a
# later date — a weekend benchmark, or a row a sibling/prior-session fetch advanced —
# can no longer push the anchor past the date on which the holdings the reconciliation
# consumes actually have data. Combined with the per-test hermetic fixture below
# (which holds the cache fixed), the anchor is independent of cache state and order.
_HOLDINGS_FRONTIER = _holdings_common_frontier()


@pytest.fixture
def _no_live_fetch(monkeypatch):
    """Hold the shared price cache FIXED under the reconciliation tests: any attempted
    live fetch raises, and get_prices (which swallows fetch errors and falls back to
    the cache) therefore reads the committed prices as-is. This removes the
    cache-mutability degree of freedom — a sibling/prior-session fetch can no longer
    ragged-advance the cache under the test, and the test cannot advance it mid-run.
    Scoped to the reconciliation tests by explicit request (NOT autouse), so the rest
    of the suite still fetches normally."""
    def _blocked(*args, **kwargs):
        raise RuntimeError("live price fetch disabled inside reconciliation test (hermetic)")
    monkeypatch.setattr("src.prices.fetch_prices", _blocked)
    return None


def test_holdings_frontier_floors_before_today():
    """The reconciliation anchor is the holdings' COMMON frontier — the latest date on
    which EVERY holding has real data — and is always strictly before today, so a
    partial same-day bar can never become the anchor (#39) and a non-holding ticker's
    later date can never push it past the holdings' frontier (the scope fix). A
    regression to the global cache-MAX or to raw MAX(price_date) would fail here."""
    import datetime
    assert _holdings_common_frontier() < datetime.date.today().isoformat()


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


def test_identity_bf_sum_reconciles_to_stage2(_no_live_fetch):
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
    import pandas as pd
    from src.attribution import brinson_fachler_period
    from src.holdings import get_portfolio_value_series, last_real_price_date
    from src.returns import period_bounds

    INCEPTION = "2025-05-01"
    # Anchor on the HOLDINGS' common frontier captured at import (no live fetch), NOT
    # date.today() and NOT the global cache MAX — the latter over-promises whenever a
    # non-holding/sibling-advanced ticker reaches a later date than the holdings, which
    # made this fail on the first local run after the shared cache had been ragged-
    # advanced. The identity holds on the committed prices by construction; the
    # _no_live_fetch fixture keeps the cache fixed under the test.
    TODAY = _HOLDINGS_FRONTIER

    try:
        pv_full = get_portfolio_value_series(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Portfolio data unavailable: {exc}")

    if pv_full.dropna().empty:
        pytest.skip("Portfolio data empty — skipped in local/empty-DB mode")

    # Anchor every window on the LAST REAL price date — NOT pv_full.index[-1], which is
    # forward-filled to today. Clip the value series to that frontier so both
    # reconciliation sides slice to the same real endpoint. This makes the test
    # date-independent: it previously failed whenever CI ran with today > the last
    # traded day (after the UTC rollover, on weekends/holidays), because the value
    # series ffilled to today while the BF side used real prices — a 15-42 bps gap on
    # the short 1M window.
    real_end = last_real_price_date(INCEPTION, TODAY)
    real_end_d = datetime.date.fromisoformat(real_end)
    pv_real = pv_full[pv_full.index <= pd.Timestamp(real_end)]

    for label in ("SI", "1Y", "YTD", "3M", "1M"):
        start, end = period_bounds(label, real_end_d, INCEPTION)
        try:
            bf_df = brinson_fachler_period(start, end)
        except Exception as exc:
            pytest.skip(f"BF data unavailable for {label}: {exc}")

        if bf_df.empty:
            pytest.skip(f"BF result empty for {label} — skipped in local/empty-DB mode")

        r_p_ps        = _bpr_helper(pv_real, label)
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


@pytest.mark.parametrize("sim_today_offset_days", [0, 1, 3])
def test_period_bounds_window_is_anchor_only_not_wall_clock(sim_today_offset_days):
    """Regression guard for the 1M-window anchor flake (date-driven CI failure).

    period_bounds anchors the window on the data's last date, never date.today(),
    so the attribution window is identical regardless of when CI runs. Pre-fix the
    Performance page anchored the BF window on today-N, which diverged from the
    last_date-N price-series window whenever today > last_date (CI after the UTC
    rollover, on weekends/holidays, or a sparse personal-mode portfolio) — a 15-42
    bps gap on the short 1M window that the 0.5-bps reconciliation tolerance caught.

    sim_today_offset_days models the wall-clock date CI happens to run on; the
    asserted window must NOT move with it, and must differ from the old today-N
    anchor exactly when today has rolled past last_date.
    """
    import datetime
    from src.returns import period_bounds

    last_date = datetime.date(2026, 6, 10)
    inception = "2025-05-01"
    sim_today = last_date + datetime.timedelta(days=sim_today_offset_days)

    start_1m, end_1m = period_bounds("1M", last_date, inception)
    # Anchor-only: identical for every simulated wall-clock date.
    assert (start_1m, end_1m) == ("2026-05-11", "2026-06-10")

    # The pre-fix today-anchored start diverges precisely when today > last_date.
    old_buggy_start_1m = (sim_today - datetime.timedelta(days=30)).isoformat()
    if sim_today_offset_days > 0:
        assert old_buggy_start_1m != start_1m, "today-anchored start should differ once rolled forward"
    else:
        assert old_buggy_start_1m == start_1m

    # SI anchors on inception; the window end is always the data's last date.
    assert period_bounds("SI", last_date, inception) == (inception, "2026-06-10")


@pytest.mark.parametrize("days_past_frontier", [0, 1, 3])
def test_bf_reconciles_when_wall_clock_past_price_frontier(days_past_frontier, _no_live_fetch):
    """End-to-end proof the anchor fix holds when the wall clock runs past the last
    real price date — the exact CI-failure condition (today=Jun11, prices through Jun10),
    plus weekends (+3).

    Simulates today > frontier by forward-filling the value series past the real
    frontier (what get_portfolio_value_series does with a future end date), WITHOUT
    fetching future prices — deterministic and offline. With the window anchored on the
    real frontier and the value series clipped to it (the fix), BF and the price series
    reconcile within 0.5 bps; the pre-fix code anchored on the ffilled tail (== today)
    and diverged 15-42 bps on the 1M window. Passing at +1/+3 (not just +0) proves the
    flake is dead, not dormant.
    """
    import datetime
    import pandas as pd
    from src.attribution import brinson_fachler_period
    from src.holdings import get_portfolio_value_series, last_real_price_date
    from src.returns import period_bounds

    INCEPTION = "2025-05-01"
    # Frontier = holdings' common frontier captured at import (no live fetch), not today
    # and not the global cache MAX (which a non-holding/sibling-advanced ticker could
    # push past the holdings). The _no_live_fetch fixture holds the cache fixed.
    real_end = _HOLDINGS_FRONTIER
    real_end_d = datetime.date.fromisoformat(real_end)

    # Cached through the frontier only — no future-date fetch, so this stays offline.
    pv_real = get_portfolio_value_series(INCEPTION, real_end)
    if pv_real.dropna().empty:
        pytest.skip("Portfolio data empty — skipped in local/empty-DB mode")

    # Simulate the wall clock running past the frontier (pandas ffill only — no network).
    sim_today = real_end_d + datetime.timedelta(days=days_past_frontier)
    pv_ffilled = pv_real.reindex(
        pd.date_range(INCEPTION, sim_today.isoformat(), freq="D")
    ).ffill()
    assert pv_ffilled.index[-1].date() == sim_today  # the trap: ffilled tail == wall clock

    # The fix: anchor on the real frontier and clip the value series to it.
    clipped = pv_ffilled[pv_ffilled.index <= pd.Timestamp(real_end)]
    assert clipped.index[-1].date() == real_end_d

    for label in ("1M", "3M"):
        start, end = period_bounds(label, real_end_d, INCEPTION)
        bf_df = brinson_fachler_period(start, end)
        if bf_df.empty:
            pytest.skip(f"BF result empty for {label}")
        bf = float((bf_df["w_p"] * bf_df["r_p"]).sum()) + float(bf_df.attrs.get("cash_drag", 0.0))
        sl = clipped[(clipped.index >= pd.Timestamp(start)) & (clipped.index <= pd.Timestamp(end))]
        r_p_ps = float(sl.iloc[-1] / sl.iloc[0] - 1)
        gap_bps = abs(bf - r_p_ps) * 10_000
        assert gap_bps < 0.5, (
            f"{label} @ wall-clock=frontier+{days_past_frontier}: BF {bf*10000:.1f} vs "
            f"price {r_p_ps*10000:.1f} = {gap_bps:.2f} bps. Anchor fix regressed — the "
            f"window must anchor on last_real_price_date, not the ffilled pv tail."
        )


def test_bf_per_sleeve_returns_are_total_return():
    """BF per-sleeve r_p IS the adj_close total return — dividends embedded once,
    NOT double-counted by adding DRIP shares on top.

    Income double-count fix (was Phase 10.2 pin, inverted): adj_close already
    embeds dividend reinvestment, so brinson_fachler_period() values the actual
    (non-DRIP) shares at adj_close and must NOT add DRIP-reinvested shares — doing
    so double-counted income (overstating each sleeve by ~its distribution yield,
    worst on the FI/high-yield sleeves). The single-ticker International Developed
    sleeve (VEA) r_p must therefore EQUAL VEA's adj_close ratio over the window,
    not exceed it by a dividend premium. _last_adj_price IS adjusted close (total
    return), so the pre-fix "price-only" framing was wrong — that ratio already
    includes dividends.
    """
    import datetime
    from src.attribution import brinson_fachler_period, _last_adj_price

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

    # BF values sleeves at adj_close (total return) using _last_adj_price on both
    # ends. International Developed is held via VEA only, so its BF r_p must equal
    # VEA's own adj_close ratio — no DRIP-share inflation layered on top.
    p_start = _last_adj_price("VEA", INCEPTION)
    p_end   = _last_adj_price("VEA", TODAY)
    if p_start is None or p_end is None or p_start <= 0 or p_end <= 0:
        pytest.skip("VEA price data unavailable")

    r_p_adj_close = p_end / p_start - 1
    gap_bps = abs(r_p_total_return - r_p_adj_close) * 10_000

    assert gap_bps < 5.0, (
        f"International Developed r_p ({r_p_total_return*100:.2f}%) must equal VEA's "
        f"adj_close total return ({r_p_adj_close*100:.2f}%) within 5 bps — adj_close "
        f"already embeds dividends. Gap {gap_bps:.1f} bps suggests DRIP shares are "
        f"being double-counted on top of adj_close in brinson_fachler_period()."
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


# ── Price-gap tripwire ───────────────────────────────────────────────────────
# Mirrors test_benchmarks.py's r_b tripwire (test_real_benchmark_sleeves_never_
# silently_zero) for the portfolio side: brinson_fachler()'s internal algebra
# check holds regardless of whether r_p is real, fabricated-0.0, or
# fabricated-(-1.0), so it cannot catch a price-data gap — these tests instead
# construct a genuine gap directly and check _first_adj_price/_last_adj_price/
# brinson_fachler_period's behavior in response, which no test did before this.
#
# Each test's docstring states what the assertion would find against the
# PRE-FIX code (return 0.0 with no log; a fabricated 0%/-100% row in bf_df
# instead of exclusion) — that is the proof this tripwire actually catches the
# bug, not just a description of the fix.

def test_price_gap_helper_returns_none_not_zero(monkeypatch, caplog):
    """_first_adj_price/_last_adj_price must return None — never a fabricated
    0.0 — when a price cannot be found within the lookback window, and must log
    a warning naming the ticker so the gap is debuggable rather than invisible.

    PROVES the pre-fix bug: against the pre-fix code, both assertions below
    fail — the helper returned 0.0 (a float, not None), and no warning was ever
    logged anywhere in the module (grep-confirmed zero logging calls existed).
    """
    import logging
    import pandas as pd
    import src.attribution as attr_mod

    def _empty_prices(ticker, start, end, *a, **k):
        return pd.DataFrame()

    monkeypatch.setattr(attr_mod, "get_prices", _empty_prices)

    with caplog.at_level(logging.WARNING):
        result_first = attr_mod._first_adj_price("GHOST", "2025-06-01")
        result_last  = attr_mod._last_adj_price("GHOST", "2025-06-01")

    assert result_first is None, f"expected None on a price gap, got {result_first!r}"
    assert result_last is None, f"expected None on a price gap, got {result_last!r}"
    assert any("GHOST" in rec.message for rec in caplog.records), (
        "expected a logged warning naming the gapped ticker; none found — "
        f"captured records: {[r.message for r in caplog.records]}"
    )


def test_price_gap_excludes_sleeve_start_price(monkeypatch):
    """PATH A: a held ticker's START price is missing beyond the window — the
    sleeve must be EXCLUDED from the period's attribution (flagged in
    .attrs['price_gaps']), never assigned a fabricated 0% return.

    PROVES the pre-fix bug: pre-fix, the sleeve would appear in bf_df with r_p
    exactly 0.0 and w_p exactly 0.0 (a fabricated "flat, zero-weight" row,
    silently inflating every OTHER sleeve's weight via the under-counted
    denominator) instead of being excluded and flagged.
    """
    import datetime
    import pandas as pd
    import src.attribution as attr_mod
    from src.attribution import brinson_fachler_period

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()
    GAPPED_TICKER = "VNQ"  # Real Assets sleeve
    real_get_prices = attr_mod.get_prices

    def _gap_start(ticker, start, end, *a, **k):
        # _last_adj_price(ticker, start_date) calls get_prices(ticker,
        # start_date-5d, start_date) -- match on the END bound (the actual
        # queried date), not the internal window's start bound.
        if ticker == GAPPED_TICKER and end == INCEPTION:
            return pd.DataFrame()
        return real_get_prices(ticker, start, end, *a, **k)

    monkeypatch.setattr(attr_mod, "get_prices", _gap_start)

    try:
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")
    if bf_df.empty:
        pytest.skip("BF result empty — skipped in local/empty-DB mode")

    gaps = bf_df.attrs.get("price_gaps", [])
    assert any(t == GAPPED_TICKER for t, _ in gaps), (
        f"expected {GAPPED_TICKER}'s start-price gap to be recorded in "
        f".attrs['price_gaps'], got: {gaps}"
    )
    assert "Real Assets" not in bf_df["sleeve"].tolist(), (
        "Real Assets should be EXCLUDED from the period's attribution when its "
        f"only ticker ({GAPPED_TICKER}) has a start-price gap — found it in the "
        f"output instead:\n{bf_df[bf_df['sleeve'] == 'Real Assets']}"
    )
    # No fabricated row, and the remaining (measured) sleeves still reconcile —
    # both weight columns renormalize to sum to 1.0 over the reduced universe,
    # and brinson_fachler()'s internal algebra assert did not raise (it would
    # have crashed brinson_fachler_period() had the weights not been
    # renormalized consistently on both sides).
    assert abs(bf_df["w_p"].sum() - 1.0) < 1e-6
    assert abs(bf_df["w_b"].sum() - 1.0) < 1e-6


def test_price_gap_excludes_sleeve_end_price(monkeypatch):
    """PATH B: a held ticker's END price is missing beyond the window — the
    sleeve must be EXCLUDED, never assigned a fabricated -100% return.

    PROVES the pre-fix bug: pre-fix, the sleeve's r_p would compute to exactly
    -1.0 (a fabricated "-100% return") since its start value is intact but its
    end value is silently zeroed — per the earlier diagnostic, a real chance of
    being printed as the PDF's "Top detractor" narrative sentence with no
    plausibility check.
    """
    import datetime
    import pandas as pd
    import src.attribution as attr_mod
    from src.attribution import brinson_fachler_period

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()
    GAPPED_TICKER = "VOO"  # US Large Core sleeve
    real_get_prices = attr_mod.get_prices

    def _gap_end(ticker, start, end, *a, **k):
        if ticker == GAPPED_TICKER and end == TODAY:
            return pd.DataFrame()
        return real_get_prices(ticker, start, end, *a, **k)

    monkeypatch.setattr(attr_mod, "get_prices", _gap_end)

    try:
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")
    if bf_df.empty:
        pytest.skip("BF result empty — skipped in local/empty-DB mode")

    gaps = bf_df.attrs.get("price_gaps", [])
    assert any(t == GAPPED_TICKER for t, _ in gaps), (
        f"expected {GAPPED_TICKER}'s end-price gap to be recorded in "
        f".attrs['price_gaps'], got: {gaps}"
    )
    assert "US Large Core" not in bf_df["sleeve"].tolist(), (
        "US Large Core should be EXCLUDED from the period's attribution when its "
        f"only ticker ({GAPPED_TICKER}) has an end-price gap — found it in the "
        f"output instead (would show a fabricated -100% r_p pre-fix):\n"
        f"{bf_df[bf_df['sleeve'] == 'US Large Core']}"
    )
    assert not (bf_df["r_p"] == -1.0).any(), "no sleeve should show a fabricated -100% return"
    assert abs(bf_df["w_p"].sum() - 1.0) < 1e-6
    assert abs(bf_df["w_b"].sum() - 1.0) < 1e-6


def test_price_gap_bil_guard_handles_missing_period_price(monkeypatch):
    """The BIL/cash-sleeve guard (originally only bil_inception<=0) must also
    cover bil_period_start/bil_period_end being unavailable — degrading to the
    existing flat-1.0 fallback rather than dividing straight through to a hard,
    unguarded zero for the Cash/SPAXX sleeve.

    PROVES the pre-fix bug: pre-fix, a BIL period-price gap (with bil_inception
    intact) had NO guard at all — it divided straight through to p_start/p_end
    = 0.0, silently zeroing the cash sleeve's value at that endpoint with no
    log and no flag. Cash is not "excluded" the way a real holding sleeve is
    (it's an operational float, not a strategic sleeve being measured), so all
    9 strategic sleeves remain present — only the gap flag and the guarded
    fallback are new.
    """
    import datetime
    import pandas as pd
    import src.attribution as attr_mod
    from src.attribution import brinson_fachler_period

    INCEPTION = "2025-05-01"
    TODAY = datetime.date.today().isoformat()
    real_get_prices = attr_mod.get_prices

    def _gap_bil_period_start(ticker, start, end, *a, **k):
        # bil_period_start = _last_adj_price("BIL", start_date) calls
        # get_prices("BIL", start_date-5d, start_date) -- match on the END bound
        # (== start_date == INCEPTION here) so this targets ONLY the period-start
        # price, not bil_inception (a FORWARD-window _first_adj_price call whose
        # end bound is portfolio_inception+5d, not portfolio_inception itself).
        if ticker == "BIL" and end == INCEPTION:
            return pd.DataFrame()
        return real_get_prices(ticker, start, end, *a, **k)

    monkeypatch.setattr(attr_mod, "get_prices", _gap_bil_period_start)

    try:
        bf_df = brinson_fachler_period(INCEPTION, TODAY)
    except Exception as exc:
        pytest.skip(f"Data unavailable: {exc}")
    if bf_df.empty:
        pytest.skip("BF result empty — skipped in local/empty-DB mode")

    gaps = bf_df.attrs.get("price_gaps", [])
    assert any(t == "BIL" for t, _ in gaps), (
        f"expected the BIL period-price gap to be recorded in .attrs['price_gaps'], "
        f"got: {gaps}"
    )
    assert len(bf_df) == 9, (
        "expected all 9 strategic sleeves present despite the BIL gap (cash "
        "degrades via the safe-ratio fallback, it doesn't exclude sleeves), "
        f"got {len(bf_df)}: {sorted(bf_df['sleeve'].tolist())}"
    )
    assert "cash_weight" in bf_df.attrs
