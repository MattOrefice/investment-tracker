"""tools/migrate_vgit_state_exemption.py: VGIT's prose names no state (#710).

The tool rewrites one sentence in four cells, VGIT's securities.holding_rationale and
thesis 19's macro_view, vehicle_rationale and view_summary, in the #283 shape: a
targeted swap, every UPDATE matching exactly one row or the whole run rolled back,
idempotent. These tests run it on synthetic books (the old sentence names a synthetic
place, which the tool's pattern takes like any capitalised name) and on copies of the
committed books, which already carry the new sentence.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

import tools.migrate_vgit_state_exemption as m
from tests.us_states import codes_in_text, states_named

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = {"demo.db": ROOT / "data" / "demo.db",
             "frozen book": ROOT / "tests" / "fixtures" / "frozen_book.db"}

PLACE = "Example Place"      # synthetic: the committed tree names no state
HEAD = "VGIT delivers intermediate-term US Treasury exposure. "
TAIL = " Cost minimization is the dominant selection criterion."


def _old(sep: str = ":") -> str:
    return ("Critically, Treasury interest income is exempt from state and local taxes"
            f"{sep} a real, if modest, after-tax advantage at {PLACE}'s flat rate over "
            "investment-grade corporate bond funds with similar yield.")


def _cell(sep: str = ":") -> str:
    return HEAD + _old(sep) + TAIL


def _book(tmp_path: Path, rationale: str | None = None, *, theses: bool = True,
          thesis_19: str | None = None, name: str = "book.db") -> Path:
    p = tmp_path / name
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE securities (ticker TEXT PRIMARY KEY, holding_rationale TEXT)")
    con.executemany("INSERT INTO securities VALUES (?, ?)", [
        ("VGIT", _cell() if rationale is None else rationale),
        ("OTHER", _cell()),          # the same sentence on another row stays
    ])
    if theses:
        con.execute("CREATE TABLE theses (thesis_id INTEGER PRIMARY KEY, title TEXT, "
                    "macro_view TEXT, vehicle_rationale TEXT, view_summary TEXT)")
        t19 = _cell() if thesis_19 is None else thesis_19
        con.executemany("INSERT INTO theses VALUES (?, ?, ?, ?, ?)", [
            (19, "Core Fixed Income — VGIT", t19, t19, t19),
            (20, "Another thesis", _cell(), _cell(), _cell()),
        ])
    con.execute("CREATE TABLE unrelated (k TEXT)")
    con.execute("INSERT INTO unrelated VALUES ('untouched')")
    con.commit()
    con.close()
    return p


def _dump(p: Path) -> dict:
    con = sqlite3.connect(p)
    try:
        out = {}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            out[t] = con.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()
        return out
    finally:
        con.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _changed_cells(before: dict, after: dict) -> set:
    cols = {"securities": ("ticker", "holding_rationale"),
            "theses": ("thesis_id", "title", "macro_view", "vehicle_rationale",
                       "view_summary"),
            "unrelated": ("k",)}
    assert before.keys() == after.keys()
    out = set()
    for t in before:
        assert len(before[t]) == len(after[t]), t
        for rb, ra in zip(before[t], after[t]):
            for i, (vb, va) in enumerate(zip(rb, ra)):
                if vb != va:
                    out.add((t, rb[0], cols[t][i]))
    return out


def test_exactly_the_four_cells_change_and_the_rest_of_each_cell_is_kept(tmp_path):
    book = _book(tmp_path)
    before = _dump(book)
    assert m.migrate(book) == {
        "securities.holding_rationale": "updated", "theses.macro_view": "updated",
        "theses.vehicle_rationale": "updated", "theses.view_summary": "updated"}
    after = _dump(book)
    assert _changed_cells(before, after) == {
        ("securities", "VGIT", "holding_rationale"), ("theses", 19, "macro_view"),
        ("theses", 19, "vehicle_rationale"), ("theses", 19, "view_summary")}
    vgit = dict(after["securities"])["VGIT"]
    assert vgit == HEAD + m.NEW_SENTENCE + TAIL


def test_the_dash_spelling_of_the_old_sentence_is_rewritten_too(tmp_path):
    """A book seeded before the colon replaced the dash carries " — " (the personal
    book's VGIT rationale does). The pattern takes either, and nothing else."""
    book = _book(tmp_path, _cell(" —"), theses=False)
    assert m.migrate(book)["securities.holding_rationale"] == "updated"
    assert dict(_dump(book)["securities"])["VGIT"] == HEAD + m.NEW_SENTENCE + TAIL
    other = _book(tmp_path, _cell(";"), theses=False, name="semicolon.db")
    with pytest.raises(m.MigrationRefused):
        m.migrate(other)


def test_a_second_run_writes_nothing(tmp_path):
    book = _book(tmp_path)
    m.migrate(book)
    sha = _sha(book)
    assert set(m.migrate(book).values()) == {"already"}
    assert _sha(book) == sha


@pytest.mark.parametrize("rationale, why", [
    ("prose someone rewrote by hand", "holds the old sentence 0 times"),
    (_cell() + " " + _old(), "holds the old sentence 2 times"),
])
def test_an_unexpected_vgit_cell_refuses_and_writes_nothing(tmp_path, rationale, why):
    book = _book(tmp_path, rationale)
    sha = _sha(book)
    with pytest.raises(m.MigrationRefused, match=why):
        m.migrate(book)
    assert _sha(book) == sha
    assert m.main([str(book)]) == 1


def test_a_wide_match_rolls_back_every_cell(tmp_path):
    """Two VGIT rows is what a widened predicate looks like from the UPDATE's side."""
    p = tmp_path / "wide.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE securities (ticker TEXT, holding_rationale TEXT)")
    con.executemany("INSERT INTO securities VALUES (?, ?)", [("VGIT", _cell())] * 2)
    con.commit()
    con.close()
    with pytest.raises(m.MigrationRefused, match="matched 2 rows"):
        m.migrate(p)
    assert {row[1] for row in _dump(p)["securities"]} == {_cell()}


