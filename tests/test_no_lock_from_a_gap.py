"""No quarter lock is taken from prices with a gap in them (#491).

The demo book lacked 32 closes between stored closes of ten funds, and Q2 2026's lock
froze them as nulls: nothing between the book and the lock looked inside a history. Two
things now do, and a close deleted from a scratch copy fails both:

  * tools/check_price_histories.py, the close-out's check, which the procedure runs
    before the lock (its own tests are in tests/test_price_history_ends.py);
  * cache.capture_quarter_snapshot, which refuses with LockCoverageError and persists
    nothing when a ticker it covers has no close on a date between its own first and
    last close.

On copies of the frozen book, offline.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import (
    pin_today,
    point_at_frozen_book,
    q2_2026_before_its_closes_were_filled,
    unpin_leftovers,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A frozen-book copy, offline, on the day after Q2 2026 ended, with no Q2 lock."""
    pin_today(monkeypatch, date(2026, 7, 1))
    path = point_at_frozen_book(monkeypatch, tmp_path)
    con = sqlite3.connect(path)
    try:
        with con:
            assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                               ).rowcount == 1
    finally:
        con.close()
    yield path
    unpin_leftovers()


def _delete(path, ticker, day) -> None:
    con = sqlite3.connect(path)
    try:
        with con:
            assert con.execute("DELETE FROM prices WHERE ticker = ? AND price_date = ?",
                               (ticker, day)).rowcount == 1
    finally:
        con.close()


def _locked(path) -> "list[str]":
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT quarter_id FROM quarter_snapshots "
                                          "ORDER BY quarter_id")]
    finally:
        con.close()


def test_the_book_as_committed_locks(book):
    """The control: the same capture, on the book with nothing deleted, is taken."""
    from src.cache import capture_quarter_snapshot
    from tools.check_price_histories import history_gaps
    assert history_gaps(book) == []
    capture_quarter_snapshot("2026Q2")
    assert "2026Q2" in _locked(book)


@pytest.mark.parametrize("ticker, day", [
    ("VOO", "2026-05-28"),          # inside the quarter, one of the closes #491 filled
    ("SCHP", "2025-11-12"),         # long before it: the frame runs from inception
    ("SPY", "2026-06-15"),          # a benchmark the lock covers
])
def test_a_deleted_close_fails_the_check_and_the_capture(book, ticker, day):
    from src.cache import LockCoverageError, capture_quarter_snapshot
    from tools.check_price_histories import MISSING, history_gaps
    _delete(book, ticker, day)
    before = _locked(book)

    found = history_gaps(book)
    if ticker == "SPY":
        # The reference's own gap shows against the calendar, and as a close every other
        # ticker holds on a date it lacks.
        assert ("SPY", day, MISSING) in found and len(found) > 1
    else:
        assert found == [(ticker, day, MISSING)]

    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value) == (
        f"Cannot lock 2026Q2: the price data has a gap inside a ticker's history, so a "
        f"lock now would freeze the gap. Nothing was locked. No close on {day} for "
        f"{ticker}.")
    assert _locked(book) == before and "2026Q2" not in before, "nothing was persisted"


def test_the_refusal_names_each_set_of_tickers_with_its_dates(book):
    from src.cache import LockCoverageError, capture_quarter_snapshot
    for ticker in ("IEMG", "VNQ"):
        for day in ("2026-05-12", "2026-05-13"):
            _delete(book, ticker, day)
    _delete(book, "VOO", "2026-05-28")
    with pytest.raises(LockCoverageError) as err:
        capture_quarter_snapshot("2026Q2")
    assert str(err.value).endswith(
        "No close on 2026-05-12, 2026-05-13 for IEMG, VNQ; 2026-05-28 for VOO.")


def test_a_gap_after_the_quarter_does_not_refuse_its_lock(book):
    """The lock holds prices through the quarter's last day. A gap after it is outside
    the frame, and the close-out's check still names it."""
    from src.cache import capture_quarter_snapshot
    from tools.check_price_histories import MISSING, history_gaps
    _delete(book, "VOO", "2026-07-08")
    assert history_gaps(book) == [("VOO", "2026-07-08", MISSING)]
    capture_quarter_snapshot("2026Q2")
    assert "2026Q2" in _locked(book)


def test_the_rule_reads_each_tickers_own_span():
    from src.cache import _interior_gaps
    days = [date(2026, 9, d) for d in (28, 29, 30)]
    nan = float("nan")
    frame = pd.DataFrame({"FULL": [1.0, 2.0, 3.0], "LATER": [nan, 2.0, 3.0],
                          "STOPPED": [1.0, 2.0, nan], "NEVER": [nan, nan, nan]}, index=days)
    assert _interior_gaps(frame) == []
    frame["HOLE"] = [1.0, nan, 3.0]
    frame["HOLE2"] = [1.0, nan, 3.0]
    assert _interior_gaps(frame) == ["2026-09-29 for HOLE, HOLE2"]


def _committed_q2_frame(undo: bool = False) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{(ROOT / 'data' / 'demo.db').as_posix()}?mode=ro", uri=True)
    try:
        (text,), = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                               "quarter_id = '2026Q2'").fetchall()
    finally:
        con.close()
    payload = json.loads(q2_2026_before_its_closes_were_filled(text) if undo else text)["close"]
    return pd.DataFrame(payload["data"], columns=payload["columns"],
                        index=pd.to_datetime(payload["index"]).date)


def test_the_rule_passes_the_committed_lock_and_names_the_32_in_the_lock_as_it_was():
    """demo.db's Q2 2026 frames run from 2020, before some of its funds listed, so they
    hold leading nulls and no gap. With #491's restatement undone, the same rule names
    the 32 closes the lock froze missing."""
    from src.cache import _interior_gaps
    frame = _committed_q2_frame()
    assert int(frame.isna().sum().sum()) > 0, "premise: leading nulls"
    assert _interior_gaps(frame) == []
    assert _interior_gaps(_committed_q2_frame(undo=True)) == [
        "2026-05-12, 2026-05-13, 2026-05-14, 2026-05-15 for IEMG, PDBC, VNQ",
        "2026-05-28, 2026-05-29 for AVUV, IEMG, PDBC, SCHP, SPHQ, VEA, VGIT, VNQ, VOO, VTV"]


def test_the_close_out_runs_the_check_before_the_lock():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = text.split("\n## Quarterly close-out\n", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(section.split())
    check, lock = "python tools/check_price_histories.py", "python tools/lock_demo_quarters.py"
    assert flat.count(check) >= 2, "after the advance, and again before the lock"
    assert flat.rindex(check) < flat.index(lock) < flat.index("Not yet supported")
    step = flat[flat.rindex("Demo only, to commit the quarter's lock"):]
    assert check in step and step.index(check) < step.index(lock)
    assert "LockCoverageError" in flat and "gap" in flat
