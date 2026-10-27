"""Each quarter lock holds the book's SAA policy, and a locked report reads it (#525's
second part, decided by the owner on October 13, 2026).

A lock held prices, dividends and its provider inputs, and no policy. Every locked
report read the book's asset classes, target weights, tolerance bands, benchmark specs
and ticker-to-sleeve mapping at each render, so a target changed today would have moved
every locked quarter's holdings table, benchmark returns, attribution and factor
loadings. Now:

  * src/policy.py is the one reader of the policy on a report's path, and inside a lock
    that holds one it serves the lock's; outside a lock it reads the book;
  * a lock taken from now holds the policy as it stands when it is taken;
  * the eleven committed locks (six in data/demo.db, five in the frozen book) were given
    the policy as it stood on October 14, 2026 (tools/hold_policy_in_demo_locks.py),
    marked as recorded after the quarter locked, and each cover says so;
  * the factor regressions' sleeve weights read the targets (factors.target_weights):
    the lock's inside it, the book's outside. _SAA_US and _FI_WEIGHTS are gone.

Pinned here:
  * each committed lock holds the book's own policy, recorded October 14, 2026, after it
    locked; nothing else in any lock changed, and nothing else in either book;
  * the tool makes the committed books from main's (less Q3 2026's French files,
    completed after it on October 27, 2026), changes nothing on a second run, and
    refuses a book that is not as it expects, leaving it as it was;
  * each committed cover says when the policy was recorded; a lock taken now holds the
    policy as it stands and needs no such sentence;
  * inside a lock every reader serves the held policy, and outside it the book's;
  * the proof, on a scratch book: a target changed after the quarters locked moves the
    live SAA page and no locked report, and the same change moves every locked report
    whose lock holds no policy.

The locked-figures golden passes unchanged on the committed books
(tests/test_locked_figures_golden.py): the policy recorded is the one every report read.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests import locked_figures as lf
from tests.conftest import (
    FROZEN_BOOK,
    before_the_policy_was_held,
    pin_today,
    point_at_frozen_book,
    q3_2026_before_french_was_completed,
    unpin_leftovers,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo.db"
BOOKS = [DEMO, FROZEN_BOOK]
RECORDED_ON = "2026-10-14"
SENTENCE = ("The SAA policy this report reads (its sleeves, their target weights, tolerance "
            "bands and benchmarks, and each fund's sleeve) was recorded October 14, 2026, "
            "after this quarter locked: it is the policy as it stood that day.")

# Each lock's stored payload as its SHA-256 on main at 46b5300, before the policy was
# recorded in it. The frozen book's five are the same texts as demo.db's.
PAYLOAD_BEFORE = {
    "2025Q2": "ecfb1bdde4623ff05669d06d7c33b9e48decc02be822554ea40ccc492b44ffa4",
    "2025Q3": "d2a9534938561f8d896087679174fce7178c07279c5481646317338793879125",
    "2025Q4": "163a64dcbdb2fb3eb2d90aa8a4253ae8fb33c36239b66890296a09cd07448925",
    "2026Q1": "3c4135acf4ea9dc8b429a90cc2aa287873226dd1cc1e7398710ce1bd4e342b40",
    "2026Q2": "7badb6e6e467258f14b42fc3888e67566ada98eef568877e9b39977e7c09ab37",
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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tool():
    return _load("hold_policy_in_demo_locks")


def _fingerprint(book) -> dict:
    return _load("fingerprint_db").fingerprint(book)


def _copy(src: Path, dst: Path) -> Path:
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    return dst


def _without_the_policy(src: Path, dst: Path) -> Path:
    """``src`` with the policy taken back out of every lock: the book as it stood on
    main, rebuilt from the committed one."""
    _copy(src, dst)
    con = sqlite3.connect(dst)
    try:
        with con:
            for q, text in con.execute("SELECT quarter_id, snapshot_data FROM "
                                       "quarter_snapshots").fetchall():
                assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                                   "quarter_id = ?",
                                   (before_the_policy_was_held(text), q)).rowcount == 1
    finally:
        con.close()
    return dst


# ── the committed locks ──────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_committed_lock_holds_the_books_own_policy_recorded_after_it(book):
    from src import policy
    con = _ro(book)
    try:
        own = policy.encode(policy.read_book(con), RECORDED_ON, recorded_after_lock=True)
    finally:
        con.close()
    rows = _rows(book)
    assert len(rows) == (6 if book == DEMO else 5)
    for q, (_d, captured_at, text) in rows.items():
        blob = json.loads(text)
        assert blob["inputs"]["policy"] == own, (book.name, q)
        assert blob["inputs_through"]["policy"] == RECORDED_ON, (book.name, q)
        assert captured_at[:10] < RECORDED_ON, (book.name, q, "it was recorded after the lock")
    # The book's own policy: its asset classes and securities as committed.
    assert len(own["asset_classes"]) == 18 and len(own["securities"]) == 27
    assert own["columns"] == list(policy.COLUMNS) and "rationale" not in own["columns"]


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_nothing_else_in_any_lock_changed(book):
    """Per lock: the stored text less the policy is main's, every byte, and the text
    round-trips. The policy sits where a lock keeps its inputs, so no record moved and
    the locked-figures golden's view of each lock is as it was. Q3 2026's French files,
    completed after the policy (October 27, 2026), are undone first
    (tests/test_q3_2026_lock.py pins that undoing)."""
    for q, (_d, _cap, text) in _rows(book).items():
        assert json.dumps(json.loads(text)) == text, (book.name, q, "it round-trips")
        made = q3_2026_before_french_was_completed(text)
        assert _sha(before_the_policy_was_held(made)) == PAYLOAD_BEFORE[q], (book.name, q)
        assert lf.lock_state(text) == lf.lock_state(before_the_policy_was_held(text))


def test_the_undoing_is_the_tools_own_inverse():
    """The tool gave each lock the policy as its last input. Q3 2026's French files were
    completed after it (October 27, 2026), so that lock is taken as the tool left it."""
    tool = _tool()
    for q, (_d, _cap, text) in _rows(DEMO).items():
        assert tool.without_policy(text) == before_the_policy_was_held(text), q
        made = q3_2026_before_french_was_completed(text)
        assert tool.with_policy(before_the_policy_was_held(made),
                                json.loads(text)["inputs"]["policy"]) == made, q
        assert tool.with_policy(text, {"held_on": RECORDED_ON}) is None, (
            q, "a lock that holds a policy is left alone")


def test_the_two_books_hold_the_same_five_locks():
    demo, frozen = _rows(DEMO), _rows(FROZEN_BOOK)
    assert {q: demo[q] for q in frozen} == frozen


# ── the tool ─────────────────────────────────────────────────────────────────

def _before_french(src: Path, dst: Path) -> Path:
    """``src`` with Q3 2026's French files taken back out of its lock: the committed book
    as the tool left it, before they were completed (October 27, 2026)."""
    _copy(src, dst)
    con = sqlite3.connect(dst)
    try:
        with con:
            for q, text in con.execute("SELECT quarter_id, snapshot_data FROM "
                                       "quarter_snapshots").fetchall():
                made = q3_2026_before_french_was_completed(text)
                if made != text:
                    assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? "
                                       "WHERE quarter_id = ?", (made, q)).rowcount == 1
    finally:
        con.close()
    return dst


@pytest.mark.parametrize("committed", BOOKS, ids=lambda p: p.name)
def test_the_tool_makes_the_committed_book_and_twice_changes_nothing(
        committed, tmp_path, capsys):
    tool = _tool()
    made = _before_french(committed, tmp_path / "made.db")
    before = _without_the_policy(made, tmp_path / "before.db")
    for q, (_d, _cap, text) in _rows(before).items():
        assert _sha(text) == PAYLOAD_BEFORE[q], (q, "premise: main's lock")
    n = len(_rows(made))
    assert tool.main(before, quarters=sorted(_rows(made))) == 0
    assert f"{n} lock(s) given the SAA policy as it stood on {RECORDED_ON}" in (
        capsys.readouterr().out)
    assert _rows(before) == _rows(made)
    # Every table, not only the locks: the data path is the locks alone.
    assert _fingerprint(before) == _fingerprint(made)
    assert tool.main(before, quarters=sorted(_rows(made))) == 0
    assert "0 lock(s) given the SAA policy" in capsys.readouterr().out
    assert _rows(before) == _rows(made)


def test_on_main_the_tool_changes_only_the_locks(tmp_path):
    """The per-table account of the change: in main's book every table but
    quarter_snapshots is as it is in the committed book, and quarter_snapshots holds the
    same rows with another payload."""
    before = _without_the_policy(DEMO, tmp_path / "before.db")
    was, now = _fingerprint(before), _fingerprint(DEMO)
    assert sorted(was) == sorted(now)
    assert {t for t in now if now[t] != was[t]} == {"quarter_snapshots"}
    assert now["quarter_snapshots"][0] == was["quarter_snapshots"][0] == 6


def _with_odd_lock(change):
    def stage(db: Path) -> None:
        con = sqlite3.connect(db)
        try:
            with con:
                (text,), = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                                       "quarter_id = '2026Q2'").fetchall()
                assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                                   "quarter_id = '2026Q2'", (change(text),)).rowcount == 1
        finally:
            con.close()
    return stage


def _without_inputs(text: str) -> str:
    blob = json.loads(text)
    del blob["inputs"], blob["inputs_through"]
    return json.dumps(blob)


def _missing_a_lock(db: Path) -> None:
    con = sqlite3.connect(db)
    with con:
        assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2025Q4'"
                           ).rowcount == 1
    con.close()


REFUSALS = {
    "a payload that does not round-trip": (
        _with_odd_lock(lambda t: t.replace('"gaps": []', '"gaps":[]', 1)),
        "does not round-trip through JSON"),
    "a lock with no inputs": (_with_odd_lock(_without_inputs), "holds no inputs"),
    "a book missing one of its locks": (_missing_a_lock, "holds the locks"),
}


@pytest.mark.parametrize("what", sorted(REFUSALS))
def test_the_tool_refuses_a_book_that_is_not_as_it_expects_and_leaves_it(what, tmp_path):
    """The odd lock is Q2 2026, the fifth of six, so four updates are already made when
    it is met: the transaction rolls them back."""
    stage, message = REFUSALS[what]
    tool = _tool()
    odd = _without_the_policy(DEMO, tmp_path / "demo.db")
    stage(odd)
    was = _rows(odd)
    with pytest.raises(SystemExit, match=message):
        tool.main(odd)
    assert _rows(odd) == was, "a refused run must leave every lock as it was"


def test_the_tool_names_the_locks_each_committed_book_holds():
    tool = _tool()
    assert tool.RECORDED_ON == RECORDED_ON
    assert {name: sorted(qs) for name, qs in tool.QUARTERS.items()} == {
        "demo.db": sorted(_rows(DEMO)), "frozen_book.db": sorted(_rows(FROZEN_BOOK))}


# ── the cover ────────────────────────────────────────────────────────────────

@pytest.fixture(params=BOOKS, ids=lambda p: p.name)
def book(request, tmp_path, monkeypatch):
    import src.db as db
    copy = _copy(request.param, tmp_path / request.param.name)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return copy


def test_each_committed_locks_cover_says_when_the_policy_was_recorded(book):
    from src.cache import get_quarter_snapshot, lock_holds_note, policy_recorded_note
    for q in _rows(book):
        snap = get_quarter_snapshot(q)[0]
        assert policy_recorded_note(snap) == SENTENCE, q
        assert "the SAA policy" in lock_holds_note(snap), q
    assert policy_recorded_note(None) is None


def test_the_sentence_is_read_from_the_lock(book):
    """The contrast: another day gives another sentence; a policy held when the lock
    was taken, or none, gives none."""
    from src.cache import get_quarter_snapshot, lock_holds_note, policy_recorded_note
    snap = get_quarter_snapshot("2026Q2")[0]
    held = dict(snap.inputs["policy"])

    def holding(**change):
        return snap._replace(inputs={**snap.inputs, "policy": {**held, **change}})

    assert "recorded November 2, 2026, after this quarter locked" in policy_recorded_note(
        holding(held_on="2026-11-02"))
    assert policy_recorded_note(holding(recorded_after_lock=False)) is None
    unheld = snap._replace(inputs={k: v for k, v in snap.inputs.items() if k != "policy"})
    assert policy_recorded_note(unheld) is None
    assert "the SAA policy" not in lock_holds_note(unheld)


# ── the readers ──────────────────────────────────────────────────────────────

def _change_the_policy(db: Path) -> None:
    """Two points of target moved from US Large Core to Core Fixed Income (and from
    Equity to Income), US Large Core's band narrowed to one basis point, so any drift
    would read as out of band, its benchmark changed, and PDBC moved to another sleeve:
    a policy change made after every quarter locked."""
    con = sqlite3.connect(db)
    try:
        with con:
            for name, parent, delta in (("US Large Core", 1, -0.02),
                                        ("Core Fixed Income", 2, 0.02),
                                        ("Equity", None, -0.02), ("Income", None, 0.02)):
                where = "parent_id = ?" if parent else "parent_id IS NULL"
                args = (delta, name, parent) if parent else (delta, name)
                assert con.execute(f"UPDATE asset_classes SET target_weight = target_weight "
                                   f"+ ? WHERE name = ? AND {where}", args).rowcount == 1
            assert con.execute("UPDATE asset_classes SET tolerance_band = 0.0001, "
                               "benchmark_ticker = 'VOO' WHERE name = 'US Large Core' AND "
                               "parent_id IS NOT NULL").rowcount == 1
            assert con.execute("UPDATE securities SET asset_class_id = 18 WHERE ticker = "
                               "'PDBC'").rowcount == 1
    finally:
        con.close()


def _readings() -> tuple:
    from src import factors, policy, reports
    from src.sleeve_config import sleeve_holdings, strategic_sleeve_weights
    core = next(r for r in policy.sleeves() if r["name"] == "US Large Core")
    return (round(strategic_sleeve_weights()["US Large Core"], 6), core["tolerance_band"],
            reports.sleeve_bench_ticker()["US Large Core"],
            policy.sleeve_of_ticker()["PDBC"], "PDBC" in sleeve_holdings().get("Real Assets", []),
            round(factors.us_weights()["VOO"], 6), round(factors.fi_weights()["VGIT"], 6))


def test_inside_a_lock_every_reader_serves_the_held_policy_and_outside_it_the_books(
        tmp_path, monkeypatch):
    import src.db as db
    from src.cache import get_quarter_snapshot, snapshot_price_context
    changed = _copy(FROZEN_BOOK, tmp_path / "changed.db")
    _change_the_policy(changed)
    monkeypatch.setattr(db, "DB_PATH", changed)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    assert _readings() == (0.153469, 0.0001, "VOO", "International Small Value", False,
                           0.319728, 0.665552), "outside a lock: the book's policy"
    snap = get_quarter_snapshot("2026Q2")[0]
    with snapshot_price_context(snap):
        assert _readings() == (0.173469, 0.03, "SPY", "Real Assets", True, 0.346939,
                               0.6), "inside the lock: the policy it holds"
    assert _readings()[0] == 0.153469, "and the book's again on leaving it"


# ── the proof ────────────────────────────────────────────────────────────────

def _saa_page_target(book_path: Path, monkeypatch, tmp_path) -> float:
    """US Large Core's target on the live SAA page, rendered on ``book_path``."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.db as db
    tmp_path.mkdir()
    pin_today(monkeypatch)
    point_at_frozen_book(monkeypatch, tmp_path)
    monkeypatch.setattr(db, "DB_PATH", book_path)
    st.cache_data.clear()
    try:
        at = AppTest.from_file(str(ROOT / "pages" / "1_SAA.py"), default_timeout=300).run()
    finally:
        st.cache_data.clear()
        unpin_leftovers()
    assert not at.exception, [str(e.value) for e in at.exception]
    table = next(d.value for d in at.dataframe if "Target (%)" in d.value.columns)
    return float(table.set_index("Sleeve").loc["US Large Core", "Target (%)"])


