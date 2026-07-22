"""Tests for src/style_box.py — continuous-coordinate equity style box."""
import math
import sys
import pathlib
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.style_box import (
    US_EQUITY_TICKERS,
    NON_US_EQUITY_MAP,
    assign_textposition,
    compute_size_score,
    compute_value_growth_score,
    xy_to_category,
)
from src.positioning import (
    build_style_box_figure,
    get_non_us_equity_data,
    get_style_box_data,
)


# ── Calibration anchors ───────────────────────────────────────────────────────

def test_size_score_spy_at_anchor():
    """SPY must return exactly y=2.0 (calibration anchor for Large/Blend centre)."""
    assert compute_size_score("SPY") == pytest.approx(2.0, abs=1e-9)


def test_value_growth_score_spy_at_anchor():
    """SPY must return exactly x=1.0 (zero z-score → Blend centre)."""
    assert compute_value_growth_score("SPY") == pytest.approx(1.0, abs=1e-9)


# ── Value-tilt direction ──────────────────────────────────────────────────────

def test_voo_near_blend_centre():
    """VOO (S&P 500, same fundamentals as SPY) must land very close to x=1.0."""
    x = compute_value_growth_score("VOO")
    assert abs(x - 1.0) < 0.05, f"VOO x={x:.4f} too far from Blend centre"


def test_vtv_is_value_tilted():
    """VTV (Large Value) must be left of Blend centre and inside Value column (x < 0.5)."""
    x = compute_value_growth_score("VTV")
    assert x < 1.0, "VTV must have value tilt (x < 1.0)"
    assert x < 0.5, f"VTV x={x:.4f} must be in Value column (x < 0.5)"


def test_avuv_is_deep_value():
    """AVUV (Small Value) must be more value-tilted than VTV."""
    x_vtv  = compute_value_growth_score("VTV")
    x_avuv = compute_value_growth_score("AVUV")
    assert x_avuv < x_vtv, "AVUV should be more value-tilted (lower x) than VTV"
    assert x_avuv < 1.0, "AVUV must have value tilt (x < 1.0)"


def test_sphq_is_slight_growth_tilt():
    """SPHQ (Quality) must land right of SPY (x > 1.0), slight growth lean."""
    x = compute_value_growth_score("SPHQ")
    assert x > 1.0, f"SPHQ x={x:.4f} must be slightly growth-tilted (x > 1.0)"
    assert x < 1.5, f"SPHQ x={x:.4f} should remain inside Blend column (x < 1.5)"


# ── Size score direction ──────────────────────────────────────────────────────

def test_avuv_is_small_cap():
    """AVUV must be below the Large/Mid boundary (y < 1.5)."""
    y = compute_size_score("AVUV")
    assert y < 1.5, f"AVUV y={y:.4f} must be below Large/Mid boundary (y=1.5)"


def test_voo_is_large_cap():
    """VOO (S&P 500) must be in the Large band (y >= 1.5)."""
    y = compute_size_score("VOO")
    assert y >= 1.5, f"VOO y={y:.4f} must be in Large band (y >= 1.5)"


def test_size_score_monotone():
    """Higher market-cap ETFs must have higher y scores."""
    y_voo  = compute_size_score("VOO")   # ~$250B weighted avg
    y_vtv  = compute_size_score("VTV")   # ~$150B
    y_avuv = compute_size_score("AVUV")  # ~$3B
    assert y_voo > y_vtv > y_avuv, "y scores must be ordered: VOO > VTV > AVUV"


# ── assign_textposition ───────────────────────────────────────────────────────

def test_textposition_top_half_left_of_centre():
    """Top-left quadrant of Large/Value cell → 'top left'."""
    # Large/Value cell centre at (0, 2). Dot at (0.2, 2.3) → top-right of centre.
    # Actually x=0.2 > cx=0, so → right not left. Use x=-0.1 for left.
    assert assign_textposition(-0.1, 2.3) == "top left"


