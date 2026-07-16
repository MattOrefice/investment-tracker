"""Unit tests for src/harvest.py — pure computation functions only."""
import sys
import pathlib
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.harvest import (
    DEFAULT_LT_TAX_RATE,
    DEFAULT_ST_TAX_RATE,
    HARVEST_ACTION_THRESHOLD,
    HARVEST_PCT_THRESHOLD,
    REPLACEMENT_MAP,
    WASH_SALE_WINDOW_DAYS,
    compute_harvest_candidates,
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_lot(
    trade_id: int = 1,
    ticker: str = "VOO",
    sleeve: str = "US Large Core",
    shares: float = 10.0,
    tax_status: str = "LT",
    cost_basis_per_share: float = 100.0,
    current_price: float = 80.0,
    unrealized_gl: float = -200.0,
    unrealized_gl_pct: float = -0.20,
) -> pd.DataFrame:
    """Build a single-lot DataFrame compatible with compute_harvest_candidates."""
    return pd.DataFrame(
        [
            {
                "trade_id":             trade_id,
                "ticker":               ticker,
                "sleeve":               sleeve,
                "trade_date":           "2024-01-01",
                "days_held":            400,
                "shares":               shares,
                "tax_status":           tax_status,
                "cost_basis_per_share": cost_basis_per_share,
                "cost_basis_total":     shares * cost_basis_per_share,
                "current_price":        current_price,
                "market_value":         shares * current_price,
                "unrealized_gl":        unrealized_gl,
                "unrealized_gl_pct":    unrealized_gl_pct,
                "days_to_lt":           0,
                "lot_source":           "initial",
            }
        ]
    )


# ── Threshold filtering ───────────────────────────────────────────────────────

def test_candidate_qualifies_when_both_thresholds_met():
    """$200 loss at 20% qualifies — both exceed $100 floor and 5% floor."""
    lots = _make_lot(unrealized_gl=-200.0, unrealized_gl_pct=-0.20)
    result = compute_harvest_candidates(lots, action_threshold=100.0, pct_threshold=0.05)
    assert len(result) == 1


def test_candidate_excluded_when_dollar_threshold_not_met():
    """$50 loss at 20% — excluded because abs(loss) < $100 even though pct qualifies."""
    lots = _make_lot(unrealized_gl=-50.0, unrealized_gl_pct=-0.20)
    result = compute_harvest_candidates(lots, action_threshold=100.0, pct_threshold=0.05)
    assert len(result) == 0


def test_candidate_excluded_when_pct_threshold_not_met():
    """$500 loss at 4% — excluded because pct < 5% even though dollar amount qualifies."""
    lots = _make_lot(unrealized_gl=-500.0, unrealized_gl_pct=-0.04)
    result = compute_harvest_candidates(lots, action_threshold=100.0, pct_threshold=0.05)
    assert len(result) == 0


def test_candidate_excluded_when_gain():
    """Positive G/L is never a harvest candidate."""
    lots = _make_lot(unrealized_gl=200.0, unrealized_gl_pct=0.20)
    result = compute_harvest_candidates(lots)
    assert len(result) == 0


def test_candidate_excluded_when_zero_gl():
    """Zero G/L is not a harvest candidate."""
    lots = _make_lot(unrealized_gl=0.0, unrealized_gl_pct=0.0)
    result = compute_harvest_candidates(lots)
    assert len(result) == 0


def test_empty_lots_returns_empty_list():
    """Empty DataFrame → empty list, no exception."""
    assert compute_harvest_candidates(pd.DataFrame()) == []


# ── ST vs LT tax benefit differentiation ─────────────────────────────────────

def test_st_loss_uses_st_tax_rate():
    """ST lot: estimated_tax_benefit = abs(loss) × st_tax_rate (0.22)."""
    lots = _make_lot(unrealized_gl=-200.0, unrealized_gl_pct=-0.20, tax_status="ST")
    result = compute_harvest_candidates(lots, st_tax_rate=0.22, lt_tax_rate=0.15)
    assert result[0]["estimated_tax_benefit"] == pytest.approx(200.0 * 0.22)


def test_lt_loss_uses_lt_tax_rate():
    """LT lot: estimated_tax_benefit = abs(loss) × lt_tax_rate (0.15)."""
    lots = _make_lot(unrealized_gl=-200.0, unrealized_gl_pct=-0.20, tax_status="LT")
    result = compute_harvest_candidates(lots, st_tax_rate=0.22, lt_tax_rate=0.15)
    assert result[0]["estimated_tax_benefit"] == pytest.approx(200.0 * 0.15)


def test_st_benefit_exceeds_lt_benefit_for_same_dollar_loss():
    """Same dollar loss: ST benefit > LT benefit because marginal rate > LTCG rate."""
    lot_st = _make_lot(trade_id=1, unrealized_gl=-300.0, unrealized_gl_pct=-0.30, tax_status="ST")
    lot_lt = _make_lot(trade_id=2, unrealized_gl=-300.0, unrealized_gl_pct=-0.30, tax_status="LT")
    lots = pd.concat([lot_st, lot_lt], ignore_index=True)
    result = compute_harvest_candidates(lots)
    st_benefit = next(c["estimated_tax_benefit"] for c in result if c["tax_status"] == "ST")
    lt_benefit = next(c["estimated_tax_benefit"] for c in result if c["tax_status"] == "LT")
    assert st_benefit > lt_benefit


# ── Sort order ────────────────────────────────────────────────────────────────

def test_candidates_sorted_descending_by_tax_benefit():
    """Highest estimated tax benefit appears first in the returned list."""
    small_loss = _make_lot(
        trade_id=1, ticker="VOO",
        unrealized_gl=-110.0, unrealized_gl_pct=-0.11, tax_status="LT",
    )
    large_loss = _make_lot(
        trade_id=2, ticker="IEMG", sleeve="Emerging Markets",
        unrealized_gl=-400.0, unrealized_gl_pct=-0.40, tax_status="LT",
    )
    lots = pd.concat([small_loss, large_loss], ignore_index=True)
    result = compute_harvest_candidates(lots)
    assert result[0]["ticker"] == "IEMG"
    assert result[1]["ticker"] == "VOO"


# ── Replacement map ───────────────────────────────────────────────────────────

def _saa_tickers() -> list[str]:
    """The SAA tickers, DERIVED from the committed household securities CSV.

    Previously a hand-maintained literal list. That list could never fail on a
    ticker it didn't mention: swap a sleeve's SAA ticker and the old entry stays
    listed (and still has a REPLACEMENT_MAP entry, so the test passes), while the
    NEW ticker is simply never checked. The check has to come from the same source
    the app reads, or it only guards what someone remembered to type.
    """
    import csv
    import pathlib

    csv_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "data" / "seed" / "securities_household.csv"
    )
    with csv_path.open(encoding="utf-8") as fh:
        return [
            row["symbol"].strip()
            for row in csv.DictReader(fh)
            if row["is_in_saa"].strip().lower() == "true"
        ]


