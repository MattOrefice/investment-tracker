"""Restate the Q2 2026 quarter lock for the closes it froze missing (#491), as an error
correction, in data/demo.db and the frozen test book.

The lock was taken from a book that lacked 32 closes of May 2026 for ten of the funds it
covers, so its two price frames hold nulls there. A reader carries the close before a
null forward, and SCHP's and VGIT's June 1 dividends were scaled on May 27's close, the
last one before the hole, instead of May 29's. tools/fill_demo_price_gaps.py puts the
closes into the book; this puts them into the lock:

  * its close frame takes the 32 closes;
  * its adjusted frame is recomputed by the capture's own steps (prices.get_prices, then
    prices.dividend_adjusted through the quarter's last day) on a scratch book that holds
    demo.db's closes for the lock's tickers and the dividends THE LOCK holds, nothing else;
  * the restatement is recorded in the lock under "price_corrections", which the cover
    states in one sentence (cache.filled_closes_note).

One guarded step, rolled back in both books unless it changes exactly what is named:
  * the payload must round-trip through JSON byte for byte;
  * THE CONTROL: recomputed the same way without the 32 closes, both frames must be the
    lock's own, cell for cell. So every difference in the restated frames comes from the
    closes and none from the recomputation;
  * the close frame differs in the 32 cells alone, each null before;
  * the adjusted frame differs in those 32 cells and in SCHP's and VGIT's levels before
    June 1, 2026, every one of them, each by the one ratio its dividend's two bases give;
  * every other key of the payload is unchanged, and the UPDATE is keyed on the quarter
    and the payload's current text, so the frozen book's lock must be demo.db's own.
A lock already restated is left alone, so a second run changes nothing. The frames are
computed from demo.db for both books: the frozen book keeps prices from 2024 only, and
the lock's frames start in 2020. Targets the two books explicitly and runs from main()
only. The personal book holds no committed locks.

The record is dated the day the restatement merges: today in New York unless a date is
given.

Usage:  python tools/restate_demo_filled_closes.py [YYYY-MM-DD]
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
FROZEN_BOOK = ROOT / "tests" / "fixtures" / "frozen_book.db"
sys.path.insert(0, str(ROOT))

QUARTER = "2026Q2"
INCEPTION = "2020-01-01"            # capture_quarter_snapshot's first day
RECORD = "price_corrections"
BASES = ("adj_close", "close")
# The tickers whose adjusted level also moves BEFORE an ex-date, and that ex-date: the
# dividend is now scaled on the filled close before it, not on the close before the hole.
REANCHORED = {"SCHP": "2026-06-01", "VGIT": "2026-06-01"}
SET_PAYLOAD = ("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ? "
               "AND snapshot_data = ?")


def _closes(path: Path) -> "dict[tuple[str, str], float]":
    con = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        return {(t, d): c for t, d, c in con.execute(
            "SELECT ticker, price_date, close FROM prices")}
    finally:
        con.close()


def frames(closes: dict, dividends: dict, tickers: list, end: str) -> dict:
    """{"adj_close": ..., "close": ...}: the lock's two price frames as
    cache.capture_quarter_snapshot computes and encodes them, from a book that holds
    exactly ``closes`` and ``dividends``."""
    import pandas as pd
    import src.db as db
    import src.prices as prices

    lacking = sorted(set(tickers) - {t for t, _d in closes})
    if lacking:
        raise SystemExit(f"ABORT: no close for {lacking}")
    scratch = Path(tempfile.mkdtemp(prefix="restate_q2_"))
    saved = (db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE, prices._GAP_FETCH)
    adj, raw = {}, {}
    try:
        work = scratch / "book.db"
        con = sqlite3.connect(work)
        try:
            con.executescript(db.SCHEMA)
            with con:
                con.executemany("INSERT INTO prices (ticker, price_date, close, adj_close) "
                                "VALUES (?, ?, ?, NULL)",
                                [(t, d, c) for (t, d), c in sorted(closes.items())])
                con.executemany("INSERT INTO dividends (ticker, ex_date, amount) "
                                "VALUES (?, ?, ?)",
                                [(t, d, a) for (t, d), a in sorted(dividends.items())])
        finally:
            con.close()
        db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = work, set(), None
        prices.set_gap_fetch(False)          # a read serves the scratch book, never a fetch
        for t in tickers:
            df = prices.get_prices(t, INCEPTION, end)
            adj[t] = prices.dividend_adjusted(t, df, through=end)
            raw[t] = df["close"]
    finally:
        db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = saved[:3]
        prices.set_gap_fetch(saved[3])
        shutil.rmtree(scratch, ignore_errors=True)
    out = {}
    for basis, series in (("adj_close", adj), ("close", raw)):
        df = pd.DataFrame(series)
        df.index = pd.to_datetime(df.index).date
        out[basis] = json.loads(df.to_json(orient="split", date_format="iso"))
    return out


def _differences(was: dict, new: dict) -> dict:
    """{basis: {(ticker, ISO date): (old, new)}} for every cell that differs. The two
    frames must hold the same tickers and dates."""
    out = {}
    for basis in BASES:
        a, b = was[basis], new[basis]
        if a["columns"] != b["columns"] or a["index"] != b["index"]:
            raise SystemExit(f"ABORT: the restated {basis} frame's tickers or dates are "
                             f"not the lock's")
        out[basis] = {(c, d[:10]): (x, y)
                      for d, r0, r1 in zip(a["index"], a["data"], b["data"])
                      for c, x, y in zip(a["columns"], r0, r1) if x != y}
    return out


def _require_only_the_named(was: dict, new: dict, filled: list, closes: dict,
                            dividends: dict) -> None:
    """Raise SystemExit unless ``new`` differs from ``was`` exactly as the module
    docstring names."""
    diff = _differences(was, new)
    cells = set(filled)
    if set(diff["close"]) != cells:
        raise SystemExit(f"ABORT: the close frame differs in {len(diff['close'])} cells, "
                         f"not in the {len(cells)} filled alone")
    for basis in BASES:
        for key in sorted(cells):
            old, cur = diff[basis].get(key, (0.0, None))
            if old is not None or cur is None:
                raise SystemExit(f"ABORT: {basis} {key} was {old!r} and is now {cur!r}; a "
                                 f"filled cell goes from null to a value")
    for key in sorted(cells):
        if abs(diff["close"][key][1] - closes[key]) > 1e-9:
            raise SystemExit(f"ABORT: the close frame's {key} is not the book's close")

    # Outside the filled cells, the adjusted frame may move only where a dividend's
    # base changed: every level before that ex-date, each by the same ratio.
    moved = {k: v for k, v in diff["adj_close"].items() if k not in cells}
    cols, index = was["adj_close"]["columns"], [d[:10] for d in was["adj_close"]["index"]]
    want = set()
    for ticker, ex in REANCHORED.items():
        before = sorted(d for (t, d) in closes if t == ticker and d < ex)
        base_new = before[-1]
        base_old = [d for d in before if (ticker, d) not in cells][-1]
        if (ticker, base_new) not in cells or base_old == base_new:
            raise SystemExit(f"ABORT: {ticker}'s {ex} dividend has no filled close before it")
        amount = dividends[(ticker, ex)]
        ratio = ((1.0 - amount / closes[(ticker, base_new)])
                 / (1.0 - amount / closes[(ticker, base_old)]))
        col = cols.index(ticker)
        for day, row in zip(index, was["adj_close"]["data"]):
            if day < ex and row[col] is not None:
                want.add((ticker, day))
                old, cur = moved.get((ticker, day), (row[col], row[col]))
                if cur is None or abs(cur / old - ratio) > 1e-9:
                    raise SystemExit(f"ABORT: {ticker} {day} moved from {old!r} to {cur!r}, "
                                     f"not by the ratio {ratio!r} its {ex} dividend gives")
    if set(moved) != want:
        extra = sorted(set(moved) - want)
        raise SystemExit(f"ABORT: the adjusted frame moved outside the filled cells and "
                         f"{sorted(REANCHORED)}'s earlier levels: {len(extra)} cell(s), "
                         f"e.g. {extra[:3]}")


def restated(text: str, end: str, book_closes: dict, restated_on: date) -> "str | None":
    """The payload with its price frames restated and the restatement recorded, or None
    if the lock was already restated. Raises SystemExit on anything unexpected."""
    from tools.fill_demo_price_gaps import CELLS

    blob = json.loads(text)
    if json.dumps(blob) != text:
        raise SystemExit("ABORT: the payload does not round-trip through JSON")
    if blob.get(RECORD):
        return None
    tickers = list(blob["adj_close"]["columns"])
    filled = sorted(c for c in CELLS if c[0] in tickers and INCEPTION <= c[1] <= end)
    lacking = [c for c in filled if c not in book_closes]
    if not filled or lacking:
        raise SystemExit(f"ABORT: {DEMO_DB.name} lacks {len(lacking)} of the {len(filled)} "
                         f"closes to restate with; run tools/fill_demo_price_gaps.py first")
    closes = {k: c for k, c in book_closes.items()
              if k[0] in tickers and INCEPTION <= k[1] <= end}
    held = blob["inputs"]["dividends"]["data"]
    dividends = {(t, d): a for t in tickers
                 for d, a in zip(held[t]["index"], held[t]["data"])}
    was = {basis: blob[basis] for basis in BASES}

    control = frames({k: c for k, c in closes.items() if k not in set(filled)},
                     dividends, tickers, end)
    if control != was:
        n = sum(len(v) for v in _differences(was, control).values())
        raise SystemExit(f"ABORT: recomputed without the filled closes, the frames are not "
                         f"the lock's own ({n} cell(s) differ), so the restatement would "
                         f"change more than the closes")
    new = frames(closes, dividends, tickers, end)
    _require_only_the_named(was, new, filled, closes, dividends)

    for basis in BASES:
        blob[basis] = new[basis]
    blob[RECORD] = {"restated_on": restated_on.isoformat(),
                    "filled": [list(c) for c in filled]}
    out = json.dumps(blob)

    # Nothing else changed: the old payload with the two frames and the record.
    want = json.loads(text)
    for basis in BASES:
        want[basis] = new[basis]
    want[RECORD] = blob[RECORD]
    if json.loads(out) != want:
        raise SystemExit("ABORT: the payload changed beyond its price frames and the record")
    return out


def main(restated_on: "date | None" = None) -> int:
    from src.asof import today_et
    from tools.fill_demo_price_gaps import CELLS

    on = restated_on or today_et()
    books = (DEMO_DB, FROZEN_BOOK)
    book_closes, frozen_closes = _closes(DEMO_DB), _closes(FROZEN_BOOK)
    stray = sorted(c for c in CELLS if frozen_closes.get(c) != book_closes.get(c))
    if stray:
        raise SystemExit(f"ABORT: {FROZEN_BOOK.name}'s closes are not {DEMO_DB.name}'s for "
                         f"{len(stray)} of the named cells, e.g. {stray[:3]}")
    conns = [sqlite3.connect(b) for b in books]
    count = 0
    try:
        rows = [con.execute("SELECT snapshot_date, snapshot_data FROM quarter_snapshots "
                            "WHERE quarter_id = ?", (QUARTER,)).fetchall() for con in conns]
        if any(len(r) != 1 for r in rows):
            raise SystemExit(f"ABORT: {QUARTER} is not locked once in each book")
        (end, text), = rows[0]
        new = restated(text, end, book_closes, on)
        if new is None:
            if rows[1][0][1] != text:
                raise SystemExit(f"ABORT: {FROZEN_BOOK.name}'s {QUARTER} lock is not "
                                 f"{DEMO_DB.name}'s")
        else:
            for con, book in zip(conns, books):
                n = con.execute(SET_PAYLOAD, (new, QUARTER, text)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {book.name}'s {QUARTER} payload update "
                                     f"changed {n} rows, not 1")
            count = len(json.loads(new)[RECORD]["filled"])
        for con in conns:
            con.commit()
    except BaseException:
        for con in conns:
            con.rollback()
        raise
    finally:
        for con in conns:
            con.close()
    if not count:
        print(f"{QUARTER} is already restated in {DEMO_DB.name} and {FROZEN_BOOK.name}. "
              f"Nothing was written.")
    else:
        print(f"{QUARTER} restated on {on.isoformat()} in {DEMO_DB.name} and "
              f"{FROZEN_BOOK.name}: {count} closes filled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None))
