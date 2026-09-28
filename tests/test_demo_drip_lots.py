"""The demo's committed DRIP lots are exactly what the write path derives from its
committed trades, prices and dividends (#406 item 12).

They used to be a fixture computed once and never regenerated, and they drifted from
the book: priced at adj_close, the holiday pay dates reinvested before the cash
arrived, May 2026 written twice for VGIT and SCHP, nothing after 2026-05-06, none for
AVDV, AVIV or IDHQ, and VEA's computed on shares the re-dated Phase 39 sale had
already sold. tools/rebuild_demo_drip_lots.py rebuilt them. This file keeps them
rebuilt: when the committed prices advance (the quarterly close-out), the derivation
gains the new quarter's lots and the fixture test fails until the tool is re-run.

Also pinned: every lot at the raw close on its date, positions that still close,
the cases #406 item 12 named, and the cover note on every locked quarter.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import pin_today, unpin_leftovers
from tools.rebuild_demo_drip_lots import (
    derive_lots,
    lots_differ,
    positions_that_do_not_close,
    read_lots,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
LOCKED = ("2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")


def _mismatches(path: Path) -> "list[str]":
    return lots_differ(read_lots(path), derive_lots(path))


@pytest.fixture
def copy(tmp_path):
    path = tmp_path / "demo.db"
    shutil.copyfile(DEMO_DB, path)
    os.chmod(path, 0o644)
    return path


def _exec(path: Path, sql: str, *args) -> int:
    con = sqlite3.connect(path)
    try:
        n = con.execute(sql, args).rowcount
        con.commit()
        return n
    finally:
        con.close()


# ── the fixture is the derivation ───────────────────────────────────────────────

def test_the_committed_lots_are_what_the_write_path_derives():
    """Fails after the committed prices advance until the lots are regenerated:
    python tools/rebuild_demo_drip_lots.py (the close-out's step)."""
    assert _mismatches(DEMO_DB) == []


def test_a_dropped_lot_fails_the_fixture(copy):
    assert _exec(copy, "DELETE FROM trades WHERE trade_id = (SELECT MAX(trade_id) FROM "
                       "trades WHERE lot_source = 'drip')") == 1
    assert any("only on the right" in m for m in _mismatches(copy))


def test_a_duplicated_lot_fails_the_fixture(copy):
    assert _exec(copy, "INSERT INTO trades (account_id, ticker, trade_date, action, shares, "
                       "price, fees, notes, lot_source) SELECT account_id, ticker, trade_date, "
                       "action, shares, price, fees, notes, lot_source FROM trades WHERE "
                       "lot_source = 'drip' AND ticker = 'VGIT' ORDER BY trade_date LIMIT 1") == 1
    assert any("2 lots on the left" in m for m in _mismatches(copy))


def test_a_redated_trade_without_regenerating_fails_the_fixture(copy):
    """The Phase 39 VEA sale moved from inception to August 2025: VEA held more shares
    through its June and July distributions, so their lots derive larger."""
    assert _exec(copy, "UPDATE trades SET trade_date = '2025-08-01' WHERE ticker = 'VEA' "
                       "AND LOWER(action) = 'sell'") == 1
    assert any(m.startswith("VEA ") and "shares" in m for m in _mismatches(copy))


# ── what the derivation holds ───────────────────────────────────────────────────

def test_every_lot_is_priced_at_the_raw_close_on_its_date():
    con = sqlite3.connect(f"file:{DEMO_DB.as_posix()}?mode=ro", uri=True)
    try:
        off = con.execute(
            "SELECT t.ticker, t.trade_date, t.price, p.close FROM trades t LEFT JOIN prices p "
            "ON p.ticker = t.ticker AND p.price_date = t.trade_date "
            "WHERE t.lot_source = 'drip' AND (p.close IS NULL OR p.close != t.price)").fetchall()
    finally:
        con.close()
    assert off == []


def test_the_cases_406_named():
    lots = {(t, d) for t, d, _s, _p in read_lots(DEMO_DB)}
    for t in ("VGIT", "SCHP"):
        # the holiday pair: pay dates April 3 (Good Friday) and July 3 (Independence
        # Day observed) reinvest at the next close
        assert {(t, "2026-04-06"), (t, "2026-07-06")} <= lots, t
        assert not {(t, "2026-04-03"), (t, "2026-07-03")} & lots, t
        # May's distribution is one lot, not two
        assert len([d for tt, d in lots if tt == t and d.startswith("2026-05")]) == 1, t
    for t in ("AVDV", "AVIV", "IDHQ"):
        assert any(tt == t for tt, _d in lots), f"{t} has no lot"
    assert max(d for _t, d in lots) > "2026-05-06", "lots stop where the old fixture did"


def test_positions_still_close():
    assert positions_that_do_not_close(DEMO_DB) == []


def test_an_oversized_sale_is_reported(copy):
    """The check can fail: a VEA sale larger than every share VEA ever held."""
    assert _exec(copy, "UPDATE trades SET shares = 50 WHERE ticker = 'VEA' "
                       "AND LOWER(action) = 'sell'") == 1
    assert any(p.startswith("VEA ") for p in positions_that_do_not_close(copy))


# ── every locked quarter says so ────────────────────────────────────────────────

@pytest.fixture
def demo(monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", DEMO_DB)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)


def test_every_locked_quarter_records_the_rebuild_and_notes_it(demo):
    from src.cache import get_quarter_snapshot, lot_rebuild_note
    for qid in LOCKED:
        snap = get_quarter_snapshot(qid)[0]
        assert snap.lot_rebuild and snap.lot_rebuild["restated_on"], qid
        note = lot_rebuild_note(snap)
        assert note.startswith("Restated ") and (
            ": the dividend reinvestment lots were rebuilt from the trade ledger and priced "
            "at the actual close on each reinvestment date." in note), note


def test_a_lock_without_the_record_has_no_note():
    from src.cache import SnapshotFrames, lot_rebuild_note
    import pandas as pd
    assert lot_rebuild_note(SnapshotFrames(adj_close=pd.DataFrame())) is None
    assert lot_rebuild_note(None) is None


def test_a_second_record_does_not_redate_the_first(copy, monkeypatch):
    from datetime import date
    import src.db as db
    from src.cache import get_quarter_snapshot, record_lot_rebuild
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    first = get_quarter_snapshot("2026Q2")[0].lot_rebuild
    assert record_lot_rebuild("2026Q2", date(2027, 1, 1)) is False
    assert get_quarter_snapshot("2026Q2")[0].lot_rebuild == first


def test_the_q2_report_cover_states_the_rebuild(copy, monkeypatch):
    """Rendered, on a copy of the committed demo.db, offline: the note reaches the cover."""
    import socket
    import src.db as db
    import src.prices as prices
    from markupsafe import escape
    from src import reports
    from src.cache import get_quarter_snapshot, lot_rebuild_note

    def _offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch)
    try:
        note = lot_rebuild_note(get_quarter_snapshot("2026Q2")[0])
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
        assert str(escape(note)) in html
    finally:
        unpin_leftovers()
