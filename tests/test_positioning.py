"""Tests for src/positioning.py — effective duration and style box."""
import sys
import pathlib
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.positioning import (
    ETF_DURATION,
    ETF_STYLE_BOX,
    _FI_SLEEVE_HOLDING,
    build_style_box_figure,
    get_effective_duration,
    get_style_box_data,
)

# ── Shared test fixtures ──────────────────────────────────────────────────────

def _make_sw(rows: dict, cash_weight_of_total: float = 0.02) -> pd.DataFrame:
    """
    Build a sleeve-weights DataFrame matching get_sleeve_weights_on_date() output.
    rows: {sleeve_name: (actual_wt, target_wt)} — weights in decimal (not percent).

    Phase 38a — rows are the ex-cash strategic sleeves (weights of INVESTED value,
    summing to ~1.0); operational cash is carried on .attrs, never as a row.
    """
    invested = sum(v[0] * 10_000 for v in rows.values())
    total    = invested / (1.0 - cash_weight_of_total) if cash_weight_of_total < 1 else invested
    cash_mv  = total - invested
    data = {
        "Market Value":  {k: v[0] * 10_000 for k, v in rows.items()},
        "Actual Weight": {k: v[0] for k, v in rows.items()},
        "Target Weight": {k: v[1] for k, v in rows.items()},
        "Drift":         {k: v[0] - v[1] for k, v in rows.items()},
    }
    df = pd.DataFrame(data)
    df.attrs["total_value"]          = round(total, 2)
    df.attrs["cash_mv"]              = round(cash_mv, 2)
    df.attrs["invested_value"]       = round(invested, 2)
    df.attrs["cash_weight_of_total"] = round(cash_weight_of_total, 6)
    return df


# Ex-cash strategic sleeves (sum to 1.0); Core FI 6% + TIPS 4% = 10% of invested.
_BASELINE_ROWS = {
    "US Large Core":         (0.175, 0.175),
    "US Large Quality":      (0.155, 0.155),
    "US Large Value":        (0.09, 0.09),
    "US Small Cap":          (0.08, 0.08),
    "International Developed":(0.205, 0.205),
    "Emerging Markets":      (0.095, 0.095),
    "Core Fixed Income":     (0.06, 0.06),
    "TIPS":                  (0.04, 0.04),
    "Real Assets":           (0.10, 0.10),
}


# ── get_effective_duration ────────────────────────────────────────────────────

def test_effective_duration_known_weights():
    """Weighted duration must equal sum(actual_wt × etf_dur) / total_wt."""
    rows = dict(_BASELINE_ROWS)
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        result = get_effective_duration("2026-03-31")

    vgit_dur = ETF_DURATION[_FI_SLEEVE_HOLDING["Core Fixed Income"]]
    schp_dur = ETF_DURATION[_FI_SLEEVE_HOLDING["TIPS"]]

    # Cash/SPAXX is excluded from duration; portfolio-level duration = weighted FI only
    expected_dur = (0.06 * vgit_dur + 0.04 * schp_dur) / 1.0
    assert abs(result["duration"] - round(expected_dur, 1)) <= 0.05


def test_effective_duration_fi_weight():
    """fi_weight_pct = Core FI + TIPS only (cash excluded); fi_weight_incl_cash_pct adds cash."""
    rows = dict(_BASELINE_ROWS)
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        result = get_effective_duration("2026-03-31")
    # Core FI (6%) + TIPS (4%) = 10%; Cash/SPAXX (2%) is excluded from fi_weight_pct
    assert abs(result["fi_weight_pct"] - 10.0) < 0.1
    assert abs(result["cash_weight_pct"] - 2.0) < 0.1
    assert abs(result["fi_weight_incl_cash_pct"] - 12.0) < 0.1


def test_effective_duration_empty_portfolio():
    """Empty portfolio must return zeros without raising."""
    sw = pd.DataFrame()
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        result = get_effective_duration("2026-03-31")
    assert result["duration"] == 0.0
    assert result["fi_weight_pct"] == 0.0


