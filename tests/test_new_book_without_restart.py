"""#480: a running demo serves a new data/demo.db from the next page load, with no restart.

The runtime cache was seeded once per process. A process that outlived a deploy kept the
seed it took from the book before, runtime quarter locks included, beside trades it read
from the new file, until someone rebooted it. src.db.use_runtime_cache now looks at
demo.db on every page load. Pinned here, on a copy of the frozen book standing in for
the committed file and a second book put in its place the way a deploy puts one:

  * a new book re-seeds the cache at the next load, which drops a runtime quarter lock
    and every fetched row, and serves the new book's;
  * an unchanged book costs one stat a load: it is not hashed and nothing re-seeds;
  * a book touched and not changed is hashed once, and nothing re-seeds;
  * the re-seed waits while another page is still rendering, and a load that begins
    during one waits for it and reads the new seed;
  * the seed is one transaction: one that fails leaves the seed before it whole;
  * what the process built on the old seed goes with it: the last fetch's timer, the
    trailing-fetch memo, and the figures the pages cached.
"""
from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "frozen_book.db"   # its prices end 2026-07-20
RUNTIME_LOCK = "taken at runtime"
COMMITTED_LOCK = "committed with the new book"


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _deploy(demo: Path, *, broken: bool = False) -> str:
    """Put a new book in place of ``demo``, as a deploy does: built beside it, then moved
    over it. It holds one more ticker and a committed Q3 2026 lock. ``broken`` gives it a
    dividends table the seed cannot copy. Returns its SHA-256."""
    new = demo.with_name("demo.next.db")
    shutil.copyfile(SNAPSHOT, new)
    os.chmod(new, 0o644)
    con = sqlite3.connect(new)
    with con:
        con.execute("INSERT INTO prices VALUES ('ZZZZ', '2026-07-20', 1.0, NULL)")
        con.execute("INSERT INTO quarter_snapshots (quarter_id, snapshot_date, captured_at, "
                    "snapshot_data) VALUES ('2026Q3', '2026-09-30', ?, '{}')", (COMMITTED_LOCK,))
        if broken:
            con.execute("ALTER TABLE dividends RENAME COLUMN amount TO paid")
    con.close()
    os.replace(new, demo)
    return _sha(demo)


def _served() -> dict:
    """What a page reads through the app's own connection."""
    with db.get_connection() as conn:
        lock = conn.execute("SELECT captured_at FROM quarter_snapshots WHERE quarter_id = "
                            "'2026Q3'").fetchone()
        return {
            "q3_lock": lock[0] if lock else None,
            "fetched": conn.execute("SELECT COUNT(*) FROM prices WHERE price_date > "
                                    "'2026-07-20'").fetchone()[0],
            "new_ticker": conn.execute("SELECT COUNT(*) FROM prices WHERE ticker = 'ZZZZ'"
                                       ).fetchone()[0],
            "prices": conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
            "dividends": conn.execute("SELECT COUNT(*) FROM dividends").fetchone()[0],
        }


