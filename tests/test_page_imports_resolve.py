"""Every ``from src.* import (...)`` in a page must resolve — checked statically.

WHY THIS EXISTS
---------------
A page-level ImportError is invisible until someone navigates to that page:
Streamlit imports a page lazily, so a broken import list ships green and fires
in production on the first click. The render tests in tests/render/ catch this
for the pages they cover, but coverage is uneven and cannot be made even:

* ``pages/3_Macro.py``'s render tests are all ``@pytest.mark.live_data`` (live
  FRED) and are excluded from the default suite by pytest.ini — correctly, and
  CLAUDE.md forbids un-excluding them. Macro therefore has NO executing import
  check in CI and cannot get one without live external calls on every push.
* ``pages/13_Household_View.py``, ``14_Asset_Location.py`` and
  ``15_Liquidity.py`` are personal-mode only (gated behind ``if not IS_DEMO``
  in app.py) and have no render tests at all. 14 currently raises on load in
  demo mode, so an execute-every-page guard would fail on day one.

This guard closes that hole from the other side: it never executes anything, so
it is immune to mode, database contents, network and marker exclusions. It
parses each page and each ``src`` module it imports from, and checks that every
imported name is actually defined at that module's top level.

SCOPE — what this does and does not prove
-----------------------------------------
Catches: a name that does not exist in the target module (the failure this was
written for), and a ``src`` module that does not exist at all. Also covers
function-level ``from src.x import y`` (the lazy imports in src/asof.py's
pattern), which only raise when the function is first called.

Does not catch: anything requiring execution — circular imports, a module whose
body raises, or third-party imports. Those are the render tests' job. The two
layers are complementary, not redundant.

Pages are enumerated by GLOB, never a hardcoded list: a static list is exactly
how the next new page slips through uncovered.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _page_files() -> list[Path]:
    """Every Streamlit entry point: all pages plus the landing app.

    Glob, not a literal list — see the module docstring.
    """
    return sorted(_ROOT.glob("pages/*.py")) + [_ROOT / "app.py"]


def _module_path(dotted: str) -> Path | None:
    """Resolve a dotted module name to its file, or None if it does not exist.

    Handles both a plain module (``src.asof`` -> src/asof.py) and a package
    (``src.ingestion`` -> src/ingestion/__init__.py).
    """
    parts = dotted.split(".")
    flat = _ROOT.joinpath(*parts).with_suffix(".py")
    if flat.is_file():
        return flat
    pkg = _ROOT.joinpath(*parts, "__init__.py")
    if pkg.is_file():
        return pkg
    return None


def _toplevel_names(tree: ast.Module) -> tuple[set[str], bool]:
    """Names importable from a module, and whether the answer is trustworthy.

    Walks the module body and recurses through ``if`` / ``try`` / ``with``
    blocks — a name defined under ``if TYPE_CHECKING:`` or in a try/except
    import fallback is still a module attribute, and missing those would make
    this guard fail on correct code. Function and class bodies are NOT entered:
    names bound in there are locals, not module attributes.

    Imported names count: ``from datetime import date`` in src/asof.py genuinely
    makes ``src.asof.date`` importable, so treating it as absent would be wrong.

    The bool is False when the module does ``from x import *``, which can bind
    arbitrary names this parser cannot see. Callers skip those modules rather
    than risk a false failure — a guard that cries wolf gets deleted.
    """
    names: set[str] = set()
    resolvable = True

    def visit(body: list[ast.stmt]) -> None:
        nonlocal resolvable
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        resolvable = False
                    else:
                        names.add(alias.asname or alias.name)
            elif isinstance(node, ast.If):
                visit(node.body)
                visit(node.orelse)
            elif isinstance(node, ast.Try):
                visit(node.body)
                visit(node.orelse)
                visit(node.finalbody)
                for handler in node.handlers:
                    visit(handler.body)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                visit(node.body)

    visit(tree.body)
    return names, resolvable


def _src_imports(tree: ast.Module):
    """Yield (lineno, module, [names]) for every ``from src... import`` in a file.

    Uses ast.walk so function-level imports are covered too — src/asof.py defers
    its ``from src.holdings import ...`` inside functions specifically to dodge a
    module-load DB import, and those raise at call time rather than page load.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:                       # relative import — not used by pages
            continue
        if not node.module or node.module.split(".")[0] != "src":
            continue
        wanted = [a.name for a in node.names if a.name != "*"]
        if wanted:
            yield node.lineno, node.module, wanted


@pytest.mark.parametrize("page", _page_files(), ids=lambda p: p.name)
def test_page_src_imports_resolve(page: Path) -> None:
    """Every name a page imports from ``src`` must exist in that module."""
    tree = ast.parse(page.read_text(encoding="utf-8"), filename=str(page))
    failures: list[str] = []

    for lineno, module, wanted in _src_imports(tree):
        target = _module_path(module)
        if target is None:
            failures.append(
                f"{page.name}:{lineno} imports from '{module}', which does not exist"
            )
            continue

        available, resolvable = _toplevel_names(
            ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        )
        if not resolvable:
            continue                          # star-import module — cannot judge

        # A submodule is importable by name even though it is not bound in the
        # parent's body: `from src.ingestion import fidelity`.
        missing = [
            name for name in wanted
            if name not in available and _module_path(f"{module}.{name}") is None
        ]
        if missing:
            failures.append(
                f"{page.name}:{lineno} imports {missing} from '{module}', "
                f"which does not define {'them' if len(missing) > 1 else 'it'}"
            )

    assert not failures, (
        "Unresolvable src import(s) — this page raises ImportError the moment a "
        "user navigates to it:\n  " + "\n  ".join(failures)
    )


def test_guard_covers_every_page_on_disk() -> None:
    """The glob must actually find the pages — a silently empty parametrization passes.

    Without this, deleting the pages/ directory or breaking the glob would turn
    the guard above into zero tests that all pass, which is the failure mode this
    whole file exists to prevent.
    """
    found = {p.name for p in _page_files()}
    on_disk = {p.name for p in _ROOT.glob("pages/*.py")} | {"app.py"}
    assert found == on_disk, f"Guard missed page files: {on_disk - found}"
    assert len(found) >= 10, f"Suspiciously few pages found ({len(found)}) — glob broken?"
