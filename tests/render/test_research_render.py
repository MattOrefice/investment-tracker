"""Render-aware regression tests for the Research page (pages/8_Research.py).

Pinned at Phase 46 — protects high-leverage prose additions from accidental
reversion: 'How to read this page' expander, Methodology expander, Featured
Selections subtitle, VGIT duration attribution, and re-evaluation triggers
for AVUV/SCHP/SPHQ.

The page reads holding_rationale from SQLite (demo.db in CI, tracker.db
locally). Tests that check rationale text are guarded by the page exception
check. T6 ($436 vs $425) and T8 (expander default state) confirmed
non-issues in Phase 46 diagnostic — not tested here.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def research_app() -> AppTest:
    """Run the Research page."""
    at = AppTest.from_file("pages/8_Research.py", default_timeout=60)
    at.run()
    return at


def test_research_runs_without_exception(research_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not research_app.exception, (
        f"Research page raised: {research_app.exception}"
    )


def test_how_to_read_expander_present(research_app: AppTest) -> None:
    """'How to read this page' expander must be present (collapsed by default).
    Pinned: Phase 46 Item A1."""
    labels = [e.label for e in research_app.expander]
    assert any("How to read this page" in lbl for lbl in labels), (
        "'How to read this page' expander not found — Phase 46 Item A1 regression. "
        f"Expander labels found: {labels}"
    )


def test_methodology_expander_present(research_app: AppTest) -> None:
    """'Methodology' expander must be present below the sleeve sections.
    Pinned: Phase 46 Item A2."""
    labels = [e.label for e in research_app.expander]
    assert any(lbl == "Methodology" for lbl in labels), (
        "'Methodology' expander not found — Phase 46 Item A2 regression. "
        f"Expander labels found: {labels}"
    )


def test_featured_selections_subtitle_rephrase(research_app: AppTest) -> None:
    """Featured Selections subtitle must use the 'low-cost index defaults' phrasing.
    Pinned: Phase 46 Item A3."""
    all_text = " ".join(
        c.value for c in research_app.caption
        if hasattr(c, "value") and c.value
    )
    assert "low-cost index defaults" in all_text, (
        "Featured Selections subtitle not rephrased — Phase 46 Item A3 regression: "
        "'How and why this portfolio differs from low-cost index defaults' must be "
        "present as a caption."
    )
    assert "Three holdings where the case for diverging" not in all_text, (
        "Old Featured Selections subtitle still present — Phase 46 Item A3 regression."
    )


def test_vgit_duration_attribution_sentence(research_app: AppTest) -> None:
    """VGIT rationale must include the duration attribution sentence.
    Pinned: Phase 46 Item C1."""
    if research_app.exception:
        pytest.skip("Page raised an exception — DB-dependent test skipped.")
    all_md = " ".join(m.value for m in research_app.markdown)
    assert "flight-to-quality rally but limits drawdown in a rate-selloff" in all_md, (
        "VGIT duration attribution sentence not found — Phase 46 Item C1 regression: "
        "'The shorter duration means VGIT underperforms longer-dated Treasuries in a "
        "flight-to-quality rally but limits drawdown in a rate-selloff' must be present."
    )


def test_avuv_factor_decay_trigger(research_app: AppTest) -> None:
    """AVUV rationale must include the factor decay re-evaluation trigger.
    Pinned: Phase 46 Item C2a."""
    if research_app.exception:
        pytest.skip("Page raised an exception — DB-dependent test skipped.")
    all_md = " ".join(m.value for m in research_app.markdown)
    assert "factor premium decay rather than temporary cyclical lag" in all_md, (
        "AVUV factor decay trigger not found — Phase 46 Item C2a regression: "
        "'suggesting factor premium decay rather than temporary cyclical lag' must be present."
    )


def test_schp_breakeven_trigger(research_app: AppTest) -> None:
    """SCHP rationale must include the breakeven inflation re-evaluation trigger.
    Pinned: Phase 46 Item C2b."""
    if research_app.exception:
        pytest.skip("Page raised an exception — DB-dependent test skipped.")
    all_md = " ".join(m.value for m in research_app.markdown)
    assert "5-year TIPS breakeven inflation falls persistently below 1.5%" in all_md, (
        "SCHP breakeven trigger not found — Phase 46 Item C2b regression: "
        "'Would revisit if 5-year TIPS breakeven inflation falls persistently below 1.5%' "
        "must be present."
    )


def test_sphq_accruals_track_record_trigger(research_app: AppTest) -> None:
    """SPHQ rationale must include the accruals track-record re-evaluation trigger.
    Pinned: Phase 46 Item C2c."""
    if research_app.exception:
        pytest.skip("Page raised an exception — DB-dependent test skipped.")
    all_md = " ".join(m.value for m in research_app.markdown)
    assert "Sloan (1996) accounting quality premium persists in a live portfolio" in all_md, (
        "SPHQ accruals trigger not found — Phase 46 Item C2c regression: "
        "'directly testing whether the Sloan (1996) accounting quality premium "
        "persists in a live portfolio' must be present."
    )
