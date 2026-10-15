"""The PDF draws its charts (#556, #567).

plotly 7 refused kaleido 0.2.1, and the PDF then printed each chart as "... chart not
available". Under `plotly>=5.24.0`, CI installed 7.1.0 from September 23, 2026, and no
test saw it: every other test stubs the chart render, and the two that build a real PDF
do not read its charts. requirements.txt pinned plotly==6.7.0 (#556).

kaleido is pinned at 1.5.0 since #567 (decided by the owner on October 15, 2026).
kaleido 1.x bundles no browser; it drives an installed Chrome or Chromium:
- on Cloud, Debian's chromium package, listed in packages.txt;
- in CI, the Chrome GitHub's runner already has, with no apt step (the last one hung);
- on the owner's Windows machine, the installed Chrome.
kaleido 0.2.1 returned no image there: each render timed out at 25 s. kaleido 1.5.0
renders there, so the chart test runs on every platform now.

The chart test renders one chart, the allocation chart, through the PDF's own path: the
holdings section builds the figure and calls the chart render, and the report's
template prints the image or the placeholder.
"""
import base64
import re
from importlib.metadata import version
from pathlib import Path

import pytest

from src import reports
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent


def _pinned(name: str) -> str:
    """The version requirements.txt pins ``name`` to with ==, or "" if it pins none."""
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(rf"{re.escape(name)}==(\S+)", line.strip())
        if m:
            return m.group(1)
    return ""


def test_plotly_and_kaleido_are_pinned_exactly():
    """plotly 6.7.0, the version every local run behind the audit used; kaleido 1.5.0,
    which plotly 6.7.0's own kaleido extra accepts (it asks for 1.1.0 or later)."""
    assert _pinned("plotly") == "6.7.0"
    assert _pinned("kaleido") == "1.5.0"


def test_the_installed_plotly_and_kaleido_are_the_pinned_ones():
    """CI installs requirements.txt, so there this reads the pins back; on a local machine
    it fails when what is installed is not what CI and Cloud run."""
    assert version("plotly") == _pinned("plotly")
    assert version("kaleido") == _pinned("kaleido")


def test_cloud_gets_chromium_from_packages_txt_and_ci_installs_no_apt_package():
    """Where each environment's browser comes from (#567). Cloud installs packages.txt's
    apt packages; CI has no apt step and uses the runner's own Chrome."""
    packages = {ln.strip() for ln in (ROOT / "packages.txt").read_text(encoding="utf-8")
                .splitlines() if ln.strip() and not ln.strip().startswith("#")}
    assert "chromium" in packages, packages
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = [ln for ln in ci.splitlines() if not ln.strip().startswith("#")]
    assert not [ln for ln in steps if "apt-get" in ln or re.search(r"\bapt\b", ln)], (
        "CI installs no apt package: its last apt step hung (ci.yml's REMOVED note)")


def test_one_chart_renders_through_the_pdfs_path(tmp_path):
    with pytest.MonkeyPatch.context() as mp:
        pin_today(mp)
        point_at_frozen_book(mp, tmp_path)
        hold = reports._build_holdings_section("2026-06-30")
    unpin_leftovers()
    assert hold["rows"], "premise: the frozen book holds positions on 2026-06-30"

    template = (reports.TEMPLATES_DIR / "quarterly_report.html").read_text(encoding="utf-8")
    block = re.search(r"\{% if hold\.chart_b64 %\}.*?\{% endif %\}", template, re.S)
    assert block and "chart not available" in block.group(0), (
        "premise: the template prints the allocation chart or its placeholder here")
    html = reports._make_report_env().from_string(block.group(0)).render(hold=hold)

    printed = re.sub(r"<[^>]+>", "", html).strip()
    assert "chart not available" not in html, (
        f"the PDF would print {printed!r}: plotly {version('plotly')} with kaleido "
        f"{version('kaleido')} returned no image (#556, #567)")
    png = re.search(r'src="data:image/png;base64,([^"]+)"', html)
    assert png, "the template printed neither the chart nor its placeholder"
    assert base64.b64decode(png.group(1)).startswith(b"\x89PNG\r\n\x1a\n"), (
        "the chart the template printed is not a PNG")
