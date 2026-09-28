"""The ETF metadata's pending line names the date that disqualifies the file (#463).

A quarter lock takes the fact-sheet file only when every as_of falls inside the quarter
(#389). The lock recorded the file's OLDEST date as pending whatever the reason, and the
line printed it: right for a file too old, wrong for one too new, where it named a date
inside the quarter. Since #455 the file carries durations dated after Q2, so a fresh Q2
lock on it is too new. Now the lock records the newest date when the file is too new and
the oldest when it is too old (and when it is both), and the line says which way.
Frozen book, offline, Q2's lock removed, today pinned to July 1.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

HEAD = "Pending: this style box locks when the ETF fact-sheet data covers the quarter. "


@pytest.fixture
def q2_unlocked(tmp_path, monkeypatch):
    path = point_at_frozen_book(monkeypatch, tmp_path)
    con = sqlite3.connect(path)
    with con:
        assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                           ).rowcount == 1
    con.close()
    pin_today(monkeypatch, date(2026, 7, 1))
    yield path
    unpin_leftovers()


def _dated(tmp_path, monkeypatch, *stamps):
    """The committed file with its entries' as_of set to ``stamps`` in turn."""
    from src import style_box
    meta = json.loads(Path(style_box._META_PATH).read_text())
    keys = [k for k in meta if not k.startswith("_")]
    for i, k in enumerate(keys):
        meta[k]["as_of"] = stamps[i % len(stamps)]
    out = tmp_path / "meta.json"
    out.write_text(json.dumps(meta))
    monkeypatch.setattr(style_box, "_META_PATH", out)


def _lock_and_line():
    from src import reports
    from src.cache import capture_quarter_snapshot
    from src.input_lock import ETF_METADATA
    snap, _ = capture_quarter_snapshot("2026Q2")
    return (snap.inputs_pending.get(ETF_METADATA),
            reports._pending_note(snap, "positioning", subject="style box"))


def test_a_file_too_new_names_its_newest_date(q2_unlocked):
    """The committed file: its durations are dated September 25, after Q2 ended."""
    from src.style_box import _META_PATH
    stamps = sorted(v["as_of"] for v in json.loads(Path(_META_PATH).read_text()).values()
                    if isinstance(v, dict) and v.get("as_of"))
    assert stamps[0] >= "2026-04-01" and stamps[-1] == "2026-09-25", "premise: too new only"
    recorded, line = _lock_and_line()
    assert recorded == "2026-09-25"
    assert line == HEAD + ("The newest ETF fact-sheet data on file is dated September 25, "
                           "2026, after the quarter ended June 30, 2026.")


def test_a_file_too_old_names_its_oldest_date(q2_unlocked, tmp_path, monkeypatch):
    _dated(tmp_path, monkeypatch, "2026-01-15", "2026-05-15")
    recorded, line = _lock_and_line()
    assert recorded == "2026-01-15"
    assert line == HEAD + ("The oldest ETF fact-sheet data on file is dated January 15, "
                           "2026, before the quarter began April 1, 2026.")


def test_a_file_both_too_old_and_too_new_names_its_oldest(q2_unlocked, tmp_path, monkeypatch):
    _dated(tmp_path, monkeypatch, "2026-01-15", "2026-07-15")
    recorded, line = _lock_and_line()
    assert recorded == "2026-01-15"
    assert "The oldest ETF fact-sheet data on file is dated January 15, 2026" in line


def test_a_file_inside_the_quarter_waits_on_nothing(q2_unlocked, tmp_path, monkeypatch):
    """The contrast: dated inside Q2, the lock takes the file and no line is printed."""
    _dated(tmp_path, monkeypatch, "2026-04-15", "2026-06-30")
    recorded, line = _lock_and_line()
    assert recorded is None and line is None
