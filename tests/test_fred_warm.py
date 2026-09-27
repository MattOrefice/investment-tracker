"""The startup FRED warm, and the Macro page's wait for it (audit item 15f).

A fresh container's first Macro visit waited about 15 seconds for 22 fetches. The
router now starts a background thread once a day that fetches the Macro page's series
into today's cache, and get_series takes one fetch per series at a time, so a visit that
arrives mid-warm waits for the fetch in flight instead of repeating it.

The page waits for the warm at most PAGE_WAIT_SECONDS. One visit in #429's
measurements took 184 s, cause unrecorded. Past the wait, the page renders the stored
copies with the date each was fetched, and the warm carries on.

The suite turns the warm off (tests/conftest.py, DEMO_FRED_WARM=0). A test that turned
the fetch timer on for the price refresh used to start it too, and the thread outlived
that test by 17 more, migrating their databases while they migrated them. The tests
here turn it on for themselves and join what they start.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import src.config as config
import src.db as db
import src.demo_refresh as demo_refresh
import src.macro as macro
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent
PAGE = str(ROOT / "pages" / "3_Macro.py")
ALL_SERIES = sorted(s for s, _ in macro.MACRO_PAGE_SERIES)


def _page_requests() -> "list[tuple[str, str]]":
    tree = ast.parse((ROOT / "pages" / "3_Macro.py").read_text(encoding="utf-8"))
    return [(n.args[0].value, n.args[1].value) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_try_fred"
            and len(n.args) == 2 and all(isinstance(a, ast.Constant) for a in n.args)]


def _stored_fred() -> "dict[str, pd.Series]":
    """Each series as demo.db last stored it: realistic data for a stubbed FRED."""
    conn = sqlite3.connect(f"file:{(ROOT / 'data' / 'demo.db').as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT series_id, data FROM macro_cache m WHERE fetch_date = "
            "(SELECT MAX(fetch_date) FROM macro_cache WHERE series_id = m.series_id)").fetchall()
    finally:
        conn.close()
    out = {}
    for series_id, data in rows:
        p = json.loads(data)
        out[series_id] = pd.Series(
            [float(v) if v is not None else float("nan") for v in p["values"]],
            index=pd.to_datetime(p["dates"]), name=series_id)
    return out


def _answer(stored, series_id, start_date):
    s = stored.get(series_id)
    if s is None:                       # NFCI: never stored; a weekly stand-in
        idx = pd.date_range("1971-01-08", "2026-09-18", freq="W-FRI")
        s = pd.Series([-0.5] * len(idx), index=idx, name=series_id)
    return s[s.index >= start_date]


def _long(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


@pytest.fixture
def warm_on(monkeypatch):
    """The warm's gate on for this test, its per-process state fresh; any warm the test
    started is joined before it ends."""
    monkeypatch.setenv("DEMO_FRED_WARM", "1")
    monkeypatch.setattr(macro, "_WARM_THREAD", None)
    monkeypatch.setattr(macro, "_WARM_DAY", None)
    monkeypatch.setattr(macro, "_SERIES_LOCKS", {})
    monkeypatch.setattr(macro, "_FAILED", {})
    yield
    thread = macro._WARM_THREAD
    if thread is not None:
        thread.join(60)
        assert not thread.is_alive(), "the warm did not finish"


@pytest.fixture
def warm_db(tmp_path, monkeypatch, warm_on):
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(demo_refresh, "enabled", lambda: True)
    return copy


def _offline(*a, **k):
    raise OSError("offline")


@pytest.fixture
def render_env(monkeypatch):
    """What a Macro render needs besides FRED: demo mode, prices offline and not
    gap-filled (as the demo's refresh leaves them), and an empty st.cache_data."""
    import streamlit as st
    monkeypatch.setattr(config, "IS_DEMO", True)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    st.cache_data.clear()
    yield
    st.cache_data.clear()


# ── what the warm fetches, and when it starts ────────────────────────────────

def test_the_warm_list_is_exactly_what_the_macro_page_requests():
    page = _page_requests()
    assert len(page) == 22, "premise: the page's 22 series"
    assert sorted(page) == sorted(macro.MACRO_PAGE_SERIES)


def test_the_router_starts_the_warm_in_the_demo_branch_after_the_price_refresh():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    demo = src[src.index("if IS_DEMO:"):src.index("else:", src.index("if IS_DEMO:"))]
    assert demo.index("refresh_if_due()") < demo.index("warm_cache_in_background()")
    assert src.count("warm_cache_in_background") == 2, "one import and one call"


def test_the_warm_is_off_in_the_suite_and_off_the_fetch_timer(monkeypatch):
    monkeypatch.setattr(macro, "_WARM_THREAD", None)
    monkeypatch.setattr(macro, "_WARM_DAY", None)
    assert os.environ["DEMO_FRED_WARM"] == "0", "premise: tests/conftest.py turns it off"
    monkeypatch.setattr(demo_refresh, "enabled", lambda: True)
    assert macro.warm_cache_in_background() is None, "the suite: off even on the timer"
    monkeypatch.setenv("DEMO_FRED_WARM", "1")
    monkeypatch.setattr(demo_refresh, "enabled", lambda: False)
    assert macro.warm_cache_in_background() is None, "personal mode: no fetch timer"


def test_the_router_warms_once_a_day(warm_db, monkeypatch):
    calls = []
    stored = _stored_fred()
    monkeypatch.setattr(macro, "fetch_fred_series",
                        lambda s, start, end=None: calls.append(s) or _answer(stored, s, start))
    t = macro.warm_cache_in_background()
    assert t is not None
    t.join(30)
    assert sorted(calls) == ALL_SERIES
    assert macro.warm_cache_in_background() is None, "once a day"
    for series_id, start in macro.MACRO_PAGE_SERIES:
        macro.get_series(series_id, start)
    assert len(calls) == 22, "every page request is now a cache hit"
    monkeypatch.setattr(macro, "_WARM_DAY", "2026-01-01")          # the next day
    t = macro.warm_cache_in_background()
    assert t is not None, "a new day, a new warm"
    t.join(30)


def test_a_request_during_the_warm_waits_for_the_fetch_in_flight(warm_db, monkeypatch):
    calls, gate = [], threading.Event()
    stored = _stored_fred()

    def fetch(series_id, start_date, end_date=None):
        calls.append(series_id)
        if series_id == "USREC":
            gate.wait(10)
        return _answer(stored, series_id, start_date)

    monkeypatch.setattr(macro, "fetch_fred_series", fetch)
    t = macro.warm_cache_in_background()
    for _ in range(200):                       # until the warm is inside USREC's fetch
        if "USREC" in calls:
            break
        threading.Event().wait(0.01)
    assert calls == ["USREC"], "premise: the warm is fetching USREC"
    got = {}
    reader = threading.Thread(target=lambda: got.update(s=macro.get_series("USREC",
                                                                            "1945-01-01")))
    reader.start()
    threading.Event().wait(0.2)
    assert reader.is_alive(), "the page's request waits for the fetch in flight"
    gate.set()
    reader.join(10)
    t.join(30)
    assert calls.count("USREC") == 1, calls
    assert not got["s"].empty


# ── the page's wait ──────────────────────────────────────────────────────────

def test_with_the_warm_on_the_page_never_fetches_on_its_own_thread(warm_db, monkeypatch):
    """A fetch on the page's thread is a wait the page cannot bound: the page asks the
    warm, starting one when none is running, and reads the row it writes."""
    threads = []
    stored = _stored_fred()

    def fetch(series_id, start_date, end_date=None):
        threads.append(threading.current_thread().name)
        return _answer(stored, series_id, start_date)

    monkeypatch.setattr(macro, "fetch_fred_series", fetch)
    s = macro.get_series_for_page("DGS10", "1990-01-01", time.monotonic() + 30)
    assert not s.empty
    assert threads and set(threads) == {"fred-warm"}, threads


def test_a_series_the_warm_failed_raises_its_retry_wait_as_before(warm_db, monkeypatch):
    def fetch(series_id, start_date, end_date=None):
        raise macro.FREDFetchError(series_id, OSError("FRED unreachable"))

    monkeypatch.setattr(macro, "fetch_fred_series", fetch)
    t0 = time.monotonic()
    with pytest.raises(macro.FREDRetryWait):
        macro.get_series_for_page("DGS10", "1990-01-01", t0 + 30)
    assert time.monotonic() - t0 < 10, "the failure is reported when it happens"


def test_the_stored_copy_is_the_newest_that_reaches_back_to_the_request(warm_db):
    """The PDF asks for T10Y2Y from 1990 and the page from 1976. demo.db holds both
    kinds of row (three from 1990, in May), so the newest row is not always one a
    1976 panel can use."""
    def row(fetched, first, requested=None):
        idx = pd.date_range(first, "2026-07-01", freq="MS")
        payload = {"dates": [str(d.date()) for d in idx], "values": [1.0] * len(idx)}
        if requested:
            payload["requested_start"] = requested
        return ("T10Y2Y", fetched, json.dumps(payload))

    with db.get_connection() as conn:
        conn.execute("INSERT INTO macro_cache VALUES (?, ?, ?)",
                     row("2026-07-20", "1990-01-01"))            # PDF-like, no record
    s, on = macro._read_stored("T10Y2Y", "1976-06-01")
    assert on == "2026-07-16", "the newest row starting in 1976, not the newer 1990 one"
    assert str(s.index[0].date()) == "1976-06-01"

    with db.get_connection() as conn:
        conn.execute("INSERT INTO macro_cache VALUES (?, ?, ?)",
                     row("2026-08-01", "1990-01-01", requested="1990-01-01"))
    assert macro._read_stored("T10Y2Y", "1976-06-01")[1] == "2026-07-16"
    assert macro._read_stored("T10Y2Y", "1990-01-01")[1] == "2026-08-01", (
        "a row that records its start serves any request from that start on")
    assert macro._read_stored("NFCI", "1971-01-01") == (None, None)


@pytest.fixture
def fresh_container(tmp_path, monkeypatch, warm_on, render_env):
    """The live demo on a fresh container: a demo.db copy behind an empty runtime
    cache, seeded from it, with the fetch timer on."""
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_SEEDED", set())
    db.use_runtime_cache(tmp_path / "cache.db", committed=copy)
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    return copy


def test_a_hanging_fred_holds_the_macro_page_for_its_wait_and_no_longer(fresh_container,
                                                                         monkeypatch):
    """FRED stubbed to hang: the warm's first fetch never returns until released. The
    page renders at its wait with the stored copies and their dates, and fetches
    nothing itself; once FRED answers, the warm finishes and the next visit reads
    today's rows."""
    from streamlit.testing.v1 import AppTest
    release, calls = threading.Event(), []
    stored = _stored_fred()

    def hang(series_id, start_date, end_date=None):
        calls.append(series_id)
        release.wait(300)
        return _answer(stored, series_id, start_date)

    monkeypatch.setattr(macro, "fetch_fred_series", hang)
    bound = macro.PAGE_WAIT_SECONDS + 45
    # A page that never stops waiting cannot be stopped by AppTest's timeout (a stop
    # lands at the script's next st call), so FRED is released just past the bound:
    # such a page then renders late and fails the bound, rather than hanging the run.
    watchdog = threading.Timer(bound + 5, release.set)
    watchdog.start()
    warm = macro.warm_cache_in_background()              # the router's call
    assert warm is not None
    try:
        t0 = time.monotonic()
        at = AppTest.from_file(PAGE, default_timeout=180).run()
        elapsed = time.monotonic() - t0
        assert not at.exception, [str(e.value) for e in at.exception]
        assert macro.PAGE_WAIT_SECONDS <= elapsed < bound, elapsed
        assert calls == ["USREC"] and warm.is_alive(), (
            "FRED still hangs in the warm's first fetch, and the page fetched nothing")
        notices = [i.value for i in at.info if "did not arrive" in i.value]
        assert len(notices) == 1, notices
        notice = notices[0]
        assert notice.startswith("Today's FRED data did not arrive within 15 seconds, so "
                                 "21 of 22 series are shown as stored, with the date each "
                                 "was fetched: USREC (July 16, 2026), "), notice
        for series_id, when in [("T10Y2Y", "2026-07-16"), ("DFF", "2026-05-27"),
                                ("UNRATE", "2026-07-16"), ("DGS10", "2026-05-27")]:
            assert f"{series_id} ({_long(when)})" in notice, series_id
        assert "NFCI" not in notice, "nothing stored for NFCI"
        nfci = [c.value for c in at.caption
                if isinstance(c.value, str) and c.value.startswith("FREDStillFetching:")]
        assert nfci == ["FREDStillFetching: FRED has not returned 'NFCI' within 15 "
                        "seconds; no earlier copy is stored. The fetch continues in the "
                        "background."], nfci
        sources = " ".join(c.value for c in at.caption if isinstance(c.value, str))
        assert "stored copy fetched July 16, 2026" in sources
    finally:
        watchdog.cancel()
        watchdog.join()
        release.set()
        warm.join(60)
    assert sorted(calls) == ALL_SERIES, "the warm finished in the background"

    at = AppTest.from_file(PAGE, default_timeout=180).run()
    assert not at.exception
    assert not [i.value for i in at.info if "did not arrive" in i.value]
    assert sorted(calls) == ALL_SERIES, "the next visit reads today's rows"


def test_the_warm_and_a_page_load_migrate_a_fresh_database_once(tmp_path, monkeypatch,
                                                                  warm_on, render_env):
    """The path #429's first run broke: the warm's first connection and a page's, on a
    database this process has not migrated. Here the book lacks
    accounts.included_in_household, the column _auto_migrate adds.

    The race is decided, not left to timing. A thread that reaches that ALTER holds
    there until the other thread has opened its own first connection: then either it
    reaches the ALTER too (both passed the check, the defect), or a second passes
    without it (it is waiting its turn). Unlocked, both ALTER and one fails with
    "duplicate column name"."""
    from streamlit.testing.v1 import AppTest
    book = tmp_path / "unmigrated.db"
    shutil.copyfile(ROOT / "data" / "demo.db", book)
    os.chmod(book, 0o644)
    conn = sqlite3.connect(book)
    try:
        conn.execute("ALTER TABLE accounts DROP COLUMN included_in_household")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(db, "DB_PATH", book)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(demo_refresh, "enabled", lambda: True)
    calls, stored = [], _stored_fred()
    monkeypatch.setattr(macro, "fetch_fred_series",
                        lambda s, start, end=None: calls.append(s) or _answer(stored, s, start))

    cond = threading.Condition()
    opened, at_alter, overlapped = set(), set(), []
    real_init = db._ClosingConnection.__init__
    real_execute = db._ClosingConnection.execute

    def init(self, *a, **k):
        real_init(self, *a, **k)
        with cond:
            opened.add(threading.current_thread().name)
            cond.notify_all()

    def execute(self, sql, *args):
        if sql.startswith("ALTER TABLE accounts ADD COLUMN included_in_household"):
            me = threading.current_thread().name
            with cond:
                at_alter.add(me)
                cond.notify_all()
                end, seen = time.monotonic() + 60, None
                while len(at_alter) < 2 and time.monotonic() < end:
                    if opened - {me}:
                        seen = seen or time.monotonic()
                        if time.monotonic() - seen > 1.0:
                            break
                    cond.wait(0.05)
                overlapped.append(len(at_alter) == 2 or seen is not None)
        return real_execute(self, sql, *args)

    ran, errors = [], []
    real_migrate = db._auto_migrate

    def migrate(conn):
        ran.append(threading.current_thread().name)
        try:
            return real_migrate(conn)
        except Exception as exc:
            errors.append((threading.current_thread().name, f"{type(exc).__name__}: {exc}"))
            raise

    monkeypatch.setattr(db._ClosingConnection, "__init__", init)
    monkeypatch.setattr(db._ClosingConnection, "execute", execute)
    monkeypatch.setattr(db, "_auto_migrate", migrate)

    warm = macro.warm_cache_in_background()
    assert warm is not None
    at = AppTest.from_file(PAGE, default_timeout=180).run()
    warm.join(60)

    assert overlapped and all(overlapped), (
        "premise: the page's first connection met the migration")
    assert errors == [], errors
    assert len(ran) == 1, f"migrated once, by one thread: {ran}"
    assert not at.exception, [str(e.value) for e in at.exception]
    assert sorted(calls) == ALL_SERIES, "the warm fetched every series"
    check = sqlite3.connect(book)
    try:
        cols = [r[1] for r in check.execute("PRAGMA table_info(accounts)")]
    finally:
        check.close()
    assert cols.count("included_in_household") == 1