def test_replacement_map_covers_all_saa_tickers():
    """Every SAA holding ticker must have an explicit entry in REPLACEMENT_MAP."""
    saa_tickers = _saa_tickers()
    assert saa_tickers, "Derived no SAA tickers from securities_household.csv"
    missing = [t for t in saa_tickers if t not in REPLACEMENT_MAP]
    assert missing == [], (
        f"Missing REPLACEMENT_MAP entries: {missing}. Derived from "
        f"data/seed/securities_household.csv (is_in_saa=true), so a newly-adopted "
        f"SAA ticker is caught here rather than being silently unlisted."
    )


def test_replacement_map_entries_have_correct_structure():
    """Every REPLACEMENT_MAP value must be a 3-tuple (ticker|None, str, risk_level)."""
    valid_risk_levels = {"clean", "moderate", "high"}
    for ticker, entry in REPLACEMENT_MAP.items():
        assert isinstance(entry, tuple) and len(entry) == 3, (
            f"{ticker}: expected 3-tuple, got {type(entry)}"
        )
        repl, rationale, risk = entry
        assert repl is None or isinstance(repl, str), f"{ticker}: replacement must be str or None"
        assert isinstance(rationale, str) and rationale, f"{ticker}: rationale must be non-empty string"
        assert risk in valid_risk_levels, f"{ticker}: risk_level '{risk}' not in {valid_risk_levels}"


def test_high_risk_replacement_not_surfaced_as_actionable():
    """PDBC maps to 'high' risk — compute_harvest_candidates must set replacement_ticker=None."""
    lots = _make_lot(
        ticker="PDBC", sleeve="Real Assets",
        unrealized_gl=-300.0, unrealized_gl_pct=-0.30,
    )
    result = compute_harvest_candidates(lots)
    assert len(result) == 1
    assert result[0]["replacement_ticker"] is None
    assert "consult" in result[0]["replacement_rationale"].lower()


# ── Wash sale window ──────────────────────────────────────────────────────────

def test_wash_sale_window_is_30_days_each_side():
    """wash_sale_start = as_of − 30 days; wash_sale_end = as_of + 30 days."""
    as_of = date(2025, 6, 15)
    lots  = _make_lot(unrealized_gl=-200.0, unrealized_gl_pct=-0.20)
    result = compute_harvest_candidates(lots, as_of=as_of)
    assert result[0]["wash_sale_start_date"] == as_of - timedelta(days=30)
    assert result[0]["wash_sale_end_date"]   == as_of + timedelta(days=30)


def test_wash_sale_window_constant_is_30():
    assert WASH_SALE_WINDOW_DAYS == 30


# ── Default threshold / rate constants ───────────────────────────────────────

def test_action_threshold_is_100():
    assert HARVEST_ACTION_THRESHOLD == 100.0


def test_pct_threshold_is_5_percent():
    assert HARVEST_PCT_THRESHOLD == pytest.approx(0.05)


def test_default_st_tax_rate_is_22_percent():
    assert DEFAULT_ST_TAX_RATE == pytest.approx(0.22)


def test_default_lt_tax_rate_is_15_percent():
    assert DEFAULT_LT_TAX_RATE == pytest.approx(0.15)
