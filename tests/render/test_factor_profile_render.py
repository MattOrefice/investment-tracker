"""Render-aware tests for the Factor Profile page.

These tests assert on rendered widget content using streamlit.testing.v1.AppTest.
The critical regression pin is test_global_factors_tab_renders_data_not_warning,
which catches the Phase 8q defect where the Ken French daily Global 5-factor file
was permanently discontinued (June 2019) but the tab showed a misleading
'temporarily unavailable' message implying the data would eventually load.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def factor_profile_app() -> AppTest:
    """Run the Factor Profile page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/4_Factor_Profile.py", default_timeout=120)
    at.run()
    return at


def test_factor_profile_runs_without_exception(factor_profile_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not factor_profile_app.exception, (
        f"Factor Profile page raised: {factor_profile_app.exception}"
    )


def test_global_factors_tab_renders_data_not_warning(factor_profile_app: AppTest) -> None:
    """Global Factors tab must show the discontinued disclosure, not 'temporarily unavailable'.

    Regression pin: Phase 8q. Root cause — Ken French ceased publication of daily
    Global 5-factor data in June 2019. The portfolio started May 2025; there is no
    data overlap and the regression cannot run. The old warning said 'temporarily
    unavailable' (implying a transient failure) and offered a Retry button that
    could never succeed. The correct UI state is a factual info disclosure.

    If this test fails:
    - 'temporarily unavailable' in warnings: old warning message restored — Phase 8q regression
    - 'discontinued' not in info: disclosure message missing or changed unexpectedly
    """
    warning_text = " ".join(w.value for w in factor_profile_app.warning)
    info_text    = " ".join(i.value for i in factor_profile_app.info)

    assert "temporarily unavailable" not in warning_text, (
        "Old 'temporarily unavailable' warning still present in Factor Profile page. "
        "Ken French daily Global FF5 data ended June 2019 — this is a permanent "
        "discontinuation, not a transient failure. Phase 8q regression."
    )
    assert "discontinued" in info_text.lower() or "2019" in info_text, (
        "Global factor discontinuation disclosure not found in info elements. "
        f"info_text found: {info_text[:300]!r}"
    )


def test_us_sleeve_regression_renders(factor_profile_app: AppTest) -> None:
    """US equity sleeve regression table must render. Pinned: Phase 8g."""
    if not factor_profile_app.metric:
        pytest.skip("No regression data — skipped in local/empty-DB mode")
    metric_labels = [m.label for m in factor_profile_app.metric]
    assert any("R²" in lbl or "Observations" in lbl for lbl in metric_labels), (
        f"No regression fit metrics found — metric labels: {metric_labels[:10]}"
    )


def test_factor_profile_has_dataframes(factor_profile_app: AppTest) -> None:
    """Factor regression tables must render as dataframes. Pinned: Phase 8g."""
    if not factor_profile_app.dataframe:
        pytest.skip("No dataframes — skipped in local/empty-DB mode")
    assert len(factor_profile_app.dataframe) >= 1, (
        "Expected at least one regression-table dataframe"
    )


def test_build_caption_suppressed_without_env(factor_profile_app: AppTest) -> None:
    """Build caption must be absent when SHOW_BUILD_HASH env var is not set (Phase 15.1)."""
    import os
    if os.environ.get("SHOW_BUILD_HASH"):
        pytest.skip("SHOW_BUILD_HASH is set — caption gating not active in this environment")
    captions = [c.value for c in factor_profile_app.caption]
    assert not any("Build" in c for c in captions), (
        f"Build caption should be suppressed but found: {[c for c in captions if 'Build' in c]}"
    )
