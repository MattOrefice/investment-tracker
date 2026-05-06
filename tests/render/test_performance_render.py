"""Render-aware tests for the Performance & Attribution page.

These tests assert on rendered widget content using streamlit.testing.v1.AppTest.
They confirm the page renders without NaN metrics, which was the observable
symptom of the Phase 8p duplicate-price-index bug ($30 current value, 0% TWR).
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def performance_app() -> AppTest:
    """Run the Performance page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/4_Performance.py", default_timeout=120)
    at.run()
    return at


def test_performance_runs_without_exception(performance_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not performance_app.exception, (
        f"Performance page raised: {performance_app.exception}"
    )


def test_performance_metrics_are_finite(performance_app: AppTest) -> None:
    """No metric on the Performance page may render 'nan'.

    Regression pin: Phase 8p. Root cause — duplicate price-date index in
    get_prices() caused reindex() to raise 'cannot reindex on an axis with
    duplicate labels'. The exception was silently caught, leaving every ETF
    with a NaN price series. SPAXX's $1 fallback produced a flat $30 series,
    giving 0% TWR and nan bps vs. benchmarks. The dedup guard in get_prices
    prevents this. If this test fails, check src/prices.py get_prices dedup.
    """
    metric_values = [m.value for m in performance_app.metric]
    nan_metrics = [v for v in metric_values if "nan" in str(v).lower()]
    assert not nan_metrics, (
        f"Performance page rendered NaN metrics: {nan_metrics}. "
        f"Phase 8p regression — paper-trade portfolio loader produced degenerate output."
    )


def test_performance_has_headline_metrics(performance_app: AppTest) -> None:
    """Performance page renders at least 4 metric widgets (the headline row). Pinned: Phase 4.

    Skipped when running against an empty portfolio (tracker.db with no trades) because
    the page hits the empty-state guard (st.stop()) before rendering metrics. Runs on
    Cloud against demo.db which has a full paper-trade portfolio.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    assert len(performance_app.metric) >= 4, (
        f"Expected at least 4 metric widgets, got {len(performance_app.metric)}"
    )


def test_period_returns_table_renders(performance_app: AppTest) -> None:
    """Period returns section renders a dataframe widget. Pinned: Phase 4."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    assert len(performance_app.dataframe) >= 1, (
        "No dataframe rendered — Period Returns table missing"
    )


def test_generate_report_expander_present(performance_app: AppTest) -> None:
    """Generate Quarterly Report expander must render (appears before empty-state guard). Pinned: Phase 6."""
    expander_labels = [e.label for e in performance_app.expander]
    assert any("report" in lbl.lower() for lbl in expander_labels), (
        f"Quarterly Report expander not found in: {expander_labels}"
    )


def test_methodology_expander_present(performance_app: AppTest) -> None:
    """Methodology validation expander must render. Pinned: Phase 4."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    expander_labels = [e.label for e in performance_app.expander]
    assert any("methodology" in lbl.lower() or "validation" in lbl.lower()
               for lbl in expander_labels), (
        f"Methodology expander not found in: {expander_labels}"
    )


def test_build_caption_renders(performance_app: AppTest) -> None:
    """Footer must include deployed build SHA. Pinned: Phase 8o."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    assert any("Build" in c for c in captions), (
        f"Deployed SHA caption not rendered — captions found: {captions[:5]}"
    )
