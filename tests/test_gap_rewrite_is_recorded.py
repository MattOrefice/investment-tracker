"""The five locks #469 rewrote carry a record of it, and their covers say so (#478).

Each lock that existed before #469 carried one gap, 'VNQ+DBC', and its cover said one
series "could not be fetched when prices were locked". The gap was never one: the lock's
ticker list could not parse a parent row's legacy benchmark spec, and no price was
missing. #469 emptied each lock's gaps list. That took a disclosure off five covers and
left nothing in the lock to say it had been there.

Pinned here:
  * each of the five locks, in both books, carries the record, and no other lock does;
  * nothing else in any lock changed: each payload is main's, every byte, with the
    record appended;
  * each cover says so in one sentence, read from the record;
  * tools/restate_demo_gap_record.py produces exactly the committed locks from the
    ones before it, changes nothing on a second run, and refuses a lock that is not as
    it expects, leaving the book as it was;
  * #477's cover sentence names the callout that went with the style box;
  * the Performance page cannot show a quarter locked before the blended benchmarks'
    quarterly rule, so its tiles need no construction note.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from markupsafe import escape

from tests.conftest import (
    FROZEN_BOOK,
    GAP_RECORD_APPENDED,
    before_the_gap_record,
    before_the_policy_was_held,
    pin_today,
    point_at_frozen_book,
    record_the_policy,
    unpin_leftovers,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo.db"
BOOKS = [DEMO, FROZEN_BOOK]
QUARTERS = ["2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
RECORD = {"restated_on": "2026-09-28", "removed": ["VNQ+DBC"]}
APPENDED = GAP_RECORD_APPENDED
SENTENCE = ("Restated September 28, 2026 to correct an error. An earlier version of this "
            "report said one benchmark series could not be fetched when prices were locked; "
            "none was missing.")
STYLE_BOX = ("Restated September 28, 2026 to correct an error. The equity style box is "
             "withdrawn, and the Non-US Equity Sleeve callout that sat under it: the style "
             "box's fact-sheet figures had no recorded source.")

# Each lock's payload as the SHA-256 of its stored text, read off main's data/demo.db at
# cc9c31d, before the record (the frozen book's five are the same texts). 2026Q3 was
# locked after the rewrite and is not touched.
PAYLOAD_BEFORE = {
    "2025Q2": "328256b86e80e21d4f3f5ffdd1d1c68513a9e55a208cbc1e2ebdcea1f4bef88c",
    "2025Q3": "d5bfda797eafba9b23e066c5f38878fc65a6145c78b7643c5345f5fe3f1cf8c9",
    "2025Q4": "75df988319b18ea2840bac6d7557a8eb81e1e883cb5b3796a02c92fbf57605cd",
    "2026Q1": "4e2a6fc7a4197d6dc9e09f37fe5686e40d63af8f4ecae39e3864c8a80c8d69c4",
    "2026Q2": "d82e4e8fe7ae725ac68db6c0c44767308f2d0d636ee564bb58c9f94a49de8995",
    "2026Q3": "680b5a200c644a6f7bff9334e9cd2c2e7d3a2ec843c9da8a8831b7389a93850a",
}


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _rows(book) -> "dict[str, tuple]":
    con = _ro(book)
    try:
        return {q: (d, cap, text) for q, d, cap, text in con.execute(
            "SELECT quarter_id, snapshot_date, captured_at, snapshot_data FROM "
            "quarter_snapshots ORDER BY quarter_id")}
    finally:
        con.close()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tool():
    spec = importlib.util.spec_from_file_location(
        "restate_demo_gap_record", ROOT / "tools" / "restate_demo_gap_record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the committed locks ──────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_each_of_the_five_locks_carries_the_record_and_no_other_does(book):
    rows = _rows(book)
    assert set(QUARTERS) <= set(rows)
    for q in QUARTERS:
        blob = json.loads(rows[q][2])
        assert blob["gap_corrections"] == RECORD, (book.name, q)
        assert blob["gaps"] == [], (book.name, q)
        assert rows[q][1][:10] < RECORD["restated_on"], (
            book.name, q, "premise: the lock was taken before the rewrite")
    for q in sorted(set(rows) - set(QUARTERS)):
        assert "gap_corrections" not in json.loads(rows[q][2]), (book.name, q)
        assert rows[q][1][:10] >= RECORD["restated_on"], (book.name, q)


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_nothing_else_in_any_lock_changed(book):
    """Per lock: the stored text is main's, every byte, with the record appended as its
    last key. A lock taken since is main's text exactly. Each lock was later given the
    SAA policy (#525), which is undone first (tests/test_locks_hold_the_policy.py pins
    the undoing)."""
    rows = _rows(book)
    for q in QUARTERS:
        assert json.dumps(json.loads(rows[q][2])) == rows[q][2], (book.name, q, "round-trips")
        text = before_the_policy_was_held(rows[q][2])
        assert text.endswith(APPENDED), (book.name, q)
        assert _sha(text[:-len(APPENDED)] + "}") == PAYLOAD_BEFORE[q], (book.name, q)
    for q in sorted(set(rows) - set(QUARTERS)):
        assert _sha(before_the_policy_was_held(rows[q][2])) == PAYLOAD_BEFORE[q], (book.name, q)
    assert set(rows) - set(QUARTERS) == ({"2026Q3"} if book == DEMO else set())


def test_the_appended_text_is_the_record_as_the_tool_writes_it():
    """The shared helper other tests undo the record with strips exactly what the tool
    appends: the record as the payload's last key."""
    tool = _tool()
    assert APPENDED == (", " + json.dumps(tool.RECORD) + ": " + json.dumps(RECORD) + "}")
    assert (tool.RESTATED_ON, tool.REMOVED) == (RECORD["restated_on"], RECORD["removed"])
    assert list(tool.QUARTERS) == QUARTERS
    text = _rows(DEMO)["2026Q1"][2]
    assert _sha(before_the_gap_record(text)) == PAYLOAD_BEFORE["2026Q1"]
    q3 = _rows(DEMO)["2026Q3"][2]
    assert before_the_gap_record(q3) == before_the_policy_was_held(q3), (
        "a lock with no record is left as it was before the policy")


