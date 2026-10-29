"""Quarter-snapshot price cache for immutable PDF report generation.

Stores a frozen copy of all portfolio and benchmark adj_close prices at
quarter-end so that report regeneration always produces identical numbers
regardless of retroactive adj_close adjustments from the upstream data provider.
"""
import io
import json
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple, Optional

import pandas as pd

import src.prices as _prices_module
from src.coverage import TickerStatus
from src.db import get_connection
from src.input_lock import (
    ALL_INPUTS,
    CAPE,
    DIVIDENDS,
    ETF_METADATA,
    FF5_DEVELOPED_EXUS,
    FF5_US,
    HYG,
    POLICY,
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
    """Return True once New York's date is strictly past the quarter-end date.

    New York's date (asof.today_et), not the machine's: on the demo's UTC server
    date.today() passes the quarter's last day at 8 PM ET, four hours before New
    York's quarter ends, and a lock taken then was measured freezing Q2 on June 29's
    closes (#454)."""
    from src.asof import today_et
    end = _parse_quarter_end(quarter_id)
    return end is not None and today_et() > end


def last_session_on_or_before(d: date) -> date:
    """The last NYSE session on or before ``d``, by the static holiday table
    (demo_refresh.is_session, #412). A quarter that ends on a weekend or a holiday is
    covered by the session before it, and by no earlier one (#454)."""
    from src.demo_refresh import is_session
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def lock_price_frontier(today: "date | None" = None) -> "Optional[str]":
    """The latest date by which every ticker a quarter lock covers has a stored close:
    MIN over the lock's tickers (_get_all_snapshot_tickers, every security and every
    benchmark constituent) of each one's latest stored close on or before ``today``
    (New York's date by default). DB only, never a fetch. None when none is stored.

    The reportable-quarter cap reads it (#458), so the page names a quarter only once
    the lock's coverage gate (_short_coverage) would pass. The holdings' frontier
    (holdings.committed_price_frontier) cannot stand in: the lock also needs every
    benchmark's close, so a quarter it named could still be refused. A ticker with no
    stored price at all is skipped, as the lock skips it to a disclosed gap."""
    from src.asof import today_et
    ref = (today or today_et()).isoformat()
    tickers, _gaps = _get_all_snapshot_tickers()
    if not tickers:
        return None
    marks = ",".join("?" * len(tickers))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT ticker, MAX(price_date) FROM prices WHERE ticker IN ({marks}) "
            "AND price_date <= ? GROUP BY ticker", (*tickers, ref)).fetchall()
    ends = [r[1] for r in rows if r[1]]
    return min(ends) if ends else None


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
    # Inputs a later correction replaced because the lock had taken them short of
    # the quarter, by src.input_lock name: {"was_through", "restated_on"} (#386).
    input_corrections: "dict | None" = None
    # The blended benchmarks' rule the lock's report measures with: BLENDED_RULE for a
    # lock taken from #383 on; None before, when each figure held its own window's
    # basket, and those reports keep that construction (window_start_baskets).
    benchmark_rule: "str | None" = None
    # {"restated_on": ISO} when the book's DRIP lots were rebuilt from the ledger
    # after this lock was taken (#406 item 12). A lock holds prices and inputs, not
    # trades, so its report reads the rebuilt lots; this is what makes it say so.
    lot_rebuild: "dict | None" = None
    # {"restated_on": ISO, "filled": [[ticker, ISO date], ...]} when the lock froze
    # closes its book lacked and was restated with them filled (#491).
    price_corrections: "dict | None" = None
    # {"restated_on": ISO, "removed": [spec, ...]} when the lock's gaps list was
    # rewritten to remove a gap that was never one (#459, #469): the cover had said a
    # benchmark series could not be fetched when prices were locked. The rewrite left
    # no record; this is the record, added afterwards (#478).
    gap_corrections: "dict | None" = None
    # The valuation rule the lock's figures are on (#369): RAW_CLOSE_RULE or
    # ADJUSTED_LEVELS_RULE, set by the quarter the lock covers (valuation_rule_for).
    # None for a lock taken before the marker existed, which is on adjusted levels
    # (lock_valuation_rule). The value series reads it inside the lock (valuation_in_force).
    valuation_rule: "str | None" = None
    # A raw-close lock's ledger through its quarter's last day, every trades row with
    # the LEDGER_COLUMNS, DRIP lots included, so a later lot rebuild cannot move the
    # lock. None on an adjusted-levels lock, which reads the live ledger as before.
    # Inside the lock, holdings reads its trades from it (valuation_in_force).
    ledger: "pd.DataFrame | None" = None


# Every lock captured from #368 on: prices AND dividends through the quarter's last
# day. Stored in the lock's payload so a reader can tell which rule produced it.
QUARTER_END_RULE = "quarter_end"

# THE VALUATION RULE (#369, decided by the owner on October 27 and 28, 2026). Holdings are
# to be valued at raw closes times every share held, with dividends as income, from the
# Q4 2026 lock on; Q2 and Q3 2026 and every earlier quarter stay on dividend-adjusted
# levels. The rule is set by the QUARTER a lock covers, not by when it is captured: a
# lock for a quarter ending on or after RAW_CLOSE_RULE_FROM is on the raw-close rule, and
# a lock for any earlier quarter is on adjusted levels whenever it is taken. So a book's
# partial first quarter, reportable and not yet locked, stays on the rule of the quarters
# after it. Every lock captured from here on records its rule. Each lock already holds
# its tickers' dividends through its last day (#382, input_lock.DIVIDENDS); a raw-close
# lock holds the ledger too.
RAW_CLOSE_RULE = "raw_close"
ADJUSTED_LEVELS_RULE = "adjusted_levels"
RAW_CLOSE_RULE_FROM = date(2026, 12, 31)

# The trades columns a raw-close lock holds: what the value series and the flow series
# read from the ledger, and trade_id for their order.
LEDGER_COLUMNS = ("trade_id", "account_id", "trade_date", "ticker", "action", "shares",
                  "price", "fees", "lot_source")


def valuation_rule_for(quarter_end: date) -> str:
    """The valuation rule a lock for the quarter ending ``quarter_end`` is taken on."""
    return RAW_CLOSE_RULE if quarter_end >= RAW_CLOSE_RULE_FROM else ADJUSTED_LEVELS_RULE


