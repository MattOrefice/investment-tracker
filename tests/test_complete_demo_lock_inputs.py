"""tools/complete_demo_lock_inputs.py commits a quarter lock's pending inputs into the book.

The report path locks a pending input once its data covers the quarter, but in the demo
that write lands in a container's runtime cache, which a re-seed rebuilds from the files
committed then. The tool writes the same completion into data/demo.db, guarded. Staged
here on copies of the book whose Q3 2026 lock is put back as it was taken, waiting on
French's three files and on the fund durations, with the files as they stood when the
durations were completed: the durations the lock now holds, and French's ending where
the lock recorded them. French's three were completed in a later step, with the files
as they stood for it: the frames the lock now holds. (A later completion is a further
step: it adds its inputs to STEPS and stages the files as they stood for it.)

  * the runs, each with the files as they stood for it, write the committed lock, byte
    for byte, and change nothing else in the book;
  * it is the report path's own completion;
  * it refuses, and writes nothing, when a named input still does not cover the quarter,
    when an input would complete without being named, when anything else in the payload
    would move, and when the lock is not waiting on a name;
  * no committed lock waits on an input the committed files cover, so the book is never
    behind them.
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

from tests.conftest import q3_2026_before_french_was_completed

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo.db"
Q3 = "2026Q3"
# What Q3's lock waited on when it was taken on October 13, 2026, in the payload's order,
# and the inputs completed in the committed book since, one step each, in the order they
# were made: the fund durations on October 13, and French's three files on October 27,
# once French published September. The SAA policy was recorded between them (#525).
PENDING_AS_TAKEN = {"ff5_us": "2026-08-31", "ff5_developed_exus": "2026-08-31",
                    "umd": "2026-08-31", "etf_metadata": "2026-06-30"}
STEPS = (("etf_metadata",), ("ff5_us", "ff5_developed_exus", "umd"))
COMPLETED = tuple(name for step in STEPS for name in step)
# SHA-256 of the lock's stored payload as taken (main at 838b612).
AS_TAKEN = "12ed469fbfb5cf1fb9a3a6048523288e01a7cb39c6ccad0dcc6b3250b35d8b0b"


def _tool():
    spec = importlib.util.spec_from_file_location(
        "complete_demo_lock_inputs", ROOT / "tools" / "complete_demo_lock_inputs.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def _fingerprint(book) -> dict:
    spec = importlib.util.spec_from_file_location(
        "fingerprint_db", ROOT / "tools" / "fingerprint_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.fingerprint(book)


def _locks(book) -> "dict[str, tuple]":
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return {r[0]: tuple(r[1:]) for r in con.execute(
            "SELECT quarter_id, snapshot_date, captured_at, snapshot_data FROM "
            "quarter_snapshots")}
    finally:
        con.close()


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def as_taken(text: str) -> str:
    """Q3's committed payload with every input completed since put back among those it
    waits on: the lock as it was taken."""
    blob = json.loads(text)
    for name in COMPLETED:
        del blob["inputs"][name]
        del blob["inputs_through"][name]
    blob["inputs_pending"] = dict(PENDING_AS_TAKEN)
    return json.dumps(blob)


@pytest.fixture(scope="module")
def committed() -> str:
    """The committed lock as the completions left it, less the SAA policy recorded
    between them (#525); test_a_run_writes_the_committed_lock_and_nothing_else records
    it again at its step and arrives at the committed bytes."""
    from tests.conftest import before_the_policy_was_held
    return before_the_policy_was_held(_locks(DEMO)[Q3][2])


def serve_french_as_completed(monkeypatch, committed: str) -> None:
    """French's three files as they stood when the lock completed them (October 27,
    2026): the frames the committed lock holds. The committed files move on with each
    refresh, which can revise earlier rows as well as add new ones (the one that
    completed this lock did); what the lock recorded does not."""
    from src import factors
    from src.cache import _dec
    held = json.loads(committed)["inputs"]
    monkeypatch.setattr(factors, "load_factors", lambda region: _dec(
        held["ff5_us" if region == "us" else "ff5_developed_exus"]))
    monkeypatch.setattr(factors, "load_umd_factor", lambda: _dec(held["umd"]))


@pytest.fixture
def staged(tmp_path, monkeypatch, committed):
    """(tool, book): a copy of the book whose Q3 lock is as it was taken, with the
    durations the committed lock holds served as the ETF metadata file, and French's
    files ending where the lock recorded them when it was taken: the files as they stood
    for the first step (serve_french_as_completed serves the second's). The committed
    files move on, with each quarter's figures and each refresh; what the lock recorded
    does not."""
    import pandas as pd
    from src import etf_metadata, factors
    book = tmp_path / "demo.db"
    shutil.copyfile(DEMO, book)
    os.chmod(book, 0o644)
    con = sqlite3.connect(book)
    with con:
        assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                           "quarter_id = ?", (as_taken(committed), Q3)).rowcount == 1
    con.close()
    held = tmp_path / "etf_metadata_as_locked.json"
    held.write_text(json.dumps(json.loads(committed)["inputs"]["etf_metadata"]["data"]))
    monkeypatch.setattr(etf_metadata, "META_PATH", held)
    french, momentum = factors.load_factors, factors.load_umd_factor

    def through(obj, name):
        return obj[obj.index <= pd.Timestamp(PENDING_AS_TAKEN[name])]

    monkeypatch.setattr(factors, "load_factors", lambda region: through(
        french(region), "ff5_us" if region == "us" else "ff5_developed_exus"))
    monkeypatch.setattr(factors, "load_umd_factor", lambda: through(momentum(), "umd"))
    return _tool(), book


def test_the_committed_lock_is_the_lock_as_taken_with_the_completed_inputs_moved(committed):
    """Every other byte: put the completed inputs back among those the lock waits on, and
    the payload is the one locked on October 13, by hash."""
    blob = json.loads(committed)
    assert hashlib.sha256(as_taken(committed).encode("utf-8")).hexdigest() == AS_TAKEN
    assert list(blob["inputs_pending"]) == [n for n in PENDING_AS_TAKEN if n not in COMPLETED]
    for name in COMPLETED:
        assert name in blob["inputs"] and name in blob["inputs_through"], name


def test_a_run_writes_the_committed_lock_and_nothing_else(staged, committed, capsys,
                                                          monkeypatch):
    """Each step as it was made, on the lock as taken: the durations, the SAA policy
    recorded the next day (#525), then French's three files. The book arrives at the
    committed locks, byte for byte, and nothing else in it changes."""
    from tests.conftest import record_the_policy
    tool, book = staged
    before, locks = _fingerprint(book), _locks(book)
    assert tool.main(Q3, list(STEPS[0]), book=book) == 0
    assert "now holds " + ", ".join(STEPS[0]) in capsys.readouterr().out
    after = _locks(book)
    assert after[Q3] == locks[Q3][:2] + (q3_2026_before_french_was_completed(committed),), (
        "its date and capture time stay")
    assert {q: v for q, v in after.items() if q != Q3} == {
        q: v for q, v in locks.items() if q != Q3}
    now = _fingerprint(book)
    assert {t for t in now if now[t] != before[t]} == {"quarter_snapshots"}
    assert now["quarter_snapshots"][0] == before["quarter_snapshots"][0]

    # A second run finds nothing to do and writes nothing.
    image = _sha(book)
    assert tool.main(Q3, list(STEPS[0]), book=book) == 0
    assert "Nothing was written" in capsys.readouterr().out
    assert _sha(book) == image

    # The SAA policy (#525), then French's three files as they stood for them.
    record_the_policy(book)
    serve_french_as_completed(monkeypatch, committed)
    locks = _locks(book)
    assert tool.main(Q3, list(STEPS[1]), book=book) == 0
    assert ("now holds " + ", ".join(STEPS[1]) + "; it waits on nothing."
            in capsys.readouterr().out)
    after = _locks(book)
    assert after[Q3][:2] == locks[Q3][:2], "its date and capture time stay"
    assert {q: v for q, v in after.items() if q != Q3} == {
        q: v for q, v in locks.items() if q != Q3}
    assert after == _locks(DEMO)
    assert _fingerprint(book) == _fingerprint(DEMO)


def test_it_is_the_report_paths_own_completion(staged, committed, monkeypatch):
    """The committed lock is what cache.complete_quarter_inputs writes on the same book
    and files at each step, so a container's runtime completion and the committed one
    agree."""
    tool, book = staged
    image = _sha(book)
    assert tool.by_the_report_path(book, Q3) == q3_2026_before_french_was_completed(committed)
    assert _sha(book) == image, "computed on a scratch copy: the book itself is only read"

    # French's step, on the lock as the durations and the SAA policy left it.
    held = _locks(DEMO)[Q3][2]
    con = sqlite3.connect(book)
    with con:
        assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                           "quarter_id = ?",
                           (q3_2026_before_french_was_completed(held), Q3)).rowcount == 1
    con.close()
    serve_french_as_completed(monkeypatch, committed)
    image = _sha(book)
    assert tool.by_the_report_path(book, Q3) == held
    assert _sha(book) == image


def _refused(tool, book, names) -> str:
    image = _sha(book)
    with pytest.raises(SystemExit) as exc:
        tool.main(Q3, names, book=book)
    assert _sha(book) == image, "a refusal writes nothing"
    return str(exc.value)


def test_an_input_that_still_does_not_cover_the_quarter_refuses(staged, tmp_path, monkeypatch,
                                                                committed):
    """SCHP's figure dated June 30, as the file stood when the lock was taken."""
    from src import etf_metadata
    tool, book = staged
    meta = json.loads(committed)["inputs"]["etf_metadata"]["data"]
    meta["SCHP"]["as_of"] = "2026-06-30"
    short = tmp_path / "etf_metadata_june.json"
    short.write_text(json.dumps(meta))
    monkeypatch.setattr(etf_metadata, "META_PATH", short)
    said = _refused(tool, book, ["etf_metadata"])
    assert "not complete, so nothing was written" in said
    assert "etf_metadata's data ends 2026-06-30" in said


def test_an_input_that_would_complete_unnamed_refuses(staged, monkeypatch):
    """Momentum covers the quarter too, and only the durations are named."""
    import pandas as pd
    from src import factors
    tool, book = staged
    real = factors.load_umd_factor

    def through_september():
        s = real()
        s = s[s.index <= pd.Timestamp("2026-08-31")]
        return pd.concat([s, pd.Series([0.1], index=[pd.Timestamp("2026-09-30")], name=s.name)])

    monkeypatch.setattr(factors, "load_umd_factor", through_september)
    said = _refused(tool, book, ["etf_metadata"])
    assert "would complete too and were not named" in said and "'umd'" in said


def test_a_payload_that_moves_anywhere_else_refuses(staged, committed, monkeypatch):
    """The completion re-dates an input the lock still waits on: one more byte than the
    named input, so nothing is written."""
    tool, book = staged
    blob = json.loads(committed)
    still = next(iter(blob["inputs_pending"]), None)
    if still is None:
        blob["rule"] = blob["rule"] + " "
    else:
        blob["inputs_pending"][still] = "2026-09-15"
    monkeypatch.setattr(tool, "by_the_report_path", lambda *_a, **_k: json.dumps(blob))
    assert "changed beyond the named inputs" in _refused(tool, book, list(COMPLETED))


def test_a_name_the_lock_does_not_wait_on_refuses(staged):
    tool, book = staged
    assert "is not waiting on ['hyg']" in _refused(tool, book, ["etf_metadata", "hyg"])
    assert "name each input to complete once" in _refused(tool, book, ["durations"])
    assert "name each input to complete once" in _refused(
        tool, book, ["etf_metadata", "etf_metadata"])
    with pytest.raises(SystemExit) as exc:
        tool.main("2031Q4", ["etf_metadata"], book=book)
    assert "holds no lock for 2031Q4" in str(exc.value)


def test_no_committed_lock_waits_on_an_input_the_committed_files_cover(tmp_path, monkeypatch):
    """The book is never behind the files beside it: run on a copy, the report path's
    completion leaves every committed lock as it is. A refresh that lets a pending input
    cover its quarter is committed with the lock completed (CLAUDE.md, Quarterly
    close-out), or a re-seeded container would lock it from whatever files came later."""
    import src.db as db
    import src.prices as prices
    from src.cache import complete_quarter_inputs
    book = tmp_path / "demo.db"
    shutil.copyfile(DEMO, book)
    os.chmod(book, 0o644)
    monkeypatch.setattr(db, "DB_PATH", book)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    before = _locks(book)
    assert len(before) >= 6, "premise: the committed locks"
    for quarter in sorted(before):
        complete_quarter_inputs(quarter)
    after = _locks(book)
    moved = {q: sorted(set(json.loads(before[q][2])["inputs_pending"])
                       - set(json.loads(after[q][2])["inputs_pending"]))
             for q in before if after[q] != before[q]}
    assert not moved, (f"the committed files now cover {moved}; complete them with "
                       f"tools/complete_demo_lock_inputs.py and commit data/demo.db")
