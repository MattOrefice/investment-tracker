"""Tests for src/positioning.py — effective duration. (The style box tests went with the
style box, #468.)"""
import sys
import pathlib
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.positioning import (
    _FI_SLEEVE_HOLDING,
    get_effective_duration,
    live_fund_durations,
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

    vgit_dur = live_fund_durations()[_FI_SLEEVE_HOLDING["Core Fixed Income"]]
    schp_dur = live_fund_durations()[_FI_SLEEVE_HOLDING["TIPS"]]

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
    """A held FI-sleeve holding with no duration in the ETF metadata must RAISE — a
    silent 0 would understate the FI sleeve duration with nothing visibly wrong."""
    sw = _make_sw(dict(_BASELINE_ROWS))
    # Core FI's holding (VGIT) loses its duration entry.
    patched = {k: v for k, v in live_fund_durations().items()
               if k != _FI_SLEEVE_HOLDING["Core Fixed Income"]}
    with patch("src.positioning.get_sleeve_weights_on_date", return_value=sw), \
         patch("src.positioning.fund_durations", return_value=patched):
        with pytest.raises(ValueError, match="has no duration in data/etf_metadata.json"):
            get_effective_duration("2026-03-31")
