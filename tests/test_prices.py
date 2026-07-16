"""Unit tests for src/prices.py — dedup and caching behaviour."""
import pathlib
import sqlite3
import sys
import tempfile
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _make_db_with_prices(rows: list[tuple]) -> pathlib.Path:
    """Create a temp SQLite DB with a prices table pre-populated with *rows*.

    rows: list of (ticker, price_date, close, adj_close)
    Returns the db path.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = pathlib.Path(tmp.name)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE prices (
            ticker TEXT NOT NULL,
            price_date TEXT NOT NULL,
            close REAL NOT NULL,
            adj_close REAL,
            PRIMARY KEY (ticker, price_date)
        )"""
    )
    conn.executemany(
        "INSERT OR IGNORE INTO prices (ticker, price_date, close, adj_close) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def test_get_prices_deduplicates_index(monkeypatch):
    """get_prices must not return a DataFrame with a duplicated index.

    Scenario mirrors the Cloud $30 bug: the DB has prices up to May-5, and the
    trailing-fetch call returns a response that overlaps May-5 (Yahoo Finance
    UTC-midnight boundary maps the May-6 request back to May-5 data). After
    pd.concat the index has a duplicate May-5. Without the dedup guard,
    downstream reindex() raises "cannot reindex on an axis with duplicate labels".
    """
    import src.prices as prices_mod

    ticker = "TST"
    rows = [
        (ticker, "2025-01-02", 100.0, 100.0),
        (ticker, "2025-01-03", 101.0, 101.0),
        (ticker, "2025-01-06", 102.0, 102.0),
    ]
    db_path = _make_db_with_prices(rows)

    # Patch get_connection so prices.py uses our temp DB
    import sqlite3 as _sq3
    from contextlib import contextmanager

    @contextmanager
    def _fake_conn():
        conn = _sq3.connect(str(db_path))
        conn.row_factory = _sq3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(prices_mod, "get_connection", _fake_conn)

    # Patch fetch_prices to simulate the overlap: requesting Jan-7 returns Jan-6
    def _fake_fetch(ticker, start_date, end_date=None):
        # Simulate Yahoo Finance returning the previous day's data for a future request
        return pd.DataFrame(
            [{"price_date": date(2025, 1, 6), "close": 102.0, "adj_close": 102.0}]
        ).set_index("price_date")

    monkeypatch.setattr(prices_mod, "fetch_prices", _fake_fetch)

    result = prices_mod.get_prices(ticker, "2025-01-02", "2025-01-07")

    assert not result.index.duplicated().any(), (
        f"get_prices returned a DataFrame with duplicate index entries: "
        f"{result.index[result.index.duplicated(keep=False)].tolist()}"
    )


def test_get_prices_returns_dataframe_with_correct_columns(monkeypatch):
    """get_prices returns a DataFrame with close and adj_close columns."""
    import src.prices as prices_mod

    ticker = "TST"
    rows = [
        (ticker, "2025-01-02", 100.0, 99.5),
        (ticker, "2025-01-03", 101.0, 100.4),
    ]
    db_path = _make_db_with_prices(rows)

    import sqlite3 as _sq3
    from contextlib import contextmanager

    @contextmanager
    def _fake_conn():
        conn = _sq3.connect(str(db_path))
        conn.row_factory = _sq3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(prices_mod, "get_connection", _fake_conn)

    # No trailing gap — fetch_prices should not be called
    monkeypatch.setattr(prices_mod, "fetch_prices", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("fetch_prices should not be called when cache is complete")
    ))

    result = prices_mod.get_prices(ticker, "2025-01-02", "2025-01-03")

    assert set(result.columns) >= {"close", "adj_close"}
    assert len(result) == 2


# ── Ticker format validation ───────────────────────────────────────────────────
# The Candidate Screen (pages/5_Asset_Evaluation.py) puts a free-text box in
# front of the price fetcher, and the demo is public. Validation lives at
# fetch_prices — the choke point — so nothing malformed becomes an outbound
# request or a URL path, regardless of which caller supplied it.