def test_the_two_books_hold_the_same_five_locks():
    demo, frozen = _rows(DEMO), _rows(FROZEN_BOOK)
    assert {q: demo[q] for q in QUARTERS} == {q: frozen[q] for q in QUARTERS}


# ── the cover ────────────────────────────────────────────────────────────────

@pytest.fixture(params=BOOKS, ids=lambda p: p.name)
def book(request, tmp_path, monkeypatch):
    import src.db as db
    copy = tmp_path / request.param.name
    shutil.copyfile(request.param, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return copy


def test_every_rewritten_locks_cover_says_so_and_no_other_does(book):
    from src.cache import get_quarter_snapshot, gap_corrections_note
    for q in QUARTERS:
        snap = get_quarter_snapshot(q)[0]
        assert snap.gap_corrections == RECORD, q
        assert gap_corrections_note(snap) == SENTENCE, q
    later = get_quarter_snapshot("2026Q3")[0]
    if later is not None:
        assert later.gap_corrections is None and gap_corrections_note(later) is None
    assert gap_corrections_note(None) is None


def test_the_sentence_is_read_from_the_record(book):
    """The contrast: another date and another count give another sentence, and a lock
    with no record gives none."""
    from src.cache import get_quarter_snapshot, gap_corrections_note
    snap = get_quarter_snapshot("2026Q2")[0]
    two = snap._replace(gap_corrections={"restated_on": "2026-10-02",
                                         "removed": ["VNQ+DBC", "EFA+EEM"]})
    assert gap_corrections_note(two) == (
        "Restated October 2, 2026 to correct an error. An earlier version of this report "
        "said 2 benchmark series could not be fetched when prices were locked; none was "
        "missing.")
    assert gap_corrections_note(snap._replace(gap_corrections=None)) is None


def test_the_style_box_sentence_names_the_callout_that_went_with_it(book):
    from src.cache import get_quarter_snapshot, style_box_withdrawn_note
    for q in QUARTERS:
        assert style_box_withdrawn_note(get_quarter_snapshot(q)[0]) == STYLE_BOX, q


@pytest.fixture(scope="module")
def q2_cover(tmp_path_factory):
    """The cover of the frozen book's committed Q2 2026 report, as rendered text."""
    from src import reports
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
        mp.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
        pin_today(mp)
        point_at_frozen_book(mp, tmp_path_factory.mktemp("q2-gap-record"))
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
    unpin_leftovers()
    flat = " ".join(html.split())
    return flat, flat.split("<h2>Executive Summary</h2>", 1)[0]


def test_the_q2_cover_carries_both_sentences_once(q2_cover):
    report, cover = q2_cover
    for sentence in (SENTENCE, STYLE_BOX):
        assert cover.count(str(escape(sentence))) == 1, sentence
        assert report.count(str(escape(sentence))) == 1, "on the cover, and nowhere else"
    # The line the rewrite removed does not come back: the lock's gaps list is empty.
    assert "that could not be fetched when prices were locked (" not in report
    assert "The equity style box is withdrawn: its fact-sheet" not in report


# ── the tool ─────────────────────────────────────────────────────────────────

def _before_image(src: Path, dst: Path) -> None:
    """``src`` with the record taken back out of the five locks: the book as it stood
    on main, rebuilt from the committed one."""
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    con = sqlite3.connect(dst)
    try:
        with con:
            for q in QUARTERS:
                (text,), = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                                       "quarter_id = ?", (q,)).fetchall()
                # The SAA policy was recorded after the record (#525).
                text = before_the_policy_was_held(text)
                assert text.endswith(APPENDED), q
                old = text[:-len(APPENDED)] + "}"
                assert _sha(old) == PAYLOAD_BEFORE[q], q
                assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                                   "quarter_id = ?", (old, q)).rowcount == 1
    finally:
        con.close()


