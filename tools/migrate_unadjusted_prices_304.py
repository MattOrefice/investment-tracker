"""#304 — move a book's price cache to UNADJUSTED storage.

After this runs, the prices table holds settled raw closes only (adj_close NULL) and
get_prices derives the total-return series on read from close and the dividends table.
So the dividends table must be complete first, and this tool makes it so or refuses.

Per ticker, one fresh full-range fetch (the provider's settled closes and its dividend
record over the stored range) is the reference. Then, in one transaction:

  1. DIVIDENDS. Every ex-date in the fresh record that the book lacks is inserted. A
     stored ex-date the fresh record lacks is removed ONLY when it is the same
     dividend on a wrong date — the fresh record has the same amount within 5 days.
     Either the book lacks that date (a SHIFT: the dividend moves to the provider's
     date) or already has it too (a DUPLICATE: adjusting on read would count the one
     dividend twice). Measured before this ran: demo.db held BIL, SCHP and VGIT's May
     2026 dividend on both 05-01 and 05-04. Any other stored-but-not-fresh ex-date, or an amount mismatch, REFUSES the whole
     run: adjusting from a dividend record we cannot reconcile would be a new
     fabrication, not a fix.
  2. PARTIAL BARS. A stored close that differs from the settled close for the same
     date by more than half a cent was a mid-session mark; it is replaced by the
     settled close. A stored date the fresh fetch has no bar for is left as-is and
     REPORTED (it cannot be classified as partial). A partial bar is only
     identifiable against a settled close, so none is kept unfixed: a ticker whose
     fetch fails refuses the run instead (below), and nothing needs dropping.
  3. adj_close is set NULL for every row.

A ticker whose fetch fails REFUSES the run (its dividend record cannot be checked).

Usage:  python tools/migrate_unadjusted_prices_304.py <db> [--apply|--dry] [plan.json]
Without --apply it reports what it would do and writes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.prices import _SESSION, _YF_CHART_URL, _date_to_unix  # noqa: E402

PARTIAL_TOLERANCE = 0.005
SHIFT_DAYS = 5


def fresh_record(ticker: str, lo: str, hi: str) -> tuple[dict, dict]:
    """(closes by ISO date, dividends by ISO ex-date) over [lo, hi], from the provider."""
    end = (date.fromisoformat(hi) + timedelta(days=1)).isoformat()
    r = _SESSION.get(_YF_CHART_URL.format(ticker=ticker), timeout=20, params={
        "interval": "1d", "period1": _date_to_unix(lo), "period2": _date_to_unix(end),
        "events": "div,splits"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    closes = {}
    for ts, c in zip(res.get("timestamp", []), res["indicators"]["quote"][0]["close"]):
        if c is not None:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            if lo <= d <= hi:
                closes[d] = float(c)
    divs = {}
    for k, v in ((res.get("events") or {}).get("dividends") or {}).items():
        d = datetime.fromtimestamp(int(k), tz=timezone.utc).date().isoformat()
        if lo <= d <= hi and float(v.get("amount", 0)) > 0:
            divs[d] = float(v["amount"])
    return closes, divs


def plan(conn: sqlite3.Connection) -> dict:
    out = {"div_insert": [], "div_delete": [], "bar_replace": [],
           "unmatched_bars": [], "refuse": []}
    tickers = conn.execute("SELECT ticker, MIN(price_date), MAX(price_date) FROM prices "
                           "GROUP BY ticker ORDER BY ticker").fetchall()
    for i, (t, lo, hi) in enumerate(tickers):
        if i:
            time.sleep(0.4)
        try:
            closes, divs = fresh_record(t, lo, hi)
        except Exception as e:                                  # noqa: BLE001
            out["refuse"].append(f"{t}: fetch failed ({type(e).__name__})")
            continue
        have = dict(conn.execute("SELECT ex_date, amount FROM dividends WHERE ticker = ? "
                                 "AND ex_date BETWEEN ? AND ?", (t, lo, hi)).fetchall())
        missing = sorted(set(divs) - set(have))
        for ex in sorted(set(have) - set(divs)):
            shift = [m for m in divs
                     if abs((date.fromisoformat(m) - date.fromisoformat(ex)).days) <= SHIFT_DAYS
                     and abs(divs[m] - have[ex]) <= 1e-4]
            if shift:
                kind = "duplicate" if shift[0] in have else "shift"
                out["div_delete"].append((t, ex, have[ex], shift[0], kind))
            else:
                out["refuse"].append(f"{t}: stored ex-date {ex} ({have[ex]}) not in the "
                                     "provider's record and not a date shift")
        for ex in sorted(set(have) & set(divs)):
            if abs(have[ex] - divs[ex]) > 1e-4:
                out["refuse"].append(f"{t}: {ex} amount {have[ex]} vs {divs[ex]}")
        out["div_insert"] += [(t, ex, divs[ex]) for ex in missing]
        for d, c in conn.execute("SELECT price_date, close FROM prices WHERE ticker = ?",
                                 (t,)).fetchall():
            if d not in closes:
                out["unmatched_bars"].append((t, d))
            elif abs(c - closes[d]) > PARTIAL_TOLERANCE:
                out["bar_replace"].append((t, d, c, closes[d]))
    return out


def apply(conn: sqlite3.Connection, p: dict) -> None:
    with conn:
        for t, ex, _amt, _to, _kind in p["div_delete"]:
            n = conn.execute("DELETE FROM dividends WHERE ticker = ? AND ex_date = ?",
                             (t, ex)).rowcount
            assert n == 1, (t, ex, n)
        for t, ex, amt in p["div_insert"]:
            conn.execute("INSERT INTO dividends (ticker, ex_date, amount) VALUES (?, ?, ?)",
                         (t, ex, amt))
        for t, d, _old, new in p["bar_replace"]:
            n = conn.execute("UPDATE prices SET close = ? WHERE ticker = ? AND price_date = ?",
                             (new, t, d)).rowcount
            assert n == 1, (t, d, n)
        conn.execute("UPDATE prices SET adj_close = NULL")


def main() -> None:
    db, do_apply = sys.argv[1], "--apply" in sys.argv[2:]
    conn = sqlite3.connect(db if do_apply else f"file:{Path(db).as_posix()}?mode=ro",
                           uri=not do_apply)
    p = plan(conn)
    summary = {k: (len(v) if isinstance(v, list) else v) for k, v in p.items()}
    print(json.dumps(summary))
    for line in p["refuse"]:
        print("  REFUSE", line)
    if p["refuse"]:
        print("REFUSED: nothing written.")
        sys.exit(1)
    if do_apply:
        apply(conn, p)
        print("APPLIED.")
    else:
        print("dry run: nothing written.")
    if len(sys.argv) > 3 and sys.argv[-1].endswith(".json"):
        Path(sys.argv[-1]).write_text(json.dumps(p, indent=1))


if __name__ == "__main__":
    main()