@pytest.mark.parametrize("ticker", [
    "QQQ", "VOO", "SPY", "F",              # plain symbols, incl. single-char
    "BRK.B",                                # dot class share
    "BTC-USD",                              # hyphenated pair
    "EURUSD=X",                             # FX
    "^GSPC",                                # index
    "RFUTX", "SPAXX", "GHYIX", "MCO",       # symbols this repo actually holds
    "qqq",                                  # lowercase — unchanged prior behaviour
    "  QQQ  ",                              # surrounding whitespace tolerated
])
def test_valid_ticker_formats_accepted(ticker):
    """A format check must not reject real, exchange-listed symbols."""
    from src.prices import is_valid_ticker
    assert is_valid_ticker(ticker) is True


@pytest.mark.parametrize("ticker", [
    "",                                      # empty
    "   ",                                   # whitespace only
    "A" * 16,                                # over length
    "../../etc/passwd",                      # path traversal
    "QQQ/../../v7/finance/quote",            # traversal via a valid-looking stem
    "QQQ?symbol=EVIL",                       # query injection
    "QQQ#frag",                              # fragment
    "QQQ%2F..",                              # pre-encoded metacharacter
    "QQQ&x=1",
    "QQQ QQQ",                               # internal whitespace
    "<script>alert(1)</script>",
    "DROP TABLE prices;",
    "VNQ+DBC",                               # a blend LABEL, not a symbol
    "VNQ (60%) + DBC (40%)",                 # the other blend label
    ".",                                     # punctuation only
    "-",
    "^",
    None,                                    # wrong type
    123,
    ["QQQ"],
])
def test_invalid_ticker_formats_rejected(ticker):
    """Garbage, URL metacharacters, blend labels, and wrong types are rejected."""
    from src.prices import is_valid_ticker
    assert is_valid_ticker(ticker) is False


@pytest.mark.parametrize("garbage", [
    "../../etc/passwd", "QQQ?symbol=EVIL", "", "A" * 40, "VNQ+DBC", "<script>",
])
def test_fetch_prices_rejects_garbage_without_network_call(garbage, monkeypatch):
    """Garbage must raise BEFORE any outbound request — the fix for unbounded
    external calls driven by public free-text input."""
    import src.prices as prices

    def _boom(*a, **k):
        raise AssertionError("network call fired for invalid ticker")

    monkeypatch.setattr(prices._SESSION, "get", _boom)
    with pytest.raises(ValueError, match="not a valid ticker format"):
        prices.fetch_prices(garbage, "2025-01-01", "2025-02-01")


def test_fetch_prices_rejects_garbage_without_db_write(monkeypatch):
    """Garbage must not reach the prices table."""
    import src.prices as prices

    def _boom_conn(*a, **k):
        raise AssertionError("DB connection opened for invalid ticker")

    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network call fired for invalid ticker")))
    monkeypatch.setattr(prices, "get_connection", _boom_conn)
    with pytest.raises(ValueError, match="not a valid ticker format"):
        prices.fetch_prices("../../evil", "2025-01-01", "2025-02-01")


def test_get_prices_surfaces_clear_error_for_garbage(monkeypatch):
    """The message a caller sees names the problem — a format issue, not a
    connection failure — so the page can say something true about the input."""
    import src.prices as prices

    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network call fired for invalid ticker")))
    with pytest.raises(ValueError) as exc:
        prices.get_prices("not a ticker!", "2025-01-01", "2025-02-01")
    assert "not a valid ticker format" in str(exc.value)


def test_valid_ticker_still_reaches_the_fetcher(monkeypatch):
    """The guard must not block real symbols — a well-formed ticker still calls out."""
    import src.prices as prices
    called = {}

    class _Resp:
        status_code = 200
        def json(self):
            called["hit"] = True
            return {"chart": {"result": None, "error": "stubbed"}}

    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: _Resp())
    with pytest.raises(ValueError, match="No price data returned"):
        prices.fetch_prices("QQQ", "2025-01-01", "2025-02-01")
    assert called.get("hit") is True, "valid ticker never reached the request"
