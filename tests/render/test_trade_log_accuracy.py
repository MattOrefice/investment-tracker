"""Trade Log accuracy (2026-09-25 audit, item 10), rendered on the demo book.

The header said every trade documents a position thesis. On the demo the IDHQ, AVIV
and AVDV buys of the international split carry none, and the international theses
still name the undivided "International Developed" sleeve. Writing theses for the tilt
sleeves is authoring, so the claim now says what the data holds. The tab row also sat
outside the content column that every tab's body used.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent


def _render(tmp_path, monkeypatch, prepare=None):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    if prepare:
        c = sqlite3.connect(copy)
        prepare(c)
        c.commit()
        c.close()
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(config, "IS_DEMO", True)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "10_Trade_Log.py"), default_timeout=600).run()
    st.cache_data.clear()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _claim(at):
    return next(str(c.value) for c in at.caption if str(c.value).startswith("Every trade"))


def test_the_claim_names_the_trades_without_a_thesis(tmp_path, monkeypatch):
    con = sqlite3.connect(f"file:{(ROOT / 'data' / 'demo.db').as_posix()}?mode=ro", uri=True)
    unlinked = con.execute(
        "SELECT ticker FROM trades WHERE thesis_id IS NULL "
        "AND COALESCE(lot_source, 'initial') != 'drip' ORDER BY trade_date DESC, trade_id DESC"
    ).fetchall()
    con.close()
    assert sorted(t for (t,) in unlinked) == ["AVDV", "AVIV", "IDHQ"], "premise"
    claim = _claim(_render(tmp_path, monkeypatch))
    assert "except 3 trades: the AVDV, AVIV and IDHQ trades carry no thesis." in claim, claim
    assert ("2 active theses (International Developed and International Developed — VEA) "
            "name a sleeve this book no longer has.") in claim


def test_a_book_with_no_gaps_keeps_the_plain_claim(tmp_path, monkeypatch):
    """The contrast: link the three trades and point the two theses at a sleeve that
    exists, and the sentence is the original one, unqualified."""
    def close_the_gaps(c):
        n = c.execute("UPDATE trades SET thesis_id = 17 WHERE thesis_id IS NULL AND "
                      "COALESCE(lot_source, 'initial') != 'drip'").rowcount
        assert n == 3
        m = c.execute("UPDATE theses SET target_sleeves = '[\"International Core\"]' "
                      "WHERE target_sleeves = '[\"International Developed\"]'").rowcount
        assert m == 2
    claim = _claim(_render(tmp_path, monkeypatch, close_the_gaps))
    assert claim == ("Every trade documents a position thesis, which rolls up to an "
                     "investment view, which carries theme tags.")


def test_the_tabs_sit_in_the_content_column(tmp_path, monkeypatch):
    at = _render(tmp_path, monkeypatch)

    def path_to_tabs(node, path):
        kind = getattr(node, "type", type(node).__name__)
        if kind == "tab_container":
            return path
        children = getattr(node, "children", None)
        for child in (children.values() if isinstance(children, dict) else []):
            found = path_to_tabs(child, path + [kind])
            if found is not None:
                return found
        return None

    path = path_to_tabs(at._tree, [])
    assert path is not None, "premise: the page has tabs"
    assert path[-1] == "column", path
