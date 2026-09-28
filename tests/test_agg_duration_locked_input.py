"""The Bloomberg US Agg's duration becomes a dated, locked input, AGG's (#462).

The FI sleeve's duration was compared with positioning.BLOOMBERG_AGG_DURATION_YEARS, an
undated 6.0 the lock never snapshotted. It now lives in data/etf_metadata.json like the
funds' durations (#455), with its measure, source and as-of date, so a quarter lock takes
it with the fact-sheet data. Bloomberg's own index page could not be read (HTTP 403), so
the figure is AGG's, the iShares ETF tracking the Agg, and every place it is shown names
it as AGG's. Pinned here:

  * the file's AGG entry is dated, sourced and labelled as AGG's;
  * the live metric, the report's duration line and the Performance page read it, and
    name it as AGG's;
  * a lock holding it serves its own; every committed lock holds none, so it reports the
    6.0 it was reported with, under the Agg's name, and its cover sentence says the
    Agg's figure was undated too.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import FROZEN_BOOK, pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", FROZEN_BOOK]
QUARTERS = ("2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")
AGG_NAME = "AGG, the iShares ETF tracking the Bloomberg US Agg"


def _point_at(mp, tmp_path, book: Path) -> Path:
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
    import streamlit as st
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    st.cache_data.clear()
    yield path
    st.cache_data.clear()
    unpin_leftovers()


def test_the_file_carries_aggs_duration_dated_sourced_and_labelled_as_aggs():
    from src.positioning import AGG_TICKER
    from src.etf_metadata import META_PATH
    e = json.loads(Path(META_PATH).read_text())[AGG_TICKER]
    assert isinstance(e["duration_years"], float) and e["duration_years"] > 0
    assert e["duration_measure"].strip() and date.fromisoformat(e["as_of"])
    assert "AGG" in e["duration_source"] and "not the index's" in e["duration_source"]


def test_the_live_figures_read_it_and_name_it_as_aggs(tmp_path, monkeypatch):
    from unittest.mock import patch
    from src import etf_metadata, positioning, reports
    from tests.test_positioning import _BASELINE_ROWS, _make_sw
    meta = json.loads(Path(etf_metadata.META_PATH).read_text())
    meta["AGG"]["duration_years"] = 9.9
    out = tmp_path / "meta.json"
    out.write_text(json.dumps(meta))
    monkeypatch.setattr(etf_metadata, "META_PATH", out)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    assert positioning.benchmark_duration() == {"years": 9.9, "name": AGG_NAME, "short": "AGG"}
    with patch("src.positioning.get_sleeve_weights_on_date",
               return_value=_make_sw(dict(_BASELINE_ROWS))):
        dur = positioning.get_effective_duration("2026-09-25")
        line = reports._build_positioning_section("2026-09-25")["duration_line"]
    assert (dur["agg_benchmark"], dur["agg_name"], dur["agg_short"]) == (9.9, AGG_NAME, "AGG")
    assert f"yrs vs 9.9 yrs for {AGG_NAME} (" in line


def test_a_lock_holding_aggs_duration_serves_its_own():
    from src.input_lock import ETF_METADATA, input_context
    from src.positioning import benchmark_duration
    held = {"SPY": {"as_of": "2026-09-15"},
            "AGG": {"duration_years": 1.1, "as_of": "2026-09-15"}}
    with input_context({ETF_METADATA: held}, {}, "2026-09-30"):
        assert benchmark_duration()["years"] == 1.1
    assert benchmark_duration()["years"] != 1.1, "outside the lock, the file's"


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_committed_lock_reports_the_undated_six_under_the_aggs_name(book, tmp_path,
                                                                         monkeypatch):
    from src.cache import get_quarter_snapshot, snapshot_price_context, undated_durations_note
    from src.positioning import benchmark_duration
    _point_at(monkeypatch, tmp_path, book)
    assert benchmark_duration()["years"] != 6.0, "premise: the file's figure differs"
    for q in QUARTERS:
        snap = get_quarter_snapshot(q)[0]
        with snapshot_price_context(snap):
            assert benchmark_duration() == {"years": 6.0, "name": "the Bloomberg US Agg",
                                            "short": "Bloomberg US Agg"}, (book.name, q)
        assert undated_durations_note(snap).endswith(
            "and the Bloomberg US Agg duration they were compared with was undated too."), q
    # A lock from before inputs were locked holds no metadata at all: the undated 6.0 too.
    before_inputs = snap._replace(inputs=None, inputs_pending=None, inputs_rule=None)
    with snapshot_price_context(before_inputs):
        assert benchmark_duration()["years"] == 6.0


def test_the_performance_page_names_it_as_aggs(frozen):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    metric = next(m for m in at.metric if m.label == "FI Sleeve Duration (Core FI + TIPS)")
    assert metric.delta.endswith(" yrs vs AGG (5.7 yrs)"), metric.delta
    caption = next(str(c.value) for c in at.caption if "Durations from each fund" in str(c.value))
    assert f"{AGG_NAME}." in caption or f"{AGG_NAME}." in " ".join(
        str(c.value) for c in at.caption), caption
    assert "AGG 5.7 yrs, effective duration as of September 25, 2026" in caption