def test_textposition_bottom_right_quadrant():
    """Bottom-right of Small/Value cell → 'bottom right'."""
    # Small/Value centre at (0, 0). Dot at (0.3, -0.2) → bottom-right.
    assert assign_textposition(0.3, -0.2) == "bottom right"


def test_textposition_near_horizontal_centre():
    """Dot very close to cell horizontal centre uses 'center' alignment."""
    # Blend centre x=1, dot at x=1.02 (within 0.05 → center)
    pos = assign_textposition(1.02, 2.3)
    assert pos in ("top center", "bottom center"), f"Near-centre got {pos!r}"


def test_textposition_spy_position():
    """SPY at (1.0, 2.0) → 'top center' (exactly at Blend centre, top of Large)."""
    assert assign_textposition(1.0, 2.0) == "top center"


# ── Universe filtering ────────────────────────────────────────────────────────

def test_us_equity_tickers_set():
    """US_EQUITY_TICKERS must include exactly the four US equity holdings."""
    assert US_EQUITY_TICKERS == {"VOO", "VTV", "SPHQ", "AVUV"}


def test_non_us_equity_map():
    """NON_US_EQUITY_MAP must contain VEA and IEMG with region labels."""
    assert "VEA" in NON_US_EQUITY_MAP
    assert "IEMG" in NON_US_EQUITY_MAP


def test_build_style_box_figure_excludes_non_us():
    """VEA and IEMG must not appear as text labels in the continuous-mode figure."""
    data = [
        {"ticker": "VOO",  "x": 1.00, "y": 2.00, "weight_pct": 16.0},
        {"ticker": "VTV",  "x": 0.39, "y": 1.78, "weight_pct":  8.0},
        {"ticker": "SPHQ", "x": 1.17, "y": 1.95, "weight_pct": 14.0},
        {"ticker": "AVUV", "x":-0.09, "y": 0.08, "weight_pct":  7.0},
    ]
    fig = build_style_box_figure(data)
    all_text = []
    for trace in fig.data:
        all_text.extend(list(trace.text))
    assert "VEA"  not in all_text, "VEA must not appear in the US equity style box"
    assert "IEMG" not in all_text, "IEMG must not appear in the US equity style box"


# ── Continuous-mode figure geometry ──────────────────────────────────────────

def test_build_style_box_continuous_dot_count():
    """Continuous mode must produce exactly one dot per input ETF."""
    data = [
        {"ticker": "VOO",  "x": 1.00, "y": 2.00, "weight_pct": 16.0},
        {"ticker": "VTV",  "x": 0.39, "y": 1.78, "weight_pct":  8.0},
        {"ticker": "SPHQ", "x": 1.17, "y": 1.95, "weight_pct": 14.0},
        {"ticker": "AVUV", "x":-0.09, "y": 0.08, "weight_pct":  7.0},
    ]
    fig = build_style_box_figure(data)
    total_points = sum(len(trace.x) for trace in fig.data)
    assert total_points == 4


def test_build_style_box_continuous_dots_distinct():
    """All four US equity dots must have distinct (x, y) coordinates."""
    data = [
        {"ticker": "VOO",  "x": 1.00, "y": 2.00, "weight_pct": 16.0},
        {"ticker": "VTV",  "x": 0.39, "y": 1.78, "weight_pct":  8.0},
        {"ticker": "SPHQ", "x": 1.17, "y": 1.95, "weight_pct": 14.0},
        {"ticker": "AVUV", "x":-0.09, "y": 0.08, "weight_pct":  7.0},
    ]
    fig = build_style_box_figure(data)
    coords = []
    for trace in fig.data:
        coords.extend(list(zip(trace.x, trace.y)))
    assert len(set(coords)) == 4, "All four dots must have distinct (x, y) positions"