def test_a_thesis_cell_with_neither_sentence_is_left_as_it_is(tmp_path):
    book = _book(tmp_path, thesis_19="a thesis this book wrote itself")
    out = m.migrate(book)
    assert out["securities.holding_rationale"] == "updated"
    assert {out[f"theses.{c}"] for c in ("macro_view", "vehicle_rationale",
                                          "view_summary")} == {"left as it is"}
    assert dict((r[0], r[2]) for r in _dump(book)["theses"])[19] == (
        "a thesis this book wrote itself")


def test_a_book_without_theses_rewrites_the_rationale_alone(tmp_path):
    book = _book(tmp_path, theses=False)
    out = m.migrate(book)
    assert out["securities.holding_rationale"] == "updated"
    assert out["theses.macro_view"] == "left as it is"


@pytest.mark.parametrize("name", list(COMMITTED))
def test_the_committed_books_already_carry_the_sentence(tmp_path, name):
    """Run on a copy: every cell reads "already", and the copy is byte-identical."""
    copy = tmp_path / "copy.db"
    shutil.copyfile(COMMITTED[name], copy)
    copy.chmod(0o644)
    sha = _sha(copy)
    assert set(m.migrate(copy).values()) == {"already"}
    assert _sha(copy) == sha


def test_the_new_sentence_says_what_its_source_says_and_names_no_state():
    s = m.NEW_SENTENCE
    assert s.startswith("Treasury interest is exempt from state and local income tax "
                        "under federal law (31 U.S.C. § 3124(a);")
    assert "2024 edition" in s and "read November 17, 2026" in s
    assert states_named(s) == [] and codes_in_text(s) == []
    assert "%" not in s and "corporate" not in s


def test_the_book_is_named_never_defaulted(tmp_path, capsys):
    assert m.main([]) == 2
    assert "usage" in capsys.readouterr().out
    with pytest.raises(m.MigrationRefused, match="is not a file"):
        m.migrate(tmp_path / "absent.db")
