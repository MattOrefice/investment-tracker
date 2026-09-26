"""#368 item 2: the demo app writes to a runtime cache, never to demo.db.

Three writers used to reach demo.db in demo mode: fetched prices and dividends
(prices.fetch_prices), FRED series (macro.get_series) and quarter locks
(cache.capture_quarter_snapshot). 127 FRED rows from past runs were committed that
way. With the cache in use, demo.db opens read-only and those four tables become
per-connection views of the cache's copy of them: seeded from demo.db once per
demo.db, then written by the run.

The fetch never reaches the network here: `_session_get` stands in for Yahoo.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import src.db as db
import src.prices as prices

_ROOT = Path(__file__).resolve().parent.parent
_DEMO = _ROOT / "data" / "demo.db"


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _session_get(url, params=None, timeout=None):
    """A settled Yahoo chart response: a flat close on every weekday in the window,
    and one dividend on the window's first Monday."""
    ticker = url.rstrip("/").split("/")[-1]
    start = datetime.fromtimestamp(params["period1"], tz=timezone.utc).date()
    stop = datetime.fromtimestamp(params["period2"], tz=timezone.utc).date()
    days = [start + timedelta(days=i) for i in range((stop - start).days)
            if (start + timedelta(days=i)).weekday() < 5]
    ts = [int(datetime(d.year, d.month, d.day, 20, tzinfo=timezone.utc).timestamp()) for d in days]
    monday = next((t for d, t in zip(days, ts) if d.weekday() == 0), None)
    past = int(datetime(2000, 1, 3, tzinfo=timezone.utc).timestamp())
    return _Resp({"chart": {"result": [{
        "meta": {"currentTradingPeriod": {"regular": {"start": past, "end": past + 3600}}},
        "timestamp": ts,
        "indicators": {"quote": [{"close": [100.0] * len(ts)}], "adjclose": [{"adjclose": [100.0] * len(ts)}]},
        "events": {"dividends": {str(monday): {"amount": 0.5}}} if monday else {},
    }], "error": None}, "ticker": ticker})


@pytest.fixture
def overlaid(tmp_path, monkeypatch):
    """A demo.db copy standing in for the committed file, with the runtime cache on."""
    demo = tmp_path / "demo.db"
    shutil.copyfile(_DEMO, demo)
    os.chmod(demo, 0o644)
    cache = tmp_path / "runtime" / "cache.db"
    monkeypatch.setattr(db, "DB_PATH", demo)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    db.use_runtime_cache(cache, committed=demo)
    monkeypatch.setattr(prices._SESSION, "get", _session_get)
    prices._reset_trailing_memo()
    return demo, cache, _sha(demo)


def test_fetched_prices_and_dividends_land_in_the_cache_not_demo_db(overlaid):
    demo, cache, before = overlaid
    got = prices.get_prices("VOO", "2026-07-01", "2026-07-31")
    assert max(got.index) > date(2026, 7, 20), "the fetched days are served"
    assert date(2026, 7, 1) in got.index, "the committed days are served with them"
    c = sqlite3.connect(cache)
    assert c.execute("SELECT COUNT(*) FROM runtime_prices WHERE ticker = 'VOO' "
                     "AND price_date > '2026-07-20'").fetchone()[0] > 0
    assert c.execute("SELECT COUNT(*) FROM runtime_dividends WHERE ticker = 'VOO' "
                     "AND ex_date > '2026-07-20'").fetchone()[0] == 1
    c.close()
    assert _sha(demo) == before


def test_fred_series_land_in_the_cache_and_the_committed_rows_stay_in_demo_db(overlaid, monkeypatch):
    """The 127 committed FRED rows stay IN demo.db, byte for byte. The run reads and
    writes the cache's copy, so clearing empties that copy and never the file."""
    import src.macro as macro
    demo, cache, before = overlaid
    monkeypatch.setattr(macro, "fetch_fred_series", lambda sid, start, end=None: pd.Series(
        [1.0, 2.0], index=pd.to_datetime(["1990-01-01", "2026-09-01"]), name=sid))
    committed = sqlite3.connect(f"file:{demo}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM macro_cache").fetchone()[0]
    assert committed == 127, "fixture premise: the 127 committed FRED rows"
    macro.get_series("TESTSERIES", "1990-01-01")
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM macro_cache").fetchone()[0] == committed + 1
    macro.clear_macro_cache()
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM macro_cache").fetchone()[0] == 0
    assert sqlite3.connect(f"file:{demo}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM macro_cache").fetchone()[0] == committed
    assert _sha(demo) == before


