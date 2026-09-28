"""The equity style box is withdrawn, and every committed lock restated for it (#468).

Its fact-sheet figures (SPY, VOO, VTV, SPHQ, AVUV, stamped April 15, 2026) had no recorded
source: SPY and VOO matched in all five fields, and #468 found them far from the issuers'
own. The method was fitted to them too (size bands offset from SPY's $250B; VALUE_SCALE
set from VTV's ratios), and no issuer set gives the five fields on one definition. Pinned:

  * the style box and its non-US callout are gone from the code, the Factor Profile
    page and the quarterly PDF;
  * data/etf_metadata.json holds the durations only, so Q3's lock waits on SCHP's
    June 30 figure and on nothing else from it;
  * every committed lock, in both books, holds no equity entry, records the restatement
    under input_corrections, and says so on its cover in one sentence; nothing else in
    its payload changes; #388's line about the fact sheets' date is gone;
  * tools/restate_demo_style_box.py restores the committed book exactly from the book
    with the entries put back, and a second run changes nothing;
  * the close-out no longer asks for fact-sheet figures.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from markupsafe import escape

from tests.conftest import FROZEN_BOOK, pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", FROZEN_BOOK]
QUARTERS = ["2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
EQUITY = ["SPY", "VOO", "VTV", "SPHQ", "AVUV"]
RESTATED_ON = "2026-09-28"
SENTENCE = ("Restated September 28, 2026 to correct an error. The equity style box is "
            "withdrawn: its fact-sheet figures had no recorded source.")


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _payloads(book) -> "dict[str, dict]":
    con = _ro(book)
    try:
        return {q: json.loads(p) for q, p in con.execute(
            "SELECT quarter_id, snapshot_data FROM quarter_snapshots")}
    finally:
        con.close()


def _tool():
    spec = importlib.util.spec_from_file_location(
        "restate_demo_style_box", ROOT / "tools" / "restate_demo_style_box.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the code ─────────────────────────────────────────────────────────────────

def test_the_style_box_is_gone_from_the_code():
    import src.positioning as positioning
    assert not (ROOT / "src" / "style_box.py").exists()
    for name in ("ETF_STYLE_BOX", "get_style_box_data", "build_style_box_figure",
                 "get_non_us_equity_data", "_portfolio_market_values"):
        assert not hasattr(positioning, name), name
    for path in ("pages/4_Factor_Profile.py", "templates/quarterly_report.html",
                 "src/reports.py", "src/positioning.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        for phrase in ("Equity Style Profile", "Non-US Equity Sleeve", "pos.style_box",
                       "STYLE_BOX", "get_style_box_data", "get_non_us_equity_data", "3×3",
                       "Style Box Methodology", "VALUE_SCALE", "Morningstar"):
            assert phrase not in text, (path, phrase)
    for path in ("README.md", "docs/architecture.md"):
        assert "style box" not in (ROOT / path).read_text(encoding="utf-8").lower(), path


# ── the metadata file ───────────────────────────────────────────────────────

def test_the_metadata_file_holds_durations_only():
    from src.etf_metadata import META_PATH
    meta = json.loads(Path(META_PATH).read_text())
    entries = {k: v for k, v in meta.items() if not k.startswith("_")}
    assert set(entries) == {"VGIT", "SCHP", "IEF", "TIP", "BIL", "AGG"}
    assert all(set(v) == {"duration_years", "duration_measure", "duration_source", "as_of"}
               for v in entries.values()), entries


def _extend_prices_through(book, day: str):
    con = sqlite3.connect(book)
    last = {t: (d, c) for t, d, c in con.execute(
        "SELECT p.ticker, p.price_date, p.close FROM prices p JOIN (SELECT ticker, "
        "MAX(price_date) mx FROM prices GROUP BY ticker) m ON p.ticker = m.ticker "
        "AND p.price_date = m.mx")}
    rows = []
    for t, (d, c) in last.items():
        cur = date.fromisoformat(d) + timedelta(days=1)
        while cur <= date.fromisoformat(day):
            if cur.weekday() < 5:
                rows.append((t, cur.isoformat(), c))
            cur += timedelta(days=1)
    with con:
        con.executemany("INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close) "
                        "VALUES (?, ?, ?, NULL)", rows)
    con.close()


def test_q3s_lock_waits_on_schps_duration_and_nothing_else_from_the_file(tmp_path, monkeypatch):
    """The committed file on October 1: every entry but SCHP's (June 30) is dated inside
    Q3, so the metadata waits on June 30. With SCHP re-dated inside Q3, it locks."""
    from src import etf_metadata
    from src.cache import capture_quarter_snapshot
    from src.input_lock import ETF_METADATA
    book = point_at_frozen_book(monkeypatch, tmp_path)
    _extend_prices_through(book, "2026-09-30")
    pin_today(monkeypatch, date(2026, 10, 1))
    try:
        meta = json.loads(Path(etf_metadata.META_PATH).read_text())
        outside = sorted(k for k, v in meta.items() if isinstance(v, dict)
                         and not "2026-07-01" <= v["as_of"] <= "2026-09-30")
        assert outside == ["SCHP"], outside
        snap, _ = capture_quarter_snapshot("2026Q3")
        assert snap.inputs_pending.get(ETF_METADATA) == "2026-06-30"

        con = sqlite3.connect(book)
        with con:
            assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q3'"
                               ).rowcount == 1
        con.close()
        meta["SCHP"]["as_of"] = "2026-09-30"
        (tmp_path / "meta.json").write_text(json.dumps(meta))
        monkeypatch.setattr(etf_metadata, "META_PATH", tmp_path / "meta.json")
        snap, _ = capture_quarter_snapshot("2026Q3")
        assert ETF_METADATA not in snap.inputs_pending and ETF_METADATA in snap.inputs
    finally:
        unpin_leftovers()


# ── the committed locks ─────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_committed_lock_is_restated(book):
    payloads = _payloads(book)
    assert sorted(payloads) == QUARTERS
    for q, blob in payloads.items():
        data = blob["inputs"]["etf_metadata"]["data"]
        assert not [t for t in EQUITY if t in data], (book.name, q)
        assert blob["input_corrections"]["etf_metadata"] == {
            "restated_on": RESTATED_ON, "removed": EQUITY}, (book.name, q)
    assert "hyg" in payloads["2026Q2"]["input_corrections"], "#386's record is kept"


# Each lock's payload less the five equity entries, as the SHA-256 of its json.dumps, read
# off main's data/demo.db and frozen book at 10313a9, before the restatement (the two books'
# locks are identical). The committed payload less the record must hash the same.
REST_BEFORE = {
    "2025Q2": "321a1646a3461bc8619d7c005d654e5cacc0581dc4d6ed018b7dbd4bff96f01a",
    "2025Q3": "89482e404f2edb92e12bbfbd07281692f13a1e3af8abc359475d26f9fc04ab73",
    "2025Q4": "4dcc4a13e0c09290dcd8dd0b85897d0fc55c49db650c892d34b684a436c55301",
    "2026Q1": "645bed3344db5ab5ea481c9bdd0097caa3cbee0fa48100f99f7b6b669a826660",
    "2026Q2": "8f2ef9cea78d790315a7b4e86d17673aa8b4231299754707cc65fdd1a40479ca",
}


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_nothing_else_in_any_lock_changed(book):
    for q, blob in _payloads(book).items():
        del blob["input_corrections"]["etf_metadata"]
        if not blob["input_corrections"]:
            del blob["input_corrections"]
        rest = hashlib.sha256(json.dumps(blob).encode("utf-8")).hexdigest()
        assert rest == REST_BEFORE[q], (book.name, q)


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_committed_lock_says_so_on_its_cover(book, tmp_path, monkeypatch):
    import src.db as db
    from src.cache import get_quarter_snapshot, style_box_withdrawn_note
    copy = tmp_path / book.name
    shutil.copyfile(book, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    for q in QUARTERS:
        assert style_box_withdrawn_note(get_quarter_snapshot(q)[0]) == SENTENCE, (book.name, q)
    assert style_box_withdrawn_note(None) is None


def test_the_q2_report_carries_the_sentence_and_no_style_box(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch)
    point_at_frozen_book(monkeypatch, tmp_path)
    try:
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
    finally:
        unpin_leftovers()
    assert html.count(str(escape(SENTENCE))) == 1
    for phrase in ("Equity Style Profile", "Non-US Equity Sleeve",
                   "fractional deviation from SPY", "ETF fact sheets dated"):
        assert phrase not in html, phrase
    assert "Fixed Income Effective Duration" in html, "the positioning duration line stays"


# ── the tool ─────────────────────────────────────────────────────────────────

def _with_the_entries_back(src: Path, dst: Path) -> None:
    """``src`` with the five equity entries back in every lock's metadata and the record
    removed: a before-image, rebuilt from the committed after-image. The figures are
    placeholders; the tool removes them whatever they are."""
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    con = sqlite3.connect(dst)
    with con:
        for q, p in con.execute("SELECT quarter_id, snapshot_data FROM quarter_snapshots"
                                ).fetchall():
            blob = json.loads(p)
            data = blob["inputs"]["etf_metadata"]["data"]
            for t in EQUITY:
                data[t] = {"weighted_avg_mcap_b": 1.0, "p_b": 1.0, "p_e": 1.0,
                           "div_yield": 0.01, "p_cf": 1.0, "as_of": "2026-04-15"}
            del blob["input_corrections"]["etf_metadata"]
            if not blob["input_corrections"]:
                del blob["input_corrections"]
            con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ?",
                        (json.dumps(blob), q))
    con.close()


def test_the_tool_restores_the_committed_book_exactly_and_twice_changes_nothing(
        tmp_path, monkeypatch, capsys):
    tool = _tool()
    before = tmp_path / "before.db"
    _with_the_entries_back(ROOT / "data" / "demo.db", before)
    monkeypatch.setattr(tool, "DEMO_DB", before)
    assert tool.main(date(2026, 9, 28)) == 0
    assert "5 lock(s) restated on 2026-09-28" in capsys.readouterr().out
    committed, rebuilt = _ro(ROOT / "data" / "demo.db"), _ro(before)
    try:
        sql = "SELECT quarter_id, snapshot_date, captured_at, snapshot_data FROM quarter_snapshots ORDER BY 1"
        assert rebuilt.execute(sql).fetchall() == committed.execute(sql).fetchall()
    finally:
        committed.close()
        rebuilt.close()
    assert tool.main(date(2026, 9, 29)) == 0
    assert "0 lock(s) restated" in capsys.readouterr().out


def test_the_tool_refuses_a_lock_missing_an_entry(tmp_path, monkeypatch):
    tool = _tool()
    db = tmp_path / "odd.db"
    _with_the_entries_back(ROOT / "data" / "demo.db", db)
    con = sqlite3.connect(db)
    with con:
        (p,), = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                            "quarter_id = '2026Q2'").fetchall()
        blob = json.loads(p)
        del blob["inputs"]["etf_metadata"]["data"]["SPHQ"]
        con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = '2026Q2'",
                    (json.dumps(blob),))
    con.close()
    monkeypatch.setattr(tool, "DEMO_DB", db)
    with pytest.raises(SystemExit, match="lacks"):
        tool.main(date(2026, 9, 28))


# ── the close-out ───────────────────────────────────────────────────────────

def test_the_close_out_asks_for_durations_not_fact_sheet_figures():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = text.split("## Quarterly close-out", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(section.split())
    assert "data/etf_metadata.json" in flat and "`as_of`" in flat and "duration" in flat
    for phrase in ("style box", "fact-sheet figures", "weighted-average market cap",
                   "tests/test_fact_sheet_dates.py"):
        assert phrase not in flat, phrase
