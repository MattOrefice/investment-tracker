"""Tests for Phase 28 sidebar navigation — header helper and call sites.

Verifies that render_sidebar_header is defined, importable, and called in
every page file, mirroring the existing footer guard pattern.
"""
from __future__ import annotations

import importlib
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES_DIR = REPO_ROOT / "pages"
APP_PY    = REPO_ROOT / "app.py"


# ── Test 1: helper importable ─────────────────────────────────────────────────

def test_render_sidebar_header_exists():
    """render_sidebar_header must be importable from src.ui_helpers."""
    mod = importlib.import_module("src.ui_helpers")
    assert hasattr(mod, "render_sidebar_header"), (
        "render_sidebar_header not found in src.ui_helpers"
    )
    assert callable(mod.render_sidebar_header), (
        "render_sidebar_header is not callable"
    )


# ── Test 2: app.py calls it ───────────────────────────────────────────────────

def test_app_py_calls_render_sidebar_header():
    """app.py must import and call render_sidebar_header()."""
    src = APP_PY.read_text(encoding="utf-8")
    assert "render_sidebar_header" in src, (
        "render_sidebar_header not referenced in app.py"
    )
    assert "render_sidebar_header()" in src, (
        "render_sidebar_header() call missing from app.py"
    )


# ── Test 3: every page calls it ──────────────────────────────────────────────

def test_all_pages_call_render_sidebar_header():
    """Every numbered page in pages/ must call render_sidebar_header().

    Mirrors the footer guard: if a new page is added without the call,
    this test fails and the omission is caught before merge.
    """
    page_files = sorted(PAGES_DIR.glob("[0-9]*.py"))
    assert len(page_files) >= 12, (
        f"Expected at least 12 page files, found {len(page_files)} — "
        "guard may be misconfigured"
    )

    missing = []
    for p in page_files:
        src = p.read_text(encoding="utf-8")
        if "render_sidebar_header()" not in src:
            missing.append(p.name)

    assert not missing, (
        f"render_sidebar_header() call missing from: {', '.join(missing)}"
    )
