"""Quarter-snapshot price cache for immutable PDF report generation.

Stores a frozen copy of all portfolio and benchmark adj_close prices at
quarter-end so that report regeneration always produces identical numbers
regardless of retroactive adj_close adjustments from the upstream data provider.
"""
import io
import json
from contextlib import contextmanager
from datetime import date, datetime
from typing import NamedTuple, Optional

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


class SnapshotFrames(NamedTuple):
    """A quarter snapshot: one flat frame per price basis.

    TWO FLAT FRAMES, deliberately, rather than one frame with a (ticker, basis)
    column MultiIndex. A MultiIndex would have to survive
    ``to_json(orient="split")`` round-tripping, and defending that rests a durable
    artifact on a pandas behaviour upstream does not guarantee across the open
    ``pandas>=2.2.0`` range — the same argument that keeps PriceCoverage off
    ``DataFrame.attrs``. Two frames under top-level JSON keys have no round-trip
    question, are readable by inspection, and make legacy detection a key lookup
    rather than an ``nlevels`` check on a deserialized frame.

    ``close is None`` marks a LEGACY snapshot — one captured before the writer
    stored both bases. The reader then serves no ``close`` column at all, so a
    primary-basis consumer raises ``KeyError`` naturally rather than through any
    mechanism built for the purpose. That is the honest outcome: the data needed to
    answer the question is not there.
    """

    adj_close: pd.DataFrame
    close: "pd.DataFrame | None" = None


def _as_frames(snap) -> SnapshotFrames:
    """Accept a SnapshotFrames, or a bare adj_close-only frame (the legacy shape)."""
    if isinstance(snap, SnapshotFrames):
        return snap
    return SnapshotFrames(adj_close=snap, close=None)


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

    blob = json.loads(row["snapshot_data"])

    def _frame(payload):
        df = pd.read_json(io.StringIO(json.dumps(payload)), orient="split")
        df.index = pd.to_datetime(df.index).date
        return df

    # LEGACY DETECTION IS A KEY LOOKUP. A snapshot captured before both bases were
    # stored is a bare split-orient frame with no "adj_close"/"close" keys at all,
    # and its single series is adj_close.
    if "adj_close" not in blob:
        return SnapshotFrames(adj_close=_frame(blob), close=None), row["captured_at"]

    close = _frame(blob["close"]) if "close" in blob else None
    return (SnapshotFrames(adj_close=_frame(blob["adj_close"]), close=close),
            row["captured_at"])


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
    adj: dict = {}
    raw: dict = {}
    for ticker in tickers:
        try:
            df = _prices_module.get_prices(ticker, inception_str, end_str)
            adj[ticker] = df["adj_close"]
            # BOTH bases now. Storing adj_close alone left a raw-close consumer
            # unservable, and the reader papered over that by aliasing (#193).
            if "close" in df.columns:
                raw[ticker] = df["close"]
        except Exception:
            pass

    if not adj:
        raise RuntimeError(f"No price data fetched for snapshot {quarter_id}.")

    adj_df = pd.DataFrame(adj)
    adj_df.index = pd.to_datetime(adj_df.index).date
    raw_df = pd.DataFrame(raw)
    if not raw_df.empty:
        raw_df.index = pd.to_datetime(raw_df.index).date
    snap_df = SnapshotFrames(adj_close=adj_df,
                             close=raw_df if not raw_df.empty else None)

    captured_at = datetime.now().isoformat(timespec="seconds")
    # Two top-level keys, one flat frame each — see SnapshotFrames for why this is
    # not a column MultiIndex.
    payload = {"adj_close": json.loads(adj_df.to_json(orient="split", date_format="iso"))}
    if snap_df.close is not None:
        payload["close"] = json.loads(raw_df.to_json(orient="split", date_format="iso"))
    blob = json.dumps(payload)  # write-guard-exempt: portfolio snapshot cache, not user-mutable data

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
    frames = _as_frames(snap_df)

    def _snapshot_reader(ticker: str, start_date: str, end_date: Optional[str] = None):
        if ticker not in frames.adj_close.columns:
            return original_get_prices(ticker, start_date, end_date)

        end = end_date or date.today().isoformat()
        col = frames.adj_close[ticker].dropna()
        # Compare via ISO strings — robust against date vs datetime.date index dtype subtleties
        idx_iso = pd.Index([d.isoformat() for d in col.index])
        mask = (idx_iso >= start_date) & (idx_iso <= end)
        filtered = col[mask]

        if filtered.empty:
            return original_get_prices(ticker, start_date, end)

        # Serve ONLY the bases this snapshot actually holds. The old code returned
        # the one stored series under BOTH names, which turned "cannot serve this"
        # into "serves something wrong": a caller asking what the account is worth
        # received a total-return series under a "Prices locked" cover (#193).
        data = {"adj_close": filtered.values}
        if frames.close is not None and ticker in frames.close.columns:
            data["close"] = frames.close[ticker].reindex(filtered.index).values
        return pd.DataFrame(data, index=filtered.index)

    _prices_module.get_prices = _snapshot_reader
    try:
        yield
    finally:
        _prices_module.get_prices = original_get_prices
