"""The FRED cache's day is New York's date (listed in #451, decided by the owner on
September 28, 2026).

macro keyed its cache by the server's date: a row's fetch_date, the day a read looks
for, and the warm's once-a-day key. A stored copy past the page's wait is labelled
"fetched <date>" from that fetch_date. On the demo's UTC server the date turns at 8 PM
Eastern, so the daily re-fetch came then, and a copy fetched that evening was labelled
with the next day. Every rendered date has been New York's since #451. Now the cache's
day is too, and the daily re-fetch comes at midnight Eastern.

Each test runs on a UTC machine at 11 AM and at 9 PM Eastern on one New York day
(conftest's new_york_day). At 9 PM the machine's date is the next day.
"""
from __future__ import annotations

import os
import shutil
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

import src.db as db
import src.macro as macro

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A scratch copy of demo.db with FRED stubbed: the fetches it was asked for."""
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    from src import demo_refresh
    monkeypatch.setattr(demo_refresh, "enabled", lambda: False)
    calls = []

    def fetch(series_id, start_date, end_date=None):
        calls.append(series_id)
        idx = pd.bdate_range("2026-01-02", "2026-10-09")
        return pd.Series(1.0, index=idx, name=series_id)

    monkeypatch.setattr(macro, "fetch_fred_series", fetch)
    return calls


def _fetch_dates(series_id: str) -> "list[str]":
    with db.get_connection() as conn:
        return sorted(r[0] for r in conn.execute(
            "SELECT fetch_date FROM macro_cache WHERE series_id = ?", (series_id,)))


def test_a_row_is_stored_and_read_under_new_yorks_date(cache, new_york_day):
    assert macro._cache_day() == new_york_day.isoformat()
    macro.get_series("DGS10", "2026-01-01")
    assert new_york_day.isoformat() in _fetch_dates("DGS10")
    assert (new_york_day + timedelta(days=1)).isoformat() not in _fetch_dates("DGS10")
    macro.get_series("DGS10", "2026-01-01")
    assert cache == ["DGS10"], "read again from the row it stored"


def test_a_stored_copy_is_labelled_with_new_yorks_date(cache, new_york_day):
    macro.get_series("DGS10", "2026-01-01")
    stored, fetched = macro._read_stored("DGS10", "2026-01-01")
    assert stored is not None and fetched == new_york_day.isoformat()


@pytest.fixture
def warm(monkeypatch):
    """The warm, switched on, with a target that fetches nothing: the threads it
    started, each joined before the test ends."""
    started = []
    monkeypatch.setattr(macro, "warm_enabled", lambda: True)
    monkeypatch.setattr(macro, "_warm", lambda: started.append(1))
    monkeypatch.setattr(macro, "_WARM_THREAD", None)
    monkeypatch.setattr(macro, "_WARM_DAY", None)

    def start():
        t = macro.warm_cache_in_background()
        if t is not None:
            t.join(10)
            assert not t.is_alive()
        return t
    return start


def test_the_warm_runs_once_per_new_york_day(warm, new_york_day):
    assert warm() is not None
    assert macro._WARM_DAY == new_york_day.isoformat()
    assert warm() is None, "once a New York day"


def test_the_daily_re_fetch_comes_at_midnight_eastern(warm, new_york_day, monkeypatch):
    """A warm that ran earlier the same New York day is not run again at 9 PM Eastern,
    when the UTC date has turned; one that ran the day before is."""
    monkeypatch.setattr(macro, "_WARM_DAY", new_york_day.isoformat())
    assert warm() is None
    monkeypatch.setattr(macro, "_WARM_DAY", (new_york_day - timedelta(days=1)).isoformat())
    assert warm() is not None
