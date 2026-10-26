"""Every direct dependency is pinned exactly, and the pinned version is the one installed.

The owner decided on October 22, 2026 to pin every direct dependency. Ranges broke
things twice: plotly 7.1.0 left the PDF printing "chart not available" for every chart
(#556), and pandas 3.1.0 turned CI red the day it was released (#678). And under a
range the local harness kept numpy 2.2.6 while CI installed 2.4.6, so the two tested
different stacks. Transitive dependencies stay unpinned.

The first test reads the files: every requirement line in requirements.txt and
requirements-ci.txt is name==version, with an environment marker allowed; -r lines and
comments are skipped. The second reads the environment: every line whose marker applies
is installed at exactly that version, so a stale local environment fails here instead
of quietly testing another stack. CI installs requirements-ci.txt, so there it reads the
pins back.

The third reads the code: every third-party module imported anywhere in app.py, src/,
pages/, tools/ and tests/ comes from a distribution with a pinned line in one of the two
files. Without it a direct dependency could arrive unpinned through another package, as
packaging and markupsafe did until #683: tests import both, and neither had a line.
A module is matched to its distributions through importlib.metadata's
packages_distributions(), comparing normalized names, and a module not installed in the
running environment (xhtml2pdf off Windows) matches by its own name. The standard
library, the repo's own packages and a file's sibling modules (tests import conftest
and each other by bare name) are not third-party. scripts/ is not read: the one import
there that no file pins, playwright, is a developer tool that neither CI nor Streamlit
Cloud installs.
"""
import ast
import re
import sys
from importlib.metadata import PackageNotFoundError, packages_distributions, version
from pathlib import Path

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parent.parent
FILES = ("requirements.txt", "requirements-ci.txt")
SCANNED = ("app.py", "src", "pages", "tools", "tests")
PIN = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[0-9][A-Za-z0-9.+!-]*)"
                 r"(?:\s*;\s*(?P<marker>.+))?")


def _requirement_lines(name: str) -> "list[str]":
    """A requirements file's requirement lines: comments, blanks and -r lines dropped."""
    lines = [ln.split("#", 1)[0].strip()
             for ln in (ROOT / name).read_text(encoding="utf-8").splitlines()]
    return [ln for ln in lines if ln and not ln.startswith(("-r", "--requirement"))]


def _pins() -> "list[tuple[str, str, str, str | None]]":
    """(file, name, version, marker) for every line, which must already be name==version."""
    pins = []
    for name in FILES:
        for line in _requirement_lines(name):
            m = PIN.fullmatch(line)
            assert m, f"{name}: {line!r} is not name==version"
            pins.append((name, m["name"], m["version"], m["marker"]))
    return pins


def _normalized(name: str) -> str:
    """A distribution name as PEP 503 compares it: case-folded, runs of -_. as one -."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _local_names(directory: Path) -> "set[str]":
    """The modules and packages a bare import finds in ``directory``."""
    return ({p.stem for p in directory.glob("*.py")}
            | {p.name for p in directory.iterdir()
               if p.is_dir() and not p.name.startswith(".") and any(p.glob("*.py"))})


def _third_party_imports() -> "dict[str, set[str]]":
    """Top-level module -> the scanned files that import it, for every module that is
    neither standard library, nor the repo's own, nor a sibling of the importing file."""
    repo = _local_names(ROOT)
    files = [ROOT / "app.py"] + sorted(p for d in SCANNED[1:] for p in (ROOT / d).rglob("*.py"))
    found: "dict[str, set[str]]" = {}
    for path in files:
        local = repo | _local_names(path.parent)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".", 1)[0]
                if top not in sys.stdlib_module_names and top not in local:
                    found.setdefault(top, set()).add(path.relative_to(ROOT).as_posix())
    return found


def test_every_requirement_line_is_name_equals_version():
    bad = [f"{name}: {line!r}" for name in FILES for line in _requirement_lines(name)
           if not PIN.fullmatch(line)]
    assert not bad, "not pinned exactly:\n" + "\n".join(bad)
    assert _pins(), "no requirement lines read"


def test_every_pin_whose_marker_applies_is_the_installed_version():
    wrong = []
    for name, package, pinned, marker in _pins():
        if marker and not Marker(marker).evaluate():
            continue
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "not installed"
        if installed != pinned:
            wrong.append(f"{name}: {package}=={pinned}, installed {installed}")
    assert not wrong, ("installed versions differ from the pins; reinstall "
                       "requirements-ci.txt:\n" + "\n".join(wrong))


def test_every_third_party_import_comes_from_a_pinned_distribution():
    pinned = {_normalized(package) for _, package, _, _ in _pins()}
    installed = packages_distributions()
    imports = _third_party_imports()
    assert imports, "no third-party imports read"
    unpinned = []
    for module, files in sorted(imports.items()):
        distributions = installed.get(module) or [module]
        if not any(_normalized(d) in pinned for d in distributions):
            unpinned.append(f"{module} (distribution {', '.join(sorted(distributions))}), "
                            f"imported by {', '.join(sorted(files)[:3])}"
                            + (f" and {len(files) - 3} more" if len(files) > 3 else ""))
    assert not unpinned, ("imported but no pinned line in requirements.txt or "
                          "requirements-ci.txt:\n" + "\n".join(unpinned))
