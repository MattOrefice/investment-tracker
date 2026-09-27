"""Audit item 14b, Markets: the Research page's fund rationales after the writing sweep.

The rationales are seeded prose: src/seed_securities.py inserts them once, so
tools/migrate_markets_copy.py carries the swept text into the committed book. The SPAXX
text lives in three places (the Research page, src/seed_position_theses.py, and the
SPAXX position thesis's three columns). These tests hold each set to one text, in the
seed and in both committed books.
"""
import ast
import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]
SPAXX_THESIS = "Cash / SPAXX — SPAXX"


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _tool():
    spec = importlib.util.spec_from_file_location(
        "migrate_markets_copy", ROOT / "tools" / "migrate_markets_copy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_seeded_fund_rationale_is_the_books(book):
    from src.seed_securities import HOLDINGS
    seeded = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS if h.get("holding_rationale")}
    assert len(seeded) >= 13, len(seeded)  # premise: the seed was read (13 funds today)
    con = _ro(book)
    try:
        for ticker, text in seeded.items():
            row = con.execute("SELECT holding_rationale FROM securities WHERE ticker = ?",
                              (ticker,)).fetchone()
            assert row is not None and row[0] == text, (book.name, ticker)
    finally:
        con.close()


def _page_spaxx_rationale() -> str:
    tree = ast.parse((ROOT / "pages" / "8_Research.py").read_text(encoding="utf-8"))
    (value,) = [node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "SPAXX_RATIONALE" for t in node.targets)]
    return ast.literal_eval(value)


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_the_spaxx_thesis_repeats_the_research_page(book):
    from src.seed_position_theses import SPAXX_VEHICLE_RATIONALE
    text = _page_spaxx_rationale()
    assert text == SPAXX_VEHICLE_RATIONALE
    con = _ro(book)
    try:
        rows = con.execute("SELECT macro_view, view_summary, vehicle_rationale FROM theses "
                           "WHERE title = ?", (SPAXX_THESIS,)).fetchall()
    finally:
        con.close()
    assert rows == [(text, text, text)], book.name


def _run_on_copy(tmp_path, monkeypatch, capsys, drift=None):
    mod = _tool()
    copy = tmp_path / "demo.db"
    shutil.copyfile(BOOKS[0], copy)
    copy.chmod(0o644)
    if drift:
        con = sqlite3.connect(copy)
        with con:
            con.execute("UPDATE securities SET holding_rationale = ? WHERE ticker = ?", drift)
        con.close()
    monkeypatch.setattr(mod, "DEMO_DB", copy)
    assert mod.main() == 0
    return copy, capsys.readouterr().out


def test_a_second_run_changes_nothing(tmp_path, monkeypatch, capsys):
    _, out = _run_on_copy(tmp_path, monkeypatch, capsys)
    assert "demo.db: 0 fund rationale(s), 0 SPAXX thesis row(s) changed" in out, out


def test_the_tool_rewrites_a_rationale_that_drifted(tmp_path, monkeypatch, capsys):
    """The positive control for the test above."""
    from src.seed_securities import HOLDINGS
    copy, out = _run_on_copy(tmp_path, monkeypatch, capsys, drift=("drifted", "VGIT"))
    assert "demo.db: 1 fund rationale(s), 0 SPAXX thesis row(s) changed" in out, out
    con = _ro(copy)
    try:
        (got,) = con.execute("SELECT holding_rationale FROM securities WHERE ticker = 'VGIT'").fetchone()
    finally:
        con.close()
    assert got == next(h["holding_rationale"] for h in HOLDINGS if h["ticker"] == "VGIT")
