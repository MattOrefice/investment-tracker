"""#304 — the price cache stores UNADJUSTED closes; the total-return series is derived on
read from close and the dividends table.

The provider re-anchors adj_close at every new dividend, so rows cached on different days
carried different anchors and a return spanning two fetch dates crossed a seam (measured
at 2026-06-09 on both books: 0.29% on VOO, 0.88% on VNQ). A raw close does not move once
it settles, so storing it and adjusting on read removes the seam by construction.
"""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import src.prices as prices

DEMO_DB = Path(__file__).resolve().parent.parent / "data" / "demo.db"

D = [date(2026, 6, d) for d in (1, 2, 3, 4, 5)]
CLOSES = [100.0, 101.0, 102.0, 103.0, 104.0]


@pytest.fixture
def book(tmp_path, monkeypatch):
    """Five settled closes and one dividend (ex 06-04, 1.02 on a prior close of 102)."""
    path = tmp_path / "p.db"
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, adj_close REAL,"
        " PRIMARY KEY (ticker, price_date));"
        "CREATE TABLE dividends (ticker TEXT, ex_date TEXT, amount REAL,"
        " PRIMARY KEY (ticker, ex_date));")
    c.executemany("INSERT INTO prices VALUES ('T', ?, ?, NULL)",
                  [(d.isoformat(), x) for d, x in zip(D, CLOSES)])
    c.execute("INSERT INTO dividends VALUES ('T', '2026-06-04', 1.02)")
    c.commit()
    c.close()

    @contextmanager
    def _conn():
        k = sqlite3.connect(path)
        k.row_factory = sqlite3.Row
        try:
            yield k
            k.commit()
        finally:
            k.close()

    monkeypatch.setattr(prices, "get_connection", _conn)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    prices._reset_trailing_memo()
    return path


def test_closes_before_the_ex_date_are_scaled_and_later_ones_are_not(book):
    adj = prices.get_prices("T", "2026-06-01", "2026-06-05")["adj_close"]
    f = 1 - 1.02 / 102.0
    # Two-sided at the ex-date: the day before is scaled, the ex-date itself is not.
    assert adj[D[2]] == pytest.approx(102.0 * f, rel=1e-12)
    assert adj[D[3]] == pytest.approx(103.0, rel=1e-12)
    assert adj[D[0]] == pytest.approx(100.0 * f, rel=1e-12)
    assert adj[D[4]] == pytest.approx(104.0, rel=1e-12)


def test_a_window_ending_before_the_ex_date_carries_the_same_anchor(book):
    """Anchored on every stored dividend, not only those in the window: a read that
    ends before the ex-date returns the same level as a read spanning it. The prior
    close then comes from the cache, not the frame."""
    short = prices.get_prices("T", "2026-06-01", "2026-06-02")["adj_close"]
    full = prices.get_prices("T", "2026-06-01", "2026-06-05")["adj_close"]
    assert short[D[1]] == pytest.approx(full[D[1]], rel=1e-12)
    assert short[D[1]] != pytest.approx(101.0, rel=1e-9)


def test_the_total_return_includes_the_dividend(book):
    adj = prices.get_prices("T", "2026-06-01", "2026-06-05")["adj_close"]
    price_only = 104.0 / 100.0 - 1
    assert adj.iloc[-1] / adj.iloc[0] - 1 == pytest.approx(
        (104.0 / (100.0 * (1 - 1.02 / 102.0))) - 1, rel=1e-12)
    assert adj.iloc[-1] / adj.iloc[0] - 1 > price_only


def test_a_book_with_no_dividends_table_adjusts_nothing_and_creates_nothing(tmp_path, monkeypatch):
    path = tmp_path / "n.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, adj_close REAL,"
              " PRIMARY KEY (ticker, price_date))")
    c.executemany("INSERT INTO prices VALUES ('T', ?, ?, NULL)",
                  [(d.isoformat(), x) for d, x in zip(D, CLOSES)])
    c.commit()
    c.close()

    @contextmanager
    def _conn():
        k = sqlite3.connect(path)
        k.row_factory = sqlite3.Row
        try:
            yield k
        finally:
            k.close()

    monkeypatch.setattr(prices, "get_connection", _conn)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    prices._reset_trailing_memo()
    p = prices.get_prices("T", "2026-06-01", "2026-06-05")
    assert list(p["adj_close"]) == CLOSES
    k = sqlite3.connect(path)
    assert k.execute("SELECT name FROM sqlite_master WHERE name = 'dividends'").fetchone() is None


def test_a_fetch_stores_the_close_and_never_the_providers_adjustment(book, monkeypatch):
    ts = int(datetime(2026, 6, 8, 20, 0, tzinfo=timezone.utc).timestamp())

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"chart": {"result": [{
                "meta": {}, "timestamp": [ts],
                "indicators": {"quote": [{"close": [105.0]}],
                               "adjclose": [{"adjclose": [99.99]}]}}]}}

    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(prices, "unsettled_bar_date", lambda result: None)
    got = prices.get_prices("T", "2026-06-01", "2026-06-08")
    k = sqlite3.connect(book)
    row = k.execute("SELECT close, adj_close FROM prices WHERE price_date = '2026-06-08'").fetchone()
    assert row == (105.0, None)
    # What the caller sees is derived, not the provider's 99.99.
    assert got["adj_close"].iloc[-1] == pytest.approx(105.0)


