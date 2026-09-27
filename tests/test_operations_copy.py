"""Audit item 14b, Operations: the Trade Log's thesis prose after the writing sweep.

The theses are seeded prose, written into demo.db once, so tools/migrate_operations_copy.py
carries the swept text into the committed book cell by cell. These tests hold every place
that carries a cell to one text: the tool's table, both committed books, and the seed
modules wherever they hold a cell verbatim (the system theses' exit conditions, and
SLEEVE_META's exit, invalidation and expected-return fields for the sleeve theses).
"""
import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]
META_FIELDS = ("exit_conditions", "invalidation_conditions", "expected_return_scenario")


def _tool():
    spec = importlib.util.spec_from_file_location(
        "migrate_operations_copy", ROOT / "tools" / "migrate_operations_copy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cell(book, thesis_id, col):
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(f"SELECT {col} FROM theses WHERE thesis_id = ?",
                           (thesis_id,)).fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_both_books_carry_every_swept_cell(book):
    new = _tool().NEW
    assert sum(len(cols) for cols in new.values()) == 52
    for tid, cols in new.items():
        for col, text in cols.items():
            assert _cell(book, int(tid), col) == text, (book.name, tid, col)


def test_the_seeds_hold_the_books_text_where_they_hold_a_cell():
    from src.seed_investment_theses import SLEEVE_META
    from src.seed_system_theses import SYSTEM_THESES
    book = BOOKS[0]
    con = sqlite3.connect(f"file:{book.as_posix()}?mode=ro", uri=True)
    try:
        ids = dict(con.execute("SELECT title, thesis_id FROM theses WHERE level = 'investment'"))
    finally:
        con.close()
    checked = 0
    for t in SYSTEM_THESES:
        assert _cell(book, ids[t["title"]], "exit_conditions") == t["exit_conditions"], t["title"]
        checked += 1
    for sleeve, meta in SLEEVE_META.items():
        if sleeve not in ids:  # the split sleeves have position theses only
            continue
        for col in META_FIELDS:
            if col in meta:
                assert _cell(book, ids[sleeve], col) == meta[col], (sleeve, col)
                checked += 1
    # Premise: the loops compared cells rather than walking empty mappings.
    assert checked >= 20, checked


def _run_on_copy(tmp_path, monkeypatch, capsys, drift=None):
    mod = _tool()
    copy = tmp_path / "demo.db"
    shutil.copyfile(BOOKS[0], copy)
    copy.chmod(0o644)
    if drift:
        con = sqlite3.connect(copy)
        with con:
            con.execute("UPDATE theses SET macro_view = ? WHERE thesis_id = ?", drift)
        con.close()
    monkeypatch.setattr(mod, "DEMO_DB", copy)
    assert mod.main() == 0
    return mod, copy, capsys.readouterr().out


def test_a_second_run_changes_nothing(tmp_path, monkeypatch, capsys):
    _, _, out = _run_on_copy(tmp_path, monkeypatch, capsys)
    assert "demo.db: 0 thesis cell(s) changed" in out, out


def test_the_tool_rewrites_a_cell_that_drifted(tmp_path, monkeypatch, capsys):
    """The positive control for the test above: a run that finds one cell off changes
    exactly that cell, back to the table's text."""
    mod, copy, out = _run_on_copy(tmp_path, monkeypatch, capsys, drift=("drifted", 4))
    assert "demo.db: 1 thesis cell(s) changed" in out, out
    assert _cell(copy, 4, "macro_view") == mod.NEW["4"]["macro_view"]
