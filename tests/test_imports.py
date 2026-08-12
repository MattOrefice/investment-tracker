"""
Import smoke tests: every src module and every page must be importable
in a fresh subprocess without raising.  Guards against the dual try/except
pattern that caused Streamlit Cloud ImportError on /Positioning (Phase 8e).
"""
import os
import subprocess
import sys
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC  = _ROOT / "src"
_PAGES = _ROOT / "pages"


def _importable(module_expr: str) -> tuple[bool, str]:
    """Run 'import <module_expr>' in a fresh Python subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{_ROOT}'); {module_expr}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0, result.stderr.strip()


# ── src modules ───────────────────────────────────────────────────────────────

_SRC_MODULES = [p.stem for p in sorted(_SRC.glob("*.py")) if p.stem != "__init__"]


@pytest.mark.parametrize("module", _SRC_MODULES)
def test_src_module_importable(module):
    ok, err = _importable(f"import src.{module}")
    assert ok, f"src.{module} raised on import:\n{err}"


# ── page-level import smoke ────────────────────────────────────────────────────
#
# Every OTHER page has a render test under tests/render/ that runs unconditionally in
# the default (non-live_data) suite. Two surfaces do not:
#   - pages/3_Macro.py: its render test (tests/render/test_macro_render.py) is entirely
#     @pytest.mark.live_data (it needs a real FRED_API_KEY to assert on live content),
#     so it is excluded from the default CI gate — an import-time break anywhere in
#     this ~2,300-line page would never fail CI.
#   - app.py: the navigation router / entry point has no render test at all.
# Both gaps match the shape of the Phase 8e Cloud ImportError this file's src-module
# tests already guard against. These tests run each file through Streamlit's own
# AppTest harness (which correctly executes a script-style page/router, unlike a bare
# 'import' — pages/3_Macro.py isn't even a valid module path due to the leading digit)
# inside a fresh subprocess, with every live network endpoint (Yahoo Finance, FRED,
# Yale/Shiller) monkeypatched to raise immediately — deterministic and offline
# regardless of host secrets or connectivity. Both files already catch every individual
# data-fetch failure internally and render an empty/error state rather than
# propagating it, so blocking network here only isolates import-time breaks — exactly
# the failure class this guard targets.

def _page_importable_offline(page_relpath: str) -> tuple[bool, str]:
    """Run a Streamlit page/router file through AppTest in a fresh, network-blocked
    subprocess. Returns (ok, stderr); ok is False if AppTest recorded an unhandled
    exception or the subprocess itself crashed (e.g. an ImportError at module level)."""
    script = f"""
import sys, os
sys.path.insert(0, {str(_ROOT)!r})
os.environ["TRACKER_MODE"] = "demo"
os.environ.pop("FRED_API_KEY", None)

import src.prices as _prices
import src.macro as _macro
import src.shiller as _shiller

def _blocked(*args, **kwargs):
    raise RuntimeError("network disabled in import-smoke test")

_prices.fetch_prices = _blocked
_macro.fetch_fred_series = _blocked
_shiller.fetch_cape_dataframe = _blocked

from streamlit.testing.v1 import AppTest
at = AppTest.from_file({page_relpath!r}, default_timeout=60)
at.run()
if at.exception:
    print("EXC:", at.exception, file=sys.stderr)
    sys.exit(1)
