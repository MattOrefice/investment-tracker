"""Unit tests for src/rebalance.py — pure functions, no DB."""
import sys
import pathlib

import pytest
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.rebalance import (
    compute_drift, suggest_buys, suggest_contributions, unfunded_target_sleeves,
)


_TARGETS = {
    "US Large Core":           0.16,
    "US Large Quality":        0.14,
    "International Developed": 0.19,
    "Cash / SPAXX":            0.03,
}

_BANDS = {
    "US Large Core":           0.03,
    "US Large Quality":        0.03,
    "International Developed": 0.03,
    "Cash / SPAXX":            0.02,
}

_TICKER_MAP = {
    "VOO":   "US Large Core",
    "SPHQ":  "US Large Quality",
    "VEA":   "International Developed",
    "SPAXX": "Cash / SPAXX",
}

_PRICES = {"VOO": 500.0, "SPHQ": 50.0, "VEA": 50.0}


def _drift(weights: dict) -> pd.DataFrame:
    return compute_drift(weights, _TARGETS, _BANDS)


# ── compute_drift ─────────────────────────────────────────────────────────────

def test_compute_drift_all_in_band():
    # Each sleeve exactly at target → drift 0 → all in band
    weights = {
        "US Large Core": 0.16, "US Large Quality": 0.14,
        "International Developed": 0.19, "Cash / SPAXX": 0.03,
    }
    df = compute_drift(weights, _TARGETS, _BANDS)
    assert df["In Band"].all()
    assert df.loc["US Large Core", "Drift"] == pytest.approx(0.0)


def test_compute_drift_one_underweight():
    weights = {
        "US Large Core": 0.10,   # 6pp under target (band ±3pp) → outside
        "US Large Quality": 0.14,
        "International Developed": 0.19,
        "Cash / SPAXX": 0.57,
    }
    df = compute_drift(weights, _TARGETS, _BANDS)
    assert not df.loc["US Large Core", "In Band"]
    assert df.loc["US Large Core", "Drift"] == pytest.approx(-0.06)
    assert df.loc["US Large Quality", "In Band"]
    assert df.loc["International Developed", "In Band"]


def test_compute_drift_one_overweight():
    weights = {
        "US Large Core": 0.22,   # 6pp over target (band ±3pp) → outside
        "US Large Quality": 0.14,
        "International Developed": 0.19,
        "Cash / SPAXX": 0.45,
    }
    df = compute_drift(weights, _TARGETS, _BANDS)
    assert not df.loc["US Large Core", "In Band"]
    assert df.loc["US Large Core", "Drift"] == pytest.approx(0.06)


def test_compute_drift_raises_on_missing_band():
    """A held sleeve with no tolerance band must RAISE — a fallback band would
    fabricate its in/out-of-band verdict (and split ±0.02 here vs ±0.03 in the
    Performance chart for the same sleeve)."""
    weights = {"US Large Core": 0.16, "Off-SAA Thing": 0.05}
    targets = {"US Large Core": 0.16}                       # off-SAA has no target
    bands   = {"US Large Core": 0.03}                       # ...and no band → raise
    with pytest.raises(ValueError, match="tolerance band"):
        compute_drift(weights, targets, bands)


def test_compute_drift_untargeted_sleeve_is_overweight_not_raised():
    """The target lookup deliberately keeps its 0.0 fallback: a held sleeve with a
    band but no target reads as fully overweight vs a 0% target — a display-only,
    semantically-defensible reading, NOT a raise (contrast the band above)."""
    weights = {"US Large Core": 0.16, "Off-SAA": 0.05}
    targets = {"US Large Core": 0.16}                       # Off-SAA absent → target 0.0
    bands   = {"US Large Core": 0.03, "Off-SAA": 0.02}      # ...but band present
    df = compute_drift(weights, targets, bands)
    assert df.loc["Off-SAA", "Target Weight"] == 0.0
    assert df.loc["Off-SAA", "Drift"] == pytest.approx(0.05)
    assert not df.loc["Off-SAA", "In Band"]                 # 5pp drift > 2pp band


# ── suggest_buys ──────────────────────────────────────────────────────────────

def test_suggest_buys_no_cash():
    df = _drift({"US Large Core": 0.10, "US Large Quality": 0.14,
                 "International Developed": 0.19, "Cash / SPAXX": 0.57})
    result = suggest_buys(df, 10_000.0, 0.0, _TICKER_MAP, _PRICES)
    assert result.empty


