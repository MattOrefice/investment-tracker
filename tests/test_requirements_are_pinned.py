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
"""
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parent.parent
FILES = ("requirements.txt", "requirements-ci.txt")
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
