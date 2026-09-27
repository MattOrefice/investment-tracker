"""The startup FRED warm (audit item 15f).

A fresh container's first Macro visit waited about 15 seconds for 22 fetches. The
router now starts one background thread per process that fetches the Macro page's
series into today's cache, and get_series takes one fetch per series at a time, so a
visit that arrives mid-warm waits for the fetch in flight instead of repeating it.
"""
from __future__ import annotations

import ast
import os
import shutil
import threading
from pathlib import Path

import pandas as pd
import pytest

import src.db as db
import src.macro as macro

ROOT = Path(__file__).resolve().parent.parent


def _page_requests() -> "list[tuple[str, str]]":
    tree = ast.parse((ROOT / "pages" / "3_Macro.py").read_text(encoding="utf-8"))
    return [(n.args[0].value, n.args[1].value) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_try_fred"
            and len(n.args) == 2 and all(isinstance(a, ast.Constant) for a in n.args)]


def test_the_warm_list_is_exactly_what_the_macro_page_requests():
    page = _page_requests()
    assert len(page) == 22, "premise: the page's 22 series"
    assert sorted(page) == sorted(macro.MACRO_PAGE_SERIES)


def test_the_router_starts_the_warm_in_the_demo_branch_after_the_price_refresh():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    demo = src[src.index("if IS_DEMO:"):src.index("else:", src.index("if IS_DEMO:"))]
    assert demo.index("refresh_if_due()") < demo.index("warm_cache_in_background()")
    assert src.count("warm_cache_in_background") == 2, "one import and one call"


def test_the_warm_is_off_when_the_fetch_timer_is(monkeypatch):
    monkeypatch.setattr(macro, "_WARM_THREAD", None)
    assert macro.warm_cache_in_background() is None, "the suite runs with the timer off"


@pytest.fixture
def warm_db(tmp_path, monkeypatch):
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(macro, "_WARM_THREAD", None)
    monkeypatch.setattr(macro, "_SERIES_LOCKS", {})
    import src.demo_refresh as demo_refresh
    monkeypatch.setattr(demo_refresh, "enabled", lambda: True)
    monkeypatch.setattr(macro, "_FAILED", {})
    return copy


def _fake(calls, gate=None, gated=None):
    def fetch(series_id, start_date, end_date=None):
        calls.append(series_id)
        if gate is not None and series_id == gated:
            gate.wait(10)
        return pd.Series([1.0, 2.0], index=pd.to_datetime([start_date, "2026-09-24"]),
                         name=series_id)
    return fetch


def test_the_warm_fills_todays_cache_once_per_process(warm_db, monkeypatch):
    calls = []
    monkeypatch.setattr(macro, "fetch_fred_series", _fake(calls))
    t = macro.warm_cache_in_background()
    assert t is not None
    t.join(30)
    assert sorted(calls) == sorted(s for s, _ in macro.MACRO_PAGE_SERIES)
    assert macro.warm_cache_in_background() is None, "once per process"
    for series_id, start in macro.MACRO_PAGE_SERIES:
        macro.get_series(series_id, start)
    assert len(calls) == 22, "every page request is now a cache hit"


def test_a_request_during_the_warm_waits_for_the_fetch_in_flight(warm_db, monkeypatch):
    calls, gate = [], threading.Event()
    monkeypatch.setattr(macro, "fetch_fred_series", _fake(calls, gate, "USREC"))
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
