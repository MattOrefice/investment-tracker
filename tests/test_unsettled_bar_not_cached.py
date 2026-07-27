"""The in-flight bar is RETURNED but never CACHED.

Defect this prevents: fetch_prices persisted whatever Yahoo returned, including the
bar of a session still open — an intraday quote stored as if it were a close. Nothing
ever repairs it, because get_prices only gap-fills BEYOND cached_end, so a date at or
below the frontier is never re-read. Committed demo.db carries ~96 such rows across 23
tickers from ~10 mid-session runs (measured 2026-07-27 against settled closes: VOO off
0.51% at 2026-07-20, QQQ 0.79%, AVUV 0.64%).

Both states are CONSTRUCTED from synthetic API payloads rather than asserted against
whatever the market is doing when the suite runs: a test that only passes outside
trading hours is not a test of this. Every case builds its own currentTradingPeriod
around a fixed clock, so the result is identical at 3am and at 11am on a Tuesday.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

import src.prices as prices


# ── Synthetic Yahoo payloads ──────────────────────────────────────────────────

_DAY = 86_400
# A fixed reference session: 2026-07-27 13:30Z open, 20:00Z close (09:30-16:00 EDT).
_SESSION_OPEN = int(datetime(2026, 7, 27, 13, 30, tzinfo=timezone.utc).timestamp())
_SESSION_CLOSE = int(datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc).timestamp())
_PRIOR_BAR = _SESSION_OPEN - 3 * _DAY          # 2026-07-24, a settled Friday


def _payload(bars, *, period="present", regular="present", end=_SESSION_CLOSE,
             start=_SESSION_OPEN, market_time=_SESSION_CLOSE - 600):
    """A chart-API `result` dict. `bars` is [(timestamp, close, adjclose), ...]."""
    meta: dict = {"exchangeTimezoneName": "America/New_York"}
    if market_time is not None:
        meta["regularMarketTime"] = market_time
    if period == "present":
        reg = None
        if regular == "present":
            reg = {}
            if start is not None:
                reg["start"] = start
            if end is not None:
                reg["end"] = end
        meta["currentTradingPeriod"] = {"regular": reg} if reg is not None else {}
    elif period == "malformed":
        meta["currentTradingPeriod"] = "not-a-dict"
    return {
        "meta": meta,
        "timestamp": [b[0] for b in bars],
        "indicators": {
            "quote": [{"close": [b[1] for b in bars]}],
            "adjclose": [{"adjclose": [b[2] for b in bars]}],
        },
    }


_TWO_BARS = [(_PRIOR_BAR, 100.0, 100.0), (_SESSION_OPEN, 111.11, 111.11)]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An isolated prices DB wired into src.prices, plus a cleared memo."""
    path = tmp_path / "p.db"
    sqlite3.connect(path).executescript(
        "CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, adj_close REAL,"
        " PRIMARY KEY (ticker, price_date));"
        "CREATE TABLE dividends (ticker TEXT, ex_date TEXT, amount REAL,"
        " PRIMARY KEY (ticker, ex_date));"
    )

    @contextmanager
    def _conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    monkeypatch.setattr(prices, "get_connection", _conn)
    prices._reset_trailing_memo()
    return path


def _install_response(monkeypatch, result, *, now):
    """Serve `result` from the session, with the clock pinned to `now`."""
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"chart": {"result": [result]}}

    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: _Resp())

    real_datetime = prices.datetime

    class _Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.fromtimestamp(now, tz=tz or timezone.utc)

    monkeypatch.setattr(prices, "datetime", _Clock)


def _cached_dates(path, ticker="TEST"):
    c = sqlite3.connect(path)
    rows = [r[0] for r in c.execute(
        "SELECT price_date FROM prices WHERE ticker=? ORDER BY price_date", (ticker,))]
    c.close()
    return rows


# ── The two states ────────────────────────────────────────────────────────────

def test_in_flight_bar_is_returned_but_not_written(db, monkeypatch):
    """Session OPEN: the caller gets today's bar, the cache does not."""
    _install_response(monkeypatch, _payload(_TWO_BARS), now=_SESSION_OPEN + 3600)

    df = prices.fetch_prices("TEST", "2026-07-24", "2026-07-27")

    returned = [str(d) for d in df.index]
    assert returned == ["2026-07-24", "2026-07-27"], "the live bar must reach the caller"
    assert float(df.loc[df.index[-1], "close"]) == 111.11
    assert _cached_dates(db) == ["2026-07-24"], (
        "the bar of an OPEN session must not be persisted — nothing ever repairs a "
        "row at or below cached_end"
    )


def test_settled_bar_is_written(db, monkeypatch):
    """Session CLOSED: the same bar is a real close and persists normally."""
    _install_response(monkeypatch, _payload(_TWO_BARS), now=_SESSION_CLOSE + 60)

    df = prices.fetch_prices("TEST", "2026-07-24", "2026-07-27")

    assert [str(d) for d in df.index] == ["2026-07-24", "2026-07-27"]
    assert _cached_dates(db) == ["2026-07-24", "2026-07-27"], (
        "a post-close run must persist — otherwise the cache never advances and "
        "the bar is stale until tomorrow"
    )


def test_exactly_at_the_close_counts_as_settled(db, monkeypatch):
    """now == regular.end is settled. Pins the boundary rather than leaving it to
    whichever comparison operator someone edits in later."""
    _install_response(monkeypatch, _payload(_TWO_BARS), now=_SESSION_CLOSE)
    prices.fetch_prices("TEST", "2026-07-24", "2026-07-27")
    assert _cached_dates(db) == ["2026-07-24", "2026-07-27"]


