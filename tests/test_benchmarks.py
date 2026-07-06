"""Tests for src/benchmarks.py — Custom Blended benchmark and per-sleeve benchmark returns.

Neither get_custom_blended_series() nor get_sleeve_benchmark_returns() had a dedicated
correctness test before this file: every reference to get_custom_blended_series() elsewhere
in the suite either mocks it outright (test_factors.py, test_reports.py) or consumes it as
an opaque data input for a different function's identity check (test_attribution.py,
test_risk_metrics.py), so the allocation/mark-to-market arithmetic itself was never
independently verified.

Separately, brinson_fachler()'s internal algebra check (sum of effects == r_p_total -
r_b_total) holds algebraically regardless of what r_b actually is. If a sleeve's benchmark
price fetch fails, get_sleeve_benchmark_returns() silently substitutes a 0.0 return for that
sleeve (src/benchmarks.py's total_frac<=0 branch), and brinson_fachler_period() zero-fills
any sleeve absent from the fetched frame (src/attribution.py's bm_returns_raw.get(s, 0.0)) --
the algebra check closes either way, so this corruption is invisible to every existing
identity test. See test_degenerate_benchmark_pins_current_zero_fill_behavior and
test_real_benchmark_sleeves_never_silently_zero below.
"""
from __future__ import annotations

import sys
import pathlib

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import src.benchmarks as bm


class _FakeConn:
    """Minimal stand-in for the sqlite3 connection context manager, returning
    hand-picked asset_classes rows instead of touching a real database."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):
        return self

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_custom_blended_series_hand_calculation(monkeypatch):
    """Hand-calculated 2-sleeve blend, fully synthetic (no DB, no price fetch).

    SleeveA (60%) tracks TICKA: 100 -> 110 -> 121 (+10%, +10%).
    SleeveB (40%) tracks TICKB: 50 -> 45 -> 40.5 (-10%, -10%).

    Day 0: $0.60 buys 0.006 units of TICKA, $0.40 buys 0.008 units of TICKB -> $1.00 total.
    Day 1: 0.006*110 + 0.008*45  = 0.66 + 0.36 = 1.02  (= 0.6*1.10 + 0.4*0.90)
    Day 2: 0.006*121 + 0.008*40.5 = 0.726 + 0.324 = 1.05 (= 0.6*1.21 + 0.4*0.81)

    This is the only test in the suite that verifies get_custom_blended_series()'s
    allocation/mark-to-market arithmetic directly rather than mocking it or treating it
    as an opaque input.
    """
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    price_a = pd.Series([100.0, 110.0, 121.0], index=dates)
    price_b = pd.Series([50.0, 45.0, 40.5], index=dates)

    monkeypatch.setattr(bm, "_SLEEVE_BENCHMARKS", {
        "SleeveA": [("TICKA", 1.0)],
        "SleeveB": [("TICKB", 1.0)],
    })
    monkeypatch.setattr(bm, "get_connection", lambda: _FakeConn([
        {"name": "SleeveA", "target_weight": 0.6},
        {"name": "SleeveB", "target_weight": 0.4},
    ]))

    def _fake_price_series(ticker, start_date, end_date, col="adj_close"):
        return {"TICKA": price_a, "TICKB": price_b}[ticker]

    monkeypatch.setattr(bm, "_get_price_series", _fake_price_series)

    result = bm.get_custom_blended_series("2026-01-01", "2026-01-03")

    expected = [1.00, 1.02, 1.05]
    assert result.tolist() == pytest.approx(expected, abs=1e-9), (
        f"get_custom_blended_series hand-calculation mismatch: "
        f"got {result.tolist()}, expected {expected}"
    )


def test_degenerate_benchmark_pins_current_zero_fill_behavior(monkeypatch):
    """PINS the CURRENT behavior: when every price component for a sleeve's benchmark
    fails to fetch, get_sleeve_benchmark_returns() returns exactly 0.0 for that sleeve
    rather than raising, warning, or excluding the sleeve from the result.

    FLAG FOR AUTHOR — this is not asserted to be correct, only pinned so a change is
    deliberate rather than accidental. A 0.0 benchmark return is indistinguishable from
    "the benchmark was genuinely flat"; downstream, brinson_fachler_period() feeds this
    straight into selection_effect = w_p*(r_p - r_b), so a failed benchmark price fetch
    silently misattributes the sleeve's ENTIRE return as manager skill (selection), with
    zero allocation-effect signal and no error surfaced anywhere in the UI. Candidate
    fixes to consider: raise / surface an explicit data-quality warning on the affected
    page, exclude the sleeve from the attribution table rather than zero-fill it, or
    fall back to a stated proxy ticker. See test_real_benchmark_sleeves_never_silently_zero
    for the tripwire that checks this isn't currently happening against real data.
    """
    def _always_fails(ticker, *args, **kwargs):
        raise RuntimeError("simulated fetch failure")

    monkeypatch.setattr(bm, "get_prices", _always_fails)

    df = bm.get_sleeve_benchmark_returns("2025-05-01", "2026-06-10")
    last = df.iloc[-1]

    assert (last == 0.0).all(), (
        f"Expected the CURRENT zero-fill behavior when all price fetches fail (every "
        f"sleeve return exactly 0.0), got:\n{last}\n"
        "If this assertion now fails, the zero-fill behavior may have been intentionally "
        "changed -- update this test to pin the NEW intended behavior rather than "
        "re-asserting zero-fill blindly."
    )


def test_real_benchmark_sleeves_never_silently_zero():
    """TRIPWIRE: no strategic sleeve's benchmark return is silently zero over the
    portfolio's full since-inception window, using the committed demo.db price cache
    (no live fetch).

    brinson_fachler()'s internal algebra check cannot distinguish a correct r_b from a
    silently-zeroed one -- the sum-of-effects identity holds either way (see module
    docstring). This test instead inspects r_b directly, per sleeve, for the real
    pipeline: a genuine multi-month benchmark return netting to EXACTLY 0.0 is not a
    real market outcome for any of these tickers (SPY/QUAL/IWD/IWM/EFA/EEM/IEF/TIP/
    VNQ+DBC/BIL), so an exact-zero r_b for a sleeve carrying real benchmark weight
    (w_b > 0) is the fingerprint of the degenerate-benchmark bug, not a coincidence.
    """
    from src.attribution import brinson_fachler_period

    try:
        bf_df = brinson_fachler_period("2025-05-01", "2026-06-10")
    except Exception as exc:
        pytest.skip(f"BF data unavailable: {exc}")

    if bf_df.empty:
        pytest.skip("BF result empty — skipped in local/empty-DB mode")

    zeroed = bf_df[(bf_df["w_b"] > 0) & (bf_df["r_b"] == 0.0)]
    assert zeroed.empty, (
        "Sleeve(s) with real benchmark weight but r_b exactly 0.0 -- this is the "
        "degenerate-benchmark fingerprint (a benchmark price fetch silently failed and "
        f"zero-filled instead of erroring):\n{zeroed[['sleeve', 'w_b', 'r_b']]}"
    )
