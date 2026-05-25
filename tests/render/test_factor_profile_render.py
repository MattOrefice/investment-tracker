"""Render-aware tests for the Factor Profile page.

These tests assert on rendered widget content using streamlit.testing.v1.AppTest.
Regression pins document which Phase introduced each invariant so that future
regressions surface a clear attribution in the failure message.
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


def test_global_factors_discontinued_in_methodology(factor_profile_app: AppTest) -> None:
    """Global factor discontinuation disclosure must appear in the Methodology expander.

    Regression pin: Phase 8q (original), updated Phase 41. Root cause — Ken French ceased
    publication of daily Global 5-factor data in June 2019. The portfolio started May 2025;
    there is no data overlap and the regression cannot run. Phase 41 dropped the Global
    Factors tab; the disclosure moved to the Methodology & Disclosure expander.

    If this test fails:
    - disclosure is absent: the methodology note was removed or the dead-code fix was reverted
    - 'temporarily unavailable' in warnings: old warning message restored — Phase 8q regression
    """
    warning_text = " ".join(w.value for w in factor_profile_app.warning)
    all_markdown  = " ".join(m.value for m in factor_profile_app.markdown)

    assert "temporarily unavailable" not in warning_text.lower() or "global" not in warning_text.lower(), (
        "Old 'temporarily unavailable' warning still present for Global Factors. "
        "Ken French daily Global FF5 data ended June 2019 — this is a permanent "
        "discontinuation, not a transient failure. Phase 8q regression."
    )
    assert "discontinued" in all_markdown.lower() or "2019" in all_markdown, (
        "Global factor discontinuation disclosure not found in methodology markdown. "
        "Phase 41: the disclosure moved from the (dropped) Global Factors tab to "
        "the Methodology & Disclosure expander."
    )


def test_us_sleeve_regression_renders(factor_profile_app: AppTest) -> None:
    """US equity sleeve regression table must render with R²/T in caption. Pinned: Phase 8g; updated Phase 41."""
    if not factor_profile_app.dataframe:
        pytest.skip("No regression data — skipped in local/empty-DB mode")
    captions = [c.value for c in factor_profile_app.caption]
    assert any("R²" in c for c in captions), (
        f"R² not found in any caption — regression table caption may be missing. "
        f"Phase 41: R²/T moved from metric cards to the table caption row."
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


def test_morningstar_independence_statement_present(factor_profile_app: AppTest) -> None:
    """Methodology expander must contain the Morningstar independence statement. Pinned: Phase 38."""
    captions = [c.value for c in factor_profile_app.caption]
    assert any("independent of Morningstar" in c for c in captions), (
        "Morningstar independence statement not found in Factor Profile methodology captions — "
        "possible Phase 38 regression"
    )


def test_institutional_prose_for_intl_developed(factor_profile_app: AppTest) -> None:
    """Intl Developed sleeve must show Korea universe-mismatch prose. Pinned: Phase 41 Item 1."""
    if not factor_profile_app.dataframe:
        pytest.skip("No regression data — skipped in local/empty-DB mode")
    all_md = " ".join(str(m.value) for m in factor_profile_app.markdown)
    assert "korea" in all_md.lower() or "universe mismatch" in all_md.lower(), (
        "Korea universe-mismatch explanation not found in page prose — "
        "Phase 41 Item 1: Intl Developed sleeve should use build_factor_prose institutional output."
    )


def test_carhart_caption_uses_dynamic_mom_loading(factor_profile_app: AppTest) -> None:
    """Carhart expander caption must reference 'Mom loading' dynamically. Pinned: Phase 41 Item 3."""
    if not factor_profile_app.dataframe:
        pytest.skip("No regression data — skipped in local/empty-DB mode")
    captions = [c.value for c in factor_profile_app.caption]
    assert any("Mom loading" in c for c in captions), (
        "Dynamic 'Mom loading' caption not found — Phase 41 Item 3: "
        "Carhart expander caption should show the actual Mom β and t-stat from live data."
    )


def test_fi_sleeve_carhart_note_present(factor_profile_app: AppTest) -> None:
    """FI sleeve must show Carhart-not-applicable caption. Pinned: Phase 41 Item 8."""
    captions = [c.value for c in factor_profile_app.caption]
    assert any("duration-driven" in c.lower() for c in captions), (
        "FI Carhart note ('duration-driven sleeve') not found in captions — "
        "Phase 41 Item 8 regression."
    )


def test_regression_window_caption_present(factor_profile_app: AppTest) -> None:
    """A caption must explain that regression windows end at the locked quarter-end. Pinned: Phase 41 Item 10."""
    captions = [c.value for c in factor_profile_app.caption]
    assert any("locked quarter-end" in c.lower() or "quarter" in c.lower() and "stability" in c.lower()
               for c in captions), (
        "Regression window stability caption not found — Phase 41 Item 10 regression."
    )


def test_no_global_factors_tab(factor_profile_app: AppTest) -> None:
    """Global Factors tab must not appear in the page. Pinned: Phase 41 Item 7."""
    tab_content = " ".join(str(getattr(t, 'label', '')) for t in getattr(factor_profile_app, 'tabs', []))
    assert "Global Factors" not in tab_content, (
        "Global Factors tab found — Phase 41 Item 7: tab was dropped; "
        "disclosure moved to Methodology expander."
    )
