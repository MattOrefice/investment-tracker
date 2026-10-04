"""Advance the demo's committed price snapshot through a settled session (#397).

The public demo serves the prices committed in data/demo.db. Its daily refresh fetches
later closes into a runtime cache and never writes this file (#373), so the committed
snapshot stood where the last one-off migration left it: 2026-07-20 for the holdings and
earlier for 18 other tickers (#406 item 8). A quarter that ended after it could be locked
only at runtime, from whatever one container had fetched. The quarterly close-out
advances it: every ticker the book holds prices for, from the day after its last stored
close through the date given, with the dividends the provider reports in that span.

How it writes (the #283 data-path shape):
  * the fetch runs against a scratch COPY of the book, through the price layer's own
    fetch (src.prices.fetch_prices), so the tracked file is not touched while fetching;
  * the delta is read off the copy: the price and dividend rows the book lacks, dated on
    or before the target;
  * nothing is written unless the delta passes every check below. Then it goes into
    data/demo.db in one transaction, as plain INSERTs, and the result is asserted per
    table against the book as it was. A failed assertion puts the book back.

The checks:
  * no price or dividend row the book already holds differs in the copy;
  * every new price row is after its ticker's last stored close and on or before the
    target, with a positive close (adj_close NULL: the book stores raw closes, #304);
  * every exchange-traded ticker gains exactly the NYSE sessions in its span, and a
    series that trades every day gains every day: no hole a later read would carry
    forward;
  * so every ticker ends on the target, which tools/check_price_histories.py confirms;
  * every new dividend's ex-date is after its ticker's last stored close;
  * afterwards only `prices` and `dividends` differ from the book as it was, by exactly
    the delta's row counts, and every row it held is still there unchanged.

Idempotent: a book already at the target is left as it is. Targets data/demo.db
explicitly (never get_db_path). main() only, so bootstrap never runs it.

Usage:  python tools/advance_demo_prices.py 2026-09-30
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

# Series that trade every calendar day. Every other ticker trades NYSE sessions.
DAILY = frozenset({"BTC-USD"})
# Between requests, so 47 of them do not draw a 429.
PAUSE_SECONDS = 0.25


class AdvanceError(RuntimeError):
    """The advance was refused, or failed a check. The book is as it was."""


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)


def _ends(path: Path) -> "dict[str, str]":
    con = _ro(path)
    try:
        return dict(con.execute("SELECT ticker, MAX(price_date) FROM prices GROUP BY ticker"))
    finally:
        con.close()


def expected_dates(ticker: str, after: str, through: str) -> "list[str]":
    """The dates ``ticker`` must gain: every NYSE session after ``after`` through
    ``through``, or every calendar day for a series in DAILY."""
    from src.demo_refresh import is_session
    out, day, last = [], date.fromisoformat(after) + timedelta(days=1), date.fromisoformat(through)
    while day <= last:
        if ticker in DAILY or is_session(day):
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _fetch_into(work: Path, ends: "dict[str, str]", through: str) -> "list[tuple[str, str]]":
    """Fetch every ticker short of ``through`` into the scratch copy. Returns the
    (ticker, reason) of each fetch that raised."""
    import src.db as db
    import src.prices as prices
    saved = (db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE)
    db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = work, set(), None
    failed = []
    try:
        for ticker, last in sorted(ends.items()):
            if last >= through:
                continue
            start = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
            try:
                prices.fetch_prices(ticker, start, through)
            except Exception as exc:                             # noqa: BLE001
                failed.append((ticker, f"{type(exc).__name__}: {exc}"[:200]))
            time.sleep(PAUSE_SECONDS)
    finally:
        db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = saved
    return failed


def _delta(book: Path, work: Path, through: str):
    """(new price rows, new dividend rows, existing rows the copy changed)."""
    con = _ro(work)
    try:
        con.execute("ATTACH DATABASE ? AS book", (f"file:{Path(book).resolve().as_posix()}?mode=ro",))
        changed = con.execute(
            "SELECT 'prices', b.ticker, b.price_date FROM book.prices b LEFT JOIN main.prices w "
            "ON w.ticker = b.ticker AND w.price_date = b.price_date "
            "WHERE w.ticker IS NULL OR w.close IS NOT b.close OR w.adj_close IS NOT b.adj_close "
            "UNION ALL "
            "SELECT 'dividends', b.ticker, b.ex_date FROM book.dividends b LEFT JOIN "
            "main.dividends w ON w.ticker = b.ticker AND w.ex_date = b.ex_date "
            "WHERE w.ticker IS NULL OR w.amount IS NOT b.amount").fetchall()
        new_prices = con.execute(
            "SELECT w.ticker, w.price_date, w.close FROM main.prices w LEFT JOIN book.prices b "
            "ON b.ticker = w.ticker AND b.price_date = w.price_date "
            "WHERE b.ticker IS NULL AND w.price_date <= ? ORDER BY 1, 2", (through,)).fetchall()
        new_divs = con.execute(
            "SELECT w.ticker, w.ex_date, w.amount FROM main.dividends w LEFT JOIN "
            "book.dividends b ON b.ticker = w.ticker AND b.ex_date = w.ex_date "
            "WHERE b.ticker IS NULL AND w.ex_date <= ? ORDER BY 1, 2", (through,)).fetchall()
    finally:
        con.close()
    return new_prices, new_divs, changed


def _check(ends: "dict[str, str]", through: str, new_prices, new_divs, changed) -> None:
    problems = []
    if changed:
        problems.append(f"{len(changed)} existing row(s) differ in the fetched copy, e.g. "
                        f"{changed[:3]}")
    gained: "dict[str, list[str]]" = {}
    for ticker, day, close in new_prices:
        gained.setdefault(ticker, []).append(day)
        if not close or close <= 0:
            problems.append(f"{ticker} {day}: close {close!r} is not positive")
    for ticker, last in sorted(ends.items()):
        want = expected_dates(ticker, last, through) if last < through else []
        got = sorted(gained.get(ticker, []))
        if got != want:
            missing = sorted(set(want) - set(got))
            extra = sorted(set(got) - set(want))
            problems.append(f"{ticker}: after {last} it should gain {len(want)} close(s) "
                            f"through {through} and gained {len(got)}; missing "
                            f"{missing[:5]}, unexpected {extra[:5]}")
    for ticker in sorted(set(gained) - set(ends)):
        problems.append(f"{ticker}: prices for a ticker the book does not hold")
    for ticker, ex_date, amount in new_divs:
        if ticker not in ends or ex_date <= ends[ticker]:
            problems.append(f"{ticker}: dividend {ex_date} ({amount}) is not after the "
                            f"ticker's last stored close, {ends.get(ticker)}")
    if problems:
        raise AdvanceError("the fetched delta failed its checks, nothing was written:\n  "
                           + "\n  ".join(problems))


def advance(through: date, today: "date | None" = None) -> dict:
    """Advance DEMO_DB through ``through``. Returns the per-table delta it wrote."""
    from src.asof import today_et
    from src.demo_refresh import is_session
    from tools.check_price_histories import history_ends
    from tools.fingerprint_db import fingerprint

    today = today or today_et()
    if not is_session(through):
        raise AdvanceError(f"{through} is not an NYSE session: there is no close to advance to.")
    if through >= today:
        raise AdvanceError(f"{through} is not before today ({today}, New York): its close "
                           f"may not be settled.")
    target = through.isoformat()
    ends = _ends(DEMO_DB)
    past = sorted(t for t, last in ends.items()
                  if t not in DAILY and last > target)
    if past:
        raise AdvanceError(f"the book already holds closes after {target} for {past}.")
    before = fingerprint(DEMO_DB)
    report = {"through": target, "ends_before": history_ends(DEMO_DB),
              "prices": (before["prices"][0], before["prices"][0]),
              "dividends": (before["dividends"][0], before["dividends"][0]),
              "new_dividends": [], "unchanged_tables": len(before)}
    if all(last >= target for last in ends.values()):
        return report                                   # already there: nothing to write

    scratch = Path(tempfile.mkdtemp(prefix="advance_demo_"))
    try:
        work, image = scratch / "work.db", scratch / "before.db"
        shutil.copyfile(DEMO_DB, image)
        shutil.copyfile(DEMO_DB, work)
        os.chmod(work, 0o644)
        failed = _fetch_into(work, ends, target)
        if failed:
            raise AdvanceError("the fetch failed for " + "; ".join(f"{t} ({why})" for t, why in failed)
                               + ". Nothing was written.")
        new_prices, new_divs, changed = _delta(DEMO_DB, work, target)
        _check(ends, target, new_prices, new_divs, changed)

        con = sqlite3.connect(DEMO_DB)
        try:
            with con:
                con.executemany("INSERT INTO prices (ticker, price_date, close, adj_close) "
                                "VALUES (?, ?, ?, NULL)", new_prices)
                con.executemany("INSERT INTO dividends (ticker, ex_date, amount) "
                                "VALUES (?, ?, ?)", new_divs)
        finally:
            con.close()

        # The result, asserted per table against the book as it was.
        after = fingerprint(DEMO_DB)
        ends_after = history_ends(DEMO_DB)
        _, _, lost = _delta(image, DEMO_DB, target)
        wrong = []
        if set(after) != set(before):
            wrong.append(f"the table list changed: {sorted(set(after) ^ set(before))}")
        moved = sorted(t for t in before if after.get(t) != before[t])
        if set(moved) - {"prices", "dividends"}:
            wrong.append(f"tables other than prices and dividends changed: {moved}")
        if after["prices"][0] != before["prices"][0] + len(new_prices):
            wrong.append(f"prices rows {before['prices'][0]} -> {after['prices'][0]}, "
                         f"expected +{len(new_prices)}")
        if after["dividends"][0] != before["dividends"][0] + len(new_divs):
            wrong.append(f"dividends rows {before['dividends'][0]} -> {after['dividends'][0]}, "
                         f"expected +{len(new_divs)}")
        if lost:
            wrong.append(f"{len(lost)} row(s) the book held are gone or changed: {lost[:3]}")
        if list(ends_after) != [target]:
            wrong.append(f"the histories end on {list(ends_after)}, not on {target} alone")
        if wrong:
            shutil.copyfile(image, DEMO_DB)
            raise AdvanceError("the written result failed its assertions and the book was "
                               "put back:\n  " + "\n  ".join(wrong))
        report.update(prices=(before["prices"][0], after["prices"][0]),
                      dividends=(before["dividends"][0], after["dividends"][0]),
                      new_dividends=new_divs, unchanged_tables=len(before) - len(moved))
        return report
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: "list[str]") -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    try:
        report = advance(date.fromisoformat(argv[1]))
    except AdvanceError as exc:
        print(f"ABORT: {exc}")
        return 1
    was = ", ".join(f"{day} ({len(t)})" for day, t in report["ends_before"].items())
    p0, p1 = report["prices"]
    d0, d1 = report["dividends"]
    if p1 == p0 and d1 == d0:
        print(f"{DEMO_DB.name} already ends on {report['through']} for every ticker. "
              f"Nothing was written.")
        return 0
    for ticker, ex_date, amount in report["new_dividends"]:
        print(f"  dividend  {ticker:<8} {ex_date}  {amount}")
    print(f"prices:    {p0} -> {p1} rows (+{p1 - p0}); histories ended {was}")
    print(f"dividends: {d0} -> {d1} rows (+{d1 - d0})")
    print(f"every other table unchanged ({report['unchanged_tables']} tables)")
    print(f"Advanced {DEMO_DB.name} through {report['through']}: every ticker's history "
          f"ends there.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
