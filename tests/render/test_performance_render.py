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


def test_risk_metrics_radio_has_five_options(performance_app: AppTest) -> None:
    """Risk-Adjusted Metrics window radio must offer exactly five period options. Pinned: Phase 8u.

    Options must match the BF Attribution period set in order:
    1 Month, 3 Months, YTD, 1 Year, Since Inception.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    risk_radios = [r for r in performance_app.radio if r.key == "risk_metrics_window"]
    assert risk_radios, "Risk-Adjusted Metrics window radio (key='risk_metrics_window') not found"
    opts = risk_radios[0].options
    assert len(opts) == 5, f"Expected 5 window options, got {len(opts)}: {opts}"
    assert list(opts) == ["1 Month", "3 Months", "YTD", "1 Year", "Since Inception"], (
        f"Window options order or labels incorrect: {list(opts)}"
    )


def test_risk_metrics_1m_window_no_nan() -> None:
    """Switching risk metrics to 1 Month window must produce no NaN metric values. Pinned: Phase 8u.

    Uses a fresh AppTest instance (not the module fixture) to avoid contaminating
    shared widget state. Sets the risk_metrics_window radio to '1 Month' and
    re-runs the page, then asserts all rendered metric values are finite.
    """
    at = AppTest.from_file("pages/4_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    risk_radios = [r for r in at.radio if r.key == "risk_metrics_window"]
    if not risk_radios:
        pytest.skip("Risk metrics radio not found")
    risk_radios[0].set_value("1 Month")
    at.run()
    assert not at.exception, f"Page raised after 1M radio switch: {at.exception}"
    nan_metrics = [m.value for m in at.metric if "nan" in str(m.value).lower()]
    assert not nan_metrics, f"1M window rendered NaN metrics: {nan_metrics}"


def test_risk_metrics_1m_sharpe_differs_from_si() -> None:
    """Sharpe at 1M window must differ from Sharpe at SI by ≥ 0.01. Pinned: Phase 8u.1.

    Window-distinguishing integration pin. If window filtering collapses (the
    view/copy bug recurs), all windows return SI values and Sharpe values are
    identical. The 1M portfolio Sharpe is substantially higher than SI Sharpe
    for the current demo portfolio (recent month outperformed the full inception
    period), so any identity in displayed values is a clear signal of collapse.

    Fix if failing: restore .copy() after each cutoff slice in compute_risk_metrics.
    """
    at = AppTest.from_file("pages/4_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")

    # Capture SI Sharpe (default window = Since Inception, radio index=4)
    sharpe_si_widgets = [m for m in at.metric if m.label == "Sharpe"]
    if not sharpe_si_widgets or sharpe_si_widgets[0].value == "—":
        pytest.skip("Sharpe metric unavailable in default (SI) window")
    sharpe_si = float(sharpe_si_widgets[0].value)

    # Switch to 1 Month and re-run
    risk_radios = [r for r in at.radio if r.key == "risk_metrics_window"]
    if not risk_radios:
        pytest.skip("risk_metrics_window radio not found")
    risk_radios[0].set_value("1 Month")
    at.run()
    assert not at.exception, f"Page raised after 1M radio switch: {at.exception}"

    sharpe_1m_widgets = [m for m in at.metric if m.label == "Sharpe"]
    if not sharpe_1m_widgets or sharpe_1m_widgets[0].value == "—":
        pytest.skip("Sharpe metric unavailable in 1M window")
    sharpe_1m = float(sharpe_1m_widgets[0].value)

    assert abs(sharpe_si - sharpe_1m) >= 0.01, (
        f"SI Sharpe ({sharpe_si}) and 1M Sharpe ({sharpe_1m}) are indistinguishable. "
        f"Window filter likely collapsed — all windows returning SI-length series. "
        f"Fix: restore .copy() after cutoff slice in src/performance.py."
    )


def test_reconciliation_no_latex_artifacts(performance_app: AppTest) -> None:
    """Reconciliation paragraph must escape dollar signs to prevent LaTeX rendering.

    Regression pin: Phase 8t. Phase 8s introduced **$...**  bold syntax in the
    reconciliation st.caption. Unescaped $ chars triggered Streamlit's LaTeX
    extension, rendering bold dollar amounts as math-mode gibberish (spaced-out
    characters, ∗∗ instead of bold, → rendered as math arrow). Fix: escape every
    $ in the f-string template as \\$. The raw caption value passed to st.caption
    must contain \\$ — if it contains a bare $N pattern (dollar + digit inside a
    bold span), LaTeX will corrupt the output on next render.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    recon = next((c for c in captions if "Reconciliation" in c), None)
    assert recon is not None, "Reconciliation paragraph not found in page captions"
    assert "\\$" in recon, (
        f"Reconciliation caption lacks escaped dollar signs (\\$). "
        f"Unescaped $ will trigger Streamlit LaTeX mode and corrupt bold amounts. "
        f"Caption starts: {recon[:120]}"
    )


def test_two_stage_attribution_section_renders(performance_app: AppTest) -> None:
    """Two-Stage Attribution section must render with three metric tiles. Pinned: Phase 9.

    Verifies that the new Stage 1 / Stage 2 / Total tiles are present and that
    switching the BF window radio updates the tile values (wired to same radio).
    Skipped when no portfolio data exists (local empty-DB mode).
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")

    metric_labels = [m.label for m in performance_app.metric]
    assert "Stage 1: SAA Design" in metric_labels, (
        f"'Stage 1: SAA Design' tile not found in metric labels: {metric_labels}"
    )
    assert "Stage 2: Implementation" in metric_labels, (
        f"'Stage 2: Implementation' tile not found in metric labels: {metric_labels}"
    )
    assert "Total: Portfolio vs. 60/40" in metric_labels, (
        f"'Total: Portfolio vs. 60/40' tile not found in metric labels: {metric_labels}"
    )

    # Switching the BF period radio must change Stage 1 value (window-sensitivity check)
    at = AppTest.from_file("pages/4_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data in fresh AppTest instance")

    def _get_stage1(app) -> str | None:
        hits = [m for m in app.metric if m.label == "Stage 1: SAA Design"]
        return hits[0].value if hits else None

    bf_radios = [r for r in at.radio if r.key == "bf_period"]
    if not bf_radios:
        pytest.skip("bf_period radio not found")

    stage1_3m = _get_stage1(at)    # default is 3M

    bf_radios[0].set_value("SI")
    at.run()
    assert not at.exception, f"Page raised after bf_period switch to SI: {at.exception}"
    stage1_si = _get_stage1(at)

    assert stage1_3m is not None and stage1_si is not None, (
        "Stage 1 tile missing after radio switch"
    )
    assert stage1_3m != stage1_si, (
        f"Stage 1 value unchanged after switching 3M → SI: both = {stage1_3m!r}. "
        "Window filtering may have collapsed — check _load_attribution or _benchmark_period_return."
    )