"""
    env = dict(os.environ)
    env.pop("FRED_API_KEY", None)
    env["TRACKER_MODE"] = "demo"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_ROOT),
        env=env,
    )
    return result.returncode == 0, result.stderr.strip()


def test_macro_page_importable_offline():
    """pages/3_Macro.py must execute without an unhandled exception in a fresh,
    network-blocked subprocess.

    tests/render/test_macro_render.py already exercises this page's rendered content in
    depth, but every test there is @pytest.mark.live_data and excluded from the default
    CI gate — an import-time break (a bad import, a renamed function, a typo'd
    attribute) would currently reach Cloud undetected. This test needs no live data:
    every fetch on the page is individually try/except-guarded and renders an empty
    state on failure, so blocking network here only isolates import-time correctness.
    """
    ok, err = _page_importable_offline("pages/3_Macro.py")
    assert ok, f"pages/3_Macro.py raised on import/render:\n{err}"


def test_app_py_importable_offline():
    """app.py (the navigation router / entry point) must execute without an unhandled
    exception in a fresh, network-blocked subprocess.

    app.py has no render test at all, yet it is the one file every page depends on to
    be reachable — an import-time break here (a bad import in the page-group
    construction, a typo in a st.Page(...) call) has had no test in the default suite
    that would catch it before deployment.
    """
    ok, err = _page_importable_offline("app.py")
    assert ok, f"app.py raised on import/render:\n{err}"


# ── CI guard: ban the dual try/except pattern ─────────────────────────────────

_DUAL_PATTERN = re.compile(
    r"except ImportError:\s*\n\s+from (?!dotenv|streamlit)",
    re.MULTILINE,
)


def test_no_dual_import_fallback_in_src():
    """src/*.py must not contain 'except ImportError: from <bare module>' fallbacks."""
    violations = []
    for pyfile in sorted(_SRC.glob("*.py")):
        if _DUAL_PATTERN.search(pyfile.read_text(encoding="utf-8")):
            violations.append(f"src/{pyfile.name}")
    for pyfile in sorted(_PAGES.glob("*.py")):
        if _DUAL_PATTERN.search(pyfile.read_text(encoding="utf-8")):
            violations.append(f"pages/{pyfile.name}")
    assert not violations, (
        "Dual try/except import pattern found in: "
        + ", ".join(violations)
        + "\nUse 'from src.X import Y' everywhere — no bare fallbacks."
    )


# ── Import-time DB isolation: importing a module must not touch the database ──
#
# A module-level constant computed through get_connection() makes `import`
# itself a DB access: get_connection() runs _auto_migrate on the first
# connection per process, whose UPDATE statement demands write access even when
# it matches zero rows — so importing such a module WRITE-ATTEMPTS the
# committed demo.db (in demo mode) or the personal tracker.db (locally, where
# .env resolves personal) from pytest collection, a bare REPL import, or any
# subprocess. It also freezes DB-derived values at import, which goes stale
# across an in-process migration (the phase-46 international split renames the
# sleeve these constants label).
#
# These tests are the UNCONDITIONAL form of that guarantee. The importability
# tests above only catch an import-time DB touch when the DB file happens to be
# unwritable — on a writable checkout the write lands silently and the import
# "succeeds". Here the spawned interpreter replaces sqlite3.connect with a trap
# that raises BEFORE the target is imported, so any import-time connection —
# read or write, any DB path, any OS, any file permissions — fails the test
# with the offending import chain in the traceback. Parametrized over the same
# _SRC_MODULES glob as the importability tests, so a future module enrolls
# automatically; the extra case runs tests/render/conftest.py itself, whose
# module-level imports execute during pytest's initial conftest loading —
# before any plugin hook can intervene — and must therefore be DB-free.

_DB_TRAP_PREAMBLE = (
    "import sqlite3, sys\n"
    "def _trap(*a, **k):\n"
    "    raise AssertionError('import-time DB access: sqlite3.connect() called during import')\n"
    "sqlite3.connect = _trap\n"
    "sys.path.insert(0, {root!r})\n"
)


def _run_with_db_trap(code: str) -> tuple[bool, str]:
    """Run `code` in a fresh interpreter whose sqlite3.connect raises."""
    script = _DB_TRAP_PREAMBLE.format(root=str(_ROOT)) + code
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_ROOT),
    )
    return result.returncode == 0, result.stderr.strip()


@pytest.mark.parametrize("module", _SRC_MODULES)
def test_src_module_import_touches_no_db(module):
    ok, err = _run_with_db_trap(f"import src.{module}")
    assert ok, f"src.{module} opened a database connection at import time:\n{err}"


def test_render_conftest_chain_touches_no_db():
    """tests/render/conftest.py's module-level imports load during pytest's
    initial conftest phase, before any plugin hook can patch DB paths — so this
    exact chain must never reach the database."""
    conftest_path = _ROOT / "tests" / "render" / "conftest.py"
    code = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('_render_conftest_probe', {str(conftest_path)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
    )
    ok, err = _run_with_db_trap(code)
    assert ok, (
        "tests/render/conftest.py's import chain opened a database connection "
        f"during initial conftest loading:\n{err}"
    )