def test_suggest_buys_no_underweight():
    # All non-cash sleeves at or above target
    df = _drift({"US Large Core": 0.16, "US Large Quality": 0.14,
                 "International Developed": 0.19, "Cash / SPAXX": 0.51})
    result = suggest_buys(df, 10_000.0, 500.0, _TICKER_MAP, _PRICES)
    assert result.empty


def test_suggest_buys_proportional_allocation():
    # Core 6pp under ($600 shortfall), Intl 5pp under ($500 shortfall), cash=$500
    # total shortfall $1100 > cash $500 → proportional
    df = _drift({"US Large Core": 0.10, "US Large Quality": 0.14,
                 "International Developed": 0.14, "Cash / SPAXX": 0.62})
    result = suggest_buys(df, 10_000.0, 500.0, _TICKER_MAP, _PRICES)

    assert not result.empty
    assert "VOO" in result["Ticker"].values
    assert "VEA" in result["Ticker"].values
    assert "SPAXX" not in result["Ticker"].values
    # All cash deployed
    assert result["Suggested $"].sum() == pytest.approx(500.0, rel=1e-3)
    # VOO gets more than VEA (larger shortfall)
    voo_dollars = float(result.loc[result["Ticker"] == "VOO", "Suggested $"].iloc[0])
    vea_dollars = float(result.loc[result["Ticker"] == "VEA", "Suggested $"].iloc[0])
    assert voo_dollars > vea_dollars


def test_suggest_buys_full_fill_when_shortfall_under_cash():
    # Core = 0.12 (target=0.16, band=±0.03, lower=0.13) → breached, 4pp under
    # shortfall $400 < cash $500 → full fill, only $400 deployed
    df = _drift({"US Large Core": 0.12, "US Large Quality": 0.14,
                 "International Developed": 0.19, "Cash / SPAXX": 0.55})
    assert not df.loc["US Large Core", "In Band"]   # confirm breach
    result = suggest_buys(df, 10_000.0, 500.0, _TICKER_MAP, _PRICES)

    assert not result.empty
    # shortfall = 0.04 × 10000 = $400 (gap to target, not band edge)
    assert result["Suggested $"].sum() == pytest.approx(400.0, rel=1e-3)


def test_suggest_buys_in_band_below_target_returns_empty():
    # Core = 0.15 (target=0.16, band=±0.03, lower=0.13) → within band
    # Below target but NOT breached → no action
    weights = {"US Large Core": 0.15, "US Large Quality": 0.14,
               "International Developed": 0.19, "Cash / SPAXX": 0.52}
    df = _drift(weights)
    assert df.loc["US Large Core", "In Band"]    # confirm in band
    assert df.loc["US Large Core", "Drift"] < 0  # confirm below target
    result = suggest_buys(df, 10_000.0, 500.0, _TICKER_MAP, _PRICES)
    assert result.empty


def test_suggest_buys_band_breach_shortfall_to_target_not_band_edge():
    # Core = 0.10 (target=0.16, band=±0.03, lower=0.13) → breached 3pp below lower
    # Shortfall must be gap-to-TARGET ($600), not gap-to-lower-edge ($300)
    weights = {"US Large Core": 0.10, "US Large Quality": 0.14,
               "International Developed": 0.19, "Cash / SPAXX": 0.57}
    df = _drift(weights)
    assert not df.loc["US Large Core", "In Band"]
    result = suggest_buys(df, 10_000.0, 10_000.0, _TICKER_MAP, _PRICES)
    assert not result.empty
    voo_dollars = float(result.loc[result["Ticker"] == "VOO", "Suggested $"].iloc[0])
    assert voo_dollars == pytest.approx(600.0, rel=1e-3)   # 0.06 × 10000 to target


def test_suggest_buys_mixed_breached_and_in_band():
    # Core = 0.10 (breached below lower=0.13); Quality = 0.13 (in band, lower=0.11)
    # Only Core should appear in suggestions
    weights = {"US Large Core": 0.10, "US Large Quality": 0.13,
               "International Developed": 0.19, "Cash / SPAXX": 0.58}
    df = _drift(weights)
    assert not df.loc["US Large Core", "In Band"]
    assert df.loc["US Large Quality", "In Band"]    # below target but in band
    assert df.loc["US Large Quality", "Drift"] < 0
    result = suggest_buys(df, 10_000.0, 1_000.0, _TICKER_MAP, _PRICES)
    assert not result.empty
    assert "VOO" in result["Ticker"].values
    assert "SPHQ" not in result["Ticker"].values    # Quality in band → excluded


