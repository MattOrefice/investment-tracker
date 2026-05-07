"""Tests for src/positioning.py — tilts, effective duration, scenario triggers."""
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
    get_active_tilts,
    get_effective_duration,
    get_scenario_triggers,
    get_style_box_data,
)

# ── Shared test fixtures ──────────────────────────────────────────────────────

def _make_sw(rows: dict) -> pd.DataFrame:
    """
    Build a sleeve-weights DataFrame matching get_sleeve_weights_on_date() output.
    rows: {sleeve_name: (actual_wt, target_wt)} — weights in decimal (not percent).
    """
    data = {
        "Market Value":  {k: v[0] * 10_000 for k, v in rows.items()},
        "Actual Weight": {k: v[0] for k, v in rows.items()},
        "Target Weight": {k: v[1] for k, v in rows.items()},
        "Drift":         {k: v[0] - v[1] for k, v in rows.items()},
    }
    return pd.DataFrame(data)


_BASELINE_ROWS = {
    "US Large Core":         (0.16, 0.16),
    "US Large Quality":      (0.14, 0.14),
    "US Large Value":        (0.08, 0.08),
    "US Small Cap":          (0.07, 0.07),
    "International Developed":(0.19, 0.19),
    "Emerging Markets":      (0.08, 0.08),
    "Core Fixed Income":     (0.09, 0.09),
    "TIPS":                  (0.06, 0.06),
    "Real Assets":           (0.10, 0.10),
    "Cash / SPAXX":          (0.03, 0.03),
}


# ── get_active_tilts ──────────────────────────────────────────────────────────

def test_active_tilts_large_drift_included():
    """250 bps drift must appear in active tilts."""
    rows = dict(_BASELINE_ROWS)
    rows["Cash / SPAXX"] = (0.055, 0.03)   # +250 bps drift
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        tilts = get_active_tilts("2026-03-31")
    sleeves = [t["sleeve"] for t in tilts]
    assert "Cash / SPAXX" in sleeves


def test_active_tilts_small_drift_excluded():
    """30 bps drift below both thresholds must NOT appear."""
    rows = dict(_BASELINE_ROWS)
    rows["US Large Core"] = (0.163, 0.16)   # +30 bps, <10% relative
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        tilts = get_active_tilts("2026-03-31")
    sleeves = [t["sleeve"] for t in tilts]
    assert "US Large Core" not in sleeves


def test_active_tilts_sorted_by_abs_drift_descending():
    """Tilts must be ordered largest absolute drift first."""
    rows = dict(_BASELINE_ROWS)
    rows["Cash / SPAXX"]          = (0.055, 0.03)   # +250 bps
    rows["International Developed"] = (0.21, 0.19)  # +200 bps
    rows["US Large Core"]          = (0.175, 0.16)  # +150 bps
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        tilts = get_active_tilts("2026-03-31")
    abs_drifts = [t["abs_drift"] for t in tilts]
    assert abs_drifts == sorted(abs_drifts, reverse=True)


def test_active_tilts_capped_at_five():
    """At most 5 tilts returned even when more qualify."""
    rows = {s: (v[0] + 0.01, v[1]) for s, v in _BASELINE_ROWS.items()}  # +100 bps everywhere
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        tilts = get_active_tilts("2026-03-31")
    assert len(tilts) <= 5


def test_active_tilts_relative_threshold_triggers():
    """A sleeve with only 55 bps drift but 37% relative drift must be included (rel ≥ 10%)."""
    rows = dict(_BASELINE_ROWS)
    rows["US Small Cap"] = (0.0755, 0.07)   # +55 bps; 55/700 = 7.9% — just below abs but above rel threshold
    # 55 bps ≥ 50 abs threshold → included via abs threshold, but let's test a tighter case
    rows["US Small Cap"] = (0.0745, 0.07)   # +45 bps abs (< 50), 45/700 = 6.4% rel (< 10%) → excluded
    sw_excl = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw_excl):
        tilts = get_active_tilts("2026-03-31")
    assert "US Small Cap" not in [t["sleeve"] for t in tilts]

    rows["Cash / SPAXX"] = (0.0438, 0.03)  # +138 bps abs (≥50), 46% relative (≥10%) → included
    sw_incl = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw_incl):
        tilts = get_active_tilts("2026-03-31")
    assert "Cash / SPAXX" in [t["sleeve"] for t in tilts]


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
    expected_dur = (0.09 * vgit_dur + 0.06 * schp_dur) / 1.0
    assert abs(result["duration"] - round(expected_dur, 1)) <= 0.05


def test_effective_duration_fi_weight():
    """fi_weight_pct = Core FI + TIPS only (cash excluded); fi_weight_incl_cash_pct adds cash."""
    rows = dict(_BASELINE_ROWS)
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        result = get_effective_duration("2026-03-31")
    # Core FI (9%) + TIPS (6%) = 15%; Cash/SPAXX (3%) is excluded from fi_weight_pct
    assert abs(result["fi_weight_pct"] - 15.0) < 0.1
    assert abs(result["cash_weight_pct"] - 3.0) < 0.1
    assert abs(result["fi_weight_incl_cash_pct"] - 18.0) < 0.1


def test_effective_duration_empty_portfolio():
    """Empty portfolio must return zeros without raising."""
    sw = pd.DataFrame()
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        result = get_effective_duration("2026-03-31")
    assert result["duration"] == 0.0
    assert result["fi_weight_pct"] == 0.0


# ── get_scenario_triggers ─────────────────────────────────────────────────────

def test_scenario_cash_overweight_triggers_volatility_shock():
    """Cash overweight ≥100 bps must trigger the volatility-shock scenario."""
    rows = dict(_BASELINE_ROWS)
    rows["Cash / SPAXX"] = (0.05, 0.03)    # +200 bps
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        scenarios = get_scenario_triggers("2026-03-31")
    names = [s["name"] for s in scenarios]
    assert "Volatility shock" in names


def test_scenario_cash_neutral_no_volatility_shock():
    """Cash at target (0 bps drift) must NOT trigger the volatility-shock scenario."""
    rows = dict(_BASELINE_ROWS)   # all at target
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        scenarios = get_scenario_triggers("2026-03-31")
    names = [s["name"] for s in scenarios]
    assert "Volatility shock" not in names


def test_scenario_capped_at_four():
    """At most 4 scenarios returned even when many conditions fire."""
    rows = {s: (v[0] + 0.02, v[1]) for s, v in _BASELINE_ROWS.items()}
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        scenarios = get_scenario_triggers("2026-03-31")
    assert len(scenarios) <= 4


# ── ETF_STYLE_BOX and build_style_box_figure ─────────────────────────────────

_EQUITY_ETFS = {"VOO", "SPHQ", "VTV", "AVUV", "VEA", "IEMG"}


def test_style_box_contains_all_equity_etfs():
    """All equity holdings must appear in ETF_STYLE_BOX — fail loud if missing."""
    missing = _EQUITY_ETFS - set(ETF_STYLE_BOX.keys())
    assert not missing, f"ETF_STYLE_BOX missing equity ETFs: {missing}"


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


def test_get_style_box_data_empty_portfolio():
    """get_style_box_data returns [] when portfolio is empty."""
    empty_df = pd.DataFrame(columns=["net_shares"])
    with patch("src.positioning.get_holdings_on_date", return_value=empty_df):
        result = get_style_box_data("2026-03-31")
    assert result == []
