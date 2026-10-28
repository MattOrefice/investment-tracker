"""The PDF's chart export waits 60 s for the first export a process makes and 25 s for
every later one (decided by the owner on October 28, 2026, #698).

On GitHub's runner, while every export waited 25 s, each of three CI runs' slowest
export, 9.03 to 12.17 s, was the first its worker process made. A hung export is still
abandoned when its wait ends, and its chart prints the placeholder.

Each test here stands in for pio.to_image, so no Chrome runs, and sets the process's
first-export flag itself. monkeypatch puts the flag back, so the first real export on
the worker still waits 60 s.

The suite times every real export and prints the times at the end of the run
(tests/conftest.py), so a CI run shows how near its exports came to their waits. The
last tests here check what it times and what it prints.
"""
import re
import threading

import plotly.graph_objects as go
import pytest

from src import reports
from tests import conftest
from tests.conftest import (ABANDONED_EXPORT, chart_export_lines, join_for_cleanup,
                            pin_today, point_at_frozen_book, thread_target,
                            unpin_leftovers)

_PNG = b"\x89PNG\r\n\x1a\n"


def _fig() -> go.Figure:
    return go.Figure(go.Scatter(x=[1, 2, 3], y=[1, 3, 2]))


class _Export:
    """Stands in for pio.to_image. Each call waits until the PDF path is joining its
    _render thread, so the join can read that thread's target, then returns a PNG. The
    first ``hangs`` calls do not return until released."""

    def __init__(self, hangs: int = 0):
        self.waits: "list[float]" = []
        self.joining = threading.Event()
        self.release = threading.Event()
        self.hangs = hangs

    def to_image(self, *args, **kwargs) -> bytes:
        self.joining.wait(5)
        self.joining.clear()
        if self.hangs:
            self.hangs -= 1
            self.release.wait(30)
        return _PNG


def _joins_recorded(mp: pytest.MonkeyPatch, export: _Export, cut: int = 0):
    """Record the wait of each join on the export's _render thread. The first ``cut``
    joins are cut to 0.2 s."""
    join = threading.Thread.join

    def recording(thread, timeout=None):
        if thread_target(thread) == ABANDONED_EXPORT:
            export.waits.append(timeout)
            export.joining.set()
            if len(export.waits) <= cut:
                timeout = 0.2
        return join(thread, timeout)

    mp.setattr(threading.Thread, "join", recording)


def test_the_first_export_a_process_makes_waits_60_s_and_every_later_one_25_s(monkeypatch):
    monkeypatch.setattr(reports, "_first_export_made", False)
    export = _Export()
    monkeypatch.setattr(reports.pio, "to_image", export.to_image)
    with pytest.MonkeyPatch.context() as mp:
        _joins_recorded(mp, export)
        pngs = [reports._render_chart_to_png(_fig()) for _ in range(3)]
    assert pngs == [_PNG] * 3
    assert export.waits == [60, 25, 25]


def test_a_hung_first_export_prints_the_placeholder_and_the_next_still_waits_25_s(
        monkeypatch, tmp_path):
    """The hung export is abandoned when its wait ends, cut here to 0.2 s, and the
    allocation chart prints its placeholder through the PDF's own path. The first export
    was made, however it ended, so the next one waits 25 s."""
    monkeypatch.setattr(reports, "_first_export_made", False)
    export = _Export(hangs=1)
    monkeypatch.setattr(reports.pio, "to_image", export.to_image)
    before = set(threading.enumerate())
    try:
        with pytest.MonkeyPatch.context() as mp:
            pin_today(mp)
            point_at_frozen_book(mp, tmp_path)
            _joins_recorded(mp, export, cut=1)
            hold = reports._build_holdings_section("2026-06-30")
            abandoned = [t for t in threading.enumerate()
                         if t not in before and thread_target(t) == ABANDONED_EXPORT]
            later = reports._render_chart_to_png(_fig())
        unpin_leftovers()
        assert hold["rows"], "premise: the frozen book holds positions on 2026-06-30"
        assert len(abandoned) == 1, "premise: the hung export was abandoned in flight"
        assert export.waits == [60, 25]
        assert hold["chart_b64"] is None and later == _PNG

        template = (reports.TEMPLATES_DIR / "quarterly_report.html").read_text(
            encoding="utf-8")
        block = re.search(r"\{% if hold\.chart_b64 %\}.*?\{% endif %\}", template, re.S)
        html = reports._make_report_env().from_string(block.group(0)).render(hold=hold)
        assert "Allocation chart not available" in html
        assert "data:image/png" not in html
    finally:
        export.release.set()
        join_for_cleanup([t for t in threading.enumerate() if t not in before])


# ── the suite's export times (tests/conftest.py) ─────────────────────────────

def test_the_suite_times_an_export_through_plotly_and_not_a_stubbed_one(monkeypatch,
                                                                        request):
    """An export is timed when pio.to_image is plotly's own, and recorded as the
    process's first when it is the one that waits 60 s. One a test stubs is not."""
    timed = reports._render_chart_to_png
    assert timed.__globals__ is vars(conftest), "premise: the suite wraps the render"
    monkeypatch.setattr(conftest, "_chart_exports", [])

    def stub(*args, **kwargs):
        return _PNG

    monkeypatch.setattr(reports, "_first_export_made", False)
    monkeypatch.setattr(reports.pio, "to_image", stub)
    reports._render_chart_to_png(_fig())
    assert conftest._chart_exports == []

    stub.__module__ = "plotly.io._kaleido"
    monkeypatch.setattr(reports, "_first_export_made", False)
    reports._render_chart_to_png(_fig())
    reports._render_chart_to_png(_fig())
    first, later = conftest._chart_exports
    assert (first["test"], first["first"], first["wait"], first["image"]) == (
        request.node.nodeid, True, 60, True)
    assert (later["first"], later["wait"]) == (False, 25)


def test_the_summary_prints_each_processs_first_export_its_slowest_and_any_without_an_image():
    def export(test, seconds, first=False, image=True):
        return {"test": test, "first": first, "seconds": seconds,
                "wait": 60 if first else 25, "image": image}

    lines = chart_export_lines({
        "gw2": [export("t::a", 12.17, first=True), export("t::a", 3.26)],
        "gw0": [export("t::b", 1.7, first=True), export("t::c", 6.19),
                export("t::d", 0.2, image=False)],
        "gw1": [],
    })
    assert lines == [
        "gw0: 3 export(s); first 1.70 s of its 60 s; slowest 6.19 s of its 25 s",
        "gw2: 2 export(s); first 12.17 s of its 60 s; slowest 12.17 s of its 60 s",
        "slowest of all: 12.17 s of its 60 s, gw2's first export, in t::a",
        "returned no image: gw0, 0.20 s of its 25 s, in t::d",
    ]
    assert chart_export_lines({"gw0": [], "gw1": []}) == []
    assert chart_export_lines({"this process": [export("t::a", 2.0)]})[0] == (
        "this process: 1 export(s); first none timed; slowest 2.00 s of its 25 s")