def lock_valuation_rule(snap: "SnapshotFrames | None") -> str:
    """The valuation rule a lock is on. A lock with no marker, as every lock taken before
    the marker existed is, is on adjusted levels."""
    return getattr(snap, "valuation_rule", None) or ADJUSTED_LEVELS_RULE


# The valuation rule and ledger of the quarter lock in force, or None outside one. Set
# only by snapshot_price_context, the same mechanism as src.prices._PRICE_LOCK.
_VALUATION_LOCK: "ContextVar[Optional[tuple]]" = ContextVar("valuation_lock", default=None)


@contextmanager
def on_adjusted_levels():
    """Reads inside the block are on adjusted levels, as inside a lock with no marker,
    and read the live ledger. For the Performance page's attribution check alone, until
    #369's PR 3 (decided by the owner on October 28, 2026): Brinson-Fachler values each
    sleeve on adjusted levels, so the check compares it with the book's TWR on that basis
    while the Stage 2 tile shows the raw-close TWR. PR 3 retires it."""
    token = _VALUATION_LOCK.set((ADJUSTED_LEVELS_RULE, None))
    try:
        yield
    finally:
        _VALUATION_LOCK.reset(token)


def valuation_in_force() -> "tuple[str, pd.DataFrame | None]":
    """(rule, ledger) for a read in this context (#369's PR 2, decided by the owner on
    October 27 and 28, 2026). Live reads are on the raw-close rule and read the live
    ledger (None). Inside a quarter lock, the lock's own marker, adjusted levels when it
    has none, and the ledger it holds, None for an adjusted-levels lock."""
    held = _VALUATION_LOCK.get()
    return held if held is not None else (RAW_CLOSE_RULE, None)


def _capture_ledger(end: date) -> dict:
    """The book's ledger through ``end``: every trades row, DRIP lots included, in the
    order the value series reads them, as {"columns", "data"}."""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(LEDGER_COLUMNS)} FROM trades WHERE trade_date <= ? "
            "ORDER BY trade_date, trade_id", (end.isoformat(),)).fetchall()
    return {"columns": list(LEDGER_COLUMNS), "data": [list(r) for r in rows]}


def _ledger_frame(payload: dict) -> pd.DataFrame:
    return pd.DataFrame(payload["data"], columns=payload["columns"])

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


# What the cover calls each input a lock can hold, in the order it lists them. Every
# name in ALL_INPUTS has one (tests/test_cover_names_what_the_lock_holds.py), so an
# input added later cannot be left off the cover silently.
_INPUT_WORDS = {
    DIVIDENDS: "dividends",
    CAPE: "CAPE",
    FF5_US: "the US Fama-French factors",
    FF5_DEVELOPED_EXUS: "the Developed ex-US Fama-French factors",
    UMD: "the momentum factor",
    HYG: "the HYG credit proxy",
    ETF_METADATA: "the fund durations",
    POLICY: "the SAA policy",
}

# The report sections built outside the lock, as their headings name them. They read
# through the day the report is generated (reports.generate_quarterly_report_bytes).
UNLOCKED_SECTIONS = ("Macro Context", "Asset Evaluation")


def _named(names) -> "list[str]":
    """The cover's words for input ``names``, in _INPUT_WORDS order, the two French
    files as one item when both are there."""
    words = [_INPUT_WORDS[n] for n in _INPUT_WORDS if n in names]
    us, dev = _INPUT_WORDS[FF5_US], _INPUT_WORDS[FF5_DEVELOPED_EXUS]
    if us in words and dev in words:
        words[words.index(us)] = "the Fama-French factors"
        words.remove(dev)
    return words


def _listed(items: "list[str]") -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def lock_holds_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover sentence saying what this quarter's lock holds, or None with no lock.

    Read from the lock's own lists (#478 section 3): its price frames, the inputs it
    holds and the inputs it is still waiting on. The cover used to claim the quarter
    locked every one of its report's inputs, beside two sections the same report
    labels live. The ETF metadata is named only when it holds a duration: the locks taken
    before #455 hold the file with none, and their durations came from the undated
    table (undated_durations_note)."""
    if snap is None:
        return None
    held = dict(getattr(snap, "inputs", None) or {})
    meta = held.get(ETF_METADATA)
    if ETF_METADATA in held and not any(
            isinstance(v, dict) and v.get("duration_years") is not None
            for v in (meta or {}).values()):
        del held[ETF_METADATA]
    waiting = _named(getattr(snap, "inputs_pending", None) or {})
    text = "This quarter's lock holds " + _listed(["prices"] + _named(held))
    if waiting:
        text += ", and is waiting on " + _listed(waiting)
    return (f"{text}. {_listed(list(UNLOCKED_SECTIONS))} are not locked: they are "
            f"computed each time the report is generated.")


def price_data_lock_note(snap: "SnapshotFrames | None", locked_on: "str | None") -> "str | None":
    """The methodology's Price Data sentences about the lock, or None with no lock.

    The entry said a lock used "only prices and dividends through the quarter's last
    day, so a later dividend or a later price update cannot shift this report's
    numbers" (#482). Two sections of the same report are built outside the lock and
    read prices through the day it is generated, and a locked figure has moved by
    restatement, each named on a cover. So it says what the lock holds, from the lock
    (dividends only when it holds them: a lock from before inputs were locked holds
    prices alone), how a locked figure can change, and which sections are not locked,
    in the cover's own words for them."""
    if snap is None or not locked_on:
        return None
    held = getattr(snap, "inputs", None) or {}
    what = "prices and dividends" if DIVIDENDS in held else "prices"
    return (f"Quarter prices locked {locked_on}. The lock holds {what} through the "
            f"quarter's last day, and a locked figure changes only by a restatement the "
            f"cover names. {_listed(list(UNLOCKED_SECTIONS))} are not locked: they are "
            f"computed each time the report is generated.")


