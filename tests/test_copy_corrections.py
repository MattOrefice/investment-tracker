"""Copy corrections, the 2026-09-25 audit's resumed run, item C. Text only: no number
changes.

1. #415's synonym swaps are reverted on the SAA page, in the sleeve rationales, and in
   every thesis that copied them: the original word where it carries meaning, never a
   synonym.
2. (15d) The PDF's bitcoin tax line is the page's (tests/test_drawdown_subject_named.py),
   and its PDBC line states what the fund's documents do: a RIC reporting on Form 1099,
   no K-1.
3. (#406 item 4) Theses 7 and 17, written for the undivided International Developed
   sleeve, are condensed from International Core's rationale with no claim it lacks,
   and name sleeves the book has.
4. (#406 item 11) SCHP is not "identical exposure" to TIP: the two track different
   indexes, per their own documents.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ["data/demo.db", "tests/fixtures/frozen_book.db"]

# (#415's word, the original it replaced), as the texts now read around them.
SWAPS = [
    ("at CAPE levels well below the US", "at CAPE levels meaningfully below the US"),
    ("with substantially lower drawdowns", "with materially lower drawdowns"),
    ("Small caps do diversify:", "Small caps offer genuine diversification:"),
    ("a deviation only means something if", "a deviation is only meaningful if"),
    ("proved much weaker in", "proved materially weaker in"),
    ("carries substantial country-specific governance risk",
     "includes meaningful country-specific governance risk"),
    ("governance risk worsens substantially or", "governance risk materially worsens or"),
    # #415 moved this one as well as swapping it; "a wide margin" survives a line break
    # in the source.
    ("a wide margin", "diverged materially from the domestic one"),
]
SOURCES = ["pages/1_SAA.py", "src/seed_saa.py", "src/seed_investment_theses.py",
           "tools/migrate_operations_copy.py", "tools/link_intl_split_theses.py"]
_TOKEN = re.compile(r"\d+(?:\.\d+)?%?|\b[A-Z]{3,5}\b")


def _ro(rel):
    con = sqlite3.connect(f"file:{(ROOT / rel).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _book_texts(rel) -> str:
    con = _ro(rel)
    try:
        rows = con.execute("SELECT rationale FROM asset_classes WHERE rationale IS NOT NULL"
                           ).fetchall()
        rows += con.execute(
            "SELECT COALESCE(macro_view, '') || ' ' || COALESCE(view_summary, '') || ' ' || "
            "COALESCE(vehicle_rationale, '') || ' ' || COALESCE(exit_conditions, '') || ' ' || "
            "COALESCE(expected_return_scenario, '') FROM theses").fetchall()
    finally:
        con.close()
    return " ".join(r[0] for r in rows)


def _source(rel) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _module(rel):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. #415's swaps ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("where", SOURCES + BOOKS)
def test_no_415_synonym_is_left(where):
    text = _book_texts(where) if where.endswith(".db") else _source(where)
    assert [s for s, _ in SWAPS if s in text] == []


@pytest.mark.parametrize("book", BOOKS)
def test_each_original_word_is_back_in_the_rationales_and_every_thesis_that_copies_them(book):
    """Each restored phrase is in the sleeve rationales and in each thesis that repeats
    it: 4, 6, 8, 24 and 26. #432's report named two; 24 and 26 came from #428, and 6 and
    24 were missed (24's swap was moved as well as reworded)."""
    con = _ro(book)
    try:
        rationales = " ".join(r[0] for r in con.execute(
            "SELECT rationale FROM asset_classes WHERE rationale IS NOT NULL"))
        theses = {r[0]: " ".join(x or "" for x in r[1:]) for r in con.execute(
            "SELECT thesis_id, macro_view, view_summary, vehicle_rationale, exit_conditions, "
            "expected_return_scenario FROM theses")}
    finally:
        con.close()
    for _, back in SWAPS[1:]:
        assert " ".join(back.split()) in " ".join(rationales.split()), back
    per_thesis = {4: SWAPS[1][1], 6: SWAPS[2][1], 8: SWAPS[6][1], 24: SWAPS[7][1],
                  26: SWAPS[4][1]}
    for thesis_id, back in per_thesis.items():
        assert back in theses[thesis_id], (thesis_id, back)
    assert SWAPS[5][1] in theses[8]


def test_the_saa_page_source_restores_meaningfully():
    assert SWAPS[0][1] in _source("pages/1_SAA.py")


# ── 2. the PDF's PDBC line ───────────────────────────────────────────────────

def test_the_pdfs_pdbc_line_says_what_its_documents_say():
    tpl = _source("templates/quarterly_report.html")
    assert ("the portfolio holds PDBC (a regulated investment company reporting on Form "
            "1099, with no K-1)") in tpl
    for where in ("templates/quarterly_report.html", "src/reports.py"):
        assert "C-corp" not in _source(where), where


# ── 3. theses 7 and 17 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS)
@pytest.mark.parametrize("thesis_id", [7, 17])
def test_the_international_theses_add_nothing_international_cores_rationale_lacks(
        book, thesis_id):
    """The mechanical half of "no new claims": every number and ticker in the thesis is
    in International Core's rationale, as the book holds it. The undivided sleeve's
    figures (19%, CAPE 18 against 28, 3-5%) fail it."""
    con = _ro(book)
    try:
        (rationale,) = con.execute("SELECT rationale FROM asset_classes WHERE name = "
                                   "'International Core'").fetchone()
        row = con.execute("SELECT * FROM theses WHERE thesis_id = ?", (thesis_id,)).fetchone()
    finally:
        con.close()
    text = " ".join(row[c] or "" for c in ("macro_view", "view_summary", "vehicle_rationale",
                                           "exit_conditions", "invalidation_conditions",
                                           "expected_return_scenario"))
    missing = sorted(set(_TOKEN.findall(text)) - set(_TOKEN.findall(rationale)))
    assert missing == [], missing
    assert "undivided" not in text and "19%" not in text


@pytest.mark.parametrize("book", BOOKS)
def test_the_international_theses_name_sleeves_the_book_has(book):
    con = _ro(book)
    try:
        sleeves = {r[0] for r in con.execute("SELECT name FROM asset_classes")}
        got = {r[0]: (r[1], json.loads(r[2])) for r in con.execute(
            "SELECT thesis_id, title, target_sleeves FROM theses WHERE thesis_id IN (7, 17)")}
    finally:
        con.close()
    assert got[7] == ("International Developed",
                      ["International Core", "International Quality",
                       "International Large Value", "International Small Value"])
    assert got[17] == ("International Core — VEA", ["International Core"])
    assert set(got[7][1]) <= sleeves


# ── 4. SCHP ──────────────────────────────────────────────────────────────────

def test_schp_names_both_indexes_everywhere_its_rationale_is_carried(tmp_path, monkeypatch):
    from src.seed_securities import HOLDINGS
    seed = next(h["holding_rationale"] for h in HOLDINGS if h["ticker"] == "SCHP")
    indexes = ("SCHP the Bloomberg US Treasury Inflation-Linked Bond Index (Series-L), "
               "TIP the ICE US Treasury Inflation Linked Bond Index")
    assert indexes in seed and "identical exposure" not in seed
    thesis = seed.split("\n\n**Would revisit if**")[0]
    for book in BOOKS:
        con = _ro(book)
        try:
            (held,) = con.execute("SELECT holding_rationale FROM securities WHERE "
                                  "ticker = 'SCHP'").fetchone()
            cells = con.execute("SELECT macro_view, view_summary, vehicle_rationale FROM "
                                "theses WHERE title = 'TIPS — SCHP'").fetchone()
        finally:
            con.close()
        assert held == seed, book
        # Thesis 20 carries its own text since #421, its fees read from the data, and
        # the fund rationale reads them too since #456. Rendered, the thesis is still
        # the fund rationale without the revisit line.
        assert "{{er:" in cells[0] and "{{er:" in thesis, book
        assert (tuple(_render_in(book, cells, tmp_path, monkeypatch))
                == tuple(_render_in(book, [thesis], tmp_path, monkeypatch)) * 3), book


def _render_in(book, texts, tmp_path, monkeypatch):
    """``texts`` rendered against ``book`` (a copy): the theses read their fees from the
    book's securities table since #421."""
    import os
    import src.db as db
    from src.prose_figures import render
    src_path = Path(book) if Path(book).is_absolute() else ROOT / book
    copy = tmp_path / f"render_{src_path.name}"
    if not copy.exists():
        shutil.copyfile(src_path, copy)
        os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return [render(t) for t in texts]


# ── the tool ─────────────────────────────────────────────────────────────────

def _run_on_copy(tmp_path, monkeypatch, capsys, drift=None):
    mod = _module("tools/migrate_copy_corrections.py")
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    if drift:
        con = sqlite3.connect(copy)
        con.execute("UPDATE theses SET vehicle_rationale = ? WHERE thesis_id = ?", drift)
        con.commit()
        con.close()
    monkeypatch.setattr(mod, "DEMO_DB", copy)
    assert mod.main() == 0
    return mod, copy, capsys.readouterr().out


def test_a_second_run_changes_nothing(tmp_path, monkeypatch, capsys):
    _, _, out = _run_on_copy(tmp_path, monkeypatch, capsys)
    assert "demo.db: 0 thesis cell(s) changed" in out, out


def test_the_tool_rewrites_a_cell_that_drifted(tmp_path, monkeypatch, capsys):
    """The positive control for the test above."""
    mod, copy, out = _run_on_copy(tmp_path, monkeypatch, capsys, drift=("drifted", 26))
    assert "demo.db: 1 thesis cell(s) changed" in out, out
    con = sqlite3.connect(copy)
    try:
        (got,) = con.execute("SELECT vehicle_rationale FROM theses WHERE thesis_id = 26"
                             ).fetchone()
    finally:
        con.close()
    assert got == mod.cells()[26]["vehicle_rationale"]
