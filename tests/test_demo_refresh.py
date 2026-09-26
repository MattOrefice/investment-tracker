"""#368 item 3: the demo fetches settled prices daily, at runtime, into the cache.

Every test here stubs the provider (tests.test_runtime_cache._session_get); nothing
reaches the network, and conftest keeps the refresh off everywhere else
(DEMO_DAILY_FETCH=0).
"""
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.db as db
import src.demo_refresh as refresh
import src.prices as prices
from src.asof import as_of_live_line
from tests.test_runtime_cache import _session_get

_ROOT = Path(__file__).resolve().parent.parent
_DEMO = _ROOT / "data" / "demo.db"
NOW = datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc)      # after the New York close


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """A demo.db copy standing in for the committed file, the runtime cache on, the
    refresh enabled for this test only, and a call counter on the provider."""
    copy = tmp_path / "demo.db"
    shutil.copyfile(_DEMO, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_SEEDED", set())
    db.use_runtime_cache(tmp_path / "cache.db", committed=copy)
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    monkeypatch.setattr(refresh, "_STATE", None)
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    prices._reset_trailing_memo()
    calls = []

    def answer(fail=()):
        def get(url, params=None, timeout=None):
            ticker = url.rstrip("/").split("/")[-1]
            calls.append(ticker)
            if fail == "all" or ticker in fail:
                raise OSError(f"unreachable: {ticker}")
            return _session_get(url, params, timeout)
        monkeypatch.setattr(prices._SESSION, "get", get)

    return copy, calls, answer


def test_a_fetch_moves_the_frontier_to_the_latest_settled_close(demo):
    copy, calls, answer = demo
    before = hashlib.sha256(copy.read_bytes()).hexdigest()
    answer()
    st = refresh.refresh_if_due(NOW)
    assert st.status == "fetched" and st.failed == ()
    assert st.served_through == "2026-09-24", (
        "the latest settled close, as the banner reads it: today's, stored after the close")
    assert st.next_attempt_at == datetime(2026, 9, 25, 20, 30, tzinfo=timezone.utc), (
        "after a success: the next New York close plus the publish margin")
    assert len(set(calls)) == len(calls) > 30, "every ticker the demo prices, once"
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == before, "demo.db untouched"


def test_a_failed_fetch_serves_the_snapshot_and_waits_for_its_timer(demo):
    _copy, calls, answer = demo
    answer(fail="all")
    st = refresh.refresh_if_due(NOW)
    assert st.status == "failed" and st.served_through == "2026-07-20"
    assert st.fetched_before is None
    assert st.next_attempt_at == NOW + refresh.RETRY_AFTER
    n = len(calls)
    assert refresh.refresh_if_due(NOW + timedelta(minutes=10)) is st
    assert len(calls) == n, "no attempt before the timer"
    assert refresh.refresh_if_due(NOW + refresh.RETRY_AFTER + timedelta(minutes=1)) is not st
    assert len(calls) > n, "the timer lets the next attempt through"


def test_reads_do_not_fetch_once_the_refresh_runs(demo):
    """The per-render retry the timer exists to prevent: gap fills and the dividend
    re-fetch are off, and a read serves what the cache holds."""
    _copy, calls, answer = demo
    answer(fail="all")
    refresh.refresh_if_due(NOW)
    n = len(calls)
    got = prices.get_prices("VOO", "2026-07-01", "2026-12-31")
    prices.get_dividends("VOO", "2026-07-21", "2026-12-31")
    assert len(calls) == n and max(got.index) == date(2026, 7, 20)


def test_a_non_holding_that_fails_does_not_fail_the_fetch_and_a_holding_does(demo):
    _copy, _calls, answer = demo
    answer(fail=("XLK",))
    st = refresh.refresh_if_due(NOW)
    assert st.status == "fetched" and [t for t, _ in st.failed] == ["XLK"]
    refresh._STATE = None
    answer(fail=("VOO",))
    st = refresh.refresh_if_due(NOW + timedelta(days=1))
    assert st.status == "failed" and [t for t, _ in st.failed] == ["VOO"]
    assert st.fetched_before == NOW.isoformat(timespec="minutes"), "the earlier success is recorded"


def test_the_refresh_is_off_in_the_suite_and_without_the_cache(monkeypatch):
    monkeypatch.setattr(refresh, "_STATE", None)
    monkeypatch.setenv("DEMO_DAILY_FETCH", "0")
    assert refresh.refresh_if_due(NOW) is None
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    assert refresh.refresh_if_due(NOW) is None, "never fetch into demo.db itself"


# ── the banner, in each state ─────────────────────────────────────────────────

def _state(status, served, before=None, attempted=NOW):
    return refresh.RefreshState(
        status=status, attempted_at=attempted,
        next_attempt_at=(refresh.next_close_after(attempted) if status == "fetched"
                         else attempted + refresh.RETRY_AFTER),
        served_through=served, fetched_before=before)


@pytest.mark.parametrize("st, frontier, text", [
    (_state("fetched", "2026-09-23"), "2026-09-23",
     "Prices through September 23, 2026 (settled closes, fetched September 24, 2026 at 6:00 PM ET)."),
    (_state("failed", "2026-07-20"), "2026-07-20",
     "Prices through July 20, 2026 (settled closes) — 47 weekdays behind. The daily price fetch "
     "failed September 24, 2026 at 6:00 PM ET; serving the committed snapshot until it "
     "retries after September 24, 2026 at 6:30 PM ET."),
    (_state("failed", "2026-09-22", before="2026-09-23T21:05+00:00"), "2026-09-22",
     "Prices through September 22, 2026 (settled closes) — 1 weekday behind. The daily price "
     "fetch failed September 24, 2026 at 6:00 PM ET; serving prices last fetched "
     "September 23, 2026 at 5:05 PM ET until it retries after September 24, 2026 at 6:30 PM ET."),
    (None, "2026-07-20", "Prices through July 20, 2026 (settled closes) — 47 weekdays behind."),
])
def test_the_banner_states_the_date_served_its_basis_and_any_failure(monkeypatch, st, frontier, text):
    monkeypatch.setattr(refresh, "_STATE", st)
    assert as_of_live_line(date(2026, 9, 24), frontier=frontier) == text


def test_a_fetch_that_did_not_reach_this_page_still_reads_behind(monkeypatch):
    """"Fetched" drops the lag only for a date the fetch confirmed. A page served an
    older date keeps its honest lag."""
    monkeypatch.setattr(refresh, "_STATE", _state("fetched", "2026-09-23"))
    assert as_of_live_line(date(2026, 9, 24), frontier="2026-09-19") == (
        "Prices through September 19, 2026 (settled closes) — 3 weekdays behind.")


# ── the landing page, end to end ──────────────────────────────────────────────

def test_the_landing_banner_reports_the_fetched_date_before_it_renders(tmp_path, monkeypatch):
    """app.py in demo mode, fetch answered: the landing banner is read AFTER the
    startup refresh, so it names the fetched date, not the snapshot's July 20."""
    from tests.conftest import _pin_mode
    _pin_mode(monkeypatch, "demo")
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_SEEDED", set())
    monkeypatch.setenv("DEMO_RUNTIME_CACHE", str(tmp_path / "cache.db"))
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    monkeypatch.setattr(refresh, "_STATE", None)
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    monkeypatch.setattr(prices._SESSION, "get", _session_get)
    before = hashlib.sha256(_DEMO.read_bytes()).hexdigest()

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_ROOT / "app.py"), default_timeout=300).run()
    assert not at.exception, at.exception
    banner = next(m.value for m in at.markdown if 'class="endow-recency"' in m.value)
    banner = banner.split('class="endow-recency">')[1].split("</p>")[0]
    assert "fetched" in banner and "July 20" not in banner, banner
    assert hashlib.sha256(_DEMO.read_bytes()).hexdigest() == before


# ── the report's live sections say when they are from ─────────────────────────

def test_the_reports_live_sections_state_their_own_as_of(tmp_path, monkeypatch):
    from src import reports
    from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    try:
        point_at_frozen_book(monkeypatch, tmp_path)
        macro = reports._build_macro_section()
        assert macro["as_of"].startswith("Live, not locked to the report's quarter: "
                                         "each reading is its latest observation")
        assert "CAPE " in macro["as_of"]
        ae = reports._build_asset_eval_section()
        assert ae["as_of"].startswith("Live, not locked to the report's quarter: returns from ")
        assert ae["as_of"].endswith(" through 2026-07-16."), ae["as_of"]
    finally:
        unpin_leftovers()
