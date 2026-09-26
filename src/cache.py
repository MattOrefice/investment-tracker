"""Quarter-snapshot price cache for immutable PDF report generation.

Stores a frozen copy of all portfolio and benchmark adj_close prices at
quarter-end so that report regeneration always produces identical numbers
regardless of retroactive adj_close adjustments from the upstream data provider.
"""
import io
import json
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple, Optional

import pandas as pd

import src.prices as _prices_module
from src.coverage import TickerStatus, coverage_from_statuses
from src.db import get_connection
from src.input_lock import (
    ALL_INPUTS,
    CAPE,
    DIVIDENDS,
    ETF_METADATA,
    FF5_DEVELOPED_EXUS,
    FF5_US,
    HYG,
    UMD,
    input_context,
)

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
    # The quarter's last day, ISO. None only for a bare frame built outside a capture.
    quarter_end: "str | None" = None
    # Every NON-PRICE input the locked sections read (#382), decoded, by
    # src.input_lock name. None for a lock that predates inputs.
    inputs: "dict | None" = None
    # Inputs whose data did not cover the quarter when locked, with the date their
    # data ended then. Their sections render as pending and never read live data.
    inputs_pending: "dict | None" = None
    # INPUTS_RULE for a lock that holds its inputs; None before #382.
    inputs_rule: "str | None" = None


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


# #382: a lock keeps every input its sections read, not only prices, each through
# the quarter's last day. Recorded in the payload like QUARTER_END_RULE.
INPUTS_RULE = "inputs_quarter_end"

# The day inputs began to be locked. A RECORD, like QUARTER_END_RULE_SINCE: it dates
# the one figure this restated in quarters that closed before it, the executive
# summary's CAPE reading, which had been read live when a report was generated.
INPUTS_RULE_SINCE = date(2026, 9, 26)

# The earliest input row a lock keeps. The price lock reads from the same date.
_INPUT_START = "2020-01-01"

# Which inputs each locked section reads, from the census in #382's PR. The factor
# and benchmark sections render as pending while any of theirs is pending.
SECTION_INPUTS = {
    "executive_summary": (CAPE, DIVIDENDS),
    "positioning": (ETF_METADATA,),
    "factor": (FF5_US, FF5_DEVELOPED_EXUS, UMD, HYG),
    "benchmark": (FF5_US,),
}


def inputs_restatement_note(quarter_id: "str | None", snap: "SnapshotFrames | None") -> "str | None":
    """One line for a quarter whose report this rule restated, or None.

    Only when the lock carries INPUTS_RULE, holds a CAPE series, and the quarter
    closed before the rule took effect. What moved is the executive summary's CAPE
    reading, now the quarter's last monthly observation instead of the latest one."""
    end = _parse_quarter_end(quarter_id) if quarter_id else None
    if (end is None or snap is None or getattr(snap, "inputs_rule", None) != INPUTS_RULE
            or (snap.inputs or {}).get(CAPE) is None):
        return None
    if end >= INPUTS_RULE_SINCE:
        return None
    last = snap.inputs[CAPE].index.max()
    return (
        f"Restated {INPUTS_RULE_SINCE.strftime('%B')} {INPUTS_RULE_SINCE.day}, "
        f"{INPUTS_RULE_SINCE.year}: this quarter now locks every input its report "
        f"reads, not only prices. Its CAPE reading is the quarter's last monthly "
        f"observation, {last.strftime('%B')} {last.year}; earlier versions of this "
        f"report cited the latest CAPE on file when they were generated."
    )


# ── Exact encoding for locked inputs ──────────────────────────────────────────
# NOT to_json: its default keeps ten significant digits, which would make a locked
# regression differ from the same regression on the file it locked. Python floats
# through json.dumps round-trip exactly.

def _enc_frame(df: pd.DataFrame) -> dict:
    return {"kind": "frame", "index": [pd.Timestamp(d).isoformat() for d in df.index],
            "index_name": df.index.name, "columns": [str(c) for c in df.columns],
            "data": df.values.tolist()}


def _enc_series(s: pd.Series, index_kind: str) -> dict:
    idx = ([pd.Timestamp(d).isoformat() for d in s.index] if index_kind == "datetime"
           else [d.isoformat() for d in s.index])
    return {"kind": "series", "index_kind": index_kind, "index": idx,
            "index_name": s.index.name, "name": s.name,
            "data": [float(v) for v in s.tolist()]}


