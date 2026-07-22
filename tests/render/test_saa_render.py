"""Render-aware tests for the SAA page (pages/1_SAA.py).

Pinned at Phase 37 — verifies Implementation Note, Endowment Context, and
per-sleeve rationale formatting introduced in the Phase 37 polish pass.
Phase 42 adds prose regression pins for Investment Thesis, Endowment closing,
and US Large Core restructure.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def saa_app() -> AppTest:
    """Run the SAA page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/1_SAA.py", default_timeout=60)
    at.run()
    return at


def test_saa_runs_without_exception(saa_app: AppTest) -> None:
    """SAA page must complete render without raising an unhandled exception."""
    assert not saa_app.exception, f"SAA page raised: {saa_app.exception}"


def test_implementation_note_paper_trade(saa_app: AppTest) -> None:
    """Implementation note must use paper-trade framing. Pinned: Phase 37."""
    all_text = " ".join(c.value for c in saa_app.caption)
    assert "paper-trade portfolio simulated from May 2025" in all_text, (
        "Implementation note missing 'paper-trade portfolio simulated from May 2025' — "
        "old 'exercise the analytical framework' framing may have been restored"
    )


def test_endowment_context_illiquidity_premium(saa_app: AppTest) -> None:
    """Endowment Context closing must mention illiquidity premium. Pinned: Phase 37."""
    all_text = " ".join(m.value for m in saa_app.markdown)
    assert "illiquidity premium" in all_text, (
        "Endowment Context missing 'illiquidity premium' — Phase 37 prose may have been reverted"
    )


def test_all_sleeve_rationales_have_bold_would_conditions(use_demo_db) -> None:
    """Sleeve rationales in the DB must contain bold '**Would' condition blocks. Pinned: Phase 37.

    Pins the demo book (use_demo_db) — this asserts the full 12-sleeve taxonomy,
    which only the demo book carries; the personal book keeps its coherent
    9-sleeve taxonomy until the personal restructure lands.

    AppTest does not render collapsed expander content, so this checks the DB directly.
    The SAA page renders rationale verbatim from the DB via _safe_md().

    Phase 39: the four international sleeves carry "**Would revisit if" blocks —
    a third heading alongside the "**Would increase if" / "**Would reduce if"
    pairs the other eight use. Their conditions are falsifiers (what would show
    the mirror-the-US premise wrong) rather than directional sizing triggers, so
    they do not reduce to increase/reduce without changing meaning. The predicate
    matches on "**Would", which covers all three headings.
    """
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, rationale FROM asset_classes "
            "WHERE parent_id IS NOT NULL AND target_weight > 0"
        ).fetchall()

    assert len(rows) == 12, f"Expected 12 strategic sleeve rows, got {len(rows)}"
    missing = [r["name"] for r in rows if "**Would" not in (r["rationale"] or "")]
    assert not missing, (
        f"Sleeves missing bold Would conditions in DB rationale: {missing} — "
        "Phase 37 restructuring may not have been applied to the database"
    )


def test_thesis_three_paragraphs(saa_app: AppTest) -> None:
    """Investment Thesis must render as three separate markdown blocks. Pinned: Phase 42 Item 1.

    Regression: if the three paragraphs are collapsed back into one, the thesis becomes
    a five-sentence wall that an allocator cannot extract the core thesis from in one read.
    """
    all_md = [m.value for m in saa_app.markdown]
    para1_found = any("sustains" in m and "market-timing signal" in m for m in all_md)
    para2_found = any("factor and geographic diversification" in m and "market-timing calls" in m for m in all_md)
    para3_found = any("unhedged inflation tail" in m for m in all_md)
    assert para1_found, (
        "Thesis paragraph 1 (equity weight / market-timing signal) not found as distinct markdown block — "
        "Phase 42 Item 1 regression: thesis may have been collapsed back into one paragraph."
    )
    assert para2_found, (
        "Thesis paragraph 2 (factor/geographic diversification) not found as distinct markdown block — "
        "Phase 42 Item 1 regression."
    )
    assert para3_found, (
        "Thesis paragraph 3 (Intl/EM/Real Assets/FI) not found — Phase 42 Item 1 regression."
    )