def _seeded_from(cache: Path) -> str:
    con = sqlite3.connect(cache)
    try:
        return con.execute("SELECT value FROM runtime_meta WHERE key = 'seeded_from'").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def running(tmp_path, monkeypatch):
    """A process that has served one page load from a copy of the frozen book, and has
    since taken a Q3 lock and fetched a close at runtime. ``load()`` is a page load, on
    its own thread, which ends with it, as Streamlit runs a script run; ``load_here()``
    is one on the calling thread. ``did`` counts what the loads since have cost."""
    demo = tmp_path / "demo.db"
    shutil.copyfile(SNAPSHOT, demo)
    os.chmod(demo, 0o644)
    cache = tmp_path / "runtime" / "cache.db"
    monkeypatch.setattr(db, "DB_PATH", demo)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_SEEDED", {})                     # a new process
    forgot = []
    monkeypatch.setattr(db, "_AFTER_RESEED", [lambda: forgot.append("forgot")])
    did = {"stat": 0, "hash": 0, "seed": 0}
    stamp, sha, seed = db._stamp, db._sha256, db._seed_runtime_cache

    def counted(name, real):
        def call(*a, **k):
            out = real(*a, **k)
            did[name] += 1 if name != "seed" else int(bool(out))
            return out
        return call

    monkeypatch.setattr(db, "_stamp", counted("stat", stamp))
    monkeypatch.setattr(db, "_sha256", counted("hash", sha))
    monkeypatch.setattr(db, "_seed_runtime_cache", counted("seed", seed))

    def load_here():
        return db.use_runtime_cache(cache, committed=demo)

    def load():
        box = {}

        def run():
            try:
                box["out"] = load_here()
            except BaseException as exc:                     # noqa: BLE001
                box["exc"] = exc

        page = threading.Thread(target=run, name="a page load")
        page.start()
        page.join(60)
        assert not page.is_alive()
        if "exc" in box:
            raise box["exc"]
        return box["out"]

    load()
    assert did == {"stat": 1, "hash": 1, "seed": 1}, "the first load of a process seeds"
    with db.get_connection() as conn:
        conn.execute("INSERT INTO quarter_snapshots (quarter_id, snapshot_date, captured_at, "
                     "snapshot_data) VALUES ('2026Q3', '2026-09-30', ?, '{}')", (RUNTIME_LOCK,))
        conn.execute("INSERT INTO prices (ticker, price_date, close, adj_close) "
                     "VALUES ('VOO', '2026-07-21', 600.0, NULL)")
    assert _served()["q3_lock"] == RUNTIME_LOCK and _served()["fetched"] == 1
    assert _sha(demo) == _sha(SNAPSHOT), "premise: the runtime's writes never reach the book"
    did.update(stat=0, hash=0, seed=0)
    return SimpleNamespace(demo=demo, cache=cache, did=did, load=load, load_here=load_here,
                           forgot=forgot)


# ── the three cases ──────────────────────────────────────────────────────────

def test_a_new_book_is_served_from_the_next_page_load(running):
    new = _deploy(running.demo)
    assert _served()["q3_lock"] == RUNTIME_LOCK, "until a page loads, nothing has looked"
    running.load()
    assert running.did == {"stat": 1, "hash": 1, "seed": 1}
    assert _seeded_from(running.cache) == new
    got = _served()
    assert got["q3_lock"] == COMMITTED_LOCK, "the runtime lock went with the old seed"
    assert got["fetched"] == 0 and got["new_ticker"] == 1
    assert running.forgot == ["forgot"], "what the process built on the old seed is forgotten"

    running.load()
    assert running.did == {"stat": 2, "hash": 1, "seed": 1}, "and the load after is one stat"


def test_an_unchanged_book_costs_one_stat_a_load_and_nothing_else(running):
    for _ in range(5):
        running.load()
    assert running.did == {"stat": 5, "hash": 0, "seed": 0}
    assert _served()["q3_lock"] == RUNTIME_LOCK and _served()["fetched"] == 1
    assert running.forgot == []


def test_a_book_touched_and_not_changed_is_hashed_once_and_not_reseeded(running):
    before = _seeded_from(running.cache)
    later = running.demo.stat().st_mtime_ns + 5_000_000_000
    os.utime(running.demo, ns=(later, later))
    running.load()
    assert running.did == {"stat": 1, "hash": 1, "seed": 0}
    running.load()
    running.load()
    assert running.did == {"stat": 3, "hash": 1, "seed": 0}, "hashed once, not on every load"
    assert _seeded_from(running.cache) == before
    assert _served()["q3_lock"] == RUNTIME_LOCK and _served()["fetched"] == 1
    assert running.forgot == []


# ── never under a render in progress ─────────────────────────────────────────

def test_the_reseed_waits_for_a_render_in_progress(running):
    """Another page is still rendering from the cache when the new book lands. This load
    is served the seed that page is reading; the first load after it ends re-seeds."""
    began, finish = threading.Event(), threading.Event()

    def page():
        running.load_here()
        began.set()
        finish.wait(30)

    render = threading.Thread(target=page, name="a render in progress")
    render.start()
    try:
        assert began.wait(30)
        running.did.update(stat=0, hash=0, seed=0)
        _deploy(running.demo)
        running.load()
        assert running.did == {"stat": 1, "hash": 1, "seed": 0}
        assert _served()["q3_lock"] == RUNTIME_LOCK and _served()["new_ticker"] == 0
        running.load()
        assert running.did == {"stat": 2, "hash": 1, "seed": 0}, "waiting costs no second hash"
        assert running.forgot == []
    finally:
        finish.set()
        render.join(30)
    assert not render.is_alive()
    running.load()
    assert running.did == {"stat": 3, "hash": 1, "seed": 1}
    assert _served()["q3_lock"] == COMMITTED_LOCK and _served()["new_ticker"] == 1
    assert running.forgot == ["forgot"]


