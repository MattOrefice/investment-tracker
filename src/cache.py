"""Quarter-snapshot price cache for immutable PDF report generation.

Stores a frozen copy of all portfolio and benchmark adj_close prices at
quarter-end so that report regeneration always produces identical numbers
regardless of retroactive adj_close adjustments from the upstream data provider.
"""
import io
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import NamedTuple, Optional

import pandas as pd

import src.prices as _prices_module
from src.coverage import TickerStatus, coverage_from_statuses
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
    # (ticker, reason) for every series the capture could NOT lock (#204). A ticker
    # absent from the frames is read LIVE by snapshot_price_context, so a report
    # under a "Prices locked" cover is partly unlocked; this is what says so.
    gaps: tuple = ()
    # The rule the lock was taken under: QUARTER_END_RULE, or None for a lock
    # persisted before rules were recorded. Persisted locks stay as written, so the
    # report says a quarter was restated only when its lock carries the rule.
    rule: "str | None" = None


# Every lock captured from #368 on: prices AND dividends through the quarter's last
# day. Stored in the lock's payload so a reader can tell which rule produced it.
QUARTER_END_RULE = "quarter_end"

# The day the quarter-end rule took effect. A RECORD, not a data frontier: it
# governs no lock and no read. It dates the restatement of quarters that closed
# before it, which were reported under the old reads (live prices, later dividends
# included). A quarter closing on or after it was never reported that way.
QUARTER_END_RULE_SINCE = date(2026, 9, 24)


def restatement_note(quarter_id: "str | None", snap: "SnapshotFrames | None") -> "str | None":
    """One line for a report whose quarter this rule restated, or None.

    Only when the lock in use carries the quarter-end rule AND the quarter closed
    before the rule took effect. Measured on the demo book, each of the five quarters
    that closed before it moved in 31 to 60 formatted figures; the note states what
    moved and why without a magnitude, which differs by quarter and by book."""
    end = _parse_quarter_end(quarter_id) if quarter_id else None
    if end is None or snap is None or getattr(snap, "rule", None) != QUARTER_END_RULE:
        return None
    if end >= QUARTER_END_RULE_SINCE:
        return None
    return (
        f"Restated {QUARTER_END_RULE_SINCE.strftime('%B')} {QUARTER_END_RULE_SINCE.day}, "
        f"{QUARTER_END_RULE_SINCE.year}: this quarter now locks at its close on "
        f"{end.strftime('%B')} {end.day}, {end.year}, using only prices and dividends "
        f"through that day. Earlier versions of this report read prices live and "
        f"counted dividends paid after the quarter closed, so some returns, weights and "
        f"attribution effects differ slightly from those versions."
    )


def _as_frames(snap) -> SnapshotFrames:
    """Accept a SnapshotFrames, or a bare adj_close-only frame (the legacy shape)."""
    if isinstance(snap, SnapshotFrames):
        return snap
    return SnapshotFrames(adj_close=snap, close=None)


def _get_all_snapshot_tickers() -> "tuple[list, list]":
    """(tickers, gaps): every holding and every benchmark CONSTITUENT (SPAXX excluded),
    plus (spec, reason) for any benchmark spec that cannot be parsed.

    Benchmark specs are expanded with parse_benchmark_spec (#204). A composite like
    "VNQ (60%) + DBC (40%)" is not a ticker: fetched raw it can never succeed, and
    its constituents were never locked."""
    from src.sleeve_config import parse_benchmark_spec
    with get_connection() as conn:
        holdings = conn.execute("SELECT ticker FROM securities").fetchall()
        benchmarks = conn.execute(
            "SELECT DISTINCT benchmark_ticker FROM asset_classes WHERE benchmark_ticker IS NOT NULL"
        ).fetchall()
    tickers = {r["ticker"] for r in holdings}
    gaps = []
    for r in benchmarks:
        spec = r["benchmark_ticker"]
        try:
            tickers |= {t for t, _w in parse_benchmark_spec(spec)}
        except ValueError as exc:
            gaps.append((spec, f"unparseable benchmark spec: {exc}"))
    tickers.discard("SPAXX")
    return sorted(tickers), gaps


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
    gaps = tuple(tuple(g) for g in blob.get("gaps", []))
    return (SnapshotFrames(adj_close=_frame(blob["adj_close"]), close=close, gaps=gaps,
                           rule=blob.get("rule")),
            row["captured_at"])


