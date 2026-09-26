"""A cached FRED series covers requests from the start it was fetched for (#377).

get_series served today's row only when the series' first observation fell on or
before the requested start, so a series that begins after it (DGS10 from 1990-01-02
for 1990-01-01; the ICE BofA spreads from 2023 for 1996) was fetched again on every
uncached call: 17 of the Macro page's 22. Measured on 2026-09-26, a second process
rendering Macro made 17 fetches in 11.9 s before and none in 3.3 s after.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

import src.db as db
import src.macro as macro


@pytest.fixture
def fred(tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path
    copy = tmp_path / "demo.db"
    shutil.copyfile(Path(__file__).resolve().parent.parent / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    from src import demo_refresh
    monkeypatch.setattr(demo_refresh, "enabled", lambda: False)
    calls = []

    def fetch(series_id, start_date, end_date=None):
        calls.append((series_id, start_date))
        first = max(pd.Timestamp(start_date), pd.Timestamp("1990-01-02"))
        idx = pd.bdate_range(first, "2026-09-25")
        return pd.Series(range(len(idx)), index=idx, dtype=float, name=series_id)

    monkeypatch.setattr(macro, "fetch_fred_series", fetch)
    with db.get_connection() as conn:          # the committed rows are dated earlier days
        assert not conn.execute("SELECT 1 FROM macro_cache WHERE fetch_date = ?",
                                (date.today().isoformat(),)).fetchone()
    return calls


def test_a_series_that_starts_after_the_requested_date_is_served_from_the_cache(fred):
    first = macro.get_series("DGS10", "1990-01-01")
    assert first.index.min() == pd.Timestamp("1990-01-02"), "premise: FRED starts a day late"
    again = macro.get_series("DGS10", "1990-01-01")
    assert fred == [("DGS10", "1990-01-01")], "fetched again after a success"
    pd.testing.assert_series_equal(first, again, check_freq=False, check_names=False)


def test_a_row_fetched_from_a_later_start_does_not_cover_an_earlier_request(fred):
    macro.get_series("DGS10", "2020-01-01")
    s = macro.get_series("DGS10", "1990-01-01")
    assert fred == [("DGS10", "2020-01-01"), ("DGS10", "1990-01-01")]
    assert s.index.min() == pd.Timestamp("1990-01-02")


def test_a_later_request_is_served_and_sliced(fred):
    macro.get_series("DGS10", "1990-01-01")
    s = macro.get_series("DGS10", "2020-01-01")
    assert len(fred) == 1 and s.index.min() >= pd.Timestamp("2020-01-01")


def _legacy_row(first: str):
    """A row written before the start was recorded: dates and values only."""
    idx = pd.bdate_range(first, "2026-09-25")
    payload = {"dates": [str(d.date()) for d in idx], "values": [1.0] * len(idx)}
    macro._ensure_cache_table()
    with db.get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO macro_cache (series_id, fetch_date, data) "
                     "VALUES (?, ?, ?)", ("DGS10", date.today().isoformat(), json.dumps(payload)))


def test_a_legacy_row_falls_back_to_the_first_observation(fred):
    _legacy_row("1989-12-29")
    macro.get_series("DGS10", "1990-01-01")
    assert fred == [], "a legacy row reaching back past the start still covers it"
    _legacy_row("1990-01-02")
    macro.get_series("DGS10", "1990-01-01")
    assert fred == [("DGS10", "1990-01-01")], "and one that does not is fetched, as before"
