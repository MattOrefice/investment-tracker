"""Tests for report-level logic in src/reports.py."""
import sys
import pathlib
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import src.reports as rpt
from src.reports import _build_cumulative_chart, _build_holdings_chart, _drift_status
from src.returns import twr_daily_linked

# ── shared fixtures ──────────────────────────────────────────────────────────

_SLEEVES  = ["US Large Core", "International Developed", "Cash / SPAXX"]
_ACTUALS  = [16.0, 19.0, 3.0]
_TARGETS  = [16.0, 19.0, 3.0]

_DATES    = pd.date_range("2025-05-01", periods=60)
_PV_PCT   = pd.Series(np.linspace(0.0, 27.0, 60), index=_DATES)
_SP_PCT   = pd.Series(np.linspace(0.0, 30.0, 60), index=_DATES)
_BL_PCT   = pd.Series(np.linspace(0.0, 26.0, 60), index=_DATES)


# ── _drift_status boundary cases ────────────────────────────────────────────

def test_drift_status_both_under_thresholds():
    """190 bps drift on 1000 bps target (19% relative) → Within (both under)."""
    assert _drift_status(190, 1000) == "Within"


def test_drift_status_absolute_just_over():
    """210 bps drift on 1600 bps target (13.1% relative) → Drift (absolute threshold breached)."""
    assert _drift_status(210, 1600) == "Drift"


def test_drift_status_relative_just_over():
    """95 bps drift on 400 bps target (23.75% relative) → Drift (relative threshold breached)."""
    assert _drift_status(95, 400) == "Drift"


def test_drift_status_both_over():
    """250 bps drift on 300 bps target (83% relative) → Drift (both thresholds breached)."""
    assert _drift_status(250, 300) == "Drift"


def test_drift_status_negative_drift():
    """Negative drift uses abs() — -190 bps on 1000 bps target → Within."""
    assert _drift_status(-190, 1000) == "Within"


def test_drift_status_negative_drift_over():
    """-250 bps on 300 bps target → Drift (both breached)."""
    assert _drift_status(-250, 300) == "Drift"


def test_drift_status_cash_spaxx_scenario():
    """Cash/SPAXX: +291 bps drift, 300 bps target → Drift (absolute AND relative breached)."""
    assert _drift_status(291, 300) == "Drift"


def test_drift_status_us_large_core_scenario():
    """US Large Core: -58 bps drift, 1600 bps target (3.6% relative) → Within."""
    assert _drift_status(-58, 1600) == "Within"


def test_drift_status_zero_target_is_drift():
    """Zero target weight is treated as Drift to avoid division by zero."""
    assert _drift_status(10, 0) == "Drift"


def test_drift_status_exactly_at_absolute_boundary():
    """Exactly 200 bps drift is still Within (boundary is inclusive)."""
    assert _drift_status(200, 2000) == "Within"


def test_drift_status_exactly_at_relative_boundary():
    """Exactly 20% relative drift is still Within (boundary is inclusive)."""
    # 200 bps drift, 1000 bps target = 20.0% exactly
    assert _drift_status(200, 1000) == "Within"


# ── chart figure config tests ────────────────────────────────────────────────

def test_holdings_chart_has_sleeve_labels():
    """Holdings chart must carry tickvals + ticktext for every sleeve."""
    fig = _build_holdings_chart(_SLEEVES, _ACTUALS, _TARGETS)
    yaxis = fig.layout.yaxis
    assert yaxis.tickmode == "array"
    assert yaxis.tickvals is not None
    assert yaxis.ticktext is not None
    assert len(yaxis.tickvals) == len(_SLEEVES)
    for sleeve in _SLEEVES:
        assert sleeve in yaxis.tickvals


def test_cumulative_return_chart_has_y_tick_values():
    """Cumulative return chart must carry explicit tickvals with % ticktext."""
    fig = _build_cumulative_chart(_PV_PCT, _SP_PCT, _BL_PCT)
    yaxis = fig.layout.yaxis
    assert yaxis.tickvals is not None, "tickvals must be set (kaleido 0.2.1 ignores ticksuffix)"
    assert yaxis.ticktext is not None
    assert len(yaxis.tickvals) == len(yaxis.ticktext)
    assert all("%" in t for t in yaxis.ticktext)


def test_cumulative_return_chart_does_not_swallow_errors():
    """Empty input must raise, not silently return an empty chart."""
    with pytest.raises(Exception):
        _build_cumulative_chart(
            pd.Series(dtype=float),
            pd.Series(dtype=float),
            pd.Series(dtype=float),
        )


# ── _build_executive_summary cover TWR fix ───────────────────────────────────

def _flat_series(start: str, end: str, value: float = 1.0) -> pd.Series:
    return pd.Series(value, index=pd.date_range(start, end, freq="D"))