# ── Absent / malformed metadata: must degrade toward NOT persisting ───────────

@pytest.mark.parametrize("kwargs,label", [
    ({"period": "absent"},                 "currentTradingPeriod missing"),
    ({"period": "malformed"},              "currentTradingPeriod not a dict"),
    ({"regular": "absent"},                "regular session block missing"),
    ({"end": None},                        "regular.end missing"),
    ({"end": "16:00"},                     "regular.end non-numeric"),
    ({"start": None},                      "regular.start missing"),
])
def test_unprovable_settlement_does_not_persist_the_newest_bar(db, monkeypatch, kwargs, label):
    """If the response does not let us PROVE the session closed, skip the newest bar.

    The two failure directions are not symmetric. Defaulting to "settled" silently
    re-persists partial bars and makes the fix inert — undetectable once written.
    Defaulting to "unsettled" costs one skipped row that the next run writes
    correctly. Missing data is a reason to write less, not to write confidently.
    """
    _install_response(monkeypatch, _payload(_TWO_BARS, **kwargs), now=_SESSION_CLOSE + 60)

    df = prices.fetch_prices("TEST", "2026-07-24", "2026-07-27")

    assert len(df) == 2, f"{label}: the caller must still receive both bars"
    assert _cached_dates(db) == ["2026-07-24"], (
        f"{label}: settlement is unprovable, so the newest bar must be skipped — "
        f"defaulting to 'settled' here is what makes this whole fix inert"
    )


def test_no_timestamps_at_all_still_persists_nothing_wrong(db, monkeypatch):
    """Neither regularMarketTime nor a usable period: fall back to the last bar in
    the payload, still the conservative answer."""
    _install_response(
        monkeypatch,
        _payload(_TWO_BARS, period="absent", market_time=None),
        now=_SESSION_CLOSE + 60,
    )
    prices.fetch_prices("TEST", "2026-07-24", "2026-07-27")
    assert _cached_dates(db) == ["2026-07-24"]


# ── unsettled_bar_date in isolation ───────────────────────────────────────────

def test_unsettled_bar_date_returns_none_only_when_settlement_is_proven(monkeypatch):
    """None means 'persist everything', so it must be reachable ONLY via a proven
    close — this is the single value that permits a write."""
    import datetime as _dt

    real = prices.datetime

    def _at(now):
        class _Clock(real):
            @classmethod
            def now(cls, tz=None):
                return real.fromtimestamp(now, tz=tz or timezone.utc)
        monkeypatch.setattr(prices, "datetime", _Clock)

    _at(_SESSION_CLOSE + 1)
    assert prices.unsettled_bar_date(_payload(_TWO_BARS)) is None

    _at(_SESSION_OPEN + 60)
    assert prices.unsettled_bar_date(_payload(_TWO_BARS)) == _dt.date(2026, 7, 27)

    # Unprovable => a date (skip), never None.
    _at(_SESSION_CLOSE + 1)
    for kw in ({"period": "absent"}, {"regular": "absent"}, {"end": None}):
        assert prices.unsettled_bar_date(_payload(_TWO_BARS, **kw)) is not None


# ── The memo ──────────────────────────────────────────────────────────────────

def test_trailing_gap_is_fetched_once_per_process(db, monkeypatch):
    """With the in-flight bar uncached, cached_end < end stays true for the rest of
    the session, so get_prices re-issued the same request on every call (measured: 14
    per last_real_price_date, every call). The memo absorbs what the DB cache
    deliberately no longer does."""
    c = sqlite3.connect(db)
    c.execute("INSERT INTO prices VALUES ('TEST','2026-07-24',100.0,100.0)")
    c.commit(); c.close()

    calls = []
    real_fetch = prices.fetch_prices

    def _counting(ticker, start, end=None):
        calls.append((ticker, start, end))
        return real_fetch(ticker, start, end)

    monkeypatch.setattr(prices, "fetch_prices", _counting)
    _install_response(monkeypatch, _payload(_TWO_BARS), now=_SESSION_OPEN + 3600)

    for _ in range(3):
        out = prices.get_prices("TEST", "2026-07-24", "2026-07-27")
        assert "2026-07-27" in [str(d) for d in out.index], (
            "the live bar must be served on EVERY call, memo hit or not"
        )
    assert len(calls) == 1, f"trailing gap should be fetched once, got {len(calls)}"


def test_memo_does_not_cache_failures(db, monkeypatch):
    """A transient 429 must not pin an empty result for the life of the process."""
    c = sqlite3.connect(db)
    c.execute("INSERT INTO prices VALUES ('TEST','2026-07-24',100.0,100.0)")
    c.commit(); c.close()

    calls = []

    def _flaky(ticker, start, end=None):
        calls.append(ticker)
        if len(calls) == 1:
            raise ValueError("Yahoo Finance rate-limited (HTTP 429)")
        import pandas as pd
        import datetime as _dt
        return pd.DataFrame({"close": [111.11], "adj_close": [111.11]},
                            index=[_dt.date(2026, 7, 27)])

    monkeypatch.setattr(prices, "fetch_prices", _flaky)

    first = prices.get_prices("TEST", "2026-07-24", "2026-07-27")
    assert [str(d) for d in first.index] == ["2026-07-24"]  # swallowed, cache-only

    second = prices.get_prices("TEST", "2026-07-24", "2026-07-27")
    assert "2026-07-27" in [str(d) for d in second.index], (
        "the retry must be allowed — a memoized failure would outlive the outage"
    )
    assert len(calls) == 2
