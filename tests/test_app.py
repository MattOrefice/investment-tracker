"""Smoke tests for app.py — syntax guard and page_link path validation."""
import ast
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_REPO = pathlib.Path(__file__).resolve().parent.parent
_APP = _REPO / "app.py"


def test_app_parses_without_syntax_error():
    """app.py must parse as valid Python (catches import errors and syntax bugs)."""
    source = _APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree is not None


def test_app_page_links_reference_existing_files():
    """Every page path in st.page_link() calls in app.py must exist on disk."""
    source = _APP.read_text(encoding="utf-8")
    paths = re.findall(r'page_link\(["\']([^"\']+)["\']', source)
    assert paths, "No page_link calls found in app.py — expected at least 4"
    missing = [p for p in paths if not (_REPO / p).exists()]
    assert not missing, f"app.py page_link references non-existent files: {missing}"
