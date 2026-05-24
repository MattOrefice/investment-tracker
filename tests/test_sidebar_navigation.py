"""Tests for Phase 29 / 29.1 st.navigation() migration and page header.

Verifies:
- app.py uses st.navigation() with expanded=True and a hidden landing page
- All 12 page paths present and three section headers declared
- render_page_header callable and called by every inner page
- Landing page does not call render_page_header (would be self-referential)
- No page still references the removed render_sidebar_header helper
"""
from __future__ import annotations

import importlib
import pathlib
from unittest.mock import MagicMock, patch

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES_DIR = REPO_ROOT / "pages"
APP_PY    = REPO_ROOT / "app.py"

_EXPECTED_PAGES = [
    "pages/1_SAA.py",
    "pages/2_Performance.py",
    "pages/3_Macro.py",
    "pages/4_Factor_Profile.py",
    "pages/5_Asset_Evaluation.py",
    "pages/6_Benchmark_Attribution.py",
    "pages/8_Research.py",
    "pages/9_Correlations.py",
    "pages/10_Trade_Log.py",
    "pages/11_Capital_Deployment.py",
    "pages/12_Tax_Lots.py",
]

_EXPECTED_SECTIONS = ["Portfolio", "Markets & Macro", "Operations"]


# ── Test 1: app.py uses st.navigation ────────────────────────────────────────

def test_app_py_uses_st_navigation():
    src = APP_PY.read_text(encoding="utf-8")
    assert "st.navigation(" in src, "st.navigation() call missing from app.py"


# ── Test 2: expanded=True to prevent View N more collapse ────────────────────

def test_navigation_uses_expanded_true():
    src = APP_PY.read_text(encoding="utf-8")
    assert "expanded=True" in src, (
        "expanded=True missing from st.navigation() in app.py — "
        "nav list may auto-collapse and hide pages"
    )


# ── Test 3: landing page is hidden from the nav widget ───────────────────────

def test_landing_page_is_hidden():
    src = APP_PY.read_text(encoding="utf-8")
    assert 'visibility="hidden"' in src, (
        'visibility="hidden" missing from landing page st.Page() in app.py'
    )


# ── Test 4: all 12 page paths present ────────────────────────────────────────

def test_navigation_includes_all_12_pages():
    src = APP_PY.read_text(encoding="utf-8")
    missing = [p for p in _EXPECTED_PAGES if p not in src]
    assert not missing, (
        f"Page paths missing from app.py navigation: {', '.join(missing)}"
    )


# ── Test 5: three section headers present ────────────────────────────────────

def test_navigation_has_three_sections():
    src = APP_PY.read_text(encoding="utf-8")
    missing = [s for s in _EXPECTED_SECTIONS if s not in src]
    assert not missing, (
        f"Section headers missing from app.py navigation: {', '.join(missing)}"
    )


# ── Test 6: render_page_header importable ────────────────────────────────────

def test_render_page_header_exists():
    with patch.dict("sys.modules", {"streamlit": MagicMock()}):
        mod = importlib.import_module("src.ui_helpers")
        importlib.reload(mod)
        assert hasattr(mod, "render_page_header"), (
            "render_page_header not found in src.ui_helpers"
        )
        assert callable(mod.render_page_header)


# ── Test 7: every inner page calls render_page_header ────────────────────────

def test_all_pages_call_render_page_header():
    page_files = sorted(PAGES_DIR.glob("[0-9]*.py"))
    assert len(page_files) >= 11, (
        f"Expected at least 11 page files, found {len(page_files)}"
    )
    missing = [
        p.name for p in page_files
        if "render_page_header()" not in p.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"render_page_header() call missing from: {', '.join(missing)}"
    )


# ── Test 8: landing page does NOT call render_page_header ────────────────────

def test_landing_does_not_call_render_page_header():
    src = APP_PY.read_text(encoding="utf-8")
    assert "render_page_header" not in src, (
        "render_page_header referenced in app.py — landing page should not "
        "show the home link (self-referential)"
    )


# ── Test 9: no page imports the removed render_sidebar_header helper ─────────

def test_no_render_sidebar_header_imports():
    page_files = sorted(PAGES_DIR.glob("[0-9]*.py"))
    violating = [
        p.name for p in page_files
        if "render_sidebar_header" in p.read_text(encoding="utf-8")
    ]
    assert not violating, (
        f"render_sidebar_header still referenced in: {', '.join(violating)}"
    )
