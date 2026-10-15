"""FRED on the demo's retry timer (#368 item 3's rule for prices, applied to FRED).

After a series fails, get_series does not fetch it again until
demo_refresh.RETRY_AFTER has passed, and a page render in between fetches nothing:
before this, a failure was retried on every render, because st.cache_data does not
cache an exception. Off the timer (personal mode, and the suite by default) a failure
raises as it always did and the next call fetches again.

FRED is stubbed throughout; nothing reaches the network.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import src.config as config
import src.db as db
import src.demo_refresh as refresh
import src.macro as macro
import src.prices as prices

_ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def fred(tmp_path, monkeypatch):
    """The live demo's arrangement: a demo.db copy behind the runtime cache, the
    timer on, a settable clock, and a stubbed FRED that is down until told
    otherwise. Returns (clock, calls, up): clock[0] is now, calls lists every fetch,
    up["fred"] switches FRED on."""
    copy = tmp_path / "demo.db"
    shutil.copyfile(_ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_SEEDED", {})
    db.use_runtime_cache(tmp_path / "cache.db", committed=copy)
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    monkeypatch.setattr(macro, "_FAILED", {})
    clock = [T0]
    monkeypatch.setattr(macro, "_now", lambda: clock[0])
    calls: list[str] = []
    up = {"fred": False}

    def fetch(series_id, start_date, end_date=None):
        calls.append(series_id)
        if not up["fred"]:
            raise macro.FREDFetchError(series_id, OSError("FRED unreachable"))
        # From the requested start itself: a series whose first observation falls
        # after it is re-fetched on every call even after a success (a separate
        # get_series coverage defect), which would blur what the timer does.
        idx = pd.date_range(start_date, "2026-09-24", freq="7D")
        return pd.Series([1.0 + i / len(idx) for i in range(len(idx))], index=idx,
                         name=series_id)

    monkeypatch.setattr(macro, "fetch_fred_series", fetch)
    return clock, calls, up


def test_a_failure_waits_for_the_timer_without_fetching(fred):
    clock, calls, up = fred
    with pytest.raises(macro.FREDRetryWait) as err:
        macro.get_series("DGS10", "1990-01-01")
    assert (err.value.failed_at, err.value.retry_at) == (T0, T0 + refresh.RETRY_AFTER)
    assert "it retries after September 25, 2026 at 12:30 PM ET" in str(err.value)
    assert calls == ["DGS10"]

    up["fred"] = True                    # FRED is back, but the wait still holds
    clock[0] = T0 + refresh.RETRY_AFTER - timedelta(minutes=1)
    with pytest.raises(macro.FREDRetryWait):
        macro.get_series("DGS10", "1990-01-01")
    assert calls == ["DGS10"], "nothing fetches inside the wait"

    clock[0] = T0 + refresh.RETRY_AFTER
    assert not macro.get_series("DGS10", "1990-01-01").empty
    assert calls == ["DGS10", "DGS10"], "the timer lets the next attempt through"
    macro.get_series("DGS10", "1990-01-01")
    assert len(calls) == 2, "and a success is served from today's cache row"


def test_the_wait_is_the_demo_refresh_timer(fred, monkeypatch):
    """The same timer as prices, not a copy of its value: change RETRY_AFTER and FRED
    moves with it."""
    clock, calls, up = fred
    monkeypatch.setattr(refresh, "RETRY_AFTER", timedelta(minutes=5))
    with pytest.raises(macro.FREDRetryWait) as err:
        macro.get_series("DGS10", "1990-01-01")
    assert err.value.retry_at == T0 + timedelta(minutes=5)
    up["fred"] = True
    clock[0] = T0 + timedelta(minutes=5)
    macro.get_series("DGS10", "1990-01-01")
    assert calls == ["DGS10", "DGS10"]


def test_one_failing_series_does_not_hold_back_another(fred):
    _clock, calls, up = fred
    with pytest.raises(macro.FREDRetryWait):
        macro.get_series("DGS10", "1990-01-01")
    up["fred"] = True
    assert not macro.get_series("DGS2", "1990-01-01").empty
    assert calls == ["DGS10", "DGS2"]


def test_off_the_timer_a_failure_raises_as_before_and_the_next_call_fetches(fred, monkeypatch):
    """Personal mode and the suite: no timer, the original error, a fetch per call."""
    _clock, calls, _up = fred
    monkeypatch.setenv("DEMO_DAILY_FETCH", "0")
    for _ in range(2):
        with pytest.raises(macro.FREDFetchError) as err:
            macro.get_series("DGS10", "1990-01-01")
        assert type(err.value) is macro.FREDFetchError
    assert calls == ["DGS10", "DGS10"]
    assert macro._FAILED == {}


# ── the Macro page ────────────────────────────────────────────────────────────

def _render(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(config, "IS_DEMO", True)
    at = AppTest.from_file(str(_ROOT / "pages" / "3_Macro.py"), default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    captions = [c.value for c in at.caption if isinstance(c.value, str)]
    retry_buttons = [b.key for b in at.button if b.label == "Retry"]
    unavailable = [m.value for m in at.markdown
                   if isinstance(m.value, str) and "temporarily unavailable" in m.value]
    return captions, retry_buttons, unavailable


@pytest.fixture
def page(fred, monkeypatch):
    import streamlit as st
    monkeypatch.setattr(prices, "_GAP_FETCH", False)      # as the demo's refresh leaves it
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    st.cache_data.clear()
    yield fred
    st.cache_data.clear()


def _join_warm() -> None:
    thread = macro._WARM_THREAD
    if thread is not None:
        thread.join(60)


@pytest.mark.parametrize("warm", [False, True], ids=["page-fetches", "warm-fetches"])
def test_the_macro_page_does_not_fetch_fred_between_renders(page, monkeypatch, warm):
    """Both ways the page reaches FRED: its own fetch (the warm off, as in the suite),
    and the live demo's, where the startup warm (audit item 15f) fetches and the page
    waits for it."""
    if warm:
        monkeypatch.setenv("DEMO_FRED_WARM", "1")
        monkeypatch.setattr(macro, "_WARM_THREAD", None)
        monkeypatch.setattr(macro, "_WARM_DAY", None)
        monkeypatch.setattr(macro, "_SERIES_LOCKS", {})
    clock, calls, up = page
    _caps, retry, unavailable = _render(monkeypatch)
    _join_warm()
    first = len(calls)
    assert first >= 20 and len(set(calls)) == first, "each series tried once"
    assert unavailable and not retry, "no Retry button while the timer governs"

    clock[0] = T0 + timedelta(minutes=10)
    captions, retry, unavailable = _render(monkeypatch)
    _join_warm()
    assert len(calls) == first, "a render inside the wait fetches nothing"
    waits = [c for c in captions if c.startswith("FREDRetryWait:")]
    assert len(waits) == len(unavailable) and not retry
    assert all("it retries after September 25, 2026 at 12:30 PM ET" in c for c in waits)

    up["fred"] = True
    clock[0] = T0 + refresh.RETRY_AFTER
    _caps, retry, unavailable = _render(monkeypatch)
    _join_warm()
    assert len(calls) > first and not unavailable, "after the wait, the page fetches and renders"


def test_off_the_timer_the_page_keeps_its_retry_buttons(page, monkeypatch):
    """The contrast: without the timer the failure is the original error, and the
    Retry button is still there, because a click does fetch."""
    _clock, calls, _up = page
    monkeypatch.setenv("DEMO_DAILY_FETCH", "0")
    captions, retry, unavailable = _render(monkeypatch)
    assert unavailable and len(retry) == len(unavailable)
    assert not any(c.startswith("FREDRetryWait:") for c in captions)


# ── a read that finds the cache locked (#580) ─────────────────────────────────
#
# Under -n 4 on GitHub's runner the warm held the runtime cache locked past SQLite's
# busy timeout (5 seconds), and the Macro page's read of USREC raised "OperationalError:
# database is locked" inside its 15-second wait: run 38757697063, the third render of
# test_the_macro_page_does_not_fetch_fred_between_renders[warm-fetches]. These tests
# shorten the timeout to BUSY_TIMEOUT so a lock held past it costs about a second.

BUSY_TIMEOUT = 0.2
HOLD = 1.5


def _short_busy_timeout(monkeypatch) -> None:
    """Every SQLite connection the test opens waits BUSY_TIMEOUT for a lock."""
    import sqlite3
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: real(*a, **{**k, "timeout": BUSY_TIMEOUT}))


def _warm_on(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_FRED_WARM", "1")
    monkeypatch.setattr(macro, "_WARM_THREAD", None)
    monkeypatch.setattr(macro, "_WARM_DAY", None)
    monkeypatch.setattr(macro, "_SERIES_LOCKS", {})
    monkeypatch.setattr(macro, "_STALLED_WARM", None)


def _record_locked_reads(monkeypatch) -> "list[str]":
    """The sqlite error names of the read steps that raised, as each raised. A read step
    opens the cache twice, for the table check and for the row, and either can find it
    locked."""
    errors: list[str] = []

    def recording(real):
        def step(*a, **k):
            try:
                return real(*a, **k)
            except Exception as exc:
                errors.append(str(getattr(exc, "sqlite_errorname", type(exc).__name__)))
                raise
        return step

    for name in ("_ensure_cache_table", "_read_cached"):
        monkeypatch.setattr(macro, name, recording(getattr(macro, name)))
    return errors


def test_a_read_waits_out_a_warm_that_holds_the_cache_past_the_busy_timeout(fred,
                                                                            monkeypatch):
    """The warm's USREC commit holds the cache for HOLD seconds, past BUSY_TIMEOUT. The
    read finds it locked, reads again, and returns the row once the commit lands."""
    import json
    import sqlite3
    import time
    _clock, calls, up = fred
    up["fred"] = True
    _warm_on(monkeypatch)
    _short_busy_timeout(monkeypatch)
    errors = _record_locked_reads(monkeypatch)
    real_store = macro._fetch_and_store
    held: list[float] = []

    def slow_commit(series_id, start_date, today):
        if series_id != "USREC":
            return real_store(series_id, start_date, today)
        raw = macro.fetch_fred_series(series_id, start_date)
        payload = {"dates": [str(d.date()) for d in raw.index],
                   "values": [float(v) for v in raw.values],
                   "requested_start": start_date}
        conn = sqlite3.connect(str(db._RUNTIME_CACHE[1]), isolation_level=None)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("INSERT OR REPLACE INTO runtime_macro_cache "
                         "(series_id, fetch_date, data) VALUES (?, ?, ?)",
                         (series_id, today, json.dumps(payload)))
            held.append(time.monotonic())
            time.sleep(HOLD)
            conn.execute("COMMIT")
        finally:
            conn.close()
        return raw

    monkeypatch.setattr(macro, "_fetch_and_store", slow_commit)
    try:
        series = macro.get_series("USREC", "1945-01-01")
    finally:
        _join_warm()                   # no thread outlives the test, even on failure
    assert held, "premise: the warm held the cache while it wrote USREC"
    assert errors and all(e.startswith("SQLITE_BUSY") for e in errors), (
        "premise: a read found the cache locked past the busy timeout", errors)
    assert not series.empty and calls[0] == "USREC"


def test_a_cache_locked_past_the_deadline_still_ends_the_wait(fred, monkeypatch):
    """The page's wait stays bounded: a lock that does not lift ends the read soon after
    its deadline, and the read reports it. The lock is lifted at SAFETY seconds whatever
    happens, so a read that waited past its deadline ends there and fails the bound
    rather than hanging the suite."""
    import sqlite3
    import threading
    import time
    SAFETY = 8.0
    _warm_on(monkeypatch)
    _short_busy_timeout(monkeypatch)
    errors = _record_locked_reads(monkeypatch)
    blocker = sqlite3.connect(str(db._RUNTIME_CACHE[1]), isolation_level=None,
                              check_same_thread=False)
    guard = threading.Lock()
    held = [False]

    def release():
        with guard:
            if held[0]:
                blocker.execute("ROLLBACK")
                held[0] = False

    timer = threading.Timer(SAFETY, release)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        held[0] = True
        timer.start()
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            macro.get_series("USREC", "1945-01-01", deadline=start + 1.0)
        elapsed = time.monotonic() - start
    finally:
        timer.cancel()
        timer.join()
        release()
        blocker.close()
        warm = macro._WARM_THREAD
        _join_warm()
    assert len(errors) >= 2, ("premise: it read again after a locked read", errors)
    assert 1.0 <= elapsed < 5.0, elapsed
    assert warm is None, "a locked read starts no warm"


def test_a_read_error_other_than_a_lock_still_raises(fred, monkeypatch):
    """Only the busy timeout reads again. Any other OperationalError is not a row that
    has yet to arrive, and raises as it did."""
    import sqlite3
    _warm_on(monkeypatch)

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("no such table: runtime_macro_cache")

    monkeypatch.setattr(macro, "_read_cached", broken)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        macro.get_series("USREC", "1945-01-01")
    assert macro._WARM_THREAD is None