@pytest.mark.parametrize("committed", BOOKS, ids=lambda p: p.name)
def test_the_tool_makes_the_committed_locks_and_twice_changes_nothing(
        committed, tmp_path, capsys):
    tool = _tool()
    before = tmp_path / "before.db"
    _before_image(committed, before)
    assert _rows(before) != _rows(committed), "premise: the before-image differs"
    assert tool.main(before) == 0
    assert "5 lock(s) given the record of the 2026-09-28 rewrite" in capsys.readouterr().out
    # The committed locks are the record followed by the SAA policy (#525).
    record_the_policy(before)
    assert _rows(before) == _rows(committed)
    assert tool.main(before) == 0
    assert "0 lock(s) given the record" in capsys.readouterr().out
    assert _rows(before) == _rows(committed)


def _edit(db: Path, quarter: str, change) -> None:
    con = sqlite3.connect(db)
    try:
        with con:
            (cap, text), = con.execute("SELECT captured_at, snapshot_data FROM "
                                       "quarter_snapshots WHERE quarter_id = ?",
                                       (quarter,)).fetchall()
            cap, text = change(cap, text)
            assert con.execute("UPDATE quarter_snapshots SET captured_at = ?, "
                               "snapshot_data = ? WHERE quarter_id = ?",
                               (cap, text, quarter)).rowcount == 1
    finally:
        con.close()


def _with_a_gap(cap, text):
    blob = json.loads(text)
    blob["gaps"] = [["EFA", "no rows"]]
    return cap, json.dumps(blob)


REFUSALS = {
    "a lock that still lists a gap": (
        _with_a_gap, "unexpected gaps"),
    "a lock taken on the day of the rewrite": (
        lambda cap, text: ("2026-09-28T15:16:10+00:00", text),
        "on or after the rewrite and never carried the gap"),
    "a payload that does not round-trip": (
        lambda cap, text: (cap, text.replace('"gaps": []', '"gaps":[]', 1)),
        "does not round-trip through JSON"),
}