def test_a_target_changed_after_the_quarters_locked_moves_the_live_page_and_no_locked_report(
        tmp_path, monkeypatch):
    golden = lf.load_golden()["frozen_book.db"]
    changed = _copy(FROZEN_BOOK, tmp_path / "changed.db")
    _change_the_policy(changed)

    # 1. Every locked report is as the golden holds it: no figure and no label moved,
    #    and each cover says when the policy it reads was recorded.
    locked = lf.render_book("frozen_book.db", tmp_path, book=changed)
    assert sorted(locked) == sorted(golden)
    for q in golden:
        assert lf.differences(golden[q], locked[q]) == [], q
        assert SENTENCE in locked[q]["cover"], q

    # 2. The contrast: the same change in a book whose locks hold no policy moves every
    #    locked report, in its holdings table among other places.
    unheld = _without_the_policy(changed, tmp_path / "unheld.db")
    moved = lf.render_book("frozen_book.db", tmp_path, book=unheld)
    for q in golden:
        lines = lf.differences(golden[q], moved[q])
        assert any(line.startswith("Holdings & Allocation, table 1") for line in lines), q
        assert SENTENCE not in moved[q]["cover"], q

    # 3. The live page reads the book's policy now: the SAA page shows the new target.
    assert _saa_page_target(FROZEN_BOOK, monkeypatch, tmp_path / "was") == 17.3
    assert _saa_page_target(changed, monkeypatch, tmp_path / "now") == 15.3