def test_build_style_box_continuous_larger_dot_for_heavier_weight():
    """Heavier-weight ETF must produce a larger dot than lighter-weight ETF."""
    data = [
        {"ticker": "VOO",  "x": 1.00, "y": 2.00, "weight_pct": 16.0},  # heavier
        {"ticker": "AVUV", "x":-0.09, "y": 0.08, "weight_pct":  7.0},  # lighter
    ]
    fig = build_style_box_figure(data)
    all_text = []
    all_size = []
    for trace in fig.data:
        all_text.extend(list(trace.text))
        all_size.extend(list(trace.marker.size))
    voo_sz  = all_size[all_text.index("VOO")]
    avuv_sz = all_size[all_text.index("AVUV")]
    assert voo_sz > avuv_sz, "VOO (16%) must have larger dot than AVUV (7%)"


def test_build_style_box_continuous_single_trace():
    """Continuous mode must use a single scatter trace (no two-trace split needed)."""
    data = [
        {"ticker": "VOO",  "x": 1.00, "y": 2.00, "weight_pct": 16.0},
        {"ticker": "VTV",  "x": 0.39, "y": 1.78, "weight_pct":  8.0},
        {"ticker": "SPHQ", "x": 1.17, "y": 1.95, "weight_pct": 14.0},
        {"ticker": "AVUV", "x":-0.09, "y": 0.08, "weight_pct":  7.0},
    ]
    fig = build_style_box_figure(data)
    assert len(fig.data) == 1, "Continuous mode must produce exactly one scatter trace"


# ── get_non_us_equity_data ────────────────────────────────────────────────────

def test_non_us_equity_map_derives_from_current_book(use_demo_db):
    """The callout map must come from the DB, not the frozen two-ticker constant.

    Pinned to the demo book (12 sleeves): the map must carry all five non-US
    holdings labelled with their sleeve names. The static NON_US_EQUITY_MAP froze
    the VEA/IEMG world and silently dropped the tilt holdings from the callout.
    """
    from src.positioning import _non_us_equity_map

    m = _non_us_equity_map()
    assert set(m) == {"VEA", "IDHQ", "AVIV", "AVDV", "IEMG"}
    assert m["VEA"] == "International Core"
    assert m["IEMG"] == "Emerging Markets"


def test_non_us_equity_map_falls_back_to_static_when_db_unavailable():
    """DB failure degrades to the static VEA/IEMG map rather than raising."""
    from src.positioning import _non_us_equity_map

    with patch("src.sleeve_config.sleeve_holdings", side_effect=RuntimeError("db down")):
        m = _non_us_equity_map()
    assert m == NON_US_EQUITY_MAP


def test_get_non_us_equity_data_empty_portfolio():
    """Empty portfolio must return [] without raising."""
    empty_df = pd.DataFrame(columns=["net_shares"])
    with patch("src.positioning.get_holdings_on_date", return_value=empty_df):
        result = get_non_us_equity_data("2026-03-31")
    assert result == []


def test_get_non_us_equity_data_contains_vea_iemg():
    """Non-US callout must include VEA and IEMG when portfolio holds them."""
    import numpy as np
    holdings_df = pd.DataFrame(
        {"net_shares": {"VEA": 100.0, "IEMG": 80.0, "VOO": 50.0}},
    )
    mock_prices = pd.DataFrame({"close": [100.0]})

    with patch("src.positioning.get_holdings_on_date", return_value=holdings_df), \
         patch("src.positioning.get_prices", return_value=mock_prices):
        result = get_non_us_equity_data("2026-03-31")

    tickers = [r["ticker"] for r in result]
    assert "VEA"  in tickers
    assert "IEMG" in tickers
    assert "VOO"  not in tickers  # US equity must not appear in non-US callout


# ── xy_to_category ────────────────────────────────────────────────────────────

def test_xy_to_category_large_value():
    """x=0.3, y=1.8 → Large Value."""
    size, style = xy_to_category(0.3, 1.8)
    assert size == "Large"
    assert style == "Value"


def test_xy_to_category_small_value():
    """x=0.1, y=0.2 → Small Value."""
    size, style = xy_to_category(0.1, 0.2)
    assert size == "Small"
    assert style == "Value"


def test_xy_to_category_large_growth():
    """x=1.7, y=2.1 → Large Growth."""
    size, style = xy_to_category(1.7, 2.1)
    assert size == "Large"
    assert style == "Growth"