# ── The committed demo book, per table (demo.db deploys publicly) ───────────────

def _demo():
    return sqlite3.connect(f"file:{DEMO_DB.as_posix()}?mode=ro", uri=True)


def test_demo_book_stores_no_provider_adjustment():
    c = _demo()
    n, adj = c.execute("SELECT COUNT(*), COUNT(adj_close) FROM prices").fetchone()
    assert n > 0 and adj == 0, f"{adj} of {n} demo price rows still carry adj_close"


def test_demo_book_holds_each_dividend_once():
    """A dividend stored on two dates (05-01 and 05-04, measured before #304 on BIL,
    SCHP and VGIT) would be applied twice on read and overstate total return.

    One pair is the PROVIDER's own record, not a storage duplicate: TIP 0.632 on both
    2008-04-01 and 2008-04-03 (fetched 2026-09-23), and its adjclose applies both, so
    reproducing it is faithful. Named, so a new twin cannot hide behind it."""
    PROVIDER_RECORDED = {("TIP", "2008-04-01", "2008-04-03")}
    c = _demo()
    rows = c.execute("SELECT ticker, ex_date, amount FROM dividends ORDER BY ticker, ex_date").fetchall()
    twins = [(a, b) for a, b in zip(rows, rows[1:])
             if a[0] == b[0] and abs(a[2] - b[2]) < 1e-9
             and (date.fromisoformat(b[1]) - date.fromisoformat(a[1])).days <= 5
             and (a[0], a[1], b[1]) not in PROVIDER_RECORDED]
    assert not twins, twins


# ── the race, and why a return comes from ONE read (#304) ──────────────────────

def _serve_a_bar_and_a_dividend(monkeypatch):
    """The provider serves a new bar on 06-08 and a dividend going ex that day; the
    fetch stores both, exactly as get_prices' trailing fetch does."""
    ts = int(datetime(2026, 6, 8, 20, 0, tzinfo=timezone.utc).timestamp())

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"chart": {"result": [{
                "meta": {}, "timestamp": [ts],
                "indicators": {"quote": [{"close": [104.0]}],
                               "adjclose": [{"adjclose": [104.0]}]},
                "events": {"dividends": {str(ts): {"amount": 2.08, "date": ts}}}}]}}

    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(prices, "unsettled_bar_date", lambda result: None)


_TRUE_RATIO = 104.0 / (100.0 * (1 - 1.02 / 102.0) * (1 - 2.08 / 104.0))


def test_two_reads_straddling_a_new_dividend_disagree(book, monkeypatch):
    """The fault, reproduced offline: read the start (the cache has no 06-08 dividend
    yet), then read the end (its fetch stores it). The two levels sit on different
    dividend sets and the ratio misses the dividend. This is the contrast that makes
    the one-read test below mean something."""
    _serve_a_bar_and_a_dividend(monkeypatch)
    start_first = prices.get_prices("T", "2026-06-01", "2026-06-01")["adj_close"].iloc[0]
    end_later = prices.get_prices("T", "2026-06-06", "2026-06-08")["adj_close"].iloc[-1]
    assert end_later / start_first != pytest.approx(_TRUE_RATIO, rel=1e-6)
    assert end_later / start_first == pytest.approx(104.0 / (100.0 * (1 - 1.02 / 102.0)), rel=1e-12)


def test_one_read_gets_the_dividend_that_arrives_during_it(book, monkeypatch):
    """A FRESH book: the dividend is stored by this very read's fetch, and the ratio
    still includes it, because get_prices adjusts after its own fetch."""
    from src.attribution import _adj_prices_one_read, _last_on_or_before
    _serve_a_bar_and_a_dividend(monkeypatch)
    k = sqlite3.connect(book)
    assert k.execute("SELECT COUNT(*) FROM dividends WHERE ex_date = '2026-06-08'").fetchone() == (0,)
    k.close()
    one = _adj_prices_one_read("T", "2026-06-01", "2026-06-08", window_days=0)
    ratio = _last_on_or_before(one, "2026-06-08", 0) / _last_on_or_before(one, "2026-06-01", 0)
    assert ratio == pytest.approx(_TRUE_RATIO, rel=1e-12)


def test_a_window_past_the_cache_is_fetched_contiguously(book, monkeypatch):
    """A read whose window touches no cached row widens its fetch to meet the cache,
    so no hole is left for a dividend or a prior close to fall into. The caller still
    gets only the window it asked for."""
    asked = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"chart": {"result": [{"meta": {}, "timestamp": [],
                                          "indicators": {"quote": [{"close": []}]}}]}}

    def _get(url, params=None, timeout=None):
        asked.append((params["period1"], params["period2"]))
        return _Resp()

    monkeypatch.setattr(prices._SESSION, "get", _get)
    with pytest.raises(ValueError):          # the stub serves no bars
        prices.get_prices("T", "2026-06-20", "2026-06-25")
    p1 = datetime.fromtimestamp(asked[0][0], tz=timezone.utc).date()
    assert p1 == date(2026, 6, 6), f"fetch started {p1}, leaving a hole after 06-05"