def test_suggest_buys_multi_ticker_sleeve():
    ticker_map = {"VNQ": "Real Assets", "PDBC": "Real Assets", "SPAXX": "Cash / SPAXX"}
    targets = {"Real Assets": 0.10, "Cash / SPAXX": 0.03}
    bands   = {"Real Assets": 0.02, "Cash / SPAXX": 0.02}
    prices  = {"VNQ": 100.0, "PDBC": 20.0}

    weights = {"Real Assets": 0.05, "Cash / SPAXX": 0.95}
    df = compute_drift(weights, targets, bands)
    result = suggest_buys(df, 10_000.0, 500.0, ticker_map, prices)

    assert not result.empty
    assert set(result["Ticker"].values) == {"VNQ", "PDBC"}
    # Equal split between the two holdings in the sleeve
    vnq_d = float(result.loc[result["Ticker"] == "VNQ",  "Suggested $"].iloc[0])
    pdbc_d = float(result.loc[result["Ticker"] == "PDBC", "Suggested $"].iloc[0])
    assert vnq_d == pytest.approx(pdbc_d, rel=1e-3)


# ── suggest_contributions ────────────────────────────────────────────────────
# Fixtures: 2-sleeve portfolio (Core 60%, Bonds 40%), no cash sleeve, so
# investable_total = 1.0 and residual math is clean.

_SC_TARGETS    = {"Core": 0.60, "Bonds": 0.40}
_SC_TICKER_MAP = {"CORE_ETF": "Core", "BOND_ETF": "Bonds"}
_SC_PRICES     = {"CORE_ETF": 100.0, "BOND_ETF": 50.0}


def _sc(weights, cash):
    return suggest_contributions(10_000, cash, weights, _SC_TARGETS, _SC_TICKER_MAP, _SC_PRICES)


def test_suggest_contributions_zero_cash_returns_empty():
    result = _sc({"Core": 0.60, "Bonds": 0.40}, 0)
    assert result.empty


def test_suggest_contributions_on_target_pure_target_split():
    # Portfolio at target → shortfalls = 0 → Step 3, residual = $1000
    # Core: $600 (0.60 × 1000), Bonds: $400 (0.40 × 1000)
    result = _sc({"Core": 0.60, "Bonds": 0.40}, 1_000)
    core_d = float(result.loc[result["Ticker"] == "CORE_ETF", "Suggested $"].iloc[0])
    bond_d = float(result.loc[result["Ticker"] == "BOND_ETF", "Suggested $"].iloc[0])
    assert core_d == pytest.approx(600.0, rel=1e-3)
    assert bond_d == pytest.approx(400.0, rel=1e-3)
    assert result["Suggested $"].sum() == pytest.approx(1_000.0, rel=1e-3)


def test_suggest_contributions_one_underweight_gap_exceeds_cash():
    # Core: 40% actual (target 60%) → shortfall = $2000; Bonds at target (sf=0)
    # Cash $500 < shortfall $2000 → Step 2 → all $500 to Core, Bonds gets nothing
    result = _sc({"Core": 0.40, "Bonds": 0.40}, 500)
    assert not result.empty
    assert "BOND_ETF" not in result["Ticker"].values
    core_d = float(result.loc[result["Ticker"] == "CORE_ETF", "Suggested $"].iloc[0])
    assert core_d == pytest.approx(500.0, rel=1e-3)


def test_suggest_contributions_one_underweight_gap_less_than_cash():
    # Core: 55% actual (target 60%) → shortfall = $500; Bonds at target (sf=0)
    # Cash $1000 > shortfall $500 → Step 3
    # Core: $500 (close) + $500 × 0.60 = $800   rationale "mixed"
    # Bonds: $0 + $500 × 0.40 = $200             rationale "maintain target"
    result = _sc({"Core": 0.55, "Bonds": 0.40}, 1_000)
    core_d = float(result.loc[result["Ticker"] == "CORE_ETF", "Suggested $"].iloc[0])
    bond_d = float(result.loc[result["Ticker"] == "BOND_ETF", "Suggested $"].iloc[0])
    assert core_d == pytest.approx(800.0, rel=1e-3)
    assert bond_d == pytest.approx(200.0, rel=1e-3)
    assert result["Suggested $"].sum() == pytest.approx(1_000.0, rel=1e-3)


def test_suggest_contributions_multiple_underweight_total_gap_exceeds_cash():
    # Core: 50% (sf=$1000), Bonds: 30% (sf=$1000) → total $2000 > cash $600
    # Step 2: proportional (50/50) → Core $300, Bonds $300
    result = _sc({"Core": 0.50, "Bonds": 0.30}, 600)
    core_d = float(result.loc[result["Ticker"] == "CORE_ETF", "Suggested $"].iloc[0])
    bond_d = float(result.loc[result["Ticker"] == "BOND_ETF", "Suggested $"].iloc[0])
    assert core_d == pytest.approx(300.0, rel=1e-3)
    assert bond_d == pytest.approx(300.0, rel=1e-3)
    assert result["Suggested $"].sum() == pytest.approx(600.0, rel=1e-3)