def test_a_render_does_not_wait_for_itself(running):
    """The same thread's next run is the page loading again, not another render."""
    out = {}

    def page():
        running.load_here()
        _deploy(running.demo)
        running.load_here()
        out.update(_served())

    render = threading.Thread(target=page)
    render.start()
    render.join(60)
    assert out["q3_lock"] == COMMITTED_LOCK and running.did["seed"] == 1


def test_a_load_that_begins_during_a_reseed_waits_for_it_and_reads_the_new_seed(running, monkeypatch):
    seeding, proceed = threading.Event(), threading.Event()
    seed = db._seed_runtime_cache

    def slow(*a, **k):
        if not seeding.is_set():            # the first seed is held; any other runs at once
            seeding.set()
            assert proceed.wait(30)
        return seed(*a, **k)

    monkeypatch.setattr(db, "_seed_runtime_cache", slow)
    _deploy(running.demo)
    read = {}
    first = threading.Thread(target=running.load_here)
    first.start()
    assert seeding.wait(30)

    def second_page():
        running.load_here()
        read.update(_served())

    second = threading.Thread(target=second_page)
    second.start()
    second.join(0.5)
    assert second.is_alive() and not read, "it waits on the seed lock, before reading anything"
    proceed.set()
    first.join(30)
    second.join(30)
    assert read["q3_lock"] == COMMITTED_LOCK and read["new_ticker"] == 1


# ── the seed is one transaction ──────────────────────────────────────────────

def test_a_seed_that_fails_leaves_the_seed_before_it_whole(running):
    """The new book's dividends table cannot be copied. Prices are copied first; with
    each statement committing on its own the cache was left holding the new prices and
    no dividends table. One transaction: the old seed is still all there."""
    before, sha = _served(), _seeded_from(running.cache)
    _deploy(running.demo, broken=True)
    with pytest.raises(sqlite3.OperationalError):
        running.load()
    assert running.did["seed"] == 0
    con = sqlite3.connect(running.cache)
    try:
        assert con.execute("SELECT COUNT(*) FROM runtime_prices").fetchone()[0] == before["prices"]
        assert con.execute("SELECT COUNT(*) FROM runtime_prices WHERE ticker = 'ZZZZ'"
                           ).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM runtime_dividends").fetchone()[0] == before[
            "dividends"]
        assert con.execute("SELECT captured_at FROM runtime_quarter_snapshots WHERE quarter_id "
                           "= '2026Q3'").fetchone()[0] == RUNTIME_LOCK
    finally:
        con.close()
    assert _seeded_from(running.cache) == sha and running.forgot == []

    # The load after tries again without hashing again, and a good book re-seeds.
    with pytest.raises(sqlite3.OperationalError):
        running.load()
    assert running.did["hash"] == 1
    _deploy(running.demo)
    running.load()
    assert _served()["q3_lock"] == COMMITTED_LOCK and running.forgot == ["forgot"]


# ── what the process built on the old seed goes with it ──────────────────────

def test_the_last_fetch_and_the_trailing_memo_are_forgotten_with_the_seed(running, monkeypatch):
    """After a success the refresh waits for the next close. A re-seed dropped what it
    fetched, so it fetches again at once, as a restarted process would."""
    import pandas as pd
    import src.demo_refresh as refresh
    from tests.test_runtime_cache import _session_get
    monkeypatch.setattr(db, "_AFTER_RESEED", [])      # the modules' own, as registered
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    monkeypatch.setattr(refresh, "_STATE", None)
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(url)
        return _session_get(url, params, timeout)

    monkeypatch.setattr(prices._SESSION, "get", get)
    db.after_reseed(prices._reset_trailing_memo)      # prices registers its own at import
    now = datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc)
    first = refresh.refresh_if_due(now)
    assert first.status == "fetched" and calls
    fetched = len(calls)
    assert refresh.refresh_if_due(now + timedelta(minutes=1)) is first and len(calls) == fetched
    prices._TRAILING_MEMO[("VOO", "2026-09-24", "2026-09-24")] = pd.DataFrame()

    running.load()                                     # an unchanged book: nothing forgotten
    assert refresh.state() is first and prices._TRAILING_MEMO

    _deploy(running.demo)
    running.load()
    assert refresh.state() is None and not prices._TRAILING_MEMO
    again = refresh.refresh_if_due(now + timedelta(minutes=2))
    assert again.status == "fetched" and len(calls) > fetched, "fetched again into the new seed"
    assert again.fetched_before is None, "the new seed has no earlier fetch on record"
    assert _served()["fetched"] > 0


