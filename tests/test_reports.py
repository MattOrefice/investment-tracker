"""Tests for report-level logic in src/reports.py."""
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.reports import _build_cumulative_chart, _build_holdings_chart, _drift_status

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