def _dec(payload):
    kind = payload["kind"]
    if kind == "frame":
        idx = pd.DatetimeIndex(pd.to_datetime(payload["index"]), name=payload["index_name"])
        return pd.DataFrame(payload["data"], index=idx, columns=payload["columns"],
                            dtype=float)
    if kind == "series":
        if payload["index_kind"] == "datetime":
            idx = pd.DatetimeIndex(pd.to_datetime(payload["index"]), name=payload["index_name"])
        else:
            idx = pd.Index([date.fromisoformat(d) for d in payload["index"]], dtype=object,
                           name=payload["index_name"])
        return pd.Series(payload["data"], index=idx, name=payload["name"], dtype=float)
    if kind == "json":
        return payload["data"]
    if kind == "dividends":
        return {t: _dec(p) for t, p in payload["data"].items()}
    raise ValueError(f"unknown locked-input kind {kind!r}")


def _capture_inputs(end: date, tickers: "list[str]",
                    only: "tuple | None" = None) -> "tuple[dict, dict, dict]":
    """(encoded, through, pending) for every input, or only those in ``only``.

    Each input as its section's reader returns it, cut at the quarter's last day
    (#371's rule, applied to every input). An input whose data stops short of the
    quarter is PENDING instead: the French factors (and momentum) when they end
    more than QUARTER_END_COVERAGE_DAYS before the quarter does, and CAPE when it has
    no reading for the quarter's last month. French publishes about a month late,
    so on October 1 a Q3 lock holds prices and CAPE while its factor sections wait.

    HYG is locked as read, without a coverage gate: its committed parquet is a
    static file the refresh does not update, and gating on it would leave the FI
    regression pending for good. Its end date is recorded."""
    from src import factors, shiller, style_box
    from src.asof import QUARTER_END_COVERAGE_DAYS

    want = set(only or ALL_INPUTS)
    end_ts = pd.Timestamp(end)
    start_ts = pd.Timestamp(_INPUT_START)
    floor = end - timedelta(days=QUARTER_END_COVERAGE_DAYS)
    enc: dict = {}
    through: dict = {}
    pending: dict = {}

    def cut(obj):
        return obj[(obj.index >= start_ts) & (obj.index <= end_ts)]

    def gate(name, obj, last, covered, encode):
        if covered:
            enc[name] = encode(obj)
            through[name] = last.isoformat()
        else:
            pending[name] = last.isoformat() if last is not None else None

    for name, region in ((FF5_US, "us"), (FF5_DEVELOPED_EXUS, "developed_exus")):
        if name in want:
            df = cut(factors.load_factors(region))
            last = df.index.max().date() if len(df) else None
            gate(name, df, last, last is not None and last >= floor, _enc_frame)
    if UMD in want:
        s = cut(factors.load_umd_factor())
        last = s.index.max().date() if len(s) else None
        gate(UMD, s, last, last is not None and last >= floor,
             lambda x: _enc_series(x, "datetime"))
    if HYG in want:
        s = cut(factors.hyg_credit_series(_INPUT_START, end.isoformat()))
        if len(s):
            enc[HYG] = _enc_series(s, "datetime")
            through[HYG] = s.index.max().date().isoformat()
    if CAPE in want:
        s = shiller.get_cape_series()
        s = s[s.index <= end_ts]
        last = s.index.max().date() if len(s) else None
        gate(CAPE, s, last, last is not None and last >= date(end.year, end.month, 1),
             lambda x: _enc_series(x, "datetime"))
    if ETF_METADATA in want:
        enc[ETF_METADATA] = {"kind": "json", "data": style_box._load_metadata()}
    if DIVIDENDS in want:
        divs = {}
        for t in tickers:
            s = _prices_module.get_dividends(t, _INPUT_START, end.isoformat())
            divs[t] = _enc_series(s, "date")
        enc[DIVIDENDS] = {"kind": "dividends", "data": divs}
    return enc, through, pending


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
            "SELECT snapshot_data, captured_at, snapshot_date FROM quarter_snapshots "
            "WHERE quarter_id = ?",
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
    inputs = ({k: _dec(v) for k, v in blob["inputs"].items()}
              if "inputs" in blob else None)
    return (SnapshotFrames(adj_close=_frame(blob["adj_close"]), close=close, gaps=gaps,
                           rule=blob.get("rule"), quarter_end=row["snapshot_date"],
                           inputs=inputs, inputs_pending=blob.get("inputs_pending"),
                           inputs_rule=blob.get("inputs_rule")),
            row["captured_at"])


