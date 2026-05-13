"""Smoke tests for src/ui_helpers — import guards and footer coverage."""
import importlib
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PAGES_DIR = pathlib.Path(__file__).resolve().parent.parent / "pages"


def test_render_sidebar_footer_is_callable():
    """render_sidebar_footer exists and is callable without raising."""
    with patch.dict("sys.modules", {"streamlit": MagicMock()}):
        import importlib
        import src.ui_helpers as _mod
        importlib.reload(_mod)
        assert callable(_mod.render_sidebar_footer)


def test_render_footer_is_callable():
    """render_footer exists and is callable."""
    with patch.dict("sys.modules", {"streamlit": MagicMock()}):
        import src.ui_helpers as _mod
        importlib.reload(_mod)
        assert callable(_mod.render_footer)


def test_all_pages_call_render_sidebar_footer():
    """Every file in pages/ must contain a render_sidebar_footer() call.

    Prevents new pages from shipping without the contact footer.
    """
    page_files = sorted(PAGES_DIR.glob("*.py"))
    assert page_files, "No page files found — check PAGES_DIR path"
    missing = [
        p.name for p in page_files
        if "render_sidebar_footer()" not in p.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"These pages are missing render_sidebar_footer(): {missing}"
    )