@pytest.mark.parametrize("what", sorted(REFUSALS))
def test_the_tool_refuses_a_lock_that_is_not_as_it_expects_and_leaves_the_book(
        what, tmp_path):
    """One odd lock, the last of the five, so four updates are already made when it is
    met: the transaction rolls them back."""
    change, message = REFUSALS[what]
    tool = _tool()
    odd = tmp_path / "odd.db"
    _before_image(DEMO, odd)
    _edit(odd, "2026Q2", change)
    was = _rows(odd)
    with pytest.raises(SystemExit, match=message):
        tool.main(odd)
    assert _rows(odd) == was, "a refused run must leave every lock as it was"


def test_the_tool_refuses_a_book_missing_one_of_the_five(tmp_path):
    tool = _tool()
    short = tmp_path / "short.db"
    _before_image(DEMO, short)
    con = sqlite3.connect(short)
    with con:
        assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2025Q4'"
                           ).rowcount == 1
    con.close()
    was = _rows(short)
    with pytest.raises(SystemExit, match=r"holds no lock for \['2025Q4'\]"):
        tool.main(short)
    assert _rows(short) == was


def test_a_later_rewrite_of_the_lock_keeps_the_record(tmp_path, monkeypatch):
    """The report path completes a lock's waiting inputs by rewriting its payload
    (cache.complete_quarter_inputs). Staged here: Q2 2026's lock is made to wait on the
    momentum factor, and the completion rewrites it. The record survives the rewrite."""
    import src.db as db
    from src.cache import complete_quarter_inputs
    from src.input_lock import UMD
    copy = tmp_path / "demo.db"
    shutil.copyfile(DEMO, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)

    def _waiting_on_momentum(cap, text):
        blob = json.loads(text)
        del blob["inputs"][UMD]
        blob["inputs_pending"] = {UMD: "2026-05-29"}
        return cap, json.dumps(blob)

    _edit(copy, "2026Q2", _waiting_on_momentum)
    staged = _rows(copy)["2026Q2"][2]
    snap = complete_quarter_inputs("2026Q2")
    rewritten = _rows(copy)["2026Q2"][2]
    assert rewritten != staged and UMD in json.loads(rewritten)["inputs"], (
        "premise: the completion rewrote the lock's payload")
    assert json.loads(rewritten)["gap_corrections"] == RECORD
    assert snap.gap_corrections == RECORD


# ── the Performance page and the construction note ───────────────────────────

@pytest.mark.parametrize("today", ["2026-10-01", "2026-10-13", "2027-01-04", "2027-07-01",
                                   "2030-01-02"])
def test_the_performance_page_cannot_show_a_quarter_locked_before_the_blended_rule(
        today, tmp_path, monkeypatch):
    """The PDF of a lock taken before #392 carries a note on how its Custom Blended
    figures were built (cache.benchmark_construction_note); the Performance page's
    locked tiles carry none (#478). They need none: the page shows the most recent
    reportable quarter, and on the committed book that is Q3 2026 at the earliest,
    whose lock carries the rule. The five locks without it are the earlier five."""
    import src.db as db
    from src.asof import most_recent_reportable_quarter
    from src.benchmarks import BLENDED_RULE
    from src.cache import benchmark_construction_note, get_quarter_snapshot
    from src.holdings import get_inception_date, get_portfolio_account
    copy = tmp_path / "demo.db"
    shutil.copyfile(DEMO, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    inception = get_inception_date(account_id=get_portfolio_account()["account_id"])
    _start, end, label = most_recent_reportable_quarter(inception, date.fromisoformat(today))
    assert end >= date(2026, 9, 30), (today, label)
    shown = get_quarter_snapshot("2026Q3")[0]
    assert label == "Q3 2026" and shown.benchmark_rule == BLENDED_RULE
    assert benchmark_construction_note(shown) is None
    for q in QUARTERS:
        earlier = get_quarter_snapshot(q)[0]
        assert earlier.benchmark_rule is None and benchmark_construction_note(earlier), q
