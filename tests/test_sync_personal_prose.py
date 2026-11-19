"""The personal prose runner ends VGIT at the #710 text from any of its three texts (#720).

tools/sync_personal_prose.py's step 5 (migrate_personal_vgit_jurisdiction, #283) used to
abort on a book that carried #710's sentence, so the runner stopped there. Step 5 now
reports that book as done, and the #710 tool runs as step 6. VGIT's rationale has held
three texts: the one before #283, #283's, and #710's. From each, the runner ends at
#710's, and a second run writes nothing.

Every write here is to a scratch book in a temporary directory: a small fixture in CI,
and in personal mode a copy of data/tracker.db, whose own bytes the test checks are
unchanged. The #283 sentence is built from step 5's own data, so this file names no
state.

On the personal book the runner itself stops earlier, at step 2: steps 2 and 4 compare
the book against today's seed, which has moved past them (#732). So the personal-mode
test runs steps 5 and 6 through the runner, as the CI test does, on the copy.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

import tools.migrate_personal_vgit_jurisdiction as step5
import tools.sync_personal_prose as runner
from tools.migrate_vgit_state_exemption import NEW_SENTENCE, OLD_SENTENCE

ROOT = Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"

# The #283 sentence as the personal book held it, its clauses joined by " — ".
SENTENCE_283 = ("Critically, Treasury interest income is exempt from state and local taxes "
                f"— {step5._NEW} with similar yield.")


def three_texts(text_710: str) -> "dict[str, str]":
    """VGIT's three texts, derived from one that carries #710's sentence once."""
    assert text_710.count(NEW_SENTENCE) == 1
    after_283 = text_710.replace(NEW_SENTENCE, SENTENCE_283)
    assert len(OLD_SENTENCE.findall(after_283)) == 1, "the #710 tool would not match it"
    before_283 = after_283.replace(step5._NEW, step5._OLD)
    assert step5._OLD in before_283
    return {"before #283": before_283, "after #283": after_283, "after #710": text_710}


FIXTURE_710 = f"VGIT holds Treasuries. {NEW_SENTENCE} Cost minimization matters."

# (step 5's status, step 6's status) from each text.
EXPECTED = {
    "before #283": ("applied", "applied"),
    "after #283": ("already-applied", "applied"),
    "after #710": ("already-applied", "already-applied"),
}


def _vgit(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT holding_rationale FROM securities WHERE ticker = 'VGIT'").fetchone()[0]
    finally:
        con.close()


def _set_vgit(db: Path, text: str) -> None:
    con = sqlite3.connect(db)
    try:
        with con:
            assert con.execute("UPDATE securities SET holding_rationale = ? "
                               "WHERE ticker = 'VGIT'", (text,)).rowcount == 1
    finally:
        con.close()


def _fixture(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "fixture.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE securities (ticker TEXT PRIMARY KEY, holding_rationale TEXT)")
    con.executemany("INSERT INTO securities VALUES (?, ?)",
                    [("VGIT", text), ("OTHER", "untouched prose")])
    con.commit()
    con.close()
    return p


def _statuses(out: str) -> "dict[str, str]":
    """{step name: status} from the runner's '[i/n] name: status' lines."""
    got = {}
    for line in out.splitlines():
        if line.startswith("[") and "] " in line and ": " in line:
            name, status = line.split("] ", 1)[1].split(": ", 1)
            got[name] = status
    return got


# ── step 5 alone ──────────────────────────────────────────────────────────────

def test_step_5_reports_the_710_sentence_as_done_and_writes_nothing(monkeypatch, tmp_path,
                                                                     capsys):
    db = _fixture(tmp_path, FIXTURE_710)
    monkeypatch.setattr(step5, "TRACKER_DB", db)
    before = db.read_bytes()
    assert step5.main() == 0
    out = capsys.readouterr().out
    assert "already superseded by #710's sentence — nothing to do." in out, out
    assert db.read_bytes() == before


def test_step_5_still_aborts_on_text_it_cannot_reason_about(monkeypatch, tmp_path):
    db = _fixture(tmp_path, "something a human rewrote by hand")
    monkeypatch.setattr(step5, "TRACKER_DB", db)
    assert step5.main() == 1
    assert _vgit(db) == "something a human rewrote by hand"


