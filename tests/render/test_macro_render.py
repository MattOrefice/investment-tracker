"""Render-aware tests for the Macro Dashboard.

These tests assert on rendered widget content using streamlit.testing.v1.AppTest.
They complement the math tests in tests/ — these confirm each panel actually
renders with the expected text and structure on first paint.

Regression pins document which Phase introduced each panel so that future
regressions surface a clear attribution in the failure message.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def macro_app() -> AppTest:
    """Run the Macro page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/5_Macro.py", default_timeout=60)
    at.run()
    return at


def test_macro_runs_without_exception(macro_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not macro_app.exception, f"Macro page raised: {macro_app.exception}"


def test_cape_panel_renders(macro_app: AppTest) -> None:
    """CAPE panel renders with heading. Pinned: always present."""
    headings = [m.value for m in macro_app.markdown]
    assert any("CAPE" in h for h in headings), (
        "CAPE panel heading not found in rendered markdown"
    )


def test_ecy_panel_renders(macro_app: AppTest) -> None:
    """ECY panel renders between CAPE and yield curve. Pinned: Phase 8k."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Excess CAPE Yield" in h or "ECY" in h for h in headings), (
        "ECY panel heading not found — possible Phase 8k regression"
    )


def test_yield_curve_panel_renders(macro_app: AppTest) -> None:
    """2/10 yield curve spread panel renders. Pinned: Phase 5."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Yield Curve" in h or "2/10" in h for h in headings), (
        "Yield Curve panel heading not found"
    )


def test_fed_funds_panel_renders(macro_app: AppTest) -> None:
    """Federal Funds Rate panel renders. Pinned: Phase 5."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Federal Funds" in h or "Fed Funds" in h for h in headings), (
        "Fed Funds panel heading not found"
    )


def test_hy_oas_panel_renders(macro_app: AppTest) -> None:
    """HY credit spreads panel renders. Pinned: Phase 5."""
    headings = [m.value for m in macro_app.markdown]
    assert any("HY" in h and ("OAS" in h or "Credit" in h) for h in headings), (
        "HY OAS panel heading not found"
    )


def test_force_refresh_button_present(macro_app: AppTest) -> None:
    """Force refresh button must render. Pinned: Phase 8l Section 3.4."""
    button_labels = [b.label for b in macro_app.button]
    assert any("refresh" in label.lower() for label in button_labels), (
        "Force refresh button missing — possible Phase 8l regression"
    )


def test_data_freshness_disclosure_present(macro_app: AppTest) -> None:
    """Data-source freshness expander must render. Pinned: Phase 5."""
    expander_labels = [e.label for e in macro_app.expander]
    assert any("freshness" in lbl.lower() or "data source" in lbl.lower()
               for lbl in expander_labels), (
        f"Data-source freshness disclosure not found in expanders: {expander_labels}"
    )


def test_build_caption_renders(macro_app: AppTest) -> None:
    """Section 1 deliverable: footer must include deployed build SHA."""
    captions = [c.value for c in macro_app.caption]
    assert any("Build" in c for c in captions), (
        f"Deployed SHA caption not rendered — captions found: {captions[:5]}"
    )
