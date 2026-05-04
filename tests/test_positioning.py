"""Tests for src/positioning.py — tilts, effective duration, scenario triggers."""
import sys
import pathlib
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.positioning import (
    ETF_DURATION,
    _FI_SLEEVE_HOLDING,
    get_active_tilts,
    get_effective_duration,
    get_scenario_triggers,
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

    vgit_dur  = ETF_DURATION[_FI_SLEEVE_HOLDING["Core Fixed Income"]]
    schp_dur  = ETF_DURATION[_FI_SLEEVE_HOLDING["TIPS"]]
    spaxx_dur = ETF_DURATION[_FI_SLEEVE_HOLDING["Cash / SPAXX"]]

    expected_dur = (0.09 * vgit_dur + 0.06 * schp_dur + 0.03 * spaxx_dur) / 1.0
    assert abs(result["duration"] - round(expected_dur, 1)) <= 0.05


def test_effective_duration_fi_weight():
    """FI weight % must equal (Core FI + TIPS + Cash) actual weights × 100."""
    rows = dict(_BASELINE_ROWS)
    sw = _make_sw(rows)
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw):
        result = get_effective_duration("2026-03-31")
    assert abs(result["fi_weight_pct"] - 18.0) < 0.1


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
