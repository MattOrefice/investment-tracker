"""Render-aware tests for the Benchmark Attribution page.

These tests assert on rendered widget content using streamlit.testing.v1.AppTest.
Regression pins document which Phase introduced each invariant so that future
regressions surface a clear attribution in the failure message.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def ba_app() -> AppTest:
    """Run the Benchmark Attribution page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/6_Benchmark_Attribution.py", default_timeout=120)
    at.run()
    return at


def test_benchmark_attribution_runs_without_exception(ba_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not ba_app.exception, f"Benchmark Attribution page raised: {ba_app.exception}"


def test_subhead_no_ascii_formula(ba_app: AppTest) -> None:
    """Subhead caption must not contain the ASCII regression formula. Pinned: Phase 40."""
    captions = [c.value for c in ba_app.caption]
    assert not any("R_p" in c and "HML + SMB + RMW" in c for c in captions), (
        "ASCII regression formula found in page subhead — Phase 40 regression (Item 1). "
        "Formula should only appear inside the 'How to read this page' expander."
    )


def test_cross_page_window_disclosure_present(ba_app: AppTest) -> None:
    """Interpretation section must include the since-inception vs Q1 window disclosure. Pinned: Phase 40."""
    if not ba_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    all_text = " ".join(str(w.value) for w in ba_app.markdown)
    assert "since-inception window" in all_text.lower() or "since inception" in all_text.lower(), (
        "Cross-page window disclosure not found — Phase 40 regression (Item 3). "
        "The Interpretation section must note that BF on Performance page uses a different window."
    )


def test_alpha_ci_in_interpretation_prose(ba_app: AppTest) -> None:
    """Interpretation prose must include '95% CI' for the alpha estimate. Pinned: Phase 40."""
    if not ba_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    all_text = " ".join(str(w.value) for w in ba_app.markdown)
    assert "95% CI" in all_text, (
        "Alpha 95% confidence interval not found in Interpretation prose — "
        "Phase 40 regression (Item 4)."
    )


def test_regression_window_lag_caption_present(ba_app: AppTest) -> None:
    """A caption must explain the regression window ends at the FF publication lag,
    NOT a locked quarter-end. Pinned: Phase 40; corrected 2026-07 (the window is
    actually bounded by the last date with published Fama-French factor data — an
    inner join in run_benchmark_attribution_regression against date.today(), not
    any quarter-end lock).
    """
    if not ba_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in ba_app.caption]
    assert any("publication lag" in c.lower() for c in captions), (
        "Regression window publication-lag caption not found — this caption must "
        "describe the actual mechanism (FF data publication lag), not a fictitious "
        "quarter-end lock."
    )
    assert not any("locked quarter-end" in c.lower() or "quarter lock" in c.lower() or "quarter-end lock" in c.lower()
                   for c in captions), (
        "Caption falsely claims a 'locked quarter-end' — the regression is called "
        "with end_date=date.today() (see src/factors.py); there is no quarter lock."
    )


def test_methodology_expander_present(ba_app: AppTest) -> None:
    """Methodology & Disclosure expander must render. Pinned: Phase 40."""
    if not ba_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    expander_labels = [e.label for e in ba_app.expander]
    assert any("methodology" in lbl.lower() or "disclosure" in lbl.lower()
               for lbl in expander_labels), (
        f"Methodology expander not found in: {expander_labels}"
    )


def test_factor_definitions_always_visible(ba_app: AppTest) -> None:
    """Factor Definitions table must render in the always-visible bordered container. Pinned: Phase 40.1."""
    if not ba_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    all_markdown = [m.value for m in ba_app.markdown]
    assert any("Factor Definitions" in m for m in all_markdown), (
        "Factor Definitions heading not found in rendered markdown — "
        "Phase 40.1 regression: bordered container must be always visible, not in an expander."
    )
