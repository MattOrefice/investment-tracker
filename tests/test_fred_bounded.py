"""Every FRED read is bounded where the warm runs (#435).

The Macro page waited for the warm at most PAGE_WAIT_SECONDS (audit item 15f), but every
other FRED read fetched with no limit. Since #419 the risk-free rate reads FRED's 3-month
bill series, so Performance and Asset Evaluation are readers, and so is the PDF. Each now
follows Macro's rule: past the wait, the stored copy, with its date, and the warm carries
on. One stalled warm costs the first reader the wait and later readers nothing.

Each reader is proved with FRED stubbed to hang, on the live demo's arrangement: a
demo.db copy behind a fresh runtime cache, the fetch timer and the warm on. A watchdog
releases FRED just past each bound, so a reader that never stops waiting fails the bound
rather than hanging the run.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

import src.config as config
import src.db as db
import src.macro as macro
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent
WAIT = macro.PAGE_WAIT_SECONDS
BOUND = WAIT + 45                   # the wait plus a page's own render, generously


@pytest.fixture
def hanging_fred(tmp_path, monkeypatch):
    """The live demo on a fresh container, FRED hanging until released. Yields the
    release event; the warm is joined before the test ends."""
    import streamlit as st
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_SEEDED", set())
    db.use_runtime_cache(tmp_path / "cache.db", committed=copy)
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    monkeypatch.setenv("DEMO_FRED_WARM", "1")
    for name, value in (("_WARM_THREAD", None), ("_WARM_DAY", None), ("_STALLED_WARM", None),
                        ("_SERIES_LOCKS", {}), ("_FAILED", {})):
        monkeypatch.setattr(macro, name, value)
    monkeypatch.setattr(config, "IS_DEMO", True)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    release = threading.Event()

    def hang(series_id, start_date, end_date=None):
        release.wait(300)
        raise OSError("FRED released at the end of the test")

    monkeypatch.setattr(macro, "fetch_fred_series", hang)
    watchdog = threading.Timer(BOUND + 5, release.set)
    watchdog.start()
    st.cache_data.clear()
    try:
        yield release
    finally:
        watchdog.cancel()
        watchdog.join()
        release.set()
        warm = macro._WARM_THREAD
        if warm is not None:
            warm.join(60)
        st.cache_data.clear()


def _render(page: str):
    from streamlit.testing.v1 import AppTest
    t0 = time.monotonic()
    at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300).run()
    elapsed = time.monotonic() - t0
    assert not at.exception, [str(e.value) for e in at.exception]
    return at, elapsed


def _texts(at) -> str:
    return " ".join(str(e.value) for e in list(at.markdown) + list(at.caption) + list(at.info))


def test_performance_holds_for_the_wait_at_most_and_states_the_stored_bill_rate(hanging_fred):
    warm = macro.warm_cache_in_background()                  # the router's call
    assert warm is not None and warm.is_alive()
    at, elapsed = _render("2_Performance.py")
    assert elapsed < BOUND, elapsed
    text = _texts(at)
    assert ("FRED was unavailable, so the rate comes from the stored series, which ends "
            "May 26, 2026") in text
    assert warm.is_alive(), "FRED still hangs; the page did not wait for it"


def test_asset_evaluation_waits_once_for_all_four_of_its_fred_series(hanging_fred):
    """The bill rate, the recession shading and three regime series: one wait, not four,
    and each stored copy named with the date it was fetched."""
    macro.warm_cache_in_background()
    at, elapsed = _render("5_Asset_Evaluation.py")
    assert elapsed < BOUND, elapsed
    text = _texts(at)
    notice = next(i.value for i in at.info if "did not arrive" in i.value)
    for part in ("USREC (recession shading), July 16, 2026",
                 "T10Y2Y (regime table), July 16, 2026",
                 "UNRATE (regime table), July 16, 2026",
                 "USREC (regime table), July 16, 2026"):
        assert part in notice, (part, notice)
    assert "the stored series, which ends May 26, 2026" in text
    assert "Regime data unavailable" not in text, "the regime table renders from stored copies"


def test_the_pdfs_macro_section_names_its_stored_copies(hanging_fred):
    from src import reports
    macro.warm_cache_in_background()
    t0 = time.monotonic()
    section = reports._build_macro_section()
    assert time.monotonic() - t0 < WAIT + 10
    assert section["yield_curve"]["value"] != "N/A"
    # HY OAS's copy is the one fetched 2026-05-03: FRED serves the ICE series over a
    # rolling three years, so each later copy starts later. macro._read_stored (#429)
    # takes the newest of the copies that start earliest (2023-05-02: those fetched
    # 05-02 and 05-03), not the newest copy (05-27, starting 2023-05-29).
    assert section["as_of"].endswith(
        "FRED did not answer in time, so these are stored copies, with the date each was "
        "fetched: 10Y-2Y 2026-07-16, Fed funds 2026-05-27, HY OAS 2026-05-03."), section["as_of"]


def test_one_stalled_warm_costs_the_first_reader_the_wait_and_later_readers_nothing(
        hanging_fred):
    macro.warm_cache_in_background()
    t0 = time.monotonic()
    with pytest.raises(macro.FREDStillFetching) as first:
        macro.get_series("DGS3MO", "1990-01-01", deadline=time.monotonic() + 1.0)
    assert 1.0 <= time.monotonic() - t0 < 5
    assert first.value.stored is not None
    t1 = time.monotonic()
    got, on = macro.series_or_stored("UNRATE", "1948-01-01")
    assert time.monotonic() - t1 < 1.0, "the second read did not wait again"
    assert on == "2026-07-16" and not got.empty


def test_the_warm_itself_is_not_bounded(hanging_fred, monkeypatch):
    """The warm is what the readers wait for, so it fetches through the unbounded read.
    If it went through the bounded one it would wait for itself."""
    calls = []
    monkeypatch.setattr(macro, "fetch_fred_series",
                        lambda s, start, end=None: calls.append(s) or pd.Series(
                            [1.0, 2.0], index=pd.to_datetime([start, "2026-09-24"]), name=s))
    warm = macro.warm_cache_in_background()
    warm.join(30)
    assert sorted(calls) == sorted(s for s, _ in macro.MACRO_PAGE_SERIES)
