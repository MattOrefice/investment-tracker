"""Four locked quarters disclose that their style box uses later fact sheets (#388).

The demo's locks for Q2 2025 through Q1 2026 were taken before #389's gate, holding
data/etf_metadata.json as it stood: every entry stamped April 15, 2026, after those
quarters ended. Decided: leave and disclose. Their figures do not move; each report's
style box gains one line saying when its fact sheets are dated. Q2 2026's fact sheets
fall inside its quarter, so it gains nothing, and #389's gate keeps a lock from Q3 2026
from taking a later-dated file at all.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.conftest import pin_today, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
LINE = "The style box uses ETF fact sheets dated April 15, 2026, after this quarter ended."
EARLIER = {"2025Q2": ("2025-03-31", "2025-06-30"), "2025Q3": ("2025-06-30", "2025-09-30"),
           "2025Q4": ("2025-09-30", "2025-12-31"), "2026Q1": ("2025-12-31", "2026-03-31")}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    import socket
    import src.db as db
    import src.prices as prices
    from src import reports
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)

    def _offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    # A stand-in image, so the style box block renders (the line sits with the chart).
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: b"PNG")
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch)
    yield copy
    unpin_leftovers()


def test_the_four_earlier_locks_carry_the_line_and_q2_2026_does_not(demo):
    from src.cache import fact_sheet_dated_note, get_quarter_snapshot
    for q in EARLIER:
        assert fact_sheet_dated_note(get_quarter_snapshot(q)[0]) == LINE, q
    assert fact_sheet_dated_note(get_quarter_snapshot("2026Q2")[0]) is None


@pytest.mark.parametrize("quarter", ["2025Q2", "2026Q1"])
def test_the_line_renders_with_the_style_box(demo, quarter):
    from markupsafe import escape
    from src import reports
    html = reports.generate_quarterly_report_bytes(*EARLIER[quarter], is_demo=True).decode("utf-8")
    assert 'alt="Equity Style Profile"' in html
    assert str(escape(LINE)) in html


def test_q2_2026_renders_no_line(demo):
    from src import reports
    html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                   is_demo=True).decode("utf-8")
    assert 'alt="Equity Style Profile"' in html
    assert "after this quarter ended" not in html
    # Byte-identical, not merely line-free: an empty {% if %} block must not leave a
    # whitespace line behind the caption (#388's harness showed one).
    from markupsafe import escape
    from src.style_box import STYLE_BOX_CAPTION
    assert f"{escape(STYLE_BOX_CAPTION)}</p>\n  </div>" in html


def test_the_line_names_a_range_when_the_dates_differ():
    from src.cache import SnapshotFrames, fact_sheet_dated_note
    from src.input_lock import ETF_METADATA
    import pandas as pd
    snap = SnapshotFrames(adj_close=pd.DataFrame(), quarter_end="2025-06-30", inputs={
        ETF_METADATA: {"_comment": "x", "A": {"as_of": "2026-04-15"},
                       "B": {"as_of": "2025-07-31"}, "C": {"as_of": "2025-06-15"}}})
    assert fact_sheet_dated_note(snap) == ("The style box uses ETF fact sheets dated "
                                           "July 31, 2025 to April 15, 2026, after this "
                                           "quarter ended.")


def test_the_quarterly_close_out_names_the_fact_sheets():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = text.split("## Quarterly close-out", 1)[1].split("\n## ", 1)[0]
    assert "data/etf_metadata.json" in section and "`as_of`" in section
    assert "weighted-average" in section and "median" in section