class LockCoverageError(ValueError):
    """A quarter was asked to lock before its data reaches the quarter's end.

    Raised by capture_quarter_snapshot INSTEAD of persisting a lock. Its message
    names every short ticker and its missing dates, so the page can show it as it
    stands rather than as a generic generation failure."""


def _short_coverage(statuses: "list[TickerStatus]", end: date) -> "list[str]":
    """The lines naming each ticker whose served prices stop short of ``end``.

    Read through the coverage record: frontier_served is the MIN over what was
    served, so stale_days beyond the tolerance means at least one ticker is short.
    Empty when the lock may proceed."""
    from src.asof import QUARTER_END_COVERAGE_DAYS
    cov = coverage_from_statuses(statuses, end.isoformat())
    if cov.stale_days is None or cov.stale_days <= QUARTER_END_COVERAGE_DAYS:
        return []
    floor = end - timedelta(days=QUARTER_END_COVERAGE_DAYS)
    lines = []
    for s in statuses:
        if s.resolved and s.served_through and date.fromisoformat(s.served_through) < floor:
            first_missing = date.fromisoformat(s.served_through) + timedelta(days=1)
            lines.append(f"{s.ticker}: prices end {s.served_through}, missing "
                         f"{first_missing.isoformat()} to {end.isoformat()}")
    return lines