def test_endowment_closing_asserted_not_defensive(saa_app: AppTest) -> None:
    """Endowment closing must use asserted framing, not defensive disclaimer. Pinned: Phase 42 Item 2."""
    all_md = " ".join(m.value for m in saa_app.markdown)
    assert "Endowment-return replication at retail scale is not the goal" not in all_md, (
        "Old defensive disclaimer restored — Phase 42 Item 2: closing should be 'The goal is "
        "institutional analytical framing applied at retail scale — not return replication.'"
    )
    assert "institutional analytical framing applied at retail scale" in all_md, (
        "Asserted framing not found — Phase 42 Item 2 regression."
    )


def test_us_large_core_anchor_sentence_leads() -> None:
    """US Large Core rationale must lead with the 'un-opinionated anchor' sentence. Pinned: Phase 42 Item 8.

    AppTest does not render collapsed expander content, so this checks the DB directly.
    """
    import os, sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT rationale FROM asset_classes WHERE name = 'US Large Core'"
        ).fetchone()

    rationale = row["rationale"] or ""
    first_sentence_end = rationale.find(".")
    first_sentence = rationale[:first_sentence_end + 1] if first_sentence_end >= 0 else rationale
    assert "un-opinionated anchor" in first_sentence, (
        f"US Large Core rationale does not lead with 'un-opinionated anchor' sentence. "
        f"First sentence found: {first_sentence!r} — Phase 42 Item 8 regression."
    )


def test_us_small_cap_has_both_triggers() -> None:
    """US Small Cap must have both Would increase and Would reduce triggers. Pinned: Phase 42 Item 7."""
    import os, sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT rationale FROM asset_classes WHERE name = 'US Small Cap'"
        ).fetchone()

    rationale = row["rationale"] or ""
    assert "**Would increase if**" in rationale, (
        "US Small Cap missing 'Would increase if' trigger — Phase 42 Item 7 regression."
    )
    assert "**Would reduce if**" in rationale, (
        "US Small Cap missing 'Would reduce if' trigger — Phase 42 Item 7 regression: "
        "trigger was added in Phase 42 as part of tightening the observable signal set."
    )
    assert "rolling 5-year" in rationale.lower() or "5-year" in rationale, (
        "US Small Cap 'Would increase' trigger should reference observable rolling 5-year spread — "
        "Phase 42 Item 7 regression."
    )


def test_tips_no_personal_age_reference() -> None:
    """TIPS rationale must not reference personal age ('at 27'). Pinned: Phase 42 Item 9."""
    import os, sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT rationale FROM asset_classes WHERE name = 'TIPS'"
        ).fetchone()

    rationale = row["rationale"] or ""
    assert "young investor" not in rationale, (
        "TIPS rationale still uses 'young investor' phrasing — Phase 42 Item 9 regression. "
        "Should use 'long-horizon investor'."
    )
    assert "long-horizon investor" in rationale, (
        "TIPS rationale missing 'long-horizon investor' — Phase 42 Item 9 regression."
    )


def test_allocated_badge_sums_unrounded_weights(saa_app: AppTest) -> None:
    """Allocated badge must read green 100.0% on a well-seeded book.

    The old check summed the display-rounded Target (%) column; twelve
    1dp-rounded demo values summed to exactly 99.9 and sat on the < 0.1
    tolerance boundary, flagging red a book whose true policy weights sum to
    100.0000%. The badge now sums the unrounded weights, so red means
    genuinely mis-seeded.
    """
    all_md = " ".join(m.value for m in saa_app.markdown)
    assert "does not sum to 100%" not in all_md, (
        "Allocated badge is red — either the book is genuinely mis-seeded or "
        "the badge regressed to summing the display-rounded column."
    )
    assert "Allocated: <b>100.0%" in all_md, (
        "Green Allocated badge with unrounded 100.0% total not found."
    )