def test_suggest_contributions_multiple_underweight_total_gap_less_than_cash():
    # Core: 57% (sf=$300), Bonds: 38% (sf=$200) → total $500 < cash $1000
    # Step 3: full close + residual $500 by target weight
    # Core: $300 + $500×0.60 = $600; Bonds: $200 + $500×0.40 = $400
    result = _sc({"Core": 0.57, "Bonds": 0.38}, 1_000)
    core_d = float(result.loc[result["Ticker"] == "CORE_ETF", "Suggested $"].iloc[0])
    bond_d = float(result.loc[result["Ticker"] == "BOND_ETF", "Suggested $"].iloc[0])
    assert core_d == pytest.approx(600.0, rel=1e-3)
    assert bond_d == pytest.approx(400.0, rel=1e-3)
    assert result["Suggested $"].sum() == pytest.approx(1_000.0, rel=1e-3)


def test_suggest_contributions_rationale_tagging():
    # Step 3 case: Core below target → "mixed"; Bonds at target → "maintain target"
    result_s3 = _sc({"Core": 0.57, "Bonds": 0.40}, 1_000)
    core_rat = result_s3.loc[result_s3["Ticker"] == "CORE_ETF", "Rationale"].iloc[0]
    bond_rat = result_s3.loc[result_s3["Ticker"] == "BOND_ETF", "Rationale"].iloc[0]
    assert core_rat == "mixed"
    assert bond_rat == "maintain target"

    # Step 2 case: Core severely underweight, shortfall > cash → "close drift"
    result_s2 = _sc({"Core": 0.40, "Bonds": 0.40}, 200)
    core_rat2 = result_s2.loc[result_s2["Ticker"] == "CORE_ETF", "Rationale"].iloc[0]
    assert core_rat2 == "close drift"
    assert "BOND_ETF" not in result_s2["Ticker"].values  # Bonds sf=0 → excluded in Step 2


def test_suggest_contributions_sum_invariant():
    # Every non-empty result must sum to cash_to_deploy within $0.01
    cases = [
        ({"Core": 0.60, "Bonds": 0.40}, 1_000),   # on target
        ({"Core": 0.40, "Bonds": 0.40}, 500),      # one underweight, Step 2
        ({"Core": 0.55, "Bonds": 0.40}, 1_000),    # one underweight, Step 3
        ({"Core": 0.50, "Bonds": 0.30}, 600),      # both underweight, Step 2
        ({"Core": 0.57, "Bonds": 0.38}, 1_000),    # both underweight, Step 3
        ({"Core": 0.70, "Bonds": 0.40}, 1_000),    # Core overweight, Step 3 "above target"
    ]
    for weights, cash in cases:
        result = _sc(weights, cash)
        if not result.empty:
            total = float(result["Suggested $"].sum())
            assert abs(total - cash) < 0.01, (
                f"Sum {total:.4f} differs from cash {cash} for weights={weights}"
            )


def test_suggest_contributions_benchmark_tickers_excluded_from_division():
    # Each sleeve has 1 holding + 1 benchmark (benchmark not in prices);
    # without the price-filter fix this would silently halve each sleeve's allocation.
    ticker_map = {
        "CORE_ETF": "Core",
        "CORE_BM":  "Core",   # benchmark — absent from prices
        "BOND_ETF": "Bonds",
        "BOND_BM":  "Bonds",  # benchmark — absent from prices
    }
    prices = {"CORE_ETF": 100.0, "BOND_ETF": 50.0}

    result = suggest_contributions(
        10_000, 1_000, {"Core": 0.60, "Bonds": 0.40},
        _SC_TARGETS, ticker_map, prices,
    )

    assert not result.empty
    assert "CORE_BM" not in result["Ticker"].values
    assert "BOND_BM" not in result["Ticker"].values
    assert result["Suggested $"].sum() == pytest.approx(1_000.0, rel=1e-3)
    core_d = float(result.loc[result["Ticker"] == "CORE_ETF", "Suggested $"].iloc[0])
    bond_d = float(result.loc[result["Ticker"] == "BOND_ETF", "Suggested $"].iloc[0])
    assert core_d == pytest.approx(600.0, rel=1e-3)
    assert bond_d == pytest.approx(400.0, rel=1e-3)