def test_a_quarter_lock_lands_in_the_cache(overlaid, monkeypatch):
    from src.cache import capture_quarter_snapshot, get_quarter_snapshot
    demo, cache, before = overlaid
    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    snap, captured_at = capture_quarter_snapshot("2026Q2")
    back, _ = get_quarter_snapshot("2026Q2")
    pd.testing.assert_frame_equal(back.adj_close, snap.adj_close, check_freq=False)
    # The cache starts with demo.db's committed locks (#382: five quarters ship
    # locked); the capture is the one it now holds for 2026Q2.
    c = sqlite3.connect(cache)
    assert c.execute("SELECT captured_at FROM runtime_quarter_snapshots "
                     "WHERE quarter_id = '2026Q2'").fetchall() == [(captured_at,)]
    c.close()
    assert _sha(demo) == before


def test_any_other_write_is_refused_by_the_read_only_file(overlaid):
    demo, _cache, before = overlaid
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with db.get_connection() as conn:
            conn.execute("INSERT INTO trades (account_id, ticker, trade_date, action, shares, price) "
                         "VALUES (1, 'VOO', '2026-09-24', 'Buy', 1, 1)")
    assert _sha(demo) == before


def test_another_book_is_not_overlaid(overlaid, tmp_path, monkeypatch):
    """A tool that repoints DB_PATH at the book it means to write still writes it."""
    other = tmp_path / "other.db"
    shutil.copyfile(_DEMO, other)
    monkeypatch.setattr(db, "DB_PATH", other)
    before = _sha(other)
    prices.get_prices("VOO", "2026-07-01", "2026-07-31")
    assert _sha(other) != before


def test_the_demo_app_run_that_fetched_leaves_demo_db_byte_identical(tmp_path, monkeypatch):
    """#368's acceptance, end to end: app.py in demo mode, then the Performance page,
    with the fetch answered. The committed demo.db itself is opened, read-only."""
    import src.config as config
    from tests.conftest import _pin_mode
    _pin_mode(monkeypatch, "demo")
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setenv("DEMO_RUNTIME_CACHE", str(tmp_path / "cache.db"))
    monkeypatch.setattr(prices._SESSION, "get", _session_get)
    prices._reset_trailing_memo()
    before = _sha(_DEMO)

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_ROOT / "app.py"), default_timeout=300).run()
    assert not at.exception, at.exception
    at.switch_page("pages/2_Performance.py").run()
    assert not at.exception, at.exception

    assert db._RUNTIME_CACHE is not None and db._RUNTIME_CACHE[0] == _DEMO.resolve()
    c = sqlite3.connect(tmp_path / "cache.db")
    fetched = c.execute("SELECT COUNT(*), MAX(price_date) FROM runtime_prices "
                        "WHERE price_date > '2026-07-20'").fetchone()
    c.close()
    assert fetched[0] > 0 and fetched[1] > "2026-07-20", "the run fetched"
    assert _sha(_DEMO) == before
    assert config.IS_DEMO


def test_a_fetched_row_for_a_committed_date_is_served_once_and_wins(overlaid):
    """Yahoo's UTC-midnight boundary can return a day the snapshot already holds
    (get_prices dedups it for the same reason). The view must serve that day once,
    with the fetched value, or every read of it doubles."""
    demo, _cache, before = overlaid
    with db.get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close) "
                     "VALUES ('VOO', '2026-07-20', 1.0, NULL)")
    with db.get_connection() as conn:
        rows = conn.execute("SELECT close FROM prices WHERE ticker = 'VOO' "
                            "AND price_date = '2026-07-20'").fetchall()
    assert [r[0] for r in rows] == [1.0]
    assert _sha(demo) == before


def test_the_cache_is_reseeded_when_demo_db_changes_and_only_then(overlaid, monkeypatch):
    """A new process on the SAME demo.db keeps what earlier runs fetched. A new demo.db
    (a deploy) re-seeds from it: the cache follows the committed data it overlays."""
    demo, cache, _before = overlaid
    prices.get_prices("VOO", "2026-07-01", "2026-07-31")
    fetched = "SELECT COUNT(*) FROM runtime_prices WHERE price_date > '2026-07-20'"
    assert sqlite3.connect(cache).execute(fetched).fetchone()[0] > 0

    monkeypatch.setattr(db, "_SEEDED", set())                 # a new process
    db.use_runtime_cache(cache, committed=demo)
    assert sqlite3.connect(cache).execute(fetched).fetchone()[0] > 0, "same demo.db: kept"

    c = sqlite3.connect(demo)                                  # a new demo.db
    c.execute("INSERT INTO prices VALUES ('ZZZZ', '2026-07-20', 1.0, NULL)")
    c.commit()
    c.close()
    monkeypatch.setattr(db, "_SEEDED", set())
    db.use_runtime_cache(cache, committed=demo)
    s = sqlite3.connect(cache)
    assert s.execute(fetched).fetchone()[0] == 0, "new demo.db: re-seeded"
    assert s.execute("SELECT COUNT(*) FROM runtime_prices WHERE ticker = 'ZZZZ'").fetchone()[0] == 1