def capture_quarter_snapshot(quarter_id: str) -> tuple:
    """Lock a completed quarter and return (SnapshotFrames, captured_at_str).

    Every holding's and benchmark constituent's close and total-return level, from
    inception through the quarter's last day, persisted in quarter_snapshots.

    THE LOCK RULE (#368, option B): a quarter locks at its own end, using only
    prices AND DIVIDENDS through its last day. dividend_adjusted anchors on every
    stored dividend unless told otherwise, so a lock taken without the cutoff moved
    whenever a later ex-date arrived, and depended on when it was captured. With it,
    a lock is the same whenever it is computed and whatever is fetched afterwards.

    COVERAGE, OR NO LOCK. A ticker whose prices stop short of the quarter's end,
    beyond QUARTER_END_COVERAGE_DAYS (the frontier cap's own allowance for a weekend
    or holiday close), means the data does not reach the quarter yet: after
    September 30, a container whose fetch failed would otherwise lock Q3 on prices
    ending July 20. That REFUSES with LockCoverageError, naming each short ticker's
    missing dates, and persists nothing.

    A ticker with NO usable prices at all stays what #204 made it, a disclosed gap
    read live and named on the cover. That is a different fact: the symbol cannot
    be priced from any source (the personal book holds 46 household-only symbols
    with no price rows), and refusing on it would make every personal lock
    impossible.

    Raises ValueError if the quarter has not yet ended.
    """
    end = _parse_quarter_end(quarter_id)
    if end is None:
        raise ValueError(f"Unrecognised quarter_id: {quarter_id!r}")
    if not is_quarter_complete(quarter_id):
        raise ValueError(f"Quarter {quarter_id} has not ended yet (end: {end}).")

    end_str = end.isoformat()
    inception_str = "2020-01-01"

    tickers, gaps = _get_all_snapshot_tickers()
    adj: dict = {}
    raw: dict = {}
    statuses: list[TickerStatus] = []
    for ticker in tickers:
        try:
            df = _prices_module.get_prices(ticker, inception_str, end_str)
            if df is None or df.empty:
                gaps.append((ticker, "no price data through the quarter end"))
                continue
            # The rule: dividends through the quarter's last day, never later ones.
            # Recomputed from close rather than taken from the frame, whose adj_close
            # is anchored on every stored dividend.
            adj[ticker] = (_prices_module.dividend_adjusted(ticker, df, through=end_str)
                           if "close" in df.columns else df["adj_close"])
            # BOTH bases now. Storing adj_close alone left a raw-close consumer
            # unservable, and the reader papered over that by aliasing (#193).
            if "close" in df.columns:
                raw[ticker] = df["close"]
            statuses.append(TickerStatus(ticker, True,
                                         served_through=max(df.index).isoformat()))
        except Exception as exc:                     # noqa: BLE001
            # One bad ticker does not fail the quarter, and is not dropped
            # silently either: it is stored as a disclosed gap (#204).
            gaps.append((ticker, f"{type(exc).__name__}: {exc}"[:200]))

    short = _short_coverage(statuses, end)
    if short:
        raise LockCoverageError(
            f"Cannot lock {quarter_id}: the price data does not reach the quarter's "
            f"end ({end_str}), so a lock now would freeze the quarter on older prices. "
            f"Nothing was locked. Short: " + "; ".join(short) + "."
        )

    if not adj:
        raise RuntimeError(f"No price data fetched for snapshot {quarter_id}.")

    adj_df = pd.DataFrame(adj)
    adj_df.index = pd.to_datetime(adj_df.index).date
    raw_df = pd.DataFrame(raw)
    if not raw_df.empty:
        raw_df.index = pd.to_datetime(raw_df.index).date
    snap_df = SnapshotFrames(adj_close=adj_df,
                             close=raw_df if not raw_df.empty else None,
                             gaps=tuple((str(t), str(r)) for t, r in gaps),
                             rule=QUARTER_END_RULE)

    captured_at = datetime.now().isoformat(timespec="seconds")
    # Two top-level keys, one flat frame each — see SnapshotFrames for why this is
    # not a column MultiIndex.
    payload = {"adj_close": json.loads(adj_df.to_json(orient="split", date_format="iso"))}
    if snap_df.close is not None:
        payload["close"] = json.loads(raw_df.to_json(orient="split", date_format="iso"))
    payload["gaps"] = [list(g) for g in snap_df.gaps]
    payload["rule"] = QUARTER_END_RULE
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
    Serve prices from snap_df to EVERY get_prices call made inside the block.
    Tickers absent from snap_df, and windows it holds no rows for, read the cache as
    usual. Released on exit even if an exception is raised.

    Sets src.prices._PRICE_LOCK, which get_prices consults on each call. It used to
    monkey-patch the src.prices.get_prices attribute instead, and every report
    section imports that function by name, so none of them saw the lock (#368).

    snap_df: a SnapshotFrames, or a wide adj_close frame (index=datetime.date
    objects, columns=tickers).
    """
    frames = _as_frames(snap_df)

    def _snapshot_reader(ticker: str, start_date: str, end_date: Optional[str] = None):
        """The locked frame, or None to fall through to the cache."""
        if ticker not in frames.adj_close.columns:
            return None

        end = end_date or date.today().isoformat()
        col = frames.adj_close[ticker].dropna()
        # Compare via ISO strings — robust against date vs datetime.date index dtype subtleties
        idx_iso = pd.Index([d.isoformat() for d in col.index])
        mask = (idx_iso >= start_date) & (idx_iso <= end)
        filtered = col[mask]

        if filtered.empty:
            return None

        # Serve ONLY the bases this snapshot actually holds. The old code returned
        # the one stored series under BOTH names, which turned "cannot serve this"
        # into "serves something wrong": a caller asking what the account is worth
        # received a total-return series under a "Prices locked" cover (#193).
        data = {"adj_close": filtered.values}
        if frames.close is not None and ticker in frames.close.columns:
            data["close"] = frames.close[ticker].reindex(filtered.index).values
        return pd.DataFrame(data, index=filtered.index)

    token = _prices_module._PRICE_LOCK.set(_snapshot_reader)
    try:
        yield
    finally:
        _prices_module._PRICE_LOCK.reset(token)
