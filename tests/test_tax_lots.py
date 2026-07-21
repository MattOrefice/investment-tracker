"""Unit tests for src/tax_lots.py — pure computation functions only (no DB)."""
import sys
import pathlib
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.tax_lots import (
    HARVEST_MATERIALITY_THRESHOLD,
    _LT_THRESHOLD_DAYS,
    _fifo_open_lots,
    apply_sleeve_filter,
    compute_days_held,
    compute_days_to_lt,
    compute_harvest_pool,
    compute_tax_status,
    compute_unrealized_gl,
    compute_unrealized_gl_pct,
    discretionary_trade_count,
    drip_lot_count,
    get_sleeve_rollup,
    lot_count_label,
    summary_metrics,
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_lots(**kwargs) -> pd.DataFrame:
    """Build a synthetic lots DataFrame. Provide lists for each column."""
    n = len(next(iter(kwargs.values())))
    base = {
        "trade_id":           list(range(1, n + 1)),
        "ticker":             ["VOO"] * n,
        "sleeve":             ["US Large Core"] * n,
        "trade_date":         ["2025-05-01"] * n,
        "days_held":          [400] * n,
        "shares":             [1.0] * n,
        "cost_basis_per_share": [100.0] * n,
        "cost_basis_total":   [100.0] * n,
        "current_price":      [110.0] * n,
        "market_value":       [110.0] * n,
        "unrealized_gl":      [10.0] * n,
        "unrealized_gl_pct":  [0.10] * n,
        "tax_status":         ["LT"] * n,
        "days_to_lt":         [0] * n,
        "lot_source":         ["initial"] * n,
    }
    base.update(kwargs)
    return pd.DataFrame(base)


def _make_buys(*rows) -> pd.DataFrame:
    """Make a buy-side DataFrame for _fifo_open_lots. Each row: (trade_id, trade_date, shares, price)."""
    return pd.DataFrame(
        [
            {"trade_id": r[0], "trade_date": r[1], "shares": r[2], "price": r[3], "lot_source": "initial"}
            for r in rows
        ]
    )


# ── Days held ─────────────────────────────────────────────────────────────────

def test_days_held_same_day():
    """Lot purchased and evaluated on the same day has 0 days held."""
    d = date(2025, 5, 1)
    assert compute_days_held(d, d) == 0


def test_days_held_across_year_boundary():
    """Days held spans Dec 31 → Jan 1 (365 days in a non-leap year)."""
    purchase = date(2025, 1, 1)
    as_of = date(2026, 1, 1)
    assert compute_days_held(purchase, as_of) == 365


def test_days_held_leap_year_boundary():
    """2024 is a leap year; Jan 1 2024 → Jan 1 2025 = 366 days."""
    purchase = date(2024, 1, 1)
    as_of = date(2025, 1, 1)
    assert compute_days_held(purchase, as_of) == 366


# ── LT qualification ──────────────────────────────────────────────────────────

def test_lt_threshold_constant():
    """_LT_THRESHOLD_DAYS must be 366 — IRS 'more than one year' rule."""
    assert _LT_THRESHOLD_DAYS == 366


def test_tax_status_day_365_is_st():
    """Exactly 365 days held → still short-term (not yet 'more than one year')."""
    assert compute_tax_status(365) == "ST"


def test_tax_status_day_366_is_lt():
    """Day 366 is the first day of long-term treatment."""
    assert compute_tax_status(366) == "LT"


def test_tax_status_zero_is_st():
    assert compute_tax_status(0) == "ST"


# ── Days to LT ────────────────────────────────────────────────────────────────

def test_days_to_lt_st_lot():
    """ST lot with 200 days held needs 166 more days to qualify."""
    assert compute_days_to_lt(200) == 166


def test_days_to_lt_at_threshold():
    """Exactly at threshold: 0 remaining days."""
    assert compute_days_to_lt(366) == 0


def test_days_to_lt_lt_lot_is_zero():
    """Already LT → 0 days remaining, not negative."""
    assert compute_days_to_lt(500) == 0


# ── Unrealized G/L ────────────────────────────────────────────────────────────

def test_unrealized_gl_positive_gain():
    """100 shares, cost $50, current $60 → $1,000 gain."""
    assert compute_unrealized_gl(100, 50.0, 60.0) == pytest.approx(1000.0)


def test_unrealized_gl_negative_loss():
    """100 shares, cost $50, current $40 → −$1,000 loss."""
    assert compute_unrealized_gl(100, 50.0, 40.0) == pytest.approx(-1000.0)


def test_unrealized_gl_zero_change():
    """Price unchanged → zero G/L."""
    assert compute_unrealized_gl(100, 50.0, 50.0) == pytest.approx(0.0)


def test_unrealized_gl_pct_gain():
    """G/L of $1,000 on cost basis of $5,000 → 20%."""
    assert compute_unrealized_gl_pct(1000.0, 5000.0) == pytest.approx(0.20)


def test_unrealized_gl_pct_zero_cost():
    """Zero cost basis → 0.0 to avoid division by zero."""
    assert compute_unrealized_gl_pct(100.0, 0.0) == 0.0


# ── FIFO lot matching ─────────────────────────────────────────────────────────

def test_fifo_no_sells_returns_all_buys():
    buys = _make_buys((1, "2025-01-01", 10.0, 100.0), (2, "2025-06-01", 5.0, 110.0))
    result = _fifo_open_lots(buys, 0.0)
    assert len(result) == 2
    assert result["shares"].sum() == pytest.approx(15.0)


def test_fifo_full_close_oldest_lot():
    """Selling 10 shares closes the first lot entirely; second lot remains."""
    buys = _make_buys((1, "2025-01-01", 10.0, 100.0), (2, "2025-06-01", 5.0, 110.0))
    result = _fifo_open_lots(buys, 10.0)
    assert len(result) == 1
    assert result.iloc[0]["trade_id"] == 2
    assert result.iloc[0]["shares"] == pytest.approx(5.0)


def test_fifo_partial_close():
    """Selling 3 shares from a 10-share lot → 7 shares remain on that lot."""
    buys = _make_buys((1, "2025-01-01", 10.0, 100.0))
    result = _fifo_open_lots(buys, 3.0)
    assert len(result) == 1
    assert result.iloc[0]["shares"] == pytest.approx(7.0)


def test_fifo_all_closed_returns_empty():
    buys = _make_buys((1, "2025-01-01", 10.0, 100.0))
    result = _fifo_open_lots(buys, 10.0)
    assert result.empty


# ── Sleeve roll-up reconciliation ─────────────────────────────────────────────

def test_sleeve_rollup_market_value_reconciles():
    """Roll-up market value sum must equal lot-level sum (no penny gap)."""
    lots = _make_lots(
        ticker=["VOO", "VOO", "IEMG"],
        sleeve=["US Large Core", "US Large Core", "Emerging Markets"],
        shares=[2.0, 3.0, 5.0],
        cost_basis_total=[200.0, 300.0, 500.0],
        market_value=[220.0, 330.0, 480.0],
        unrealized_gl=[20.0, 30.0, -20.0],
        unrealized_gl_pct=[0.10, 0.10, -0.04],
        tax_status=["LT", "LT", "ST"],
        days_to_lt=[0, 0, 100],
        days_held=[400, 400, 266],
    )
    rollup = get_sleeve_rollup(lots)
    assert rollup["Market Value"].sum() == pytest.approx(lots["market_value"].sum())


def test_sleeve_rollup_cost_basis_reconciles():
    """Roll-up cost basis sum must equal lot-level sum."""
    lots = _make_lots(
        ticker=["VOO", "VGIT"],
        sleeve=["US Large Core", "Core Fixed Income"],
        shares=[2.0, 4.0],
        cost_basis_total=[200.0, 400.0],
        market_value=[220.0, 390.0],
        unrealized_gl=[20.0, -10.0],
        unrealized_gl_pct=[0.10, -0.025],
        tax_status=["LT", "LT"],
        days_to_lt=[0, 0],
        days_held=[400, 400],
    )
    rollup = get_sleeve_rollup(lots)
    assert rollup["Cost Basis"].sum() == pytest.approx(lots["cost_basis_total"].sum())


def test_sleeve_rollup_st_gt_lt_split():
    """ST Gain and LT Gain should sum to total gains, Unrealized Loss to total losses."""
    lots = _make_lots(
        ticker=["VOO", "VTV", "AVUV"],
        sleeve=["US Large Core", "US Large Value", "US Small Cap"],
        shares=[1.0, 1.0, 1.0],
        cost_basis_total=[100.0, 100.0, 100.0],
        market_value=[120.0, 90.0, 115.0],
        unrealized_gl=[20.0, -10.0, 15.0],
        unrealized_gl_pct=[0.20, -0.10, 0.15],
        tax_status=["ST", "LT", "LT"],
        days_to_lt=[50, 0, 0],
        days_held=[316, 400, 400],
    )
    rollup = get_sleeve_rollup(lots)
    total_st_gain = rollup["ST Gain"].sum()
    total_lt_gain = rollup["LT Gain"].sum()
    total_loss = rollup["Unrealized Loss"].sum()
    # ST gain = 20 (VOO, ST), LT gain = 15 (AVUV, LT), LT loss = -10 (VTV, LT)
    assert total_st_gain == pytest.approx(20.0)
    assert total_lt_gain == pytest.approx(15.0)
    assert total_loss == pytest.approx(-10.0)


# ── Summary metrics ───────────────────────────────────────────────────────────

def test_summary_metrics_empty():
    """Empty DataFrame returns all-zero metrics dict."""
    m = summary_metrics(pd.DataFrame())
    assert m["unrealized_gl_total"] == 0.0
    assert m["unrealized_gl_pct"] == 0.0


def test_summary_metrics_totals_match_lots():
    """Summary market value and cost basis must match lot-level sums."""
    lots = _make_lots(
        ticker=["VOO", "IEMG"],
        sleeve=["US Large Core", "Emerging Markets"],
        shares=[2.0, 3.0],
        cost_basis_total=[200.0, 300.0],
        market_value=[220.0, 330.0],
        unrealized_gl=[20.0, 30.0],
        unrealized_gl_pct=[0.10, 0.10],
        tax_status=["LT", "ST"],
        days_to_lt=[0, 50],
        days_held=[400, 316],
    )
    m = summary_metrics(lots)
    assert m["cost_basis_total"] == pytest.approx(500.0)
    assert m["market_value_total"] == pytest.approx(550.0)
    assert m["unrealized_gl_total"] == pytest.approx(50.0)
    assert m["unrealized_gl_pct"] == pytest.approx(0.10)


def test_summary_metrics_lt_st_split():
    """LT gain is from LT lots; ST gain from ST lots; loss is negative G/L."""
    lots = _make_lots(
        ticker=["VOO", "VTV", "AVUV"],
        sleeve=["US Large Core", "US Large Value", "US Small Cap"],
        shares=[1.0, 1.0, 1.0],
        cost_basis_total=[100.0, 100.0, 100.0],
        market_value=[130.0, 85.0, 110.0],
        unrealized_gl=[30.0, -15.0, 10.0],
        unrealized_gl_pct=[0.30, -0.15, 0.10],
        tax_status=["LT", "ST", "LT"],
        days_to_lt=[0, 60, 0],
        days_held=[400, 306, 400],
    )
    m = summary_metrics(lots)
    assert m["unrealized_lt_gain"] == pytest.approx(40.0)   # VOO 30 + AVUV 10
    assert m["unrealized_st_gain"] == pytest.approx(0.0)    # VTV is a loss, not a gain
    assert m["unrealized_loss"] == pytest.approx(-15.0)


# ── Sleeve filter ─────────────────────────────────────────────────────────────

def test_sleeve_filter_empty_returns_all():
    """Empty selected_sleeves list → all lots returned (show-all semantics)."""
    lots = _make_lots(
        ticker=["VOO", "IEMG"],
        sleeve=["US Large Core", "Emerging Markets"],
    )
    result = apply_sleeve_filter(lots, [])
    assert len(result) == 2


def test_sleeve_filter_subset():
    """Non-empty list → only matching sleeve rows returned."""
    lots = _make_lots(
        ticker=["VOO", "IEMG"],
        sleeve=["US Large Core", "Emerging Markets"],
    )
    result = apply_sleeve_filter(lots, ["US Large Core"])
    assert len(result) == 1
    assert result.iloc[0]["ticker"] == "VOO"


def test_sleeve_filter_no_match_returns_empty():
    """Selecting a sleeve not present in lots → empty DataFrame."""
    lots = _make_lots(ticker=["VOO"], sleeve=["US Large Core"])
    result = apply_sleeve_filter(lots, ["Emerging Markets"])
    assert result.empty


# ── Harvest candidate pool ────────────────────────────────────────────────────

def test_harvest_pool_no_losses():
    """All lots in gain → pool is $0 and count is 0."""
    lots = _make_lots(
        unrealized_gl=[10.0, 20.0],
        ticker=["VOO", "VTV"],
        sleeve=["US Large Core", "US Large Value"],
    )
    pool, n = compute_harvest_pool(lots)
    assert pool == pytest.approx(0.0)
    assert n == 0


def test_harvest_pool_all_below_threshold():
    """Losses smaller than threshold don't count as material."""
    lots = _make_lots(
        unrealized_gl=[-5.0, -10.0],
        ticker=["VOO", "VTV"],
        sleeve=["US Large Core", "US Large Value"],
    )
    pool, n = compute_harvest_pool(lots, threshold=25.0)
    assert pool == pytest.approx(0.0)
    assert n == 0


def test_harvest_pool_material_losses():
    """Only lots with |loss| > threshold are counted; pool equals their sum."""
    lots = _make_lots(
        unrealized_gl=[-10.0, -30.0, -50.0, 5.0],
        ticker=["VOO", "VTV", "AVUV", "IEMG"],
        sleeve=["US Large Core", "US Large Value", "US Small Cap", "Emerging Markets"],
    )
    pool, n = compute_harvest_pool(lots, threshold=25.0)
    assert n == 2
    assert pool == pytest.approx(-80.0)


def test_harvest_pool_empty_lots():
    """Empty DataFrame → (0.0, 0)."""
    pool, n = compute_harvest_pool(pd.DataFrame())
    assert pool == pytest.approx(0.0)
    assert n == 0


def test_harvest_materiality_threshold_constant():
    """HARVEST_MATERIALITY_THRESHOLD must be 25.0."""
    assert HARVEST_MATERIALITY_THRESHOLD == 25.0


# ── Lot count label ───────────────────────────────────────────────────────────

def test_lot_count_label_zero():
    assert lot_count_label(0) == "0 lots"


def test_lot_count_label_one():
    assert lot_count_label(1) == "1 lot"


def test_lot_count_label_many():
    assert lot_count_label(5) == "5 lots"


# ── DRIP filter — pure DataFrame logic ───────────────────────────────────────

def _make_mixed_lots() -> pd.DataFrame:
    """DataFrame with 3 'initial' lots and 2 'drip' lots for filter testing."""
    base_date = date(2025, 5, 1)
    rows = []
    for i, src in enumerate(["initial", "initial", "initial", "drip", "drip"]):
        rows.append({
            "trade_id":             i + 1,
            "ticker":               "VOO",
            "sleeve":               "US Large Core",
            "trade_date":           base_date,
            "days_held":            366,
            "shares":               1.0,
            "cost_basis_per_share": 100.0,
            "cost_basis_total":     100.0,
            "current_price":        110.0,
            "market_value":         110.0,
            "unrealized_gl":        10.0,
            "unrealized_gl_pct":    0.10,
            "tax_status":           "LT",
            "days_to_lt":           0,
            "lot_source":           src,
        })
    return pd.DataFrame(rows)


def test_drip_toggle_off_excludes_drip_lots():
    """Filtering lot_source != 'drip' leaves only initial lots."""
    lots = _make_mixed_lots()
    display = lots[lots["lot_source"] != "drip"].copy()
    assert len(display) == 3
    assert (display["lot_source"] == "initial").all()


def test_drip_toggle_on_includes_all_lots():
    """When show_drip is True, display_lots equals lots unchanged."""
    lots = _make_mixed_lots()
    display = lots  # no filter
    assert len(display) == 5


def test_drip_toggle_off_summary_metrics_initial_only():
    """summary_metrics on initial-only lots uses only 3 lots worth of values."""
    lots = _make_mixed_lots()
    display = lots[lots["lot_source"] != "drip"].copy()
    m = summary_metrics(display)
    assert m["cost_basis_total"] == pytest.approx(300.0)
    assert m["market_value_total"] == pytest.approx(330.0)


def test_drip_toggle_off_harvest_pool_initial_only():
    """compute_harvest_pool on initial-only lots excludes DRIP lot losses."""
    lots = _make_mixed_lots()
    # Make one DRIP lot a material loss, one initial lot a smaller loss
    lots = lots.copy()
    lots.loc[lots["lot_source"] == "drip", "unrealized_gl"] = -200.0
    lots.loc[lots["lot_source"] == "initial", "unrealized_gl"] = -10.0  # below threshold

    display = lots[lots["lot_source"] != "drip"].copy()
    pool, n = compute_harvest_pool(display, threshold=25.0)
    assert n == 0  # initial losses are only $10, below $25 threshold
    assert pool == pytest.approx(0.0)


def test_drip_toggle_off_zero_discretionary_lots():
    """When all lots are DRIP, filtering to initial-only returns empty DataFrame."""
    lots = _make_mixed_lots()
    drip_only = lots[lots["lot_source"] == "drip"].copy()
    display = drip_only[drip_only["lot_source"] != "drip"].copy()
    assert display.empty
    m = summary_metrics(display)
    assert m["cost_basis_total"] == 0.0


# ── Banner count helpers — require demo DB ────────────────────────────────────

@pytest.mark.slow
def test_discretionary_trade_count_demo_db():
    """discretionary_trade_count() returns 11 against demo DB."""
    import os
    assert discretionary_trade_count() == 11


@pytest.mark.slow
def test_drip_lot_count_demo_db():
    """drip_lot_count() returns 49 against demo DB."""
    import os
    assert drip_lot_count() == 49
