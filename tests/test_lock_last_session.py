"""A quarter locks on its last NYSE session's close, and only after New York's quarter
has ended (#454).

The demo's server clock is UTC, so date.today() passed a quarter's last day at 8 PM ET,
and the lock's coverage gate allowed prices ending up to five days short, meant for a
quarter ending on a weekend or holiday. Together they let a lock taken on a weekday
quarter's last evening, before its close was stored, freeze the quarter on the
previous session's: measured on Q2 2026, SPY 741.00 against 746.77. Now:

  * every ticker the lock covers needs the close of the quarter's last NYSE session,
    from the static holiday table (#412); a weekend or holiday end needs the session
    before it, and no earlier one;
  * HYG, the factor section's input from the price layer, the same;
  * is_quarter_complete reads New York's date.

And every committed lock is checked against the rule.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]


def _drop(path, sql, *args):
    con = sqlite3.connect(path)
    with con:
        n = con.execute(sql, args).rowcount
    con.close()
    return n


@pytest.fixture
def q2_unlocked(tmp_path, monkeypatch):
    """The frozen book on July 1 2026 (after midnight ET), with its Q2 lock removed."""
    path = point_at_frozen_book(monkeypatch, tmp_path)
    assert _drop(path, "DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'") == 1
    pin_today(monkeypatch, date(2026, 7, 1))
    yield path
    unpin_leftovers()


def test_without_the_last_sessions_close_the_price_sections_stay_pending(q2_unlocked):
    """After midnight ET on July 1, SPY's June 30 close is not stored: Q2 does not
    lock, the tiles' figures refuse the same way, and nothing is persisted."""
    from src import reports
    from src.cache import LockCoverageError, capture_quarter_snapshot, get_quarter_snapshot
    assert _drop(q2_unlocked, "DELETE FROM prices WHERE ticker = 'SPY' AND "
                              "price_date = '2026-06-30'") == 1
    with pytest.raises(LockCoverageError) as exc:
        capture_quarter_snapshot("2026Q2")
    assert "prices end 2026-06-29, missing 2026-06-30 to 2026-06-30 for SPY" in str(exc.value)
    with pytest.raises(LockCoverageError):
        reports.locked_quarter_figures("Q2 2026", "2026-03-31", "2026-06-30")
    assert get_quarter_snapshot("2026Q2") == (None, None)


def test_with_the_last_sessions_close_the_quarter_locks(q2_unlocked):
    """The contrast: the same book with every close locks, through June 30."""
    from src.cache import capture_quarter_snapshot
    snap, _ = capture_quarter_snapshot("2026Q2")
    assert max(snap.adj_close.index) == date(2026, 6, 30)


def test_hyg_without_the_last_sessions_close_leaves_the_factor_section_pending(q2_unlocked):
    """HYG is read from the price layer for the FI regression: short of June 30's close,
    it waits, and the rest of the lock is taken."""
    from src.cache import capture_quarter_snapshot
    from src.input_lock import HYG
    assert _drop(q2_unlocked, "DELETE FROM prices WHERE ticker = 'HYG' AND "
                              "price_date = '2026-06-30'") == 1
    snap, _ = capture_quarter_snapshot("2026Q2")
    assert snap.inputs_pending.get(HYG) == "2026-06-29", snap.inputs_pending
    assert max(snap.adj_close.index) == date(2026, 6, 30)


def _utc_server(mp, instant):
    """A UTC machine at ``instant``: date.today() is the UTC date; today_et() reads
    the instant."""
    import src.asof as asof

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    pin_today(mp, instant.astimezone(timezone.utc).date(), pin_et=False)
    mp.setattr(asof, "datetime", _Clock)


@pytest.mark.parametrize("instant, complete", [
    (datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc), False),    # 9 PM ET June 30
    (datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc), True),    # 12:30 AM ET July 1
], ids=["9 PM ET on the last day", "after midnight ET"])
def test_the_quarter_is_complete_only_after_new_yorks_quarter_ends(instant, complete, monkeypatch):
    from src.cache import is_quarter_complete
    try:
        with monkeypatch.context() as mp:
            _utc_server(mp, instant)
            import datetime as _dt
            assert _dt.date.today() == date(2026, 7, 1), "premise: the UTC date is July 1"
            assert is_quarter_complete("2026Q2") is complete
    finally:
        unpin_leftovers()


def test_the_last_session_comes_from_the_holiday_table():
    from src.cache import last_session_on_or_before
    assert last_session_on_or_before(date(2026, 9, 30)) == date(2026, 9, 30)   # Wednesday
    assert last_session_on_or_before(date(2028, 9, 30)) == date(2028, 9, 29)   # Saturday
    assert last_session_on_or_before(date(2026, 7, 3)) == date(2026, 7, 2)     # holiday


def _ends(payload):
    last = {}
    for i, row in zip(payload["index"], payload["data"]):
        for c, v in zip(payload["columns"], row):
            if v is not None:
                last[c] = max(last.get(c, ""), str(i)[:10])
    return last


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_committed_lock_reaches_its_last_session(book):
    from src.cache import last_session_on_or_before
    con = sqlite3.connect(f"file:{book.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT quarter_id, snapshot_date, snapshot_data FROM "
                           "quarter_snapshots").fetchall()
    finally:
        con.close()
    assert len(rows) == 5
    for qid, qend, blob in rows:
        b = json.loads(blob)
        need = last_session_on_or_before(date.fromisoformat(qend)).isoformat()
        for basis in ("adj_close", "close"):
            short = {t: d for t, d in _ends(b[basis]).items() if d < need}
            assert short == {}, (book.name, qid, basis, short)
        assert b["inputs_through"]["hyg"] >= need, (book.name, qid)