# ── a lock taken now ─────────────────────────────────────────────────────────

def test_a_lock_taken_now_holds_the_policy_as_it_stands_and_says_nothing_more(
        tmp_path, monkeypatch):
    from src import policy
    from src.cache import (capture_quarter_snapshot, get_quarter_snapshot, lock_holds_note,
                           policy_recorded_note)
    from tests.conftest import FROZEN_TODAY
    pin_today(monkeypatch)
    copy = point_at_frozen_book(monkeypatch, tmp_path)
    try:
        con = sqlite3.connect(copy)
        with con:
            assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                               ).rowcount == 1
        con.close()
        capture_quarter_snapshot("2026Q2")
        snap = get_quarter_snapshot("2026Q2")[0]
        held = snap.inputs["policy"]
        # Held on the day the lock was taken: the frozen book's pinned today.
        assert held == policy.decode(policy.encode(policy.read_book(), FROZEN_TODAY.isoformat(),
                                                   recorded_after_lock=False))
        assert held["recorded_after_lock"] is False
        assert policy_recorded_note(snap) is None
        assert "the SAA policy" in lock_holds_note(snap)
    finally:
        unpin_leftovers()


# ── the regressions' weights ─────────────────────────────────────────────────

def test_a_fund_without_a_sleeve_or_two_funds_in_one_sleeve_have_no_weight():
    """The weights are each fund's sleeve's target. A fund the policy puts in no sleeve
    with a target has none to read, and two funds in one sleeve would need a rule to
    split its target: both raise, and the regression for that sleeve is not run."""
    from src import factors
    with pytest.raises(ValueError, match="XYZ has no sleeve with a positive SAA target"):
        factors.target_weights(("VOO", "XYZ"))
    with pytest.raises(ValueError, match="share a sleeve"):
        factors.target_weights(("VOO", "SPY"))
    with pytest.raises(ValueError, match="SPAXX has no sleeve with a positive SAA target"):
        factors.target_weights(("VOO", "SPAXX"))


