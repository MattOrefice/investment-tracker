"""The international split's three buys carry position theses (audit item 15e).

The demo split developed international into four sleeves and bought IDHQ, AVIV and AVDV
on its inception day with no thesis. tools/link_intl_split_theses.py wrote one for each,
condensed from its sleeve's SAA rationale with no claim of its own, and linked the trade.
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
TOOL = ROOT / "tools" / "link_intl_split_theses.py"
BOOKS = ["data/demo.db", "tests/fixtures/frozen_book.db"]


def _tool():
    spec = importlib.util.spec_from_file_location("link_intl_split_theses", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ro(rel):
    con = sqlite3.connect(f"file:{(ROOT / rel).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@pytest.mark.parametrize("book", BOOKS)
def test_every_discretionary_trade_has_a_position_thesis(book):
    con = _ro(book)
    rows = con.execute(
        "SELECT t.ticker, th.level FROM trades t LEFT JOIN theses th "
        "ON t.thesis_id = th.thesis_id WHERE COALESCE(t.lot_source, 'initial') != 'drip'"
    ).fetchall()
    con.close()
    assert rows, "premise: the book has trades"
    assert [r["ticker"] for r in rows if r["level"] != "position"] == []


def _check_fields(con):
    tool = _tool()
    parent = con.execute("SELECT thesis_id, conviction FROM theses WHERE title = ? AND "
                         "level = 'investment'", (tool.PARENT_TITLE,)).fetchone()
    for ticker, (sleeve, text) in tool.DRAFTS.items():
        th = con.execute("SELECT * FROM theses WHERE title = ?", (f"{sleeve} — {ticker}",)).fetchone()
        assert th is not None, ticker
        (weight,) = con.execute("SELECT target_weight FROM asset_classes WHERE name = ? AND "
                                "parent_id IS NOT NULL", (sleeve,)).fetchone()
        assert (th["level"], th["status"], th["horizon_months"]) == ("position", "active", 60)
        assert th["parent_thesis_id"] == parent["thesis_id"]
        assert th["conviction"] == parent["conviction"]
        assert th["target_weight"] == pytest.approx(weight)
        assert json.loads(th["target_sleeves"]) == [sleeve]
        assert th["macro_view"] == th["view_summary"] == th["vehicle_rationale"] == text
        (linked,) = con.execute("SELECT COUNT(*) FROM trades WHERE ticker = ? AND "
                                "thesis_id = ?", (ticker, th["thesis_id"])).fetchone()
        assert linked == 1, ticker


def test_the_three_theses_follow_the_seeders_fields():
    con = _ro("data/demo.db")
    _check_fields(con)
    con.close()


def test_the_tool_writes_those_fields_from_the_state_before_it(tmp_path, monkeypatch):
    """The committed rows prove what was written once; this re-runs the tool on the book
    as it stood before (no theses, no links) so a change to the tool is seen too."""
    tool = _tool()
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    con = sqlite3.connect(copy)
    for ticker, (sleeve, _text) in tool.DRAFTS.items():
        con.execute("UPDATE trades SET thesis_id = NULL WHERE ticker = ?", (ticker,))
        con.execute("DELETE FROM theses WHERE title = ?", (f"{sleeve} — {ticker}",))
    con.commit()
    con.close()
    monkeypatch.setattr(tool, "DEMO_DB", copy)
    assert tool.main() == 0
    con = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    _check_fields(con)
    con.close()


_TOKEN = re.compile(r"\d+(?:\.\d+)?%?|\b[A-Z]{3,5}\b")


@pytest.mark.parametrize("ticker", ["IDHQ", "AVIV", "AVDV"])
def test_each_thesis_adds_no_figure_or_fund_its_rationale_does_not_have(ticker):
    """The mechanical half of "no new claims": every number and every ticker in the
    thesis is in the sleeve's SAA rationale as the book holds it."""
    sleeve, text = _tool().DRAFTS[ticker]
    con = _ro("data/demo.db")
    (rationale,) = con.execute("SELECT rationale FROM asset_classes WHERE name = ? AND "
                               "parent_id IS NOT NULL", (sleeve,)).fetchone()
    con.close()
    missing = sorted({t for t in _TOKEN.findall(text)} - set(_TOKEN.findall(rationale)))
    assert missing == [], missing


def test_the_tool_is_idempotent_and_refuses_a_second_link(tmp_path, monkeypatch):
    tool = _tool()
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    monkeypatch.setattr(tool, "DEMO_DB", copy)
    assert tool.main() == 0
    tool.main()
    con = sqlite3.connect(copy)
    n = con.execute("SELECT COUNT(*) FROM theses WHERE title LIKE 'International % — %' "
                    "AND level = 'position' AND title != 'International Developed — VEA'"
                    ).fetchone()[0]
    con.execute("UPDATE trades SET thesis_id = 1 WHERE ticker = 'IDHQ'")
    con.commit()
    con.close()
    assert n == 3, "a second run adds nothing"
    with pytest.raises(SystemExit):
        tool.main()
