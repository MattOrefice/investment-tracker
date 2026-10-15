"""CI runs the suite on four workers with pytest-xdist, pinned exactly (#544).

The owner decided this on October 14, 2026, after a trial on one commit gave all 3,368
tests the same outcome and message serially and in three runs with -n 4 on GitHub's
runner (run 38636521102). CI and the local CI-style harness install pytest-xdist from
requirements-ci.txt. Streamlit Cloud installs requirements.txt alone, so the deployed
app never carries it. A test whose outcome differs between a serial run and a parallel
one is a STOP.
"""
import re
import shlex
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
XDIST = re.compile(r"^pytest[-_]xdist\b", re.I)


def _requirements(name: str) -> "list[str]":
    """A requirements file's lines, each without its comment, blanks dropped."""
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return [ln.split("#", 1)[0].strip() for ln in lines if ln.split("#", 1)[0].strip()]


def _pin() -> str:
    """The version requirements-ci.txt pins pytest-xdist to."""
    lines = [ln for ln in _requirements("requirements-ci.txt") if XDIST.match(ln)]
    assert len(lines) == 1, lines
    found = re.fullmatch(r"pytest-xdist==(\d+\.\d+\.\d+)", lines[0])
    assert found, f"pytest-xdist is not pinned exactly: {lines[0]!r}"
    return found.group(1)


def _runs() -> "list[str]":
    """Every one-line `run:` command in ci.yml."""
    text = CI.read_text(encoding="utf-8")
    return [m.group(1).strip() for m in re.finditer(r"^\s*(?:-\s*)?run:\s*(.+)$", text, re.M)]


def test_the_ci_requirements_are_the_apps_plus_xdist_pinned_exactly():
    lines = _requirements("requirements-ci.txt")
    assert "-r requirements.txt" in lines
    assert _pin()
    assert [ln for ln in lines if ln != "-r requirements.txt" and not XDIST.match(ln)] == []


def test_cloud_never_installs_xdist():
    lines = _requirements("requirements.txt")
    assert [ln for ln in lines if XDIST.match(ln)] == []
    assert [ln for ln in lines if ln.startswith(("-r", "--requirement"))] == []


def test_ci_installs_the_ci_requirements_and_runs_the_suite_on_four_workers():
    runs = _runs()
    assert [r for r in runs if r.startswith("pip install")] == [
        "pip install -r requirements-ci.txt"]
    suite = [shlex.split(r) for r in runs if r.startswith("python -m pytest")]
    assert len(suite) == 1, suite
    args = suite[0]
    assert args.count("-n") == 1 and args[args.index("-n") + 1] == "4", args


def test_the_installed_xdist_is_the_pinned_one():
    try:
        installed = version("pytest-xdist")
    except PackageNotFoundError:
        pytest.fail("pytest-xdist is not installed: the CI-style harness installs "
                    "requirements-ci.txt (CLAUDE.md, 'Before every push')")
    assert installed == _pin(), (installed, _pin())
