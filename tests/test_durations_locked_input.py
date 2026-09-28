"""Fund durations are a dated, locked input (#455).

They lived in positioning.ETF_DURATION, a table with no date or source that was out of
date by September 2026: VGIT 5.5 years against Vanguard's 4.9, IEF 7.5 against
iShares' 6.86. They now live in data/etf_metadata.json with the rest of the fact-sheet
data, each with its measure, source and as-of date, so a quarter lock snapshots them.
Pinned here:

  * every fund the duration metric or the Risk page reads carries a dated, sourced
    duration in the file;
  * the live metric and {{dur:}} read the file (the Risk page's IEF duration is pinned
    in test_risk_scenarios);
  * a lock holding durations serves its own, whatever the file says;
  * every committed lock holds none, so it reports the undated table it was reported
    with, and its cover says so in one sentence; a lock holding durations, or still
    waiting on its metadata, carries no such sentence;
  * a lock still waiting on its metadata renders the duration line pending.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from markupsafe import escape

from tests.conftest import FROZEN_BOOK, pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", FROZEN_BOOK]
QUARTERS = ("2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")
BOND_FUNDS = ("VGIT", "SCHP", "IEF", "TIP", "BIL")
Q2 = ("2026-03-31", "2026-06-30")
# Every committed lock predates #455 and #462, so its sentence names both (#462).
SENTENCE = ("This quarter's fixed-income durations came from an undated table later "
            "found out of date, and the Bloomberg US Agg duration they were compared "
            "with was undated too.")
FUNDS_ONLY = ("This quarter's fixed-income durations came from an undated table later "
              "found out of date.")
AGG_ONLY = ("This quarter's Bloomberg US Agg duration, which its fixed-income "
            "durations were compared with, was undated.")
# The frozen book's Q2 2026 report, as it was reported before the move.
Q2_DURATION_LINE = (
    "Fixed Income sleeve (Core FI + TIPS) effective duration: 6.0 yrs vs 6.0 yrs for the "
    "Bloomberg US Agg (in line with benchmark). FI weight: 7.7% of portfolio. Cash/SPAXX "
    "(1.5%) is excluded: it is not a duration-bearing asset, and the Bloomberg Agg "
    "excludes it."
)


def _point_at(mp, tmp_path, book: Path) -> Path:
    """A copy of ``book``, offline."""
    import src.db as db
    import src.prices as prices
    copy = tmp_path / book.name
    shutil.copyfile(book, copy)
    os.chmod(copy, 0o644)

    def _offline(*_a, **_k):
        raise OSError("network blocked")

    mp.setattr(socket, "getaddrinfo", _offline)
    mp.setattr(prices._SESSION, "get", _offline)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    return copy


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


def _html(monkeypatch, start, end):
    from src import reports
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    return reports.generate_quarterly_report_bytes(start, end, is_demo=True).decode("utf-8")


# ── the file ──────────────────────────────────────────────────────────────────

def test_every_bond_fund_carries_a_dated_sourced_duration():
    from src.positioning import _FI_SLEEVE_HOLDING
    from src.etf_metadata import META_PATH
    meta = json.loads(Path(META_PATH).read_text())
    for t in BOND_FUNDS:
        e = meta[t]
        assert isinstance(e["duration_years"], float) and e["duration_years"] > 0, t
        assert e["duration_measure"].strip() and e["duration_source"].strip(), t
        assert date.fromisoformat(e["as_of"]), t
    read = {t for s, t in _FI_SLEEVE_HOLDING.items() if s != "Cash / SPAXX"} | {"IEF"}
    assert read <= set(BOND_FUNDS), "every fund the metric and the Risk page read"


# ── live readers read the file ────────────────────────────────────────────────

def test_the_live_figures_read_the_file(tmp_path, monkeypatch):
    from unittest.mock import patch
    from src import etf_metadata, positioning
    from src.prose_figures import render
    from tests.test_positioning import _BASELINE_ROWS, _make_sw
    meta = json.loads(Path(etf_metadata.META_PATH).read_text())
    for t, years in (("VGIT", 3.3), ("SCHP", 8.8), ("IEF", 9.1)):
        meta[t]["duration_years"] = years
    out = tmp_path / "meta.json"
    out.write_text(json.dumps(meta))
    monkeypatch.setattr(etf_metadata, "META_PATH", out)
    assert positioning.live_fund_durations()["VGIT"] == 3.3
    assert positioning.fund_durations()["VGIT"] == 3.3, "no lock: the file's"
    assert render("{{dur:VGIT}} vs {{dur:IEF}}", targets={}) == "3.3 years vs 9.1 years"
    with patch("src.positioning.get_sleeve_weights_on_date",
               return_value=_make_sw(dict(_BASELINE_ROWS))):
        got = positioning.get_effective_duration("2026-09-25")
    assert got["fi_sleeve_duration"] == round((0.06 * 3.3 + 0.04 * 8.8) / 0.10, 1)


def test_the_performance_caption_names_each_funds_figure_measure_and_date():
    from src.positioning import live_duration_sources
    assert live_duration_sources() == (
        "VGIT 4.9 yrs, average duration as of August 31, 2026; "
        "SCHP 6.3 yrs, weighted average duration as of June 30, 2026; "
        "AGG 5.7 yrs, effective duration as of September 25, 2026")   # #462
    page = (ROOT / "pages" / "2_Performance.py").read_text(encoding="utf-8")
    assert ("Durations from each fund's issuer, and the benchmark's from AGG's: "
            in page and "{live_duration_sources()}." in page)
    assert "5.5 yrs" not in page and "Q1 2026" not in page, "the retired table's caption"


# ── locks ─────────────────────────────────────────────────────────────────────

def test_a_lock_holding_durations_serves_its_own():
    from src.input_lock import ETF_METADATA, input_context
    from src.positioning import fund_durations, live_fund_durations
    held = {"SPY": {"as_of": "2026-09-15"},
            "VGIT": {"duration_years": 1.1, "as_of": "2026-09-15"}}
    with input_context({ETF_METADATA: held}, {}, "2026-09-30"):
        assert fund_durations() == {"VGIT": 1.1}
        assert live_fund_durations()["VGIT"] != 1.1, "the live reader never reads a lock"


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_committed_lock_reports_the_undated_table_and_says_so(book, tmp_path,
                                                                     monkeypatch):
    from src.cache import get_quarter_snapshot, snapshot_price_context, undated_durations_note
    from src.positioning import _UNDATED_DURATIONS, fund_durations, live_fund_durations
    _point_at(monkeypatch, tmp_path, book)
    assert live_fund_durations() != _UNDATED_DURATIONS, "premise: the file has moved on"
    for q in QUARTERS:
        snap = get_quarter_snapshot(q)[0]
        assert snap is not None, (book.name, q)
        with snapshot_price_context(snap):
            assert fund_durations() == _UNDATED_DURATIONS, (book.name, q)
        assert undated_durations_note(snap) == SENTENCE, (book.name, q)


def test_q2s_report_keeps_its_durations_and_its_cover_says_where_they_came_from(
        frozen, monkeypatch):
    from src import reports
    from src.cache import get_quarter_snapshot, snapshot_price_context
    snap = get_quarter_snapshot("2026Q2")[0]
    with snapshot_price_context(snap):
        assert reports._build_positioning_section(Q2[1])["duration_line"] == Q2_DURATION_LINE
    # The contrast: the same section without the lock reads the file.
    assert reports._build_positioning_section(Q2[1])["duration_line"] != Q2_DURATION_LINE
    html = _html(monkeypatch, *Q2)
    assert escape(Q2_DURATION_LINE) in html
    sentence = str(escape(SENTENCE))
    assert html.count(sentence) == 1
    # In the inputs note's paragraph, after its last sentence, not a line of its own.
    assert f"when they were generated. {sentence}</p>" in html


def test_the_sentence_only_where_the_undated_table_was_used(frozen):
    from src.cache import get_quarter_snapshot, snapshot_price_context, undated_durations_note
    from src.input_lock import ETF_METADATA
    from src.positioning import _UNDATED_DURATIONS, fund_durations
    snap = get_quarter_snapshot("2026Q2")[0]
    meta = dict(snap.inputs[ETF_METADATA])
    meta["VGIT"] = {"duration_years": 4.9, "as_of": "2026-06-15"}
    holding = snap._replace(inputs={**snap.inputs, ETF_METADATA: meta})
    with snapshot_price_context(holding):
        assert fund_durations() == {"VGIT": 4.9}
    # The funds dated, the Agg not (#462): the sentence names the Agg alone.
    assert undated_durations_note(holding) == AGG_ONLY
    both = dict(meta, AGG={"duration_years": 5.7, "as_of": "2026-06-15"})
    assert undated_durations_note(
        snap._replace(inputs={**snap.inputs, ETF_METADATA: both})) is None
    agg_only = dict(snap.inputs[ETF_METADATA], AGG={"duration_years": 5.7, "as_of": "2026-06-15"})
    assert undated_durations_note(
        snap._replace(inputs={**snap.inputs, ETF_METADATA: agg_only})) == FUNDS_ONLY

    waiting = snap._replace(
        inputs={k: v for k, v in snap.inputs.items() if k != ETF_METADATA},
        inputs_pending={ETF_METADATA: "2026-04-15"})
    assert undated_durations_note(waiting) is None, "a pending line, not undated figures"

    before_inputs = snap._replace(inputs=None, inputs_pending=None, inputs_rule=None)
    with snapshot_price_context(before_inputs):
        assert fund_durations() == _UNDATED_DURATIONS
    assert undated_durations_note(before_inputs) == SENTENCE
    assert undated_durations_note(None) is None


def test_a_lock_waiting_on_its_metadata_renders_the_duration_pending(frozen, monkeypatch):
    """A fresh Q2 lock on the committed file: its durations are dated after Q2, so the
    metadata waits (#389), and the duration line with it."""
    from src.cache import get_quarter_snapshot
    from src.input_lock import ETF_METADATA
    con = sqlite3.connect(frozen)
    with con:
        assert con.execute("DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'"
                           ).rowcount == 1
    con.close()
    pin_today(monkeypatch, date(2026, 7, 1))
    html = _html(monkeypatch, *Q2)
    snap = get_quarter_snapshot("2026Q2")[0]
    assert ETF_METADATA in snap.inputs_pending, "premise: the fresh lock waits on the file"
    assert ("Pending: this duration figure locks when the ETF fact-sheet data covers the "
            "quarter.") in html
    assert "effective duration:" not in html
    assert "Fixed Income Effective Duration" in html, "the heading stands over the line"
