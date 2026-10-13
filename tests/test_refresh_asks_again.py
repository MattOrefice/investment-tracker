"""Every refresh asks again for the closes its book lacks (#501).

A provider response that lacks one session's close and holds later settled ones left a
gap inside a history, and every later fetch started after the ticker's newest stored
date, so nothing asked for that session again. Now, after the demo's daily refresh and
after each personal page load whose reads fetched, the gap check runs on the book and
each close it names is fetched again, with no stored row changed (src.price_gaps).

#501's three scenarios, each ending with no gap once the provider supplies the close,
and with the gap named while it does not:
  1. the demo's daily refresh on two days, provider complete;
  2. the same, VOO's close null on October 6 and BTC-USD's on October 7 on the first day;
  3. a personal book refreshed by reads, AVDV's close null on October 6.

Copies of the frozen book, whose prices end in July 2026 for good. The stand-in
provider answers every NYSE session asked, and every calendar day for a daily series,
except the closes it is told to lack, which come back null as #501 found them.
"""
from __future__ import annotations

import ast
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.db as db
import src.demo_refresh as refresh
import src.price_gaps as price_gaps
import src.prices as prices
from src.price_gaps import DAILY, MISSING
from tests.conftest import FROZEN_BOOK

ROOT = Path(__file__).resolve().parent.parent
DAY_1 = datetime(2026, 10, 12, 22, 0, tzinfo=timezone.utc)      # Monday, after the close
DAY_2 = datetime(2026, 10, 13, 22, 0, tzinfo=timezone.utc)
VOO_GAP = ("VOO", "2026-10-06")
BTC_GAP = ("BTC-USD", "2026-10-07")
AVDV_GAP = ("AVDV", "2026-10-06")


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class Provider:
    """The stand-in. ``lacking`` holds the (ticker, ISO date) closes it returns null;
    ``close`` is the close it reports for every other bar; ``asked`` records each
    request as (ticker, first day, last day)."""

    def __init__(self, monkeypatch):
        self.lacking: "set[tuple[str, str]]" = set()
        self.close = 100.0
        self.dividends: "dict[str, float]" = {}        # ISO ex-date -> amount, any ticker
        self.every_day = False                         # a bar on weekends and holidays too
        self.asked: "list[tuple[str, str, str]]" = []
        monkeypatch.setattr(prices._SESSION, "get", self.get)

    def get(self, url, params=None, timeout=None):
        ticker = url.rstrip("/").split("/")[-1]
        start = datetime.fromtimestamp(params["period1"], tz=timezone.utc).date()
        stop = datetime.fromtimestamp(params["period2"], tz=timezone.utc).date()
        self.asked.append((ticker, start.isoformat(), (stop - timedelta(days=1)).isoformat()))
        days = [start + timedelta(days=i) for i in range((stop - start).days)]
        days = [d for d in days if self.every_day or ticker in DAILY or refresh.is_session(d)]
        stamp = lambda d: int(datetime(d.year, d.month, d.day, 20, tzinfo=timezone.utc).timestamp())
        closes = [None if (ticker, d.isoformat()) in self.lacking else self.close for d in days]
        past = int(datetime(2000, 1, 3, tzinfo=timezone.utc).timestamp())
        events = {str(stamp(date.fromisoformat(d))): {"amount": a}
                  for d, a in self.dividends.items() if start <= date.fromisoformat(d) < stop}
        return _Resp({"chart": {"result": [{
            "meta": {"currentTradingPeriod": {"regular": {"start": past, "end": past + 3600}}},
            "timestamp": [stamp(d) for d in days],
            "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": closes}]},
            "events": {"dividends": events} if events else {},
        }], "error": None}})

    def asked_for(self, ticker) -> "list[tuple[str, str]]":
        return [(a, b) for t, a, b in self.asked if t == ticker]


def _rows(book, where="1 = 1", *args) -> "dict[tuple[str, str], float]":
    """{(ticker, date): close} as the app's own connection serves the book."""
    with db.get_connection() as conn:
        return {(t, d): c for t, d, c in conn.execute(
            f"SELECT ticker, price_date, close FROM prices WHERE {where}", args)}


def _missing() -> "list[tuple[str, str]]":
    return [(t, d) for t, d, what in price_gaps.book_gaps() if what == MISSING]


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("PRICE_GAP_REASK", "1")
    monkeypatch.setattr(prices, "_GAP_FETCH", True)
    monkeypatch.setattr(price_gaps, "_SEEN", prices.fetch_count())
    monkeypatch.setattr(price_gaps, "_LAST", None)
    prices._reset_trailing_memo()
    return Provider(monkeypatch)


