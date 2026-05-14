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


def test_app_py_calls_render_sidebar_footer():
    """app.py must call render_sidebar_footer().

    Phase 29 centralized the sidebar footer to app.py (called once after
    nav.run() so it renders below any page-specific sidebar widgets).
    """
    app_py = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    src = app_py.read_text(encoding="utf-8")
    assert "render_sidebar_footer()" in src, (
        "render_sidebar_footer() call missing from app.py"
    )
