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
