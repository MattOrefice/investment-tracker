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
    monkeypatch.setattr(db, "_SEEDED", set())
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


def test_the_macro_page_does_not_fetch_fred_between_renders(page, monkeypatch):
    clock, calls, up = page
    _caps, retry, unavailable = _render(monkeypatch)
    first = len(calls)
    assert first >= 20 and len(set(calls)) == first, "each series tried once"
    assert unavailable and not retry, "no Retry button while the timer governs"

    clock[0] = T0 + timedelta(minutes=10)
    captions, retry, unavailable = _render(monkeypatch)
    assert len(calls) == first, "a render inside the wait fetches nothing"
    waits = [c for c in captions if c.startswith("FREDRetryWait:")]
    assert len(waits) == len(unavailable) and not retry
    assert all("it retries after September 25, 2026 at 12:30 PM ET" in c for c in waits)

    up["fred"] = True
    clock[0] = T0 + refresh.RETRY_AFTER
    _caps, retry, unavailable = _render(monkeypatch)
    assert len(calls) > first and not unavailable, "after the wait, the page fetches and renders"


def test_off_the_timer_the_page_keeps_its_retry_buttons(page, monkeypatch):
    """The contrast: without the timer the failure is the original error, and the
    Retry button is still there, because a click does fetch."""
    _clock, calls, _up = page
    monkeypatch.setenv("DEMO_DAILY_FETCH", "0")
    captions, retry, unavailable = _render(monkeypatch)
    assert unavailable and len(retry) == len(unavailable)
    assert not any(c.startswith("FREDRetryWait:") for c in captions)