@pytest.fixture
def demo(tmp_path, monkeypatch, provider):
    """The frozen book standing in for the committed demo.db, the runtime cache on and
    the daily refresh enabled."""
    copy = tmp_path / "demo.db"
    shutil.copyfile(FROZEN_BOOK, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(db, "_SEEDED", {})
    db.use_runtime_cache(tmp_path / "cache.db", committed=copy)
    monkeypatch.setenv("DEMO_DAILY_FETCH", "1")
    monkeypatch.setattr(refresh, "_STATE", None)
    assert _missing() == [], "premise: the book starts with no gap"
    return copy


@pytest.fixture
def personal(tmp_path, monkeypatch, provider):
    """The frozen book standing in for a personal book: no runtime cache, gap fetches
    on, so a read fetches what lies past a ticker's newest stored close."""
    copy = tmp_path / "tracker.db"
    shutil.copyfile(FROZEN_BOOK, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    assert _missing() == [], "premise: the book starts with no gap"
    return copy


def _page_reads(through: str) -> None:
    """What a personal page load does to the book: a read of every stored ticker
    through ``through``, each fetching past its newest stored close."""
    for ticker in sorted(price_gaps.stored_dates()):
        prices.get_prices(ticker, "2026-07-01", through)


# ── scenario 1: the demo's daily refresh, provider complete ───────────────────

def test_1_a_complete_provider_leaves_no_gap_and_is_asked_once_a_ticker(demo, provider):
    for now in (DAY_1, DAY_2):
        provider.asked.clear()
        st = refresh.refresh_if_due(now)
        assert st.status == "fetched" and st.failed == () and st.gaps == ()
        assert price_gaps.book_gaps() == []
        tickers = [t for t, _a, _b in provider.asked]
        assert len(tickers) == len(set(tickers)) == 47, "nothing to ask for again"
    assert st.served_through == "2026-10-13"


# ── scenario 2: the demo's daily refresh, two closes null on the first day ────

def test_2_a_close_the_provider_lacks_is_named_asked_again_and_filled(demo, provider):
    from src.cache import quarter_lock_gaps

    provider.lacking = {VOO_GAP, BTC_GAP}
    st = refresh.refresh_if_due(DAY_1)

    # Named, while the provider lacks them: by the gap check, by the refresh, and by
    # what a page reads for a ticker a lock covers. No lock covers BTC-USD.
    assert _missing() == [BTC_GAP, VOO_GAP]
    assert st.gaps == (BTC_GAP, VOO_GAP)
    assert quarter_lock_gaps("2026Q4") == ["2026-10-06 for VOO"]
    # BTC-USD is held to every calendar day: its weekend closes arrived and are no gap.
    held = _rows(demo, "ticker = 'BTC-USD' AND price_date >= '2026-10-01'")
    assert ("BTC-USD", "2026-10-10") in held and ("BTC-USD", "2026-10-11") in held
    # The fetch did not stop at the gap, and it is not a failed fetch.
    assert st.status == "fetched" and st.failed == ()
    voo = sorted(d for t, d in _rows(demo, "ticker = 'VOO' AND price_date >= '2026-10-01'"))
    assert voo == ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-07", "2026-10-08",
                   "2026-10-09", "2026-10-12"]
    # Each was asked for again in the same attempt, for its own date and no other.
    assert provider.asked_for("VOO") == [("2026-07-20", "2026-10-12"),
                                         ("2026-10-06", "2026-10-06")]
    assert provider.asked_for("BTC-USD") == [("2026-07-16", "2026-10-12"),
                                             ("2026-10-07", "2026-10-07")]

    # The next day the provider has them. It now reports a different close for every
    # bar, so a stored row the re-ask replaced would show.
    before = _rows(demo, "price_date < '2026-10-12'")
    provider.lacking, provider.close = set(), 101.0
    provider.asked.clear()
    st = refresh.refresh_if_due(DAY_2)
    assert st.status == "fetched" and st.gaps == ()
    assert price_gaps.book_gaps() == []
    assert quarter_lock_gaps("2026Q4") == []
    after = _rows(demo, "price_date < '2026-10-12'")
    assert {k: after[k] for k in set(after) - set(before)} == {VOO_GAP: 101.0, BTC_GAP: 101.0}
    assert {k: v for k, v in after.items() if k in before} == before, "no stored row changed"
    assert provider.asked_for("VOO") == [("2026-10-12", "2026-10-13"),
                                         ("2026-10-06", "2026-10-06")]


def test_2_the_committed_book_is_not_what_the_refresh_fills(demo, provider):
    """The demo writes its runtime cache, never the book it was seeded from."""
    import hashlib
    sha = lambda: hashlib.sha256(demo.read_bytes()).hexdigest()
    before = sha()
    provider.lacking = {VOO_GAP}
    refresh.refresh_if_due(DAY_1)
    provider.lacking = set()
    refresh.refresh_if_due(DAY_2)
    assert price_gaps.book_gaps() == [] and sha() == before


# ── scenario 3: a personal book refreshed by a page's reads ───────────────────

def test_3_a_personal_load_that_fetched_asks_again_and_one_that_did_not_does_nothing(
        personal, provider):
    provider.lacking = {AVDV_GAP}
    _page_reads("2026-10-12")
    assert _missing() == [AVDV_GAP], "premise: the refresh left #501's gap"

    # What app.py runs once the page has run. The provider still lacks the close: it is
    # asked for, and stays named.
    provider.asked.clear()
    first = price_gaps.reask_after_refresh()
    assert first.named == (AVDV_GAP,) and first.filled == () and first.left == (AVDV_GAP,)
    assert provider.asked == [("AVDV", "2026-10-06", "2026-10-06")]
    assert _missing() == [AVDV_GAP]

    # A second load the same day reads only what is stored (#501: "0 requests"). The
    # book was not refreshed, so nothing is asked, though the provider now has the close.
    provider.lacking, provider.close = set(), 101.0
    provider.asked.clear()
    _page_reads("2026-10-12")
    assert price_gaps.reask_after_refresh() is None
    assert provider.asked == [] and _missing() == [AVDV_GAP]

    # The next refresh: the reads fetch the next close, and the gap is asked for again.
    _page_reads("2026-10-13")
    before = _rows(personal)
    provider.asked.clear()
    again = price_gaps.reask_after_refresh()
    assert again.named == again.filled == (AVDV_GAP,) and again.left == ()
    assert provider.asked == [("AVDV", "2026-10-06", "2026-10-06")]
    assert price_gaps.book_gaps() == []
    after = _rows(personal)
    assert {k: after[k] for k in set(after) - set(before)} == {AVDV_GAP: 101.0}
    assert {k: v for k, v in after.items() if k in before} == before, "no stored row changed"
    assert price_gaps.reask_after_refresh() is None, "its own fetch is not a refresh"


def test_3_a_daily_series_is_held_to_every_calendar_day_in_a_personal_book(personal, provider):
    """A Saturday close BTC-USD lacks is a gap, asked for and filled; the funds beside
    it lack nothing on a weekend."""
    saturday = ("BTC-USD", "2026-10-10")
    provider.lacking = {saturday}
    _page_reads("2026-10-12")
    assert _missing() == [saturday]
    assert price_gaps.reask_after_refresh().left == (saturday,)
    provider.lacking = set()
    _page_reads("2026-10-13")
    assert price_gaps.reask_after_refresh().filled == (saturday,)
    assert price_gaps.book_gaps() == []


# ── the re-ask's fetch: insert-only ───────────────────────────────────────────

# A Friday and the Monday after it, so one request for both spans a weekend.
STAGED = ("2026-07-10", "2026-07-13")


def _stage_two_gaps(book) -> None:
    con = sqlite3.connect(book)
    try:
        with con:
            for day in STAGED:
                assert con.execute("DELETE FROM prices WHERE ticker = 'VOO' AND price_date = ?",
                                   (day,)).rowcount == 1
            con.execute("INSERT OR REPLACE INTO dividends VALUES ('VOO', '2026-07-10', 1.25)")
            con.execute("DELETE FROM dividends WHERE ticker = 'VOO' AND ex_date = '2026-07-13'")
    finally:
        con.close()


def _voo(book) -> "tuple[dict, dict]":
    con = sqlite3.connect(book)
    try:
        closes = dict(con.execute("SELECT price_date, close FROM prices WHERE ticker = 'VOO'"))
        divs = dict(con.execute("SELECT ex_date, amount FROM dividends WHERE ticker = 'VOO'"))
        return closes, divs
    finally:
        con.close()


def test_the_reask_stores_the_named_closes_and_changes_no_stored_row(personal, provider):
    """Two closes missing either side of a weekend, and a third on the Wednesday, so one
    request spans stored closes too. The provider reports a different close for every
    bar, a bar on the weekend, and a different amount for a stored dividend. Only the
    three named closes, and a dividend the book lacked, land."""
    _stage_two_gaps(personal)
    con = sqlite3.connect(personal)
    with con:
        con.execute("DELETE FROM prices WHERE ticker = 'VOO' AND price_date = '2026-07-15'")
    con.close()
    closes, divs = _voo(personal)
    provider.close, provider.every_day = 555.0, True
    provider.dividends = {"2026-07-10": 9.99, "2026-07-13": 0.75}

    got = price_gaps.reask()
    named = (("VOO", "2026-07-10"), ("VOO", "2026-07-13"), ("VOO", "2026-07-15"))
    assert got.named == got.filled == named
    assert provider.asked == [("VOO", "2026-07-10", "2026-07-15")], "one request a ticker"
    after_closes, after_divs = _voo(personal)
    assert after_closes == {**closes, **{d: 555.0 for _t, d in named}}, (
        "the stored Tuesday keeps its close, and the weekend's bars, which the check did "
        "not name, are not stored")
    assert after_divs == {**divs, "2026-07-13": 0.75}, (
        "the stored dividend keeps its amount; the one the book lacked arrives with the close")
    assert price_gaps.book_gaps() == []


def test_control_the_same_fetch_without_only_missing_replaces_stored_rows(personal, provider):
    """The positive the test above needs: this provider WOULD change stored rows through
    the ordinary fetch, so "no stored row changes" is the re-ask's doing."""
    _stage_two_gaps(personal)
    closes, divs = _voo(personal)
    provider.close, provider.every_day = 555.0, True
    provider.dividends = {"2026-07-10": 9.99, "2026-07-13": 0.75}
    prices.fetch_prices("VOO", "2026-07-10", "2026-07-15")
    after_closes, after_divs = _voo(personal)
    assert after_closes["2026-07-14"] == 555.0 != closes["2026-07-14"], "a stored close replaced"
    assert after_closes["2026-07-11"] == 555.0 and "2026-07-11" not in closes, "a weekend bar stored"
    assert after_divs["2026-07-10"] == 9.99 != divs["2026-07-10"], "a stored dividend replaced"


def test_the_reask_is_insert_only_through_the_demos_runtime_cache_too(demo, provider):
    """In the demo `prices` is a view whose insert trigger REPLACES (src.db). The re-ask
    must not replace through it."""
    with db.get_connection() as conn:
        for day in ("2026-07-10", "2026-07-13", "2026-07-15"):
            conn.execute("DELETE FROM prices WHERE ticker = 'VOO' AND price_date = ?", (day,))
    before = _rows(demo, "ticker = 'VOO'")
    provider.close, provider.every_day = 555.0, True
    got = price_gaps.reask()
    named = (("VOO", "2026-07-10"), ("VOO", "2026-07-13"), ("VOO", "2026-07-15"))
    assert got.filled == named
    after = _rows(demo, "ticker = 'VOO'")
    assert after == {**before, **{k: 555.0 for k in named}}, (
        "the stored Tuesday keeps its close and the weekend's bars are not stored")


def test_a_request_that_fails_is_recorded_and_the_close_stays_named(personal, provider, monkeypatch):
    _stage_two_gaps(personal)

    def unreachable(*_a, **_k):
        raise OSError("unreachable")

    monkeypatch.setattr(prices._SESSION, "get", unreachable)
    got = price_gaps.reask()
    assert got.filled == () and len(got.left) == 2
    assert got.failed == (("VOO", "OSError: unreachable"),)
    assert _missing() == [("VOO", d) for d in STAGED]


def test_a_close_on_a_date_that_is_not_a_session_is_named_and_nothing_is_asked(personal, provider):
    """The check's other finding is a close the book should not hold: here VOO's on a
    Saturday. There is nothing to fetch for it, so it stays named and nothing is asked."""
    from src.price_gaps import OFF_SESSION
    con = sqlite3.connect(personal)
    with con:
        con.execute("INSERT INTO prices (ticker, price_date, close) "
                    "VALUES ('VOO', '2026-07-11', 1.0)")
    con.close()
    assert price_gaps.book_gaps() == [("VOO", "2026-07-11", OFF_SESSION)]
    got = price_gaps.reask()
    assert got.named == () and got.failed == () and provider.asked == []
    assert price_gaps.book_gaps() == [("VOO", "2026-07-11", OFF_SESSION)]


def test_a_book_with_no_reference_is_not_checked_and_nothing_is_asked(personal, provider):
    con = sqlite3.connect(personal)
    with con:
        con.execute("DELETE FROM prices WHERE ticker = 'SPY'")
    con.close()
    got = price_gaps.reask()
    assert got.named == () and "no SPY closes" in got.unchecked
    assert provider.asked == []


# ── where it is called from ───────────────────────────────────────────────────

def test_the_suite_turns_it_off():
    """conftest's switch, read as every test that does not ask for the provider meets it."""
    assert os.environ["PRICE_GAP_REASK"] == "0" and not price_gaps.enabled()


def test_it_is_off_in_the_suite_unless_a_test_turns_it_on(personal, provider, monkeypatch):
    """conftest's switch: the demo's refresh and a personal page load fetch for whatever
    book a test points at, so everywhere else in the suite they ask nothing."""
    import tests.conftest  # noqa: F401  (the switch is set at import)
    _stage_two_gaps(personal)
    prices.fetch_prices("SPY", "2026-07-20", "2026-07-20")       # a refresh happened
    provider.asked.clear()
    monkeypatch.setenv("PRICE_GAP_REASK", "0")
    assert price_gaps.reask_after_refresh() is None and provider.asked == []
    monkeypatch.setenv("PRICE_GAP_REASK", "1")
    assert price_gaps.reask_after_refresh().filled == tuple(("VOO", d) for d in STAGED)


def test_the_tool_and_the_app_run_one_rule(personal):
    """tools/check_price_histories.py reads a committed book by path; the app reads the
    book it serves. Same findings, from the one function."""
    import tools.check_price_histories as tool
    _stage_two_gaps(personal)
    assert tool.gaps_in is price_gaps.gaps_in
    assert tool.history_gaps(personal) == price_gaps.book_gaps() == [
        ("VOO", d, MISSING) for d in STAGED]


def _fresh_personal_router(tmp_path, monkeypatch):
    """A personal book bootstrapped from nothing (a clean clone's first launch), with
    one VOO purchase, SPY's closes for a week and VOO's without the Wednesday."""
    import src.bootstrap as bootstrap
    from tests.conftest import _pin_mode
    _pin_mode(monkeypatch, "personal")
    book = tmp_path / "data" / "tracker.db"
    book.parent.mkdir()
    book.write_bytes(b"")
    monkeypatch.setattr(db, "DB_PATH", book)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(bootstrap, "_bootstrapped", set())
    bootstrap.bootstrap_personal_db()
    week = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    with db.get_connection() as conn:
        account = conn.execute(
            "SELECT account_id FROM accounts WHERE name = 'Personal Fidelity'").fetchone()[0]
        conn.execute("INSERT INTO trades (account_id, ticker, trade_date, action, shares, price) "
                     "VALUES (?, 'VOO', '2026-10-05', 'buy', 1, 100.0)", (account,))
        conn.executemany("INSERT INTO prices (ticker, price_date, close) VALUES (?, ?, 100.0)",
                         [("SPY", d) for d in week]
                         + [("VOO", d) for d in week if d != "2026-10-07"])
    return book


def test_the_router_asks_again_after_a_personal_page_has_run(tmp_path, monkeypatch, provider):
    """app.py itself, personal mode. A read fetched before the page finished (here, just
    before the run), so the load is a refresh: once the page has run, the close VOO
    lacks is asked for and stored."""
    from streamlit.testing.v1 import AppTest
    _fresh_personal_router(tmp_path, monkeypatch)
    prices.fetch_prices("SPY", "2026-10-09", "2026-10-09")
    assert _missing() == [("VOO", "2026-10-07")]
    provider.asked.clear()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert provider.asked == [("VOO", "2026-10-07", "2026-10-07")]
    assert price_gaps.book_gaps() == []
    assert price_gaps.last().filled == (("VOO", "2026-10-07"),)


def test_the_router_asks_nothing_after_a_load_that_fetched_nothing(tmp_path, monkeypatch, provider):
    from streamlit.testing.v1 import AppTest
    _fresh_personal_router(tmp_path, monkeypatch)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert provider.asked == [] and _missing() == [("VOO", "2026-10-07")]


def test_the_routers_call_survives_a_page_that_stops():
    """22 st.stop() calls end a page early, and a stopped page has still refreshed the
    book. The call sits in the ``finally`` of the ``try`` that runs the page, in personal
    mode only: the demo asks from its own refresh."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    runs = [n for n in tree.body if isinstance(n, ast.Try)
            and any(isinstance(c, ast.Call) and ast.unparse(c.func) == "nav.run"
                    for s in n.body for c in ast.walk(s))]
    assert len(runs) == 1, "the page runs inside one try"
    guard = runs[0].finalbody[0]
    assert isinstance(guard, ast.If) and ast.unparse(guard.test) == "not IS_DEMO"
    calls = [ast.unparse(c.func) for s in guard.body for c in ast.walk(s)
             if isinstance(c, ast.Call)]
    assert calls == ["reask_after_refresh"]
    assert "price_gaps.reask()" in (ROOT / "src" / "demo_refresh.py").read_text(encoding="utf-8")