def test_suggest_contributions_step3_residual_distribution():
    # 3-sleeve at-target portfolio — residual distributes exactly by target weight
    targets_3    = {"Core": 0.60, "Bonds": 0.30, "Alt": 0.10}
    ticker_map_3 = {"CORE_ETF": "Core", "BOND_ETF": "Bonds", "ALT_ETF": "Alt"}
    prices_3     = {"CORE_ETF": 100.0, "BOND_ETF": 50.0, "ALT_ETF": 25.0}

    result = suggest_contributions(
        10_000, 1_000,
        {"Core": 0.60, "Bonds": 0.30, "Alt": 0.10},
        targets_3, ticker_map_3, prices_3,
    )

    core_d = float(result.loc[result["Ticker"] == "CORE_ETF", "Suggested $"].iloc[0])
    bond_d = float(result.loc[result["Ticker"] == "BOND_ETF", "Suggested $"].iloc[0])
    alt_d  = float(result.loc[result["Ticker"] == "ALT_ETF",  "Suggested $"].iloc[0])
    assert core_d == pytest.approx(600.0, rel=1e-3)
    assert bond_d == pytest.approx(300.0, rel=1e-3)
    assert alt_d  == pytest.approx(100.0, rel=1e-3)
    assert result["Suggested $"].sum() == pytest.approx(1_000.0, rel=1e-3)


def test_suggest_contributions_above_target_rationale():
    # Core overweight (70% vs 60% target), Bonds at target → Step 3
    # Core has no shortfall but is above target → "above target"
    # Bonds has no shortfall and is not above target → "maintain target"
    result = _sc({"Core": 0.70, "Bonds": 0.40}, 1_000)
    core_rat = result.loc[result["Ticker"] == "CORE_ETF", "Rationale"].iloc[0]
    bond_rat = result.loc[result["Ticker"] == "BOND_ETF", "Rationale"].iloc[0]
    assert core_rat == "above target"
    assert bond_rat == "maintain target"


def test_suggest_contributions_assertion_fires_on_missing_ticker_coverage():
    # Bonds sleeve gets an allocation in Step 3 but has no ticker in the map →
    # Suggested $ sum < cash → AssertionError
    ticker_map_core_only = {"CORE_ETF": "Core"}
    with pytest.raises(AssertionError, match="suggest_contributions"):
        suggest_contributions(
            10_000, 1_000, {"Core": 0.60, "Bonds": 0.40},
            _SC_TARGETS, ticker_map_core_only, _SC_PRICES,
        )


# ── Production-scenario test (the test that should have existed before the bug) ─

# Full SAA targets mirroring the DB
_PROD_TARGETS = {
    "US Large Core":           0.17,
    "US Large Quality":        0.15,
    "US Large Value":          0.09,
    "US Small Cap":            0.08,
    "International Developed": 0.20,
    "Emerging Markets":        0.09,
    "Core Fixed Income":       0.06,
    "TIPS":                    0.04,
    "Real Assets":             0.10,
    "Cash / SPAXX":            0.02,
}

# Full ticker_to_sleeve including benchmarks — exactly as the securities table
# JOIN produces it.  Benchmarks are the keys absent from _PROD_PRICES.
_PROD_TICKER_MAP = {
    # holdings
    "VOO":   "US Large Core",
    "SPHQ":  "US Large Quality",
    "VTV":   "US Large Value",
    "AVUV":  "US Small Cap",
    "VEA":   "International Developed",
    "IEMG":  "Emerging Markets",
    "VGIT":  "Core Fixed Income",
    "SCHP":  "TIPS",
    "VNQ":   "Real Assets",
    "PDBC":  "Real Assets",
    "SPAXX": "Cash / SPAXX",
    # benchmarks (not in prices — were the root cause of the 50% bug)
    "SPY":   "US Large Core",
    "QUAL":  "US Large Quality",
    "IWD":   "US Large Value",
    "IWM":   "US Small Cap",
    "EFA":   "International Developed",
    "EEM":   "Emerging Markets",
    "IEF":   "Core Fixed Income",
    "TIP":   "TIPS",
    "DJP":   "Real Assets",
    "BIL":   "Cash / SPAXX",
}

# Only holding tickers — benchmarks absent, as in production
_PROD_PRICES = {
    "VOO": 520.0, "SPHQ": 52.0, "VTV": 155.0, "AVUV": 95.0,
    "VEA": 50.0, "IEMG": 60.0, "VGIT": 62.0, "SCHP": 53.0,
    "VNQ": 90.0, "PDBC": 15.0,
}

