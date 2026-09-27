"""SPAXX's yield is stated as what it is, not as a typed figure (audit item 15b; #370).

"~4-5% currently" dated itself and followed short rates, so it went stale whenever rates
moved. The prose now says SPAXX earns roughly the Treasury bill rate less its fee, and
the Macro page shows the live rate. These check every place the figure lived: the four
code sources, the seven seeded cells in the demo book, and the frozen book built from it.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "migrate_spaxx_yield_copy.py"


def _tool():
    spec = importlib.util.spec_from_file_location("migrate_spaxx_yield_copy", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _text_cells(path: Path):
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            for col in cols:
                for (v,) in con.execute(f"SELECT {col} FROM {table}"):
                    if isinstance(v, str):
                        yield table, col, v
    finally:
        con.close()


def test_no_code_source_types_the_yield():
    hits = [str(p.relative_to(ROOT)) for d in ("pages", "src") for p in (ROOT / d).rglob("*.py")
            if "4-5%" in p.read_text(encoding="utf-8")]
    assert hits == []


@pytest.mark.parametrize("book", ["data/demo.db", "tests/fixtures/frozen_book.db"])
def test_no_seeded_cell_types_the_yield(book):
    hits = sorted({(t, c) for t, c, v in _text_cells(ROOT / book) if "4-5%" in v})
    assert hits == [], hits


def test_the_seven_cells_say_what_the_yield_is():
    tool = _tool()
    con = sqlite3.connect(f"file:{(ROOT / 'data' / 'demo.db').as_posix()}?mode=ro", uri=True)
    try:
        for table, col, key_col, key in tool.CELLS:
            (v,) = con.execute(f"SELECT {col} FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
            assert "roughly the Treasury bill rate less" in v, (table, col, key)
            assert "the macro page shows the live rate" in v.lower(), (table, col, key)
    finally:
        con.close()
    assert len(tool.CELLS) == 7, "#370 counted seven cells"


def test_the_rewrite_is_idempotent_and_refuses_ambiguity():
    tool = _tool()
    for old, new in tool.REPLACEMENTS:
        once = tool.rewrite(f"Before. {old} After.")
        assert once == f"Before. {new} After."
        assert tool.rewrite(once) == once, "a second run changes nothing"
    with pytest.raises(SystemExit):
        tool.rewrite(tool.REPLACEMENTS[0][0] + " " + tool.REPLACEMENTS[0][0])
    with pytest.raises(SystemExit):
        tool.rewrite("A cell with neither phrase.")


def _constants(rel: str) -> list[str]:
    """Every string constant in a file, as parsed: adjacent literals are one constant,
    however the source wraps them."""
    import ast
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


@pytest.mark.parametrize("rel, sentence", [
    ("pages/8_Research.py",
     "It earns roughly the Treasury bill rate less its fee; the Macro page shows the live rate."),
    ("src/seed_position_theses.py",
     "It earns roughly the Treasury bill rate less its fee; the Macro page shows the live rate."),
    ("src/seed_saa.py",
     "SPAXX earns roughly the Treasury bill rate less its fee, so the drag is muted; the "
     "Macro page shows the live rate."),
    ("src/seed_investment_theses.py",
     "Money market yield, roughly the Treasury bill rate less the fund's fee, with zero "
     "principal risk; drag offset by rebalancing optionality value and avoidance of forced "
     "selling. The Macro page shows the live rate."),
])
def test_the_code_sources_carry_the_same_sentences_as_the_book(rel, sentence):
    assert any(sentence in c for c in _constants(rel)), rel
    assert any(sentence in n for _o, n in _tool().REPLACEMENTS), (
        "the code and the demo book's migration must say the same thing")
