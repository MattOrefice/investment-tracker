"""Import-resolution smoke test for every Streamlit page (and app.py).

Why this exists: the ``cannot import name 'deploy_targets_split'`` error that broke
page 14 at load time was INVISIBLE to CI. No test imports the page scripts, so a
missing/renamed name in a page's ``from ... import`` list never surfaced. Worse, an
execution-based check would not catch it either: page 14's broken import sits BELOW
its demo-mode ``st.stop()`` gate, so rendering the page in demo/CI mode halts before
reaching the import.

So this test resolves every import in every page (and app.py) STATICALLY — the module
must be importable and each imported NAME must exist (as an attribute or a submodule) —
WITHOUT executing the page body. That needs no DB or CSV (runs in demo mode / CI),
isolates import-time errors from data-time ones, and catches a bad name regardless of
any runtime mode gate. Pages are discovered from the directory, so a new page is
covered automatically with no hardcoded list.
"""
import ast
import importlib
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Discover from the directory (no hardcoded list): app.py + every pages/*.py.
PAGE_FILES = [ROOT / "app.py"] + sorted((ROOT / "pages").glob("*.py"))


def _unresolved_imports(path: pathlib.Path) -> list[str]:
    """Every import in ``path`` that does NOT resolve: a module that is not importable,
    or a ``from module import name`` whose name is neither an attribute nor a submodule
    of that module. Parses the source and resolves imports without executing the body."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:        # skip relative imports
                continue
            try:
                mod = importlib.import_module(node.module)
            except Exception as e:                        # noqa: BLE001 — report any failure
                errors.append(f"from {node.module}: {type(e).__name__}: {e}")
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if hasattr(mod, alias.name):
                    continue
                try:                                       # `from package import submodule`
                    importlib.import_module(f"{node.module}.{alias.name}")
                except Exception:                          # noqa: BLE001
                    errors.append(f"cannot import name {alias.name!r} from {node.module!r}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    importlib.import_module(alias.name)
                except Exception as e:                     # noqa: BLE001
                    errors.append(f"import {alias.name}: {type(e).__name__}: {e}")
    return errors


def test_pages_are_discovered():
    """Guard the parametrization itself — if the glob found nothing, the smoke test
    below would vacuously pass and hide the very errors it exists to catch."""
    names = {p.name for p in PAGE_FILES}
    assert "app.py" in names, "app.py must be discovered"
    n_pages = sum(1 for p in PAGE_FILES if p.parent.name == "pages")
    assert n_pages >= 10, f"expected pages/ to be discovered; found {n_pages}"


@pytest.mark.parametrize("page_file", PAGE_FILES, ids=lambda p: p.name)
def test_page_imports_resolve(page_file):
    """Every import in the page (and app.py) resolves. Catches import-time
    'cannot import name X from Y' — the class of bug that broke page 14's load."""
    errors = _unresolved_imports(page_file)
    assert not errors, f"{page_file.name} has unresolved imports:\n  " + "\n  ".join(errors)


def test_resolver_catches_a_missing_name(tmp_path):
    """Efficacy self-test: the resolver MUST flag a genuinely-missing name — otherwise
    a green run above proves nothing. Mirrors the page-14 failure mode exactly: a
    'from src.location_actions import <name that is not exported>'."""
    bad = tmp_path / "bad_page.py"
    bad.write_text("from src.location_actions import build_deploy_answer\n", encoding="utf-8")
    errors = _unresolved_imports(bad)
    assert any("build_deploy_answer" in e for e in errors), (
        f"resolver failed to flag a missing name: {errors}"
    )