def test_the_retired_constants_are_gone():
    from src import factors
    for name in ("_SAA_US", "_SAA_US_TOTAL", "_FI_WEIGHTS"):
        assert not hasattr(factors, name), name
    assert "weights" not in factors._SLEEVES["us"] and factors._SLEEVES["us"]["funds"]


# ── a lock that predates inputs ──────────────────────────────────────────────

def test_a_lock_completed_after_it_was_taken_marks_its_policy_so(tmp_path, monkeypatch):
    """A lock taken before #382 held prices alone, and the report path completes every
    input for it when it is next read (cache.complete_quarter_inputs). The policy it
    gets then is the one standing that day, not the quarter's, so it is marked as
    recorded after the lock and the cover says so."""
    from src.cache import complete_quarter_inputs, policy_recorded_note
    pin_today(monkeypatch)
    copy = point_at_frozen_book(monkeypatch, tmp_path)
    try:
        con = sqlite3.connect(copy)
        with con:
            (text,), = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                                   "quarter_id = '2026Q2'").fetchall()
            blob = json.loads(text)
            for key in ("inputs", "inputs_through", "inputs_pending", "inputs_rule"):
                del blob[key]
            assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                               "quarter_id = '2026Q2'", (json.dumps(blob),)).rowcount == 1
        con.close()
        snap = complete_quarter_inputs("2026Q2")
        assert snap.inputs["policy"]["recorded_after_lock"] is True
        assert "after this quarter locked" in policy_recorded_note(snap)
    finally:
        unpin_leftovers()
