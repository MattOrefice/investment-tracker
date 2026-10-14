"""The PDF draws its charts (#556).

plotly 7 refuses kaleido 0.2.1, and the PDF then prints each chart as "... chart not
available". Under `plotly>=5.24.0`, CI installed 7.1.0 from September 23, 2026, and no
test saw it: every other test stubs the chart render, and the two that build a real PDF
do not read its charts. requirements.txt now pins plotly==6.7.0.

The chart test renders one chart, the allocation chart, through the PDF's own path: the
holdings section builds the figure and calls the chart render, and the report's
template prints the image or the placeholder.
"""
import base64
import re
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from src import reports
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent

# kaleido 0.2.1 returns no image on the owner's Windows machine: each render times out
# at _render_chart_to_png's 25 s wait (measured October 14, 2026), the hang its
# docstring records. CI and Streamlit Cloud run Linux, where it renders.
KALEIDO_RETURNS_NO_IMAGE_HERE = sys.platform == "win32"


def _pinned(name: str) -> str:
    """The version requirements.txt pins ``name`` to with ==, or "" if it pins none."""
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(rf"{re.escape(name)}==(\S+)", line.strip())
        if m:
            return m.group(1)
    return ""


def test_plotly_is_pinned_exactly_to_the_version_the_audit_ran():
    """6.7.0, the version every local run behind the audit used, beside the kaleido it
    accepts. 6.9.0 is the newest that accepts kaleido 0.2.1; 7.0.0 refuses it."""
    assert _pinned("plotly") == "6.7.0"
    assert _pinned("kaleido") == "0.2.1"


def test_the_installed_plotly_is_the_pinned_one():
    """CI installs requirements.txt, so there this reads the pin back; on a local machine
    it fails when the plotly installed is not the one CI and Cloud run."""
    assert version("plotly") == _pinned("plotly")


@pytest.mark.skipif(KALEIDO_RETURNS_NO_IMAGE_HERE,
                    reason="kaleido 0.2.1 returns no image on Windows here: each render "
                           "times out at 25 s. It runs in CI, on Linux, as Cloud does.")
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
        f"{version('kaleido')} returned no image (#556)")
    png = re.search(r'src="data:image/png;base64,([^"]+)"', html)
    assert png, "the template printed neither the chart nor its placeholder"
    assert base64.b64decode(png.group(1)).startswith(b"\x89PNG\r\n\x1a\n"), (
        "the chart the template printed is not a PNG")