def complete_quarter_inputs(quarter_id: str) -> "SnapshotFrames | None":
    """Lock whatever inputs a quarter's lock is still waiting on, now that their data
    may cover it, and return the lock. Never re-locks an input already locked, so a
    locked input cannot move. A lock from before #382 gets every input captured now.
    None when the quarter has no lock."""
    _ensure_table()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT snapshot_data, snapshot_date, captured_at FROM quarter_snapshots "
            "WHERE quarter_id = ?",
            (quarter_id,),
        ).fetchone()
    if row is None:
        return None
    blob = json.loads(row["snapshot_data"])
    if "adj_close" not in blob:
        return get_quarter_snapshot(quarter_id)[0]      # a bare legacy frame: leave it
    if blob.get("inputs_rule") == INPUTS_RULE:
        todo = tuple(blob.get("inputs_pending") or {})
    else:
        todo = ALL_INPUTS
    if not todo:
        return get_quarter_snapshot(quarter_id)[0]
    end = date.fromisoformat(row["snapshot_date"])
    tickers = list(blob["adj_close"].get("columns", []))
    enc, through, pending = _capture_inputs(end, tickers, only=todo)
    blob.setdefault("inputs", {}).update(enc)
    blob.setdefault("inputs_through", {}).update(through)
    blob["inputs_pending"] = pending
    blob["inputs_rule"] = INPUTS_RULE
    # INSERT OR REPLACE, keeping the lock's own date and capture time: the demo's
    # runtime cache serves quarter_snapshots through a view that takes inserts and
    # deletes only (src.db, #373).
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO quarter_snapshots
               (quarter_id, snapshot_date, captured_at, snapshot_data) VALUES (?, ?, ?, ?)""",
            (quarter_id, row["snapshot_date"], row["captured_at"], json.dumps(blob)))
    return get_quarter_snapshot(quarter_id)[0]


class LockCoverageError(ValueError):
    """A quarter was asked to lock before its data reaches the quarter's end.

    Raised by capture_quarter_snapshot INSTEAD of persisting a lock. Its message
    names every short ticker and its missing dates, so the page can show it as it
    stands rather than as a generic generation failure."""


def _short_coverage(statuses: "list[TickerStatus]", end: date) -> "list[str]":
    """One line per distinct gap, naming its missing dates and every ticker in it.

    Grouped because a failed fetch leaves every ticker short by the same dates, and
    29 copies of one sentence bury the tickers. Read through the coverage record:
    frontier_served is the MIN over what was served, so stale_days beyond the
    tolerance means at least one ticker is short. Empty when the lock may proceed."""
    from src.asof import QUARTER_END_COVERAGE_DAYS
    cov = coverage_from_statuses(statuses, end.isoformat())
    if cov.stale_days is None or cov.stale_days <= QUARTER_END_COVERAGE_DAYS:
        return []
    floor = end - timedelta(days=QUARTER_END_COVERAGE_DAYS)
    by_last: dict[str, list[str]] = {}
    for s in statuses:
        if s.resolved and s.served_through and date.fromisoformat(s.served_through) < floor:
            by_last.setdefault(s.served_through, []).append(s.ticker)
    lines = []
    for last in sorted(by_last):
        first_missing = date.fromisoformat(last) + timedelta(days=1)
        lines.append(f"prices end {last}, missing {first_missing.isoformat()} to "
                     f"{end.isoformat()} for {', '.join(sorted(by_last[last]))}")
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
    # Every other input the locked sections read, through the same day (#382).
    in_enc, in_through, in_pending = _capture_inputs(end, sorted(adj))
    snap_df = SnapshotFrames(adj_close=adj_df,
                             close=raw_df if not raw_df.empty else None,
                             gaps=tuple((str(t), str(r)) for t, r in gaps),
                             rule=QUARTER_END_RULE, quarter_end=end_str,
                             inputs={k: _dec(v) for k, v in in_enc.items()},
                             inputs_pending=in_pending, inputs_rule=INPUTS_RULE)

    # Aware UTC, so the report can show the moment in New York time whatever machine
    # recorded it. Older rows are naive local time; reports reads both.
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Two top-level keys, one flat frame each — see SnapshotFrames for why this is
    # not a column MultiIndex.
    payload = {"adj_close": json.loads(adj_df.to_json(orient="split", date_format="iso"))}
    if snap_df.close is not None:
        payload["close"] = json.loads(raw_df.to_json(orient="split", date_format="iso"))
    payload["gaps"] = [list(g) for g in snap_df.gaps]
    payload["rule"] = QUARTER_END_RULE
    payload["inputs"] = in_enc
    payload["inputs_through"] = in_through
    payload["inputs_pending"] = in_pending
    payload["inputs_rule"] = INPUTS_RULE
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
        # And every non-price input the lock holds (#382). A lock from before
        # inputs were locked holds none, and its sections read inputs as before.
        with ExitStack() as stack:
            stack.enter_context(input_context(frames.inputs or {},
                                              frames.inputs_pending or {},
                                              frames.quarter_end))
            yield
    finally:
        _prices_module._PRICE_LOCK.reset(token)
