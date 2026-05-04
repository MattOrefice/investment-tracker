"""Tests for report-level logic in src/reports.py."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.reports import _drift_status


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