def test_effective_duration_raises_on_unmapped_fi_holding():
    """A held FI-sleeve holding with no ETF_DURATION entry must RAISE — a silent 0
    would understate the FI sleeve duration with nothing visibly wrong."""
    sw = _make_sw(dict(_BASELINE_ROWS))
    # Core FI's holding (VGIT) loses its duration entry.
    patched = {k: v for k, v in ETF_DURATION.items() if k != _FI_SLEEVE_HOLDING["Core Fixed Income"]}
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw), \
         patch("src.positioning.ETF_DURATION", patched):
        with pytest.raises(ValueError, match="ETF_DURATION"):
            get_effective_duration("2026-03-31")


# ── ETF_STYLE_BOX and build_style_box_figure ─────────────────────────────────

def _saa_equity_tickers() -> set:
    """SAA equity tickers, DERIVED: the committed household CSV (is_in_saa=true)
    intersected with the DB's Equity-parent sleeves.

    Previously a hand-maintained literal set. A hand-listed set cannot fail on a
    ticker it omits — adopt a new equity SAA ticker and ETF_STYLE_BOX simply never
    gets checked for it, so the style box silently loses a holding. Both sides are
    read live: the CSV supplies is_in_saa, the DB supplies which sleeves hang off
    the Equity parent.

    Equity membership is resolved by ASSET_CLASS_ID — the same join
    compute_household_allocation uses (household.py:155) — not by display-name
    matching. The display names do not round-trip: sleeve_category
    'us_small_value' maps to the SAA sleeve 'US Small Cap', so a name-based join
    silently drops AVUV and the guard would be weaker than the literal it replaced.
    """
    import csv
    import pathlib

    from src.db import get_connection

    with get_connection() as conn:
        equity_ids = {
            r["asset_class_id"]
            for r in conn.execute(
                "SELECT c.asset_class_id FROM asset_classes c "
                "JOIN asset_classes p ON c.parent_id = p.asset_class_id "
                "WHERE p.name = 'Equity' AND c.target_weight > 0"
            ).fetchall()
        }
        ticker_to_class = {
            r["ticker"]: r["asset_class_id"]
            for r in conn.execute("SELECT ticker, asset_class_id FROM securities").fetchall()
        }

    csv_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "data" / "seed" / "securities_household.csv"
    )
    with csv_path.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["is_in_saa"].strip().lower() == "true"]

    return {
        r["symbol"].strip()
        for r in rows
        if ticker_to_class.get(r["symbol"].strip()) in equity_ids
    }


def test_style_box_contains_all_equity_etfs():
    """All SAA equity holdings must appear in ETF_STYLE_BOX — fail loud if missing."""
    equity_etfs = _saa_equity_tickers()
    assert equity_etfs, (
        "Derived no SAA equity tickers — the CSV/DB join broke, which would make "
        "this test vacuously pass."
    )
    missing = equity_etfs - set(ETF_STYLE_BOX.keys())
    assert not missing, (
        f"ETF_STYLE_BOX missing equity ETFs: {sorted(missing)}. Derived from "
        f"data/seed/securities_household.csv x the DB's Equity sleeves, so a newly-"
        f"adopted equity ticker is caught here rather than silently unlisted."
    )


def test_style_box_valid_categories():
    """Every ETF_STYLE_BOX entry must use valid Morningstar size and style values."""
    valid_sizes  = {"Large", "Mid", "Small"}
    valid_styles = {"Value", "Blend", "Growth"}
    for ticker, (size, style) in ETF_STYLE_BOX.items():
        assert size  in valid_sizes,  f"{ticker}: invalid size '{size}'"
        assert style in valid_styles, f"{ticker}: invalid style '{style}'"


def test_build_style_box_figure_returns_figure():
    """build_style_box_figure returns a Plotly Figure for valid data."""
    import plotly.graph_objects as go
    data = [
        {"ticker": "VOO",  "size": "Large", "style": "Blend", "weight_pct": 16.0},
        {"ticker": "AVUV", "size": "Small", "style": "Value", "weight_pct":  7.0},
        {"ticker": "VTV",  "size": "Large", "style": "Value", "weight_pct":  8.0},
    ]
    fig = build_style_box_figure(data)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1
    assert fig.data[0].x is not None


