"""HYG from the price layer, and no locked section reads a stale side file (#386).

The FI regression's credit proxy, HYG, came from data/cache/prices_hyg.parquet, a
committed file that ended 2026-05-05 while nothing refreshed it. The quarter lock
took it as read, ungated, so Q2 2026's fixed-income section was built on a proxy held
flat for the quarter's last 56 days, and the coverage guard never saw it because it
watched only the price layer. Pinned here:

  * the parquet is retired and HYG is read from the price layer;
  * a lock gates HYG on coverage like the French data: short, the factor section
    renders pending with HYG's end date, and locks once the data covers the quarter;
  * the hand-kept ETF fact-sheet file covers a quarter only when every source date
    falls inside it; otherwise the style box renders pending and the rest of the
    positioning section locks;
  * the demo's Q2 lock carries the corrected HYG and says so on the cover, and the
    four locks whose HYG covered their quarters are untouched.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
Q2 = ("2026-03-31", "2026-06-30")


@pytest.fixture
def book(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


def _html(monkeypatch, start, end):
    """The report's rendered HTML, as generate_quarterly_report_bytes renders it."""
    from src import reports
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    return reports.generate_quarterly_report_bytes(start, end, is_demo=True).decode("utf-8")


# ── the parquet is retired ─────────────────────────────────────────────────────