def test_cover_twr_uses_inception_slice_not_period_scoped():
    """
    portfolio_return_pct must equal twr_daily_linked on the inception series
    sliced to the report period — not a separate period-scoped call whose
    v_start can be $0 on New Year's Day.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(
        1000.0 + (idx - idx[0]).days.astype(float) * (300.0 / (len(idx) - 1)),
        index=idx,
    )
    pv_period    = pv_full[pv_full.index >= pd.Timestamp(start_date)]
    expected_pct = twr_daily_linked(
        pv_period, pd.Series(0.0, index=pv_period.index)
    ) * 100

    sp_flat = _flat_series(inception, end_date)
    bl_flat = _flat_series(inception, end_date)

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=sp_flat), \
         patch.object(rpt, "get_custom_blended_series", return_value=bl_flat), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):

        result = rpt._build_executive_summary(start_date, end_date)

    actual_pct = float(result["portfolio_return_pct"].rstrip("%"))
    assert abs(actual_pct - expected_pct) < 0.01, (
        f"Cover TWR {actual_pct:.4f}% should match period-sliced inception "
        f"TWR {expected_pct:.4f}%"
    )


def test_cover_current_value_reflects_full_portfolio():
    """current_value must equal the end-date portfolio value from the inception series."""
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1200.0, index=idx)

    sp_flat = _flat_series(inception, end_date)
    bl_flat = _flat_series(inception, end_date)

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=sp_flat), \
         patch.object(rpt, "get_custom_blended_series", return_value=bl_flat), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):

        result = rpt._build_executive_summary(start_date, end_date)

    assert result["current_value"] == "$1,200"


# ── Benchmark reconciliation: cover card vs performance table ─────────────────

def _make_exec_summary_mocks(inception: str, start_date: str, end_date: str,
                             pv_full: pd.Series, sp_full: pd.Series,
                             bl_full: pd.Series) -> dict:
    """Call _build_executive_summary with inception-scoped mocks.

    get_custom_blended_series uses side_effect so the mock slices bl_full to
    the requested start date, matching the production code path that calls
    get_custom_blended_series(start_date, end_date) and uses the full returned
    series for the period return.
    """
    def _blended_side_effect(start: str, end: str) -> pd.Series:
        return bl_full[bl_full.index >= pd.Timestamp(start)]

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=sp_full), \
         patch.object(rpt, "get_custom_blended_series", side_effect=_blended_side_effect), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):
        return rpt._build_executive_summary(start_date, end_date)


def test_cover_sp500_return_uses_inception_slice():
    """
    Cover card sp500_return_pct must equal the sliced-inception return, not a
    period-scoped return that back-fills New Year's Day from Jan 2 instead of
    forward-filling from Dec 31.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1000.0, index=idx)
    # SPY grows 0.05% per day from inception — Dec 31 and Jan 2 are distinct values
    sp_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0005, index=idx
    )
    bl_full = _flat_series(inception, end_date)

    # Expected: return computed from the inception series sliced to >= start_date
    sp_period     = sp_full[sp_full.index >= pd.Timestamp(start_date)]
    expected_sp   = float(sp_period.iloc[-1] / sp_period.iloc[0] - 1) * 100

    result     = _make_exec_summary_mocks(inception, start_date, end_date,
                                          pv_full, sp_full, bl_full)
    actual_sp  = float(result["sp500_return_pct"].rstrip("%"))

    assert abs(actual_sp - expected_sp) < 0.01, (
        f"Cover SP500 {actual_sp:.4f}% should match inception-sliced "
        f"{expected_sp:.4f}% (gap >{abs(actual_sp - expected_sp):.4f} pp)"
    )


def test_cover_blended_return_uses_period_start():
    """Cover card blended_return_pct must use period-start blended return (fresh start_date, not inception-sliced)."""
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1000.0, index=idx)
    sp_full = _flat_series(inception, end_date)
    bl_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0003, index=idx
    )

    bl_period   = bl_full[bl_full.index >= pd.Timestamp(start_date)]
    expected_bl = float(bl_period.iloc[-1] / bl_period.iloc[0] - 1) * 100

    result     = _make_exec_summary_mocks(inception, start_date, end_date,
                                          pv_full, sp_full, bl_full)
    actual_bl  = float(result["blended_return_pct"].rstrip("%"))

    assert abs(actual_bl - expected_bl) < 0.01, (
        f"Cover blended {actual_bl:.4f}% should match inception-sliced "
        f"{expected_bl:.4f}%"
    )


def test_cover_alpha_bps_are_arithmetic_of_cover_returns():
    """
    alpha_sp_str and alpha_bl_str must be arithmetically self-consistent
    with the cover's own portfolio, SP500, and blended return figures —
    not derived from a different benchmark series.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(
        1000.0 + (idx - idx[0]).days.astype(float) * 0.3, index=idx
    )
    sp_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0005, index=idx
    )
    bl_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0003, index=idx
    )

    result = _make_exec_summary_mocks(inception, start_date, end_date,
                                      pv_full, sp_full, bl_full)

    port_pct = float(result["portfolio_return_pct"].rstrip("%"))
    sp_pct   = float(result["sp500_return_pct"].rstrip("%"))
    bl_pct   = float(result["blended_return_pct"].rstrip("%"))
    alpha_sp = float(result["alpha_sp_str"].rstrip(" bps").replace("+", ""))
    alpha_bl = float(result["alpha_bl_str"].rstrip(" bps").replace("+", ""))

    # Tolerance of 2 bps covers the ±0.01pp rounding in the display strings
    assert abs(alpha_sp - (port_pct - sp_pct) * 100) < 2.0, (
        "alpha_sp_str is not arithmetic of cover portfolio and SP500 returns"
    )
    assert abs(alpha_bl - (port_pct - bl_pct) * 100) < 2.0, (
        "alpha_bl_str is not arithmetic of cover portfolio and blended returns"
    )
