"""Unit tests for src/rebalance.py — pure functions, no DB."""
import sys
import pathlib

import pytest
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.rebalance import compute_drift, suggest_buys


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
