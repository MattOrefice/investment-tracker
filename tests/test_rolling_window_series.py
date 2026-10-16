"""The rolling-window FRED series take the newest stored copy (#442, decided by the owner
on October 16, 2026).

FRED serves the ICE BofA option-adjusted spreads over a rolling window of about three
years, so no stored copy reaches back further than its fetch day's window and each later
copy starts later. macro._read_stored (#429) takes the newest copy that starts as early as
any stored one, which for these picked an old copy: demo.db's HY OAS served the copy
fetched 2026-05-03, ending 2026-04-30, where one fetched 2026-05-27, ending 2026-05-26,
is stored. The three ICE series take the newest copy now; every other series keeps #429's
rule (tests/test_fred_warm.py holds it for T10Y2Y).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

import src.db as db
from src import macro

ROOT = Path(__file__).resolve().parent.parent
ICE = ("BAMLH0A0HYM2", "BAMLC0A0CM", "BAMLH0A3HYC")


@pytest.fixture
def book(tmp_path, monkeypatch):
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return copy


def test_the_rolling_window_series_are_the_three_ice_series_the_page_reads():
    page = {sid for sid, _ in macro.MACRO_PAGE_SERIES}
    assert macro.ROLLING_WINDOW_SERIES == set(ICE)
    assert macro.ROLLING_WINDOW_SERIES <= page


@pytest.mark.parametrize("series_id, start", [
    ("BAMLH0A0HYM2", "1996-12-31"), ("BAMLC0A0CM", "1996-01-01"), ("BAMLH0A3HYC", "1996-01-01")])
def test_each_ice_series_serves_its_newest_stored_copy(book, series_id, start):
    s, fetched = macro._read_stored(series_id, start)
    assert fetched == "2026-05-27"
    assert str(s.dropna().index[-1].date()) == "2026-05-26"
    assert str(s.dropna().index[0].date()) == "2023-05-29", "the newest copy starts later"


def _row(series_id, fetched, first, last="2026-08-29"):
    idx = pd.bdate_range(first, last)
    return (series_id, fetched, json.dumps({"dates": [str(d.date()) for d in idx],
                                            "values": [1.0] * len(idx)}))


def test_a_newer_later_starting_copy_wins_only_for_an_ice_series(book):
    """The contrast, through the same function: a copy fetched later and starting later
    is served for HY OAS, and not for T10Y2Y, where #429's rule keeps the copy that
    reaches back to the request."""
    with db.get_connection() as conn:
        conn.execute("INSERT INTO macro_cache VALUES (?, ?, ?)",
                     _row("BAMLH0A0HYM2", "2026-09-01", "2023-08-31"))
        conn.execute("INSERT INTO macro_cache VALUES (?, ?, ?)",
                     _row("T10Y2Y", "2026-09-01", "2023-08-31"))
    assert macro._read_stored("BAMLH0A0HYM2", "1996-12-31")[1] == "2026-09-01"
    assert macro._read_stored("T10Y2Y", "1976-06-01")[1] == "2026-07-16"