def inputs_restatement_note(quarter_id: "str | None", snap: "SnapshotFrames | None") -> "str | None":
    """One line for a quarter whose report this rule restated, or None.

    Only when the lock carries INPUTS_RULE, holds a CAPE series, and the quarter
    closed before the rule took effect. What moved is the executive summary's CAPE
    reading, now the quarter's last monthly observation instead of the latest one,
    and the line says that and nothing wider: what the lock holds is lock_holds_note's
    to say (#478 section 3)."""
    end = _parse_quarter_end(quarter_id) if quarter_id else None
    if (end is None or snap is None or getattr(snap, "inputs_rule", None) != INPUTS_RULE
            or (snap.inputs or {}).get(CAPE) is None):
        return None
    if end >= INPUTS_RULE_SINCE:
        return None
    last = snap.inputs[CAPE].index.max()
    return (
        f"Restated {INPUTS_RULE_SINCE.strftime('%B')} {INPUTS_RULE_SINCE.day}, "
        f"{INPUTS_RULE_SINCE.year}: the lock's CAPE reading is the quarter's last monthly "
        f"observation, {last.strftime('%B')} {last.year}; earlier versions of this "
        f"report cited the latest CAPE on file when they were generated."
    )


def undated_durations_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover sentence for a quarter whose fixed-income durations came from the
    undated table they lived in before #455, or whose Bloomberg US Agg duration was
    the undated 6.0 it carried before #462, or None. Names each that was undated.

    That is a lock whose locked ETF metadata holds no durations, or which holds no
    inputs at all: positioning.fund_durations serves such a lock the undated table,
    so it reports the durations it was reported with. Not a lock still waiting on its
    metadata, whose duration line is pending. The figures are not restated here."""
    if snap is None or getattr(snap, "quarter_end", None) is None:
        return None
    if ETF_METADATA in (getattr(snap, "inputs_pending", None) or {}):
        return None
    meta = (getattr(snap, "inputs", None) or {}).get(ETF_METADATA) or {}
    dated = {t for t, v in meta.items()
             if isinstance(v, dict) and v.get("duration_years") is not None}
    funds_undated, agg_undated = not (dated - {"AGG"}), "AGG" not in dated
    if funds_undated and agg_undated:
        return ("This quarter's fixed-income durations came from an undated table later "
                "found out of date, and the Bloomberg US Agg duration they were compared "
                "with was undated too.")
    if funds_undated:
        return ("This quarter's fixed-income durations came from an undated table later "
                "found out of date.")
    if agg_undated:
        return ("This quarter's Bloomberg US Agg duration, which its fixed-income "
                "durations were compared with, was undated.")
    return None


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


def _enc_cape(readings: pd.DataFrame) -> dict:
    """CAPE with each reading's read date and source beside its month (#478 I06). A
    lock taken before this holds CAPE as a plain series (_enc_series), and stays so."""
    return {"kind": "cape_readings",
            "index": [pd.Timestamp(d).isoformat() for d in readings.index],
            "index_name": readings.index.name,
            "data": [float(v) for v in readings["cape"].tolist()],
            "read_on": [d.isoformat() if d is not None else None
                        for d in readings["read_on"].tolist()],
            "source": readings["source"].tolist()}


def _dec(payload):
    kind = payload["kind"]
    if kind == "cape_readings":
        idx = pd.DatetimeIndex(pd.to_datetime(payload["index"]), name=payload["index_name"])
        out = pd.DataFrame({"cape": pd.Series(payload["data"], index=idx, dtype=float)})
        out["read_on"] = pd.Series([date.fromisoformat(d) if d else None
                                    for d in payload["read_on"]], index=idx, dtype=object)
        out["source"] = pd.Series(payload["source"], index=idx, dtype=object)
        return out
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
    if kind == "policy":
        from src.policy import decode
        return decode(payload)
    raise ValueError(f"unknown locked-input kind {kind!r}")


def _capture_inputs(end: date, tickers: "list[str]",
                    only: "tuple | None" = None,
                    policy_after_lock: bool = False) -> "tuple[dict, dict, dict]":
    """(encoded, through, pending) for every input, or only those in ``only``.

    Each input as its section's reader returns it, cut at the quarter's last day
    (#371's rule, applied to every input). An input whose data stops short of the
    quarter is PENDING instead: the French factors (and momentum) and HYG without the
    quarter's last NYSE session (the price gate's rule, #454), and CAPE until the
    quarter's last month has a reading made after that month's last NYSE session
    (#478 I06). French publishes about a month late, so on October 1 a Q3 lock holds
    prices, and CAPE once it has been read again, while its factor sections wait.

    The French files were held to a five-calendar-day allowance until #466 (decided by
    the owner on September 28, 2026): rows ending up to five days before the quarter's
    last day were taken as covering it. French publishes whole months, so a file ends
    on a month's last business day, and every committed lock's French inputs reach
    their quarter's last session; the allowance goes, and one rule covers every dated
    input.

    HYG, the FI regression's credit proxy, comes from the price layer with the price
    rule (dividends through the quarter's last day) and the French gate (#386). The
    ETF metadata is a hand-kept file of fund durations, each stamped with its source
    date; it covers a quarter when every stamp falls inside the quarter, so a file
    nobody has updated renders the duration line as pending rather than locking.

    The SAA policy (src/policy.py: the asset classes, their targets, bands and
    benchmarks, and each ticker's sleeve) is held as it stands on the day it is read,
    which is never pending (#525). ``policy_after_lock`` marks it as given to a lock
    already taken, whose quarter it may not describe."""
    from src import etf_metadata, factors, shiller

    want = set(only or ALL_INPUTS)
    end_ts = pd.Timestamp(end)
    start_ts = pd.Timestamp(_INPUT_START)
    floor = last_session_on_or_before(end)      # the price gate's rule (#454, #466)
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
        s = _hyg_through(end)
        last = s.index.max().date() if len(s) else None
        gate(HYG, s, last, last is not None and last >= last_session_on_or_before(end),
             lambda x: _enc_series(x, "datetime"))
    if CAPE in want:
        # The quarter's last month, and only from a reading made after that month's
        # last NYSE session (#478 I06). Before then the row filed under the month is
        # the source's reading so far: on 2026-09-25 September read 41.48, and
        # multpl's September row read 40.90 once the month had ended. Pending names
        # the latest month the file holds such a reading for.
        r = shiller.get_cape_readings()
        r = r[r.index <= end_ts]
        last = next((m.date() for m, d in zip(r.index[::-1], r["read_on"].tolist()[::-1])
                     if shiller.read_after_month_end(m, d)), None)
        covered = (len(r) > 0 and r.index.max().date() >= date(end.year, end.month, 1)
                   and last == r.index.max().date())
        gate(CAPE, r, last, covered, _enc_cape)
    if ETF_METADATA in want:
        meta = etf_metadata.load_metadata()
        stamps = sorted(date.fromisoformat(v["as_of"]) for v in meta.values()
                        if isinstance(v, dict) and v.get("as_of"))
        q_start = date(end.year, end.month - 2, 1)
        oldest = stamps[0] if stamps else None
        # Pending on the date that disqualifies the file (#463): its newest when it is
        # too new for the quarter, its oldest when it is too old (and when it is both).
        # It used to be the oldest always, so a file too new named a date inside the
        # quarter.
        too_new = bool(stamps) and stamps[0] >= q_start and stamps[-1] > end
        gate(ETF_METADATA, meta, stamps[-1] if too_new else oldest,
             bool(stamps) and stamps[0] >= q_start and stamps[-1] <= end,
             lambda x: {"kind": "json", "data": x})
    if DIVIDENDS in want:
        divs = {}
        for t in tickers:
            s = _prices_module.get_dividends(t, _INPUT_START, end.isoformat())
            divs[t] = _enc_series(s, "date")
        enc[DIVIDENDS] = {"kind": "dividends", "data": divs}
    if POLICY in want:
        from src.asof import today_et
        from src import policy
        held_on = today_et().isoformat()
        enc[POLICY] = policy.encode(policy.read_book(), held_on, policy_after_lock)
        through[POLICY] = held_on
    return enc, through, pending


def _hyg_through(end: date) -> pd.Series:
    """HYG's total-return level from the price layer through ``end``, with dividends
    through ``end`` only (the price lock's rule), as a datetime-indexed series. Empty
    when the price layer holds none."""
    try:
        df = _prices_module.get_prices("HYG", _INPUT_START, end.isoformat())
    except Exception:                                    # noqa: BLE001
        return pd.Series(dtype=float)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    s = (_prices_module.dividend_adjusted("HYG", df, through=end.isoformat())
         if "close" in df.columns else df["adj_close"])
    # get_prices stops at ``end``, so the series already ends with the quarter.
    return pd.Series(s.values, index=pd.to_datetime(list(df.index)), name="adj_close",
                     dtype=float)


def restate_short_hyg(quarter_id: str, restated_on: date) -> "tuple[str, str] | None":
    """Replace a lock's HYG input taken short of the quarter, and record it (#386).

    Before #386 HYG was locked as read from a committed parquet that ended
    2026-05-05, with no coverage gate, so a quarter ending after that date locked a
    credit proxy held flat for its last weeks. This re-reads HYG from the price layer
    through the quarter's end, only for a lock whose HYG ends before the coverage
    floor, and records the old end date so the report can say what it corrected.
    Every other input, the prices and the capture time are left as they are. Returns
    (old end, new end), or None when the lock's HYG already covered the quarter."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT snapshot_data, snapshot_date, captured_at FROM quarter_snapshots "
            "WHERE quarter_id = ?", (quarter_id,)).fetchone()
    if row is None:
        return None
    blob = json.loads(row["snapshot_data"])
    old = (blob.get("inputs_through") or {}).get(HYG)
    end = date.fromisoformat(row["snapshot_date"])
    floor = last_session_on_or_before(end)      # the price gate's rule (#454)
    if old is None or date.fromisoformat(old) >= floor:
        return None
    s = _hyg_through(end)
    if not len(s) or s.index.max().date() < floor:
        raise LockCoverageError(
            f"Cannot restate {quarter_id}'s HYG: the price layer's HYG ends "
            f"{s.index.max().date() if len(s) else 'nowhere'}, short of {end}.")
    new = s.index.max().date().isoformat()
    blob["inputs"][HYG] = _enc_series(s, "datetime")
    blob["inputs_through"][HYG] = new
    blob.setdefault("input_corrections", {})[HYG] = {
        "was_through": old, "restated_on": restated_on.isoformat()}
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO quarter_snapshots
               (quarter_id, snapshot_date, captured_at, snapshot_data) VALUES (?, ?, ?, ?)""",
            (quarter_id, row["snapshot_date"], row["captured_at"], json.dumps(blob)))
    return old, new


def _blended_rule() -> str:
    """The blended benchmarks' rule a lock taken now records (#383)."""
    from src.benchmarks import BLENDED_RULE
    return BLENDED_RULE


def benchmark_construction_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover line for a quarter lock taken before the blended benchmarks' quarterly
    rule, stating the construction its benchmark figures used (#383), or None."""
    from src.benchmarks import BLENDED_RULE
    if snap is None or getattr(snap, "quarter_end", None) is None:
        return None
    if getattr(snap, "benchmark_rule", None) == BLENDED_RULE:
        return None
    return (
        "The Custom Blended figures in this report hold the SAA target-weight basket "
        "bought at the start of each period shown: the quarter from its start, each "
        "trailing period from its own start, and the since-inception chart and "
        "regression from inception. Reports from Q3 2026 rebalance it to target "
        "weights at the start of each calendar quarter and chain-link the quarters."
    )


def input_corrections_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover line for a lock whose HYG input was corrected (#386), or None."""
    fix = ((getattr(snap, "input_corrections", None) or {}).get(HYG)
           if snap is not None else None)
    if not fix:
        return None
    on = date.fromisoformat(fix["restated_on"])
    was = date.fromisoformat(fix["was_through"])
    q_end = date.fromisoformat(snap.quarter_end)

    def _long(d: date) -> str:
        return f"{d.strftime('%B')} {d.day}, {d.year}"

    return (
        f"Restated {_long(on)} to correct an incomplete input. The fixed-income "
        f"regression's credit proxy, HYG, came from a committed file that ended "
        f"{_long(was)}, {(q_end - was).days} days before the quarter did, so it was "
        f"held flat for the rest of the quarter. It now comes from the price data "
        f"through the quarter's end."
    )


def record_lot_rebuild(quarter_id: str, restated_on: date) -> bool:
    """Record in a lock that the book's DRIP lots were rebuilt after it was taken
    (#406 item 12). Every price, input and the capture time are left as they are;
    only the record is added. Returns False when there is no lock, or it already
    carries a record: a second rebuild on the same lock does not re-date the first."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT snapshot_data, snapshot_date, captured_at FROM quarter_snapshots "
            "WHERE quarter_id = ?", (quarter_id,)).fetchone()
    if row is None:
        return False
    blob = json.loads(row["snapshot_data"])
    if blob.get("lot_rebuild"):
        return False
    blob["lot_rebuild"] = {"restated_on": restated_on.isoformat()}
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ?",
            (json.dumps(blob), quarter_id))
        if cur.rowcount != 1:
            raise RuntimeError(f"recording {quarter_id}'s lot rebuild matched "
                               f"{cur.rowcount} rows, expected exactly 1")
    return True


def style_box_withdrawn_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover line for a lock restated when the equity style box was withdrawn
    (#468), or None. Its ETF metadata held the style box's fact-sheet figures, which
    had no recorded source; they were removed from the lock and the removal recorded
    under input_corrections, as #386 recorded HYG's."""
    fix = ((getattr(snap, "input_corrections", None) or {}).get(ETF_METADATA)
           if snap is not None else None)
    if not fix:
        return None
    on = date.fromisoformat(fix["restated_on"])
    # The callout went with it and the sentence did not say so (#478): it listed the
    # non-US holdings' weights under the style box, in the same block of the report.
    return (f"Restated {on.strftime('%B')} {on.day}, {on.year} to correct an error. The "
            f"equity style box is withdrawn, and the Non-US Equity Sleeve callout that "
            f"sat under it: the style box's fact-sheet figures had no recorded source.")


def gap_corrections_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover line for a lock whose gaps list was rewritten to remove a gap that was
    never one (#459), or None. The lock's cover had said a benchmark series could not
    be fetched when prices were locked: the lock's ticker list could not parse a
    parent row's legacy benchmark spec, and no price was missing. #469 emptied the
    list and left no record, so the line went from the cover with nothing said
    (#478). tools/restate_demo_gap_record.py added the record this reads."""
    rec = getattr(snap, "gap_corrections", None) if snap is not None else None
    if not rec:
        return None
    on = date.fromisoformat(rec["restated_on"])
    n = len(rec["removed"])
    said = "one benchmark series" if n == 1 else f"{n} benchmark series"
    return (f"Restated {on.strftime('%B')} {on.day}, {on.year} to correct an error. An "
            f"earlier version of this report said {said} could not be fetched when "
            f"prices were locked; none was missing.")


def filled_closes_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover line for a lock restated because it froze closes its book lacked
    (#491), or None. The lock's two price frames held nulls on those sessions; the
    closes were filled from the provider and the adjusted levels recomputed from them
    (tools/restate_demo_filled_closes.py). The count and the month are read from the
    lock's own record."""
    rec = getattr(snap, "price_corrections", None) if snap is not None else None
    if not rec:
        return None
    on = date.fromisoformat(rec["restated_on"])
    months: list = []
    for day in sorted(date.fromisoformat(d) for _t, d in rec["filled"]):
        month = f"{day.strftime('%B')} {day.year}"
        if month not in months:
            months.append(month)
    return (f"Restated {on.strftime('%B')} {on.day}, {on.year} to correct an error. The "
            f"lock froze {len(rec['filled'])} closes missing from {_listed(months)}; they "
            f"are now filled from the provider.")


def policy_recorded_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover line for a lock given the book's SAA policy after it was taken, or None
    (#525). The eleven locks committed before locks held a policy were given it as it
    stood on the day it was recorded (tools/hold_policy_in_demo_locks.py), not as it
    stood at the quarter's close; a lock taken since holds it as it stood when taken,
    and needs no line."""
    held = (getattr(snap, "inputs", None) or {}).get(POLICY) if snap is not None else None
    if not held or not held.get("recorded_after_lock"):
        return None
    on = date.fromisoformat(held["held_on"])
    return (f"The SAA policy this report reads (its sleeves, their target weights, "
            f"tolerance bands and benchmarks, and each fund's sleeve) was recorded "
            f"{on.strftime('%B')} {on.day}, {on.year}, after this quarter locked: it is "
            f"the policy as it stood that day.")


def lot_rebuild_note(snap: "SnapshotFrames | None") -> "str | None":
    """The cover line for a quarter whose DRIP lots were rebuilt after it locked, or
    None. What moved is every figure that counts reinvested shares: market value,
    weights and the trade list. Returns, attribution and benchmark figures value the
    non-DRIP shares at the adjusted close and never read a lot, so they do not move."""
    rec = getattr(snap, "lot_rebuild", None) if snap is not None else None
    if not rec:
        return None
    on = date.fromisoformat(rec["restated_on"])
    return (
        f"Restated {on.strftime('%B')} {on.day}, {on.year}: the dividend reinvestment "
        f"lots were rebuilt from the trade ledger and priced at the actual close on each "
        f"reinvestment date. Earlier versions of this report priced them at a "
        f"dividend-adjusted close and did not hold a lot for every distribution, so "
        f"market values, weights and the trade list differ from those versions. "
        f"Returns, attribution and benchmark figures do not read these lots and are "
        f"unchanged."
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
                           inputs_rule=blob.get("inputs_rule"),
                           input_corrections=blob.get("input_corrections"),
                           benchmark_rule=blob.get("benchmark_rule"),
                           lot_rebuild=blob.get("lot_rebuild"),
                           price_corrections=blob.get("price_corrections"),
                           gap_corrections=blob.get("gap_corrections"),
                           valuation_rule=blob.get("valuation_rule"),
                           ledger=(_ledger_frame(blob["ledger"]) if "ledger" in blob
                                   else None)),
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
    # The lock is already taken, so a policy captured here is today's, not the
    # quarter's (#525); a lock with inputs never waits on one.
    enc, through, pending = _capture_inputs(end, tickers, only=todo, policy_after_lock=True)
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
    """A quarter was asked to lock before its data reaches the quarter's end, or from
    prices with a gap inside a ticker's history.

    Raised by capture_quarter_snapshot INSTEAD of persisting a lock. Its message
    names every short ticker and its missing dates, so the page can show it as it
    stands rather than as a generic generation failure."""


def _short_coverage(statuses: "list[TickerStatus]", end: date) -> "list[str]":
    """One line per distinct gap, naming its missing dates and every ticker in it.

    THE RULE (#454): every ticker the lock covers must have the close of the
    quarter's last NYSE session (last_session_on_or_before). It used to allow prices
    ending up to five calendar days short, meant for a quarter ending on a
    weekend or holiday; on a weekday quarter end that let a lock taken before the
    last close was stored freeze the quarter on the previous session's, measured on
    Q2 2026 (SPY 741.00 against 746.77). A weekend or holiday end needs the session
    before it, and no earlier one.

    Grouped because a failed fetch leaves every ticker short by the same dates, and
    29 copies of one sentence bury the tickers. Empty when the lock may proceed."""
    need = last_session_on_or_before(end)
    by_last: dict[str, list[str]] = {}
    for s in statuses:
        if s.resolved and s.served_through and date.fromisoformat(s.served_through) < need:
            by_last.setdefault(s.served_through, []).append(s.ticker)
    lines = []
    for last in sorted(by_last):
        first_missing = date.fromisoformat(last) + timedelta(days=1)
        lines.append(f"prices end {last}, missing {first_missing.isoformat()} to "
                     f"{need.isoformat()} for {', '.join(sorted(by_last[last]))}")
    return lines


def _interior_gaps(frame: pd.DataFrame) -> "list[str]":
    """One clause per set of tickers that lack a close on dates between their own
    first and last close, naming the dates. Empty when the lock may proceed.

    THE RULE (#491): no lock is taken from prices with a gap in them. The demo book
    lacked 32 closes between stored closes of ten funds, and Q2 2026's lock froze them
    as nulls: a reader carries the close before a null forward, and a dividend just
    after one was scaled on the close before the gap.

    The frame's dates are the union of its tickers' dates, so a null between a
    ticker's first and last close is a date another ticker holds and it does not.
    Dates before a ticker's first close are not a gap: it had not listed. Every ticker
    a lock covers today trades NYSE sessions. A series that trades every day (BTC-USD,
    which no lock covers) would make every weekend such a date for the others, and
    this would refuse until it is taught the two calendars."""
    frame = frame.sort_index()
    days = [d if isinstance(d, str) else d.isoformat() for d in frame.index]
    missing: "dict[str, list[str]]" = {}
    for ticker in frame.columns:
        have = frame[ticker].notna().to_numpy()
        held = have.nonzero()[0]
        if not len(held):
            continue
        for i in range(held[0], held[-1] + 1):
            if not have[i]:
                missing.setdefault(days[i], []).append(str(ticker))
    groups: "dict[tuple, list[str]]" = {}
    for day in sorted(missing):
        groups.setdefault(tuple(sorted(missing[day])), []).append(day)
    return [f"{', '.join(dates)} for {', '.join(tickers)}" for tickers, dates in groups.items()]


def _held_without_price(frame: pd.DataFrame, start: date, end: date) -> "list[str]":
    """One clause per set of holdings that were held on a session in [start, end] with
    no close in ``frame`` on or before it, naming the dates. Empty when the lock may
    proceed.

    THE RULE (#639, decided by the owner on October 17, 2026): no lock is taken while a
    holding has no price on a date it was held in the quarter. The value series every
    locked return reads counts such a holding as nothing on those days, and the lock
    would freeze that. ``frame`` is the lock's adj_close frame. A session is a date on
    which some ticker in it holds a close, as _interior_gaps reads the frame, so a
    market holiday is never one. The holdings are the portfolio account's, every lot,
    with SPAXX read as BIL, its price; SPAXX is not checked when the lock does not
    cover BIL. A book with no trade ledger holds nothing."""
    from src.holdings import NO_TRADE_LEDGER, PortfolioAccountUnresolved, get_portfolio_account
    with get_connection() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('trades', 'accounts')")}
    if tables != {"trades", "accounts"}:
        return []
    try:
        account_id = get_portfolio_account()["account_id"]
    except PortfolioAccountUnresolved as exc:
        if exc.reason == NO_TRADE_LEDGER:
            return []
        raise
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT trade_date, ticker, action, shares FROM trades "
            "WHERE account_id = ? AND trade_date <= ? ORDER BY trade_date, trade_id",
            (account_id, end.isoformat())).fetchall()
    if not rows:
        return []
    frame = frame.copy()
    frame.index = [pd.Timestamp(d).date() for d in frame.index]
    frame = frame.sort_index()
    sessions = [d for d in frame.index if start <= d <= end]
    missing: "dict[str, list[str]]" = {}
    held: "dict[str, list[tuple[date, float]]]" = {}
    for r in rows:
        signed = r["shares"] if r["action"].lower() == "buy" else -r["shares"]
        held.setdefault(r["ticker"], []).append((pd.Timestamp(r["trade_date"]).date(), signed))
    for ticker, dated in held.items():
        price = "BIL" if ticker == "SPAXX" else ticker
        if ticker == "SPAXX" and price not in frame.columns:
            continue
        name = "SPAXX (BIL)" if ticker == "SPAXX" else ticker
        first = frame[price].first_valid_index() if price in frame.columns else None
        for day in sessions:
            if first is not None and day >= first:
                break
            if sum(s for d, s in dated if d <= day) > 1e-9:
                missing.setdefault(day.isoformat(), []).append(name)
    groups: "dict[tuple, list[str]]" = {}
    for day in sorted(missing):
        groups.setdefault(tuple(sorted(missing[day])), []).append(day)
    return [f"{', '.join(dates)} for {', '.join(names)}" for names, dates in groups.items()]


def _quarter_first_day(end: date) -> date:
    """The first day of the quarter whose last day is ``end``."""
    return date(end.year, end.month - 2, 1)


class LockGaps(NamedTuple):
    """What a lock of a quarter would be refused for now, by rule, each clause as the
    capture's refusal writes it: dates, then what lacks a close on them."""

    interior: "list[str]"     # a close missing inside a ticker's history (_interior_gaps)
    unpriced: "list[str]"     # a holding unpriced on a day it was held (_held_without_price)


def _leading_fetch_failures(tickers, start: str, end: str) -> "list[str]":
    """One clause per cause, naming the tickers, for each ticker the lock reads whose
    leading gap fetch for a read from ``start`` failed and is recorded
    (prices.gap_fetch_failures): its stored history begins later than ``start``, and
    the earlier closes could not be fetched. Empty when the lock may proceed.

    THE RULE (#639's second half, decided by the owner on October 17 and October 18,
    2026): a recorded leading-fetch failure for a ticker the lock reads is a gap. The
    record holds no answer that no close exists there: a window with no NYSE session is
    never fetched, and the provider's "Data doesn't exist" (a window before a fund's
    listing) is an answer, not a failure (prices._gap_fetch, #644)."""
    wanted = set(tickers)
    by_cause: "dict[str, list[str]]" = {}
    for f in _prices_module.gap_fetch_failures():
        if f.ticker in wanted and f.start == start and f.end <= end:
            by_cause.setdefault(f.cause, []).append(f.ticker)
    return [f"{', '.join(sorted(set(ts)))} ({cause})" for cause, ts in sorted(by_cause.items())]


def _unread_dividends(tickers, start: str, end: str) -> "list[str]":
    """One clause per dividend in [start, end] that a price update sent for a ticker
    the lock reads and could not read (prices.unread_dividends_for), with its date and
    why. Empty when the lock may proceed.

    THE RULE (#642, decided by the owner on October 18, 2026): a lock refuses a quarter
    whose window holds such a record for a ticker it reads. Since #658 the answer that
    held it is not stored at all, so its closes are missing from the store and the end,
    interior and leading rules see that first; this is the capture's last guard, for
    what those miss: a ticker with no stored closes, whose whole fetch raised and would
    be locked as a disclosed gap (#204)."""
    return [u.words() for t in sorted(set(tickers))
            for u in _prices_module.unread_dividends_for(t, start, end)]


def quarter_lock_refusals(quarter_id: str) -> LockGaps:
    """quarter_lock_gaps, by the rule each gap breaks: the cap needs only whether there
    is one, and the page's note words each rule as the capture's refusal does."""
    import sqlite3
    none = LockGaps([], [])
    end = _parse_quarter_end(quarter_id)
    if end is None:
        return none
    with get_connection() as conn:
        try:
            locked = conn.execute("SELECT 1 FROM quarter_snapshots WHERE quarter_id = ?",
                                  (quarter_id,)).fetchone()
        except sqlite3.OperationalError:          # no locks table yet: nothing is locked
            locked = None
    if locked:
        return none
    tickers, _gaps = _get_all_snapshot_tickers()
    if not tickers:
        return none
    marks = ",".join("?" * len(tickers))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT ticker, price_date FROM prices WHERE ticker IN ({marks}) "
            "AND price_date >= ? AND price_date <= ?",
            (*tickers, _INPUT_START, end.isoformat())).fetchall()
    if not rows:
        return none
    held = pd.DataFrame([(r[0], r[1]) for r in rows], columns=["ticker", "day"])
    held["close"] = 1.0
    frame = held.pivot(index="day", columns="ticker", values="close")
    return LockGaps(_interior_gaps(frame),
                    _held_without_price(frame, _quarter_first_day(end), end))


def quarter_lock_gaps(quarter_id: str) -> "list[str]":
    """The gaps a lock of ``quarter_id`` would be refused for now, each as the capture's
    refusal writes it (dates, then the tickers that lack a close on them), or [] when it
    would not be: the quarter is already locked, and a persisted lock is served as
    written, or the stored prices of the tickers the lock covers hold no gap from the
    lock's first day through the quarter's last, and no holding of the portfolio was
    held on a session in the quarter with no close on or before it.

    The reportable-quarter cap reads it (asof.most_recent_reportable_quarter), so a
    page names a quarter only once its lock can be taken, and until then shows the
    quarter before with the missing closes named (#491). The frame is the one the
    capture would build from the cache: the same tickers, the same first day, the same
    rules, applied by the same checks (_interior_gaps, and _held_without_price over the
    same quarter, #648). DB only: never a fetch, and never a write, so the table is not
    created.

    THE CAP STAYS ON STORED STATE (decided by the owner on October 18, 2026, #658). The
    capture's fourth and fifth rules, a failed fetch of a ticker's earlier history
    (_leading_fetch_failures, #639) and an unread dividend (_unread_dividends, #642),
    read this process's records of its fetches, not the store, and are not read here:
    they remain the capture's last guard, and its refusal names the cause. An answer
    holding a dividend that cannot be read is not stored (#658), so the closes it
    carried are missing from the store, and the end and interior rules the cap reads
    see them."""
    gaps = quarter_lock_refusals(quarter_id)
    return gaps.interior + gaps.unpriced


def capture_quarter_snapshot(quarter_id: str) -> tuple:
    """Lock a completed quarter and return (SnapshotFrames, captured_at_str).

    Every holding's and benchmark constituent's close and total-return level, from
    inception through the quarter's last day, persisted in quarter_snapshots.

    THE LOCK RULE (#368, option B): a quarter locks at its own end, using only
    prices AND DIVIDENDS through its last day. dividend_adjusted anchors on every
    stored dividend unless told otherwise, so a lock taken without the cutoff moved
    whenever a later ex-date arrived, and depended on when it was captured. With it,
    a lock is the same whenever it is computed and whatever is fetched afterwards.

    COVERAGE, OR NO LOCK. A ticker without the close of the quarter's last NYSE
    session (#454; a weekend or holiday end needs the session before it) means the
    data does not reach the quarter yet: after
    September 30, a container whose fetch failed would otherwise lock Q3 on prices
    ending July 20. That REFUSES with LockCoverageError, naming each short ticker's
    missing dates, and persists nothing.

    NO GAP INSIDE A HISTORY, OR NO LOCK (#491). A ticker with no close on a date
    between its own first and last, on which another ticker holds one, refuses the
    same way (_interior_gaps): the lock would freeze the gap.

    NO HOLDING UNPRICED ON A DAY IT WAS HELD, OR NO LOCK (#639, decided by the owner on
    October 17, 2026). The leading edge: a holding of the portfolio account with no
    close on or before a session in the quarter on which it was held refuses the same
    way (_held_without_price), naming the holding and the dates. A ticker the portfolio
    does not hold may start late (a late listing), and a market holiday is never a
    gap.

    NO FAILED FETCH OF A TICKER'S EARLIER HISTORY, OR NO LOCK (#639's second half,
    decided by the owner on October 17 and October 18, 2026). A ticker the lock reads
    whose stored history starts after this read's 2020-01-01, and whose fetch of the
    earlier stretch failed and is recorded (prices.gap_fetch_failures), refuses the same
    way (_leading_fetch_failures), naming the ticker and the cause: the lock would freeze
    that history short. The record holds only failures since #644: a window with no NYSE
    session is never fetched, and the provider's "Data doesn't exist" for a window before
    a fund's listing is an answer, not a failure. With the gap fetch off, as the demo's
    committed locks are taken (tools/lock_demo_quarters.py), nothing is fetched and
    nothing recorded: the stored history is the whole record.

    NO UNREAD DIVIDEND, OR NO LOCK (#642, decided by the owner on October 18, 2026). A
    dividend a price update sent for a ticker the lock reads, dated in the window it
    reads, that could not be read (prices.unread_dividends) refuses the same way
    (_unread_dividends), naming the ticker, the date and why: it is not stored, and the
    lock would freeze total return without it. Since #658 the answer holding it is not
    stored either, so the closes it carried are missing and the rules above refuse on
    them first; this rule is the last guard.

    THE LOCK FETCHES WHAT THE BOOK LACKS (#594, decided by the owner on October 15,
    2026). Every ticker is read through prices.get_prices, which fetches and stores
    as any read does. A ticker with no stored closes is fetched whole, whatever
    get_prices' gap fetch says. With the gap fetch on, as in personal mode, a ticker
    whose first stored close is later than this read's 2020-01-01 start is fetched
    back to it. The personal book's household-only symbols, held in accounts other
    than the portfolio's, have no stored closes until its first lock, and most are
    exchange-listed funds the provider prices. So that lock stores their histories and
    holds them as priced tickers. The portfolio's figures do not depend on it: its
    reads are scoped to its own account. The demo's committed locks are taken offline
    (tools/lock_demo_quarters.py), from the committed book.

    A ticker for which the provider returns nothing stays what #204 made it, a
    disclosed gap read live and named on the cover. That is a different fact from a
    short or gapped history: refusing on it would make every personal lock impossible.
    A holding the portfolio held in the quarter is the exception: with no price at all
    it has none on the dates it was held, and refuses as above.

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
    holes = _interior_gaps(adj_df)
    if holes:
        raise LockCoverageError(
            f"Cannot lock {quarter_id}: the price data has a gap inside a ticker's "
            f"history, so a lock now would freeze the gap. Nothing was locked. No close "
            f"on " + "; ".join(holes) + "."
        )
    unpriced = _held_without_price(adj_df, _quarter_first_day(end), end)
    if unpriced:
        raise LockCoverageError(
            f"Cannot lock {quarter_id}: a holding has no price on a date it was held in "
            f"the quarter, so a lock now would freeze it unpriced. Nothing was locked. "
            f"No close on " + "; ".join(unpriced) + "."
        )
    short_history = _leading_fetch_failures(tickers, inception_str, end_str)
    if short_history:
        raise LockCoverageError(
            f"Cannot lock {quarter_id}: the price update for the earlier history of a "
            f"ticker the lock reads failed, so a lock now would freeze that history short. "
            f"Nothing was locked. Failed: " + "; ".join(short_history) + "."
        )
    unread = _unread_dividends(tickers, inception_str, end_str)
    if unread:
        raise LockCoverageError(
            f"Cannot lock {quarter_id}: a price update sent a dividend for a ticker the "
            f"lock reads that could not be read, so a lock now would freeze total return "
            f"without it. Nothing was locked. Unread: " + "; ".join(unread) + "."
        )
    raw_df = pd.DataFrame(raw)
    if not raw_df.empty:
        raw_df.index = pd.to_datetime(raw_df.index).date
    # Every other input the locked sections read, through the same day (#382).
    in_enc, in_through, in_pending = _capture_inputs(end, sorted(adj))
    # The valuation rule, by the quarter (#369), and a raw-close lock's ledger.
    valuation_rule = valuation_rule_for(end)
    ledger = _capture_ledger(end) if valuation_rule == RAW_CLOSE_RULE else None
    snap_df = SnapshotFrames(adj_close=adj_df,
                             close=raw_df if not raw_df.empty else None,
                             gaps=tuple((str(t), str(r)) for t, r in gaps),
                             rule=QUARTER_END_RULE, quarter_end=end_str,
                             inputs={k: _dec(v) for k, v in in_enc.items()},
                             inputs_pending=in_pending, inputs_rule=INPUTS_RULE,
                             benchmark_rule=_blended_rule(),
                             valuation_rule=valuation_rule,
                             ledger=_ledger_frame(ledger) if ledger else None)

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
    payload["benchmark_rule"] = _blended_rule()
    payload["valuation_rule"] = valuation_rule
    if ledger is not None:
        payload["ledger"] = ledger
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
    # And the lock's valuation rule and ledger (#369): a lock with no marker is on
    # adjusted levels, so every lock taken before the marker values as it always has.
    v_token = _VALUATION_LOCK.set((lock_valuation_rule(frames), frames.ledger))
    try:
        # And every non-price input the lock holds (#382). A lock from before
        # inputs were locked holds none, and its sections read inputs as before.
        with ExitStack() as stack:
            stack.enter_context(input_context(frames.inputs or {},
                                              frames.inputs_pending or {},
                                              frames.quarter_end))
            # A lock taken before the blended benchmarks' quarterly rule measures each
            # figure with its own window's basket, as its report was built (#383).
            from src.benchmarks import BLENDED_RULE, window_start_baskets
            if frames.benchmark_rule != BLENDED_RULE:
                stack.enter_context(window_start_baskets())
            yield
    finally:
        _VALUATION_LOCK.reset(v_token)
        _prices_module._PRICE_LOCK.reset(token)
