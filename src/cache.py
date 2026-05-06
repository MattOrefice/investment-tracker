"""Quarter-snapshot price cache for immutable PDF report generation.

Stores a frozen copy of all portfolio and benchmark adj_close prices at
quarter-end so that report regeneration always produces identical numbers
regardless of Yahoo Finance retroactive adj_close adjustments.
"""
import io
import json
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

import pandas as pd

import src.prices as _prices_module
from src.db import get_connection

_QUARTER_ENDS = {
    "Q1": (3, 31),
    "Q2": (6, 30),
    "Q3": (9, 30),
    "Q4": (12, 31),
}

_DDL = """
CREATE TABLE IF NOT EXISTS quarter_snapshots (
    quarter_id    TEXT PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    snapshot_data BLOB NOT NULL
);
"""


def _ensure_table() -> None:
    with get_connection() as conn:
        conn.execute(_DDL)


def _parse_quarter_end(quarter_id: str) -> Optional[date]:
    """'2026Q1' → date(2026, 3, 31); returns None for unrecognised format."""
    try:
        year = int(quarter_id[:4])
        q = quarter_id[4:]
        m, d = _QUARTER_ENDS[q]
        return date(year, m, d)
    except (ValueError, KeyError, IndexError):
        return None


def label_to_quarter_id(period_label: str) -> Optional[str]:
    """'Q1 2026' → '2026Q1'; non-standard labels return None."""
    parts = period_label.strip().split()
    if len(parts) == 2 and parts[0] in _QUARTER_ENDS:
        return f"{parts[1]}{parts[0]}"
    return None


def is_quarter_complete(quarter_id: str) -> bool:
    """Return True if today is strictly past the quarter-end date."""
    end = _parse_quarter_end(quarter_id)
    return end is not None and date.today() > end


def _get_all_snapshot_tickers() -> list:
    """Query DB for all portfolio and benchmark tickers (excluding SPAXX)."""
    with get_connection() as conn:
        holdings = conn.execute("SELECT ticker FROM securities").fetchall()
        benchmarks = conn.execute(
            "SELECT DISTINCT benchmark_ticker FROM asset_classes WHERE benchmark_ticker IS NOT NULL"
        ).fetchall()
    tickers = {r["ticker"] for r in holdings} | {r["benchmark_ticker"] for r in benchmarks}
    tickers.discard("SPAXX")
    return sorted(tickers)


def get_quarter_snapshot(quarter_id: str) -> tuple:
    """
    Return (snap_df, captured_at_str) if a snapshot exists, else (None, None).
    snap_df is wide: index=datetime.date objects, columns=tickers.
    """
    _ensure_table()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT snapshot_data, captured_at FROM quarter_snapshots WHERE quarter_id = ?",
            (quarter_id,),
        ).fetchone()
    if row is None:
        return None, None

    snap_dict = json.loads(row["snapshot_data"])
    df = pd.read_json(io.StringIO(json.dumps(snap_dict)), orient="split")
    df.index = pd.to_datetime(df.index).date
    return df, row["captured_at"]


def capture_quarter_snapshot(quarter_id: str) -> tuple:
    """
    Pull adj_close for all tickers from inception through snapshot_date,
    persist in quarter_snapshots, and return (snap_df, captured_at_str).
    Raises ValueError if the quarter has not yet ended.
    """
    end = _parse_quarter_end(quarter_id)
    if end is None:
        raise ValueError(f"Unrecognised quarter_id: {quarter_id!r}")
    if not is_quarter_complete(quarter_id):
        raise ValueError(f"Quarter {quarter_id} has not ended yet (end: {end}).")

    end_str = end.isoformat()
    inception_str = "2020-01-01"

    tickers = _get_all_snapshot_tickers()
    frames: dict = {}
    for ticker in tickers:
        try:
            df = _prices_module.get_prices(ticker, inception_str, end_str)
            frames[ticker] = df["adj_close"]
        except Exception:
            pass

    if not frames:
        raise RuntimeError(f"No price data fetched for snapshot {quarter_id}.")

    snap_df = pd.DataFrame(frames)
    snap_df.index = pd.to_datetime(snap_df.index).date

    captured_at = datetime.now().isoformat(timespec="seconds")
    blob = snap_df.to_json(orient="split", date_format="iso")

    _ensure_table()
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO quarter_snapshots
               (quarter_id, snapshot_date, captured_at, snapshot_data)
               VALUES (?, ?, ?, ?)""",
            (quarter_id, end_str, captured_at, blob),
        )

    return snap_df, captured_at


@contextmanager
def snapshot_price_context(snap_df: pd.DataFrame):
    """
    Monkey-patches src.prices.get_prices to serve prices from snap_df.
    Tickers absent from snap_df fall back to the original get_prices.
    Restores original on exit even if an exception is raised.

    snap_df: wide DataFrame, index=datetime.date objects, columns=tickers (adj_close values).
    """
    original_get_prices = _prices_module.get_prices

    def _snapshot_reader(ticker: str, start_date: str, end_date: Optional[str] = None):
        if ticker not in snap_df.columns:
            return original_get_prices(ticker, start_date, end_date)

        end = end_date or date.today().isoformat()
        col = snap_df[ticker].dropna()
        # Compare via ISO strings — robust against date vs datetime.date index dtype subtleties
        idx_iso = pd.Index([d.isoformat() for d in col.index])
        mask = (idx_iso >= start_date) & (idx_iso <= end)
        filtered = col[mask]

        if filtered.empty:
            return original_get_prices(ticker, start_date, end)

        return pd.DataFrame(
            {"close": filtered.values, "adj_close": filtered.values},
            index=filtered.index,
        )

    _prices_module.get_prices = _snapshot_reader
    try:
        yield
    finally:
        _prices_module.get_prices = original_get_prices
