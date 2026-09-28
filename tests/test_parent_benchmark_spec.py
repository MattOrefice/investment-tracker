"""No parent row carries a benchmark spec, and no lock carries the false gap one made (#459).

The Real Assets parent row (parent_id NULL) carried 'VNQ+DBC', a legacy unweighted spec,
while the sleeve under it carries 'VNQ (60%) + DBC (40%)'. Nothing reads a parent's
benchmark but the lock's ticker list, which expands every distinct spec, could not parse
this one, and disclosed it on every lock's cover: "Except 1 series that could not be
fetched when prices were locked (VNQ+DBC)". No price was missing. The spec is cleared
(tools/migrate_demo_parent_benchmark.py) and the gap removed from every committed lock in
both books, each payload otherwise byte-identical. Pinned here, with the tool's guards.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import FROZEN_BOOK, pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", FROZEN_BOOK]
LINE = "series that could not be fetched when prices were locked"


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _tool():
    spec = importlib.util.spec_from_file_location(
        "migrate_demo_parent_benchmark", ROOT / "tools" / "migrate_demo_parent_benchmark.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_no_parent_row_carries_a_benchmark_spec(book):
    from src.seed_saa import PARENTS
    con = _ro(book)
    try:
        rows = con.execute("SELECT name, benchmark_ticker FROM asset_classes "
                           "WHERE parent_id IS NULL").fetchall()
        (sleeve,), = con.execute("SELECT benchmark_ticker FROM asset_classes WHERE "
                                 "name = 'Real Assets' AND parent_id IS NOT NULL").fetchall()
    finally:
        con.close()
    assert "Real Assets" in dict(rows), "premise: the parent row is there"
    assert {n: b for n, b in rows if b is not None} == {}
    assert sleeve == "VNQ (60%) + DBC (40%)", "the sleeve keeps its weighted spec"
    assert all(p.get("benchmark_ticker") is None for p in PARENTS), "a fresh build too"


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_no_committed_lock_carries_a_gap(book):
    con = _ro(book)
    try:
        payloads = dict(con.execute("SELECT quarter_id, snapshot_data FROM quarter_snapshots"))
    finally:
        con.close()
    assert sorted(payloads) == ["2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
    assert {q: json.loads(p)["gaps"] for q, p in payloads.items()} == {q: [] for q in payloads}


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


def test_neither_a_committed_lock_nor_a_fresh_one_prints_the_line(frozen, monkeypatch):
    from src import reports
    from src.cache import _get_all_snapshot_tickers, capture_quarter_snapshot
    html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                   is_demo=True).decode("utf-8")
    assert "Prices locked" in html and LINE not in html
    tickers, gaps = _get_all_snapshot_tickers()
    assert gaps == [] and {"VNQ", "DBC"} <= set(tickers)
    con = sqlite3.connect(frozen)
    with con:
        assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                           ).rowcount == 1
    con.close()
    pin_today(monkeypatch, date(2026, 7, 1))
    snap, _ = capture_quarter_snapshot("2026Q2")
    assert list(snap.gaps) == []


def test_the_check_sees_the_spec_put_back(frozen):
    """The contrast: the legacy spec restored on the parent row is a gap again."""
    from src.cache import _get_all_snapshot_tickers
    con = sqlite3.connect(frozen)
    with con:
        assert con.execute("UPDATE asset_classes SET benchmark_ticker = 'VNQ+DBC' WHERE "
                           "name = 'Real Assets' AND parent_id IS NULL").rowcount == 1
    con.close()
    assert [spec for spec, _why in _get_all_snapshot_tickers()[1]] == ["VNQ+DBC"]


def _with_the_gap_back(src: Path, dst: Path, tool) -> None:
    """``src`` with the parent's legacy spec and every lock's gap put back as the tool
    found them: the before-image, rebuilt from the committed after-image."""
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    reason = ("unparseable benchmark spec: benchmark weights sum to 2.0 (not 1.0) in spec "
              "'VNQ+DBC': [('VNQ', 1.0), ('DBC', 1.0)]. A partial blend would silently "
              "rescale the surviving legs to $1 — refuse it.")
    gap = '"gaps": ' + json.dumps([[tool.LEGACY_SPEC, reason]])
    con = sqlite3.connect(dst)
    with con:
        con.execute("UPDATE asset_classes SET benchmark_ticker = 'VNQ+DBC' WHERE "
                    "name = 'Real Assets' AND parent_id IS NULL")
        for q, p in con.execute("SELECT quarter_id, snapshot_data FROM quarter_snapshots"
                                ).fetchall():
            assert p.count('"gaps": []') == 1
            con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ?",
                        (p.replace('"gaps": []', gap), q))
    con.close()


def test_the_tool_restores_the_committed_book_exactly_and_twice_changes_nothing(
        tmp_path, monkeypatch, capsys):
    tool = _tool()
    before = tmp_path / "before.db"
    _with_the_gap_back(ROOT / "data" / "demo.db", before, tool)
    monkeypatch.setattr(tool, "DEMO_DB", before)
    assert tool.main() == 0
    assert "1 parent spec(s) cleared, 5 lock gap(s) removed" in capsys.readouterr().out
    committed, rebuilt = _ro(ROOT / "data" / "demo.db"), _ro(before)
    try:
        for sql in ("SELECT quarter_id, snapshot_data FROM quarter_snapshots ORDER BY 1",
                    "SELECT * FROM asset_classes ORDER BY asset_class_id"):
            assert rebuilt.execute(sql).fetchall() == committed.execute(sql).fetchall(), sql
    finally:
        committed.close()
        rebuilt.close()
    assert tool.main() == 0
    assert "0 parent spec(s) cleared, 0 lock gap(s) removed" in capsys.readouterr().out


def test_the_tool_refuses_a_gap_it_does_not_name(tmp_path, monkeypatch):
    tool = _tool()
    db = tmp_path / "other.db"
    shutil.copyfile(ROOT / "data" / "demo.db", db)
    os.chmod(db, 0o644)
    con = sqlite3.connect(db)
    with con:
        (p,), = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                            "quarter_id = '2026Q2'").fetchall()
        con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = '2026Q2'",
                    (p.replace('"gaps": []', '"gaps": [["XYZ", "a real gap"]]'),))
    con.close()
    monkeypatch.setattr(tool, "DEMO_DB", db)
    with pytest.raises(SystemExit, match="unexpected gaps"):
        tool.main()