def test_the_modules_register_what_they_forget():
    """The price layer registers its trailing-fetch memo when it is imported. (The
    refresh registers its timer when it runs, and the router the page caches on every
    rerun: each is exercised above and below.)"""
    assert prices._reset_trailing_memo in db._AFTER_RESEED


def test_a_hook_runs_once_a_reseed_and_never_on_a_first_seed(running, tmp_path):
    import streamlit as st
    ran = []

    def hook():
        ran.append("ran")

    for _rerun in range(3):                            # the router registers on every rerun
        db.after_reseed(hook)
        db.after_reseed(st.cache_data.clear)
    assert db._AFTER_RESEED.count(hook) == 1
    assert sum(1 for f in db._AFTER_RESEED if f == st.cache_data.clear) == 1
    other = tmp_path / "other.db"
    shutil.copyfile(SNAPSHOT, other)
    os.chmod(other, 0o644)
    db.use_runtime_cache(tmp_path / "other_cache.db", committed=other)
    assert ran == [], "a first seed has nothing to forget"
    _deploy(running.demo)
    running.load()
    running.load()
    assert ran == ["ran"] and running.forgot == ["forgot"]


def test_the_router_registers_the_page_caches_before_it_looks_at_the_book():
    """app.py, in demo mode: st.cache_data.clear is registered, then use_runtime_cache
    runs, so the load that re-seeds also drops the figures cached from the old book."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    demo = next(n for n in tree.body if isinstance(n, ast.If)
                and isinstance(n.test, ast.Name) and n.test.id == "IS_DEMO")
    calls = [ast.unparse(n.value) for n in demo.body
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    assert calls[:2] == ["after_reseed(st.cache_data.clear)", "use_runtime_cache()"], calls


def test_the_router_serves_the_new_book_and_drops_cached_figures(tmp_path, monkeypatch):
    """The real router, twice in one process, with the book swapped between: a figure a
    page cached from the old book is computed again, and the cache holds the new book.
    Without a swap, the cached figure is kept."""
    import socket
    import src.macro
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from tests.conftest import _pin_mode, pin_today, unpin_leftovers

    def offline(*_a, **_k):
        raise OSError("network blocked: this test reads the staged books only")

    monkeypatch.setattr(socket, "getaddrinfo", offline)
    monkeypatch.setattr(prices._SESSION, "get", offline)
    monkeypatch.setattr(src.macro, "fetch_fred_series", offline)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    pin_today(monkeypatch)
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    demo = root / "data" / "demo.db"
    shutil.copyfile(SNAPSHOT, demo)
    os.chmod(demo, 0o644)
    cache = tmp_path / "cache.db"
    _pin_mode(monkeypatch, "demo")
    monkeypatch.setattr(db, "_ROOT", root)
    monkeypatch.setattr(db, "DB_PATH", demo)
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_SEEDED", {})
    monkeypatch.setattr(db, "_AFTER_RESEED", [])
    monkeypatch.setenv("DEMO_RUNTIME_CACHE", str(cache))
    computed = []

    @st.cache_data
    def a_cached_figure():
        computed.append(1)
        return len(computed)

    def page_load():
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300).run()
        assert not at.exception, at.exception
        return a_cached_figure()

    try:
        assert page_load() == 1
        assert page_load() == 1, "the same book: the cached figure is kept"
        assert _seeded_from(cache) == _sha(SNAPSHOT)
        new = _deploy(demo)
        assert page_load() == 2, "a new book: computed again"
        assert _seeded_from(cache) == new and _served()["new_ticker"] == 1
        assert page_load() == 2
    finally:
        st.cache_data.clear()
        unpin_leftovers()
