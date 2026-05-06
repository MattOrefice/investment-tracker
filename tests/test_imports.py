"""
Import smoke tests: every src module and every page must be importable
in a fresh subprocess without raising.  Guards against the dual try/except
pattern that caused Streamlit Cloud ImportError on /Positioning (Phase 8e).
"""
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
