"""Tests for Phase 27 endowment-letter landing page styling (app.py).

Verifies CSS classes, color constants, card descriptions, and structural
invariants introduced by the Phase 27 restyle.
"""
from __future__ import annotations

import pathlib

APP_PY = pathlib.Path(__file__).resolve().parent.parent / "app.py"


def _app_text() -> str:
    return APP_PY.read_text(encoding="utf-8")


# ── Test 1: Google Fonts EB Garamond link present ─────────────────────────────

def test_app_py_imports_eb_garamond():
    """app.py must include the Google Fonts link for EB Garamond."""
    src = _app_text()
    assert "EB+Garamond" in src or "EB Garamond" in src, (
        "EB Garamond Google Fonts link not found in app.py"
    )
    assert "fonts.googleapis.com" in src, (
        "Google Fonts stylesheet link not found in app.py"
    )


# ── Test 2: endow-* CSS classes present ───────────────────────────────────────

def test_app_py_uses_endow_classes():
    """app.py must reference each required endow-* CSS class."""
    src = _app_text()
    required = ["endow-title", "endow-card", "endow-card-header", "endow-card-body"]
    for cls in required:
        assert cls in src, f"CSS class '{cls}' not found in app.py"


# ── Test 3: navy color constant ───────────────────────────────────────────────

def test_navy_color_is_correct():
    """The navy accent color #1e3a5f must appear in app.py."""
    src = _app_text()
    assert "#1e3a5f" in src, (
        "Navy color #1e3a5f not found in app.py — color may have drifted"
    )


# ── Test 4: card description text unchanged ───────────────────────────────────

def test_card_descriptions_unchanged():
    """The six card body descriptions must match the Phase 36 locked text.

    Phase 36 expanded from 4 to 6 cards (added Benchmark Attribution, Factor Profile)
    and trimmed Asset Evaluation to one sentence.

    Pins phrases unique to each card — would fail on revert to old copy.
    """
    src = _app_text()

    # SAA: "the framework treats" was dropped; "SAA is treated" is unique to the new phrasing
    saa_phrase = "SAA is treated"
    perf_phrase = "SAA-target-weighted blended benchmark"
    macro_phrase = "CAPE, ECY"
    # AE trimmed in Phase 36 to one sentence; "mean-variance contribution analysis" is unique
    ae_phrase = "mean-variance contribution analysis"
    # New cards (Phase 36)
    ba_phrase = "Newey-West HAC standard errors"
    fp_phrase = "TERM/CREDIT decomposition"

    assert saa_phrase in src, (
        f"SAA card description changed — phrase not found: {saa_phrase!r}"
    )
    assert perf_phrase in src, (
        f"Performance card description changed — phrase not found: {perf_phrase!r}"
    )
    assert macro_phrase in src, (
        f"Macro card description changed — phrase not found: {macro_phrase!r}"
    )
    assert ae_phrase in src, (
        f"Asset Evaluation card description changed — phrase not found: {ae_phrase!r}"
    )
    assert ba_phrase in src, (
        f"Benchmark Attribution card missing — phrase not found: {ba_phrase!r}"
    )
    assert fp_phrase in src, (
        f"Factor Profile card missing — phrase not found: {fp_phrase!r}"
    )


# ── Test 5: sidebar footer call still present ─────────────────────────────────

def test_sidebar_footer_call_still_present():
    """render_sidebar_footer() must still be called in app.py."""
    src = _app_text()
    assert "render_sidebar_footer()" in src, (
        "render_sidebar_footer() call missing from app.py — "
        "may have been accidentally dropped during the Phase 27 restructure"
    )


# ── Phase 36 additions ────────────────────────────────────────────────────────

def test_new_cards_route_to_correct_pages():
    """Benchmark Attribution and Factor Profile cards must link to their page files. Pinned: Phase 36."""
    src = _app_text()
    assert "pages/6_Benchmark_Attribution.py" in src, (
        "Benchmark Attribution page_link missing from landing page — Phase 36 regression"
    )
    assert "pages/4_Factor_Profile.py" in src, (
        "Factor Profile page_link missing from landing page — Phase 36 regression"
    )


def test_intro_prose_policy_driven_not_view_driven():
    """Landing page intro must use 'policy-driven, not view-driven' framing. Pinned: Phase 36."""
    src = _app_text()
    assert "policy-driven, not view-driven" in src, (
        "Intro prose TAA phrase not replaced — 'policy-driven, not view-driven' not found"
    )
    assert "warrant any tactical tilt" not in src, (
        "TAA-flavored phrase 'warrant any tactical tilt' still present in app.py"
    )