# ── the last two steps through the runner, in CI ──────────────────────────────

@pytest.mark.parametrize("start", list(EXPECTED))
def test_steps_5_and_6_end_every_text_at_the_710_text(monkeypatch, tmp_path, capsys, start):
    """The runner's wiring for the two VGIT steps: step 6 is handed the book, its
    outcome is read for whether it wrote, and from each text the book ends at #710's."""
    monkeypatch.setattr(runner, "_MIGRATIONS", runner._MIGRATIONS[-2:])
    assert runner._MIGRATIONS == ["migrate_personal_vgit_jurisdiction",
                                  "migrate_vgit_state_exemption"]
    db = _fixture(tmp_path, three_texts(FIXTURE_710)[start])
    assert runner.run(db) == 0
    got = _statuses(capsys.readouterr().out)
    assert (got["migrate_personal_vgit_jurisdiction"],
            got["migrate_vgit_state_exemption"]) == EXPECTED[start]
    assert _vgit(db) == FIXTURE_710
    once = db.read_bytes()
    assert runner.run(db) == 0
    assert set(_statuses(capsys.readouterr().out).values()) == {"already-applied"}
    assert db.read_bytes() == once, "a second run wrote"


def test_the_runner_refuses_the_committed_books(capsys):
    for book in (ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"):
        before = hashlib.sha256(book.read_bytes()).hexdigest()
        assert runner.run(book) == 1
        assert "is a committed book" in capsys.readouterr().out
        assert hashlib.sha256(book.read_bytes()).hexdigest() == before


def test_the_runner_runs_and_documents_step_6_after_step_5():
    steps = runner._MIGRATIONS
    assert steps.index("migrate_vgit_state_exemption") == len(steps) - 1
    assert steps.index("migrate_personal_vgit_jurisdiction") == len(steps) - 2
    assert "6. migrate_vgit_state_exemption" in (runner.__doc__ or "")


# ── the last two steps through the runner, on a copy of the personal book ─────

def _cells(db: Path) -> "dict[tuple, tuple]":
    """Every row of every table, keyed by (table, rowid), as stored."""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                "AND name NOT LIKE 'sqlite_%'"):
            for row in con.execute(f'SELECT rowid, * FROM "{t}"'):
                out[(t, row[0])] = row[1:]
        return out
    finally:
        con.close()


@pytest.mark.parametrize("start", list(EXPECTED))
def test_steps_5_and_6_on_a_copy_of_the_personal_book_from_each_text(monkeypatch, tmp_path,
                                                                     capsys, start):
    """Personal mode only. A copy of tracker.db is set to each of VGIT's three texts, and
    the runner's last two steps run on it: VGIT ends at the book's own #710 text, no
    other cell moves, a second run writes nothing, and tracker.db itself is not
    written. Steps 1 to 4 are not run here: the runner stops at step 2 on this book
    (#732)."""
    if not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("no personal book (personal-mode only)")
    sha = hashlib.sha256(TRACKER_DB.read_bytes()).hexdigest()
    copy = tmp_path / "tracker-copy.db"
    shutil.copyfile(TRACKER_DB, copy)
    text_710 = _vgit(copy)
    assert text_710.count(NEW_SENTENCE) == 1, "the personal book carries #710's sentence"
    original = _cells(copy)
    _set_vgit(copy, three_texts(text_710)[start])
    assert _cells(copy) != original or start == "after #710"

    monkeypatch.setattr(runner, "_MIGRATIONS", runner._MIGRATIONS[-2:])
    assert runner._MIGRATIONS == ["migrate_personal_vgit_jurisdiction",
                                  "migrate_vgit_state_exemption"]
    assert runner.run(copy) == 0
    got = _statuses(capsys.readouterr().out)
    assert (got["migrate_personal_vgit_jurisdiction"],
            got["migrate_vgit_state_exemption"]) == EXPECTED[start]
    assert _cells(copy) == original, "the steps moved a cell other than back to the book's"
    once = copy.read_bytes()
    assert runner.run(copy) == 0
    assert set(_statuses(capsys.readouterr().out).values()) == {"already-applied"}
    assert copy.read_bytes() == once, "a second run wrote"
    assert hashlib.sha256(TRACKER_DB.read_bytes()).hexdigest() == sha