# Weights reflecting observed production state vs new SAA targets
_PROD_WEIGHTS = {
    "US Large Core":           0.170,   # at 0.17
    "US Large Quality":        0.120,   # below 0.15
    "US Large Value":          0.070,   # below 0.09
    "US Small Cap":            0.080,   # at 0.08
    "International Developed": 0.210,   # above 0.20
    "Emerging Markets":        0.090,   # at 0.09
    "Core Fixed Income":       0.080,   # above 0.06
    "TIPS":                    0.050,   # above 0.04
    "Real Assets":             0.120,   # above 0.10
    "Cash / SPAXX":            0.010,
}


def test_suggest_contributions_production_scenario_sum_invariant():
    # The bug scenario: full SAA ticker map with holdings + benchmarks,
    # benchmarks absent from prices.  Before the fix, $100 → $50.89 (50% loss).
    for cash in [100.0, 500.0, 1_000.0]:
        result = suggest_contributions(
            100_000, cash, _PROD_WEIGHTS,
            _PROD_TARGETS, _PROD_TICKER_MAP, _PROD_PRICES,
        )
        assert not result.empty, f"Expected non-empty result for cash={cash}"
        total = float(result["Suggested $"].sum())
        assert abs(total - cash) <= 0.02, (
            f"Sum invariant violated for cash=${cash}: "
            f"allocated ${total:.4f} (diff=${abs(total - cash):.4f})"
        )
        for bm in ("SPY", "QUAL", "IWD", "IWM", "EFA", "EEM", "IEF", "TIP", "DJP", "BIL"):
            assert bm not in result["Ticker"].values, f"Benchmark {bm} in output"


# ── Phase 22.1: SUM_INVARIANT_TOLERANCE constant + button predicate ───────────

def test_sum_invariant_tolerance_constant_value():
    """SUM_INVARIANT_TOLERANCE must equal 0.10 — it drives both the internal
    assertion in suggest_contributions and the Execute and Log button guard."""
    from src.rebalance import SUM_INVARIANT_TOLERANCE
    assert SUM_INVARIANT_TOLERANCE == 0.10


def test_sum_invariant_tolerance_button_predicate_boundary():
    """Button predicate: abs(diff) > SUM_INVARIANT_TOLERANCE.
    diff=0.05 → within tolerance → button enabled.
    diff=0.15 → outside tolerance → button disabled."""
    from src.rebalance import SUM_INVARIANT_TOLERANCE
    assert not (abs(0.05) > SUM_INVARIANT_TOLERANCE), \
        "$0.05 diff should be within tolerance → button enabled"
    assert abs(0.15) > SUM_INVARIANT_TOLERANCE, \
        "$0.15 diff should exceed tolerance → button disabled"


# ── Phase 33: band-status surfacing (tax-aware, buy-only) ───────────────────────

from src.rebalance import (
    closest_to_breach,
    interpret_rebalance_status,
    rebalance_action_text,
)


def test_interpret_status_all_in_band_names_closest():
    targets = {"A": 0.20, "B": 0.10, "C": 0.10}
    bands   = {"A": 0.03, "B": 0.02, "C": 0.02}
    # A: +1.5% drift, ±3% band → headroom 1.5%; B: +1.0%, ±2% → headroom 1.0% (closest);
    # C: +0.5%, ±2% → headroom 1.5%. Closest is B (least headroom), not A (largest drift).
    weights = {"A": 0.215, "B": 0.110, "C": 0.105}
    d = compute_drift(weights, targets, bands)
    txt = interpret_rebalance_status(d)
    assert "within their tolerance bands" in txt
    assert "B is closest to breach" in txt
    assert "1.0% of headroom" in txt


def test_closest_to_breach_uses_headroom_not_drift():
    targets = {"A": 0.20, "B": 0.10, "C": 0.10}
    bands   = {"A": 0.03, "B": 0.02, "C": 0.02}
    weights = {"A": 0.215, "B": 0.110, "C": 0.105}  # A bigger drift, B less headroom
    c = closest_to_breach(compute_drift(weights, targets, bands))
    assert c["sleeve"] == "B"
    assert c["headroom"] == pytest.approx(0.01, abs=1e-9)
    # Guard: the largest-drift sleeve (A) is NOT the closest to breach.
    assert c["sleeve"] != "A"


def test_closest_to_breach_none_when_no_in_band():
    targets = {"A": 0.20}
    bands   = {"A": 0.02}
    weights = {"A": 0.30}  # +10% drift, out of band → no in-band sleeves
    assert closest_to_breach(compute_drift(weights, targets, bands)) is None


