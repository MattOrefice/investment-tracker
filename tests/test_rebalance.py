"""Unit tests for src/rebalance.py — pure functions, no DB."""
import sys
import pathlib

import pytest
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.rebalance import compute_drift, suggest_buys, suggest_contributions


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
    ]
    for weights, cash in cases:
        result = _sc(weights, cash)
        if not result.empty:
            total = float(result["Suggested $"].sum())
            assert abs(total - cash) < 0.01, (
                f"Sum {total:.4f} differs from cash {cash} for weights={weights}"
            )