def test_no_tracked_file_carries_hyg_and_the_series_is_the_price_layer(book):
    from src import factors
    from src.prices import get_prices, total_return_series
    tracked = subprocess.run(["git", "ls-files", "data"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    assert not [f for f in tracked if "hyg" in f.lower()], tracked
    assert not hasattr(factors, "_HYG_CACHE")
    got = factors.hyg_credit_series("2025-05-01", Q2[1])
    want = total_return_series(get_prices("HYG", "2025-05-01", Q2[1]))
    assert len(got) > 200, "premise: the frozen book prices HYG through the quarter"
    assert list(got.values) == list(want.values)


# ── HYG is gated like the French data ──────────────────────────────────────────

def _cut_hyg_after(path, day):
    con = sqlite3.connect(path)
    with con:
        n = con.execute("DELETE FROM prices WHERE ticker = 'HYG' AND price_date > ?",
                        (day,)).rowcount
    con.close()
    assert n, "premise: the book prices HYG after the cut"


def test_a_short_hyg_leaves_the_factor_section_pending_then_locks(book, tmp_path, monkeypatch):
    from src import reports
    from src.cache import capture_quarter_snapshot, complete_quarter_inputs, get_quarter_snapshot
    from src.input_lock import FF5_US, HYG
    saved = tmp_path / "saved.db"
    saved.write_bytes(Path(book).read_bytes())
    _cut_hyg_after(book, "2026-05-05")

    snap, _ = capture_quarter_snapshot("2026Q2")
    assert snap.inputs_pending == {HYG: "2026-05-05"}
    note = reports._pending_note(snap, "factor")
    assert note == ("Pending: this section locks when the HYG price data covers the quarter. "
                    "The HYG price data on file ends May 5, 2026; the quarter ended "
                    "June 30, 2026.")
    assert reports._pending_note(snap, "benchmark") is None, "benchmark reads no HYG"
    html = _html(monkeypatch, *Q2)
    assert note in html

    # The price data arrives: the next generation locks HYG, and nothing already
    # locked moves.
    con, src = sqlite3.connect(book), sqlite3.connect(saved)
    rows = src.execute("SELECT * FROM prices WHERE ticker = 'HYG' AND price_date > '2026-05-05'"
                       ).fetchall()
    with con:
        con.executemany(f"INSERT INTO prices VALUES ({','.join('?' * len(rows[0]))})", rows)
    con.close()
    src.close()
    before = get_quarter_snapshot("2026Q2")[0]
    after = complete_quarter_inputs("2026Q2")
    assert after.inputs_pending == {}
    assert after.inputs[HYG].index.max() == pd.Timestamp("2026-06-30")
    pd.testing.assert_frame_equal(after.inputs[FF5_US], before.inputs[FF5_US])
    assert reports._pending_note(after, "factor") is None


def test_french_and_hyg_short_together_name_both(book):
    from src import reports
    from src.cache import SnapshotFrames
    from src.input_lock import FF5_US, HYG, UMD
    snap = SnapshotFrames(adj_close=pd.DataFrame(), quarter_end="2026-09-30",
                          inputs_pending={FF5_US: "2026-08-31", UMD: "2026-08-31",
                                          HYG: "2026-09-12"})
    assert reports._pending_note(snap, "factor") == (
        "Pending: this section locks when the factor data and the HYG price data cover "
        "the quarter. The Fama-French factor data on file ends August 31, 2026; the HYG "
        "price data on file ends September 12, 2026; the quarter ended September 30, 2026.")


# ── the ETF fact-sheet file is gated on its source dates ───────────────────────

def _metadata_dated(tmp_path, monkeypatch, *stamps):
    from src import style_box
    meta = json.loads(Path(style_box._META_PATH).read_text())
    keys = [k for k in meta if not k.startswith("_")]
    assert len(keys) >= len(stamps)
    for i, k in enumerate(keys):
        meta[k]["as_of"] = stamps[min(i, len(stamps) - 1)]
    out = tmp_path / "meta.json"
    out.write_text(json.dumps(meta))
    monkeypatch.setattr(style_box, "_META_PATH", out)


@pytest.mark.parametrize("stamps, covered", [
    (("2026-04-15",), True),                    # the committed file, inside Q2
    (("2026-04-01", "2026-06-30"), True),       # the quarter's first and last days
    (("2026-03-31",), False),                   # the day before the quarter
    (("2026-04-15", "2026-07-01"), False),      # one entry dated after it
])
def test_etf_metadata_covers_a_quarter_only_when_every_source_date_is_inside_it(
        book, tmp_path, monkeypatch, stamps, covered):
    from src.cache import capture_quarter_snapshot
    from src.input_lock import ETF_METADATA
    _metadata_dated(tmp_path, monkeypatch, *stamps)
    snap, _ = capture_quarter_snapshot("2026Q2")
    assert (ETF_METADATA not in snap.inputs_pending) == covered
    assert (ETF_METADATA in snap.inputs) == covered


def test_a_stale_fact_sheet_file_leaves_only_the_style_box_pending(book, tmp_path, monkeypatch):
    from src import reports
    from src.cache import capture_quarter_snapshot
    _metadata_dated(tmp_path, monkeypatch, "2026-01-15")
    snap, _ = capture_quarter_snapshot("2026Q2")
    note = reports._pending_note(snap, "positioning", subject="style box")
    assert note == ("Pending: this style box locks when the ETF fact-sheet data covers the "
                    "quarter. The ETF fact-sheet data on file is dated January 15, 2026; the "
                    "quarter ended June 30, 2026.")
    html = _html(monkeypatch, *Q2)
    assert note in html
    assert "Equity Style Profile" in html and "Non-US Equity Sleeve" in html, (
        "the price-based rest of the section still renders")
    assert 'alt="Equity Style Profile"' not in html, "no chart from the stale file"


# ── the demo's committed locks ─────────────────────────────────────────────────

def _demo(monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", ROOT / "data" / "demo.db")
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)


def test_the_demo_q2_lock_carries_the_corrected_hyg_and_says_so(monkeypatch):
    from src.cache import _parse_quarter_end, get_quarter_snapshot, input_corrections_note
    from src.input_lock import HYG
    _demo(monkeypatch)
    q2 = get_quarter_snapshot("2026Q2")[0]
    assert q2.inputs[HYG].index.max() == pd.Timestamp("2026-06-30")
    fix = q2.input_corrections[HYG]
    assert fix["was_through"] == "2026-05-05"
    note = input_corrections_note(q2)
    assert note.endswith(
        "to correct an incomplete input. The fixed-income regression's credit proxy, "
        "HYG, came from a committed file that ended May 5, 2026, 56 days before the "
        "quarter did, so it was held flat for the rest of the quarter. It now comes "
        "from the price data through the quarter's end."), note
    for qid in ("2025Q2", "2025Q3", "2025Q4", "2026Q1"):
        snap = get_quarter_snapshot(qid)[0]
        assert snap.input_corrections is None and input_corrections_note(snap) is None, qid
        assert snap.inputs[HYG].index.max() == pd.Timestamp(_parse_quarter_end(qid)), qid


def test_the_q2_report_cover_states_the_correction(tmp_path, monkeypatch):
    """Rendered, on a copy of the committed demo.db: the note reaches the cover."""
    import os
    import shutil
    import socket
    import src.db as db
    import src.prices as prices
    from src import reports
    from src.cache import get_quarter_snapshot, input_corrections_note
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)

    def _offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    try:
        note = input_corrections_note(get_quarter_snapshot("2026Q2")[0])
        assert note
        from markupsafe import escape
        assert str(escape(note)) in _html(monkeypatch, *Q2), "autoescaped, as rendered"
    finally:
        unpin_leftovers()


def test_restating_a_lock_whose_hyg_covered_changes_nothing(book):
    from datetime import date
    from src.cache import capture_quarter_snapshot, restate_short_hyg
    capture_quarter_snapshot("2026Q2")
    con = sqlite3.connect(book)
    before = con.execute("SELECT * FROM quarter_snapshots").fetchall()
    assert restate_short_hyg("2026Q2", date(2026, 9, 26)) is None
    assert con.execute("SELECT * FROM quarter_snapshots").fetchall() == before
    con.close()