def test_interpret_status_out_of_band_counts_and_tax_framing():
    targets = {"A": 0.20, "B": 0.10, "C": 0.10}
    bands   = {"A": 0.03, "B": 0.02, "C": 0.02}
    # A: +4% (▲ Over, ±3% → out); B: −3% (▼ Under, ±2% → out); C: in band.
    weights = {"A": 0.24, "B": 0.07, "C": 0.10}
    d = compute_drift(weights, targets, bands)
    txt = interpret_rebalance_status(d)
    assert "2 sleeves out of band: 1 over, 1 under" in txt
    assert "A" in txt and "B" in txt
    # Tax-aware framing for the overweight: contributions, not sold, capital gains.
    assert "rather than sold" in txt
    assert "capital gains" in txt
    assert "contributions" in txt


def test_rebalance_action_text_overweight_is_not_a_sell():
    over = rebalance_action_text(pd.Series({"In Band": False, "Drift": 0.04}))
    assert "Not sold here" in over
    assert "contributions" in over
    assert "tax-inefficient" in over
    # No sell-order language anywhere.
    assert "sell" not in over.lower().replace("not sold", "")


def test_rebalance_action_text_underweight_and_in_band():
    under = rebalance_action_text(pd.Series({"In Band": False, "Drift": -0.03}))
    assert "priority allocation" in under
    assert rebalance_action_text(pd.Series({"In Band": True, "Drift": 0.005})) == "—"


def test_closest_to_breach_excludes_zero_target_residual():
    # "Other / Non-SAA" has target 0 and the least headroom, but is not a
    # band-managed sleeve and must not be reported as closest to breach.
    targets = {"US Small Cap": 0.08, "Other / Non-SAA": 0.0}
    bands   = {"US Small Cap": 0.02, "Other / Non-SAA": 0.02}
    weights = {"US Small Cap": 0.093, "Other / Non-SAA": 0.018}  # SC hr 0.7%, Other hr 0.2%
    c = closest_to_breach(compute_drift(weights, targets, bands))
    assert c is not None
    assert c["sleeve"] == "US Small Cap"   # not "Other / Non-SAA"


# ── unfunded_target_sleeves — the Capital Deployment page-level guard ───────────
# Detects targeted sleeves with no priced-held ticker BEFORE suggest_contributions
# is called, so the page shows an actionable message instead of tripping the
# Suggested-$ invariant. The invariant itself is unchanged.

def test_unfunded_target_sleeves_empty_when_all_covered():
    targets = {"US Large Core": 0.5, "International Quality": 0.5}
    t2s = {"VOO": "US Large Core", "IDHQ": "International Quality"}
    prices = {"VOO": 100.0, "IDHQ": 40.0}          # both held + priced
    assert unfunded_target_sleeves(targets, t2s, prices) == []


def test_unfunded_target_sleeves_flags_unpriced_sorted():
    targets = {"US Large Core": 0.4, "International Quality": 0.3, "International Small Value": 0.3}
    t2s = {"VOO": "US Large Core", "IDHQ": "International Quality", "AVDV": "International Small Value"}
    prices = {"VOO": 100.0}                          # only VOO held; the two tilts unheld
    assert unfunded_target_sleeves(targets, t2s, prices) == [
        "International Quality", "International Small Value",
    ]


def test_unfunded_target_sleeves_ignores_zero_target_cash():
    # Cash / SPAXX carries a 0 target and is never a deploy target — even unpriced it
    # must not be flagged.
    targets = {"US Large Core": 1.0, "Cash / SPAXX": 0.0}
    t2s = {"VOO": "US Large Core", "SPAXX": "Cash / SPAXX"}
    prices = {"VOO": 100.0}
    assert unfunded_target_sleeves(targets, t2s, prices) == []


def test_unfunded_target_sleeves_zero_price_is_not_covered():
    # A held ticker with a 0 price does not "fund" its sleeve.
    targets = {"US Large Core": 0.5, "International Quality": 0.5}
    t2s = {"VOO": "US Large Core", "IDHQ": "International Quality"}
    prices = {"VOO": 100.0, "IDHQ": 0.0}
    assert unfunded_target_sleeves(targets, t2s, prices) == ["International Quality"]


# ── suggest_buys reports WHICH branch allocated, so callers can explain leftover ──
#
# Undeployed cash means opposite things in the two branches and the frame alone
# cannot tell them apart. pages/11 read every leftover as "all shortfalls fully
# filled", which is false in the proportional branch by construction: that branch
# is reached precisely because the cash ran out.