def test_build_style_box_figure_empty():
    """build_style_box_figure returns a Figure (no traces) for empty input."""
    import plotly.graph_objects as go
    fig = build_style_box_figure([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0


def test_build_style_box_figure_dot_sizes_scaled():
    """Largest-weight ETF gets the largest dot; smallest-weight gets the smallest."""
    data = [
        {"ticker": "VOO",  "size": "Large", "style": "Blend", "weight_pct": 16.0},
        {"ticker": "AVUV", "size": "Small", "style": "Value", "weight_pct":  7.0},
    ]
    fig = build_style_box_figure(data)
    sizes = list(fig.data[0].marker.size)
    voo_idx  = list(fig.data[0].text).index("VOO")
    avuv_idx = list(fig.data[0].text).index("AVUV")
    assert sizes[voo_idx] > sizes[avuv_idx], "Larger weight should produce larger dot"


def test_build_style_box_figure_crowded_cell_outward_textpositions():
    """4-dot cell must use all four outward corner textpositions, one per dot, with dark text."""
    data = [
        {"ticker": "VOO",  "size": "Large", "style": "Blend", "weight_pct": 16.0},
        {"ticker": "SPHQ", "size": "Large", "style": "Blend", "weight_pct": 14.0},
        {"ticker": "VEA",  "size": "Large", "style": "Blend", "weight_pct": 19.0},
        {"ticker": "IEMG", "size": "Large", "style": "Blend", "weight_pct":  8.0},
        {"ticker": "VTV",  "size": "Large", "style": "Value", "weight_pct":  8.0},
        {"ticker": "AVUV", "size": "Small", "style": "Value", "weight_pct":  7.0},
    ]
    fig = build_style_box_figure(data)
    outward = {"top left", "top right", "bottom left", "bottom right"}
    crowded_tickers = {"VOO", "SPHQ", "VEA", "IEMG"}

    collected_pos = []
    for trace in fig.data:
        tickers = list(trace.text)
        pos = trace.textposition
        for i, ticker in enumerate(tickers):
            if ticker in crowded_tickers:
                val = pos[i] if isinstance(pos, (list, tuple)) else pos
                collected_pos.append(val)
                assert trace.textfont.color != "white", (
                    f"{ticker}: crowded trace must use dark text (labels outside dot)"
                )

    assert len(collected_pos) == 4, f"Expected 4 crowded textpositions, got {len(collected_pos)}"
    assert set(collected_pos) == outward, (
        f"Crowded textpositions must be {outward}, got {set(collected_pos)}"
    )


def test_build_style_box_figure_4dot_cell_distinct_coordinates():
    """Four ETFs in the same cell must produce 4 distinct (x,y) points, all within the cell."""
    data = [
        {"ticker": "VOO",  "size": "Large", "style": "Blend", "weight_pct": 16.0},
        {"ticker": "SPHQ", "size": "Large", "style": "Blend", "weight_pct": 14.0},
        {"ticker": "VEA",  "size": "Large", "style": "Blend", "weight_pct": 19.0},
        {"ticker": "IEMG", "size": "Large", "style": "Blend", "weight_pct":  8.0},
        {"ticker": "VTV",  "size": "Large", "style": "Value", "weight_pct":  8.0},
        {"ticker": "AVUV", "size": "Small", "style": "Value", "weight_pct":  7.0},
    ]
    fig = build_style_box_figure(data)

    all_x, all_y = [], []
    for trace in fig.data:
        all_x.extend(list(trace.x))
        all_y.extend(list(trace.y))

    # Large/Blend center: x=1, y=2 — all 4 dots must be within the cell boundary
    lb_points = [(round(x, 6), round(y, 6))
                 for x, y in zip(all_x, all_y)
                 if abs(x - 1) < 0.5 and abs(y - 2) < 0.5]
    assert len(lb_points) == 4, f"Expected 4 dots in Large/Blend, got {len(lb_points)}"
    assert len(set(lb_points)) == 4, "All 4 Large/Blend dots must have distinct (x, y) coordinates"

    for x, y in lb_points:
        assert 0.5 <= x <= 1.5, f"x={x} out of Large/Blend cell x-range [0.5, 1.5]"
        assert 1.5 <= y <= 2.5, f"y={y} out of Large/Blend cell y-range [1.5, 2.5]"


def test_get_style_box_data_empty_portfolio(no_ambient_db):
    """get_style_box_data returns [] when portfolio is empty."""
    empty_df = pd.DataFrame(columns=["net_shares"])
    with patch("src.positioning.get_holdings_on_date", return_value=empty_df), \
         patch("src.positioning.get_portfolio_account_id", return_value=1):
        result = get_style_box_data("2026-03-31")
    assert result == []