def test_suggest_buys_reports_fully_filled_when_cash_covers_shortfall():
    # Core 6pp under ($600), Intl 5pp under ($500) → shortfall $1100, cash $2000.
    df = _drift({"US Large Core": 0.10, "US Large Quality": 0.14,
                 "International Developed": 0.14, "Cash / SPAXX": 0.62})
    result = suggest_buys(df, 10_000.0, 2_000.0, _TICKER_MAP, _PRICES)
    assert result.attrs["shortfalls_fully_filled"] is True
    assert result.attrs["total_shortfall"] == pytest.approx(1100.0, rel=1e-6)
    # Genuine surplus: the sum is the shortfall, not the cash.
    assert result["Suggested $"].sum() == pytest.approx(1100.0, rel=1e-3)


def test_suggest_buys_reports_not_fully_filled_when_cash_runs_out():
    # Same book, cash $500 < shortfall $1100 → proportional branch.
    df = _drift({"US Large Core": 0.10, "US Large Quality": 0.14,
                 "International Developed": 0.14, "Cash / SPAXX": 0.62})
    result = suggest_buys(df, 10_000.0, 500.0, _TICKER_MAP, _PRICES)
    assert result.attrs["shortfalls_fully_filled"] is False
    assert result.attrs["total_shortfall"] == pytest.approx(1100.0, rel=1e-6)
    # The cash is exhausted and $600 of breach remains — the fact the old caption
    # denied. Derived from attrs, never from the leftover amount, which is ~0 here
    # and so read as "nothing left to say".
    unfilled = result.attrs["total_shortfall"] - float(result["Suggested $"].sum())
    assert unfilled == pytest.approx(600.0, abs=0.05)


def test_suggest_buys_branch_flag_is_not_derivable_from_leftover():
    """The two branches are distinguishable by attrs and NOT by undeployed cash.

    This is the whole reason the flag exists. In the proportional branch the
    leftover is ~$0, which is also what a fully-filled-and-exactly-spent run looks
    like — so a caller branching on the amount cannot tell "all breaches closed"
    from "cash ran out with breaches open", and the page picked the wrong one.
    """
    proportional = suggest_buys(
        _drift({"US Large Core": 0.10, "US Large Quality": 0.14,
                "International Developed": 0.14, "Cash / SPAXX": 0.62}),
        10_000.0, 500.0, _TICKER_MAP, _PRICES)
    # Exactly-spent fill branch: cash equals the shortfall.
    exact = suggest_buys(
        _drift({"US Large Core": 0.10, "US Large Quality": 0.14,
                "International Developed": 0.14, "Cash / SPAXX": 0.62}),
        10_000.0, 1_100.0, _TICKER_MAP, _PRICES)

    left_prop = 500.0 - float(proportional["Suggested $"].sum())
    left_exact = 1_100.0 - float(exact["Suggested $"].sum())
    assert abs(left_prop) < 0.05 and abs(left_exact) < 0.05, (
        "both branches leave ~no undeployed cash — that is the premise of this test"
    )
    assert proportional.attrs["shortfalls_fully_filled"] is False
    assert exact.attrs["shortfalls_fully_filled"] is True


def test_suggest_buys_every_return_path_carries_the_flags():
    """Including the two guard returns, which bypass the allocation entirely.

    An unstamped frame is indistinguishable from one reporting "nothing to say",
    so a caller would fall through to silence — the same failure the record exists
    to remove. Written after the first version of this change stamped only the
    allocating path and this assertion caught it.
    """
    in_band = _drift({"US Large Core": 0.16, "US Large Quality": 0.14,
                      "International Developed": 0.19, "Cash / SPAXX": 0.51})
    breached = _drift({"US Large Core": 0.10, "US Large Quality": 0.14,
                       "International Developed": 0.14, "Cash / SPAXX": 0.62})

    # No breaches: nothing is unfilled, so True is the honest report.
    no_breach = suggest_buys(in_band, 10_000.0, 500.0, _TICKER_MAP, _PRICES)
    assert no_breach.empty
    assert no_breach.attrs["shortfalls_fully_filled"] is True
    assert no_breach.attrs["total_shortfall"] == 0.0

    # Nothing assessed: None, not True. Claiming "all filled" on a run that never
    # looked at the breaches is the class of error this whole change fixes.
    no_cash = suggest_buys(breached, 10_000.0, 0.0, _TICKER_MAP, _PRICES)
    assert no_cash.empty
    assert no_cash.attrs["shortfalls_fully_filled"] is None

    no_book = suggest_buys(breached, 0.0, 500.0, _TICKER_MAP, _PRICES)
    assert no_book.empty
    assert no_book.attrs["shortfalls_fully_filled"] is None
