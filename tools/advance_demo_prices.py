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
  * no price or dividend row the book already holds differs in the copy, except a row a
    split explains: the fetch recorded the split as applied to the copy's history
    (src.prices._apply_split), the row is dated before its ex-date, and it is the book's
    row divided by the ratio. Those rows are written too, with the split records, so
    the book's history before a split moves to the new scale once, as the fetch did;
  * every new price row is after its ticker's last stored close and on or before the
    target, with a positive close (adj_close NULL: the book stores raw closes, #304);
  * every exchange-traded ticker gains exactly the NYSE sessions in its span, and a
    series that trades every day gains every day: no hole a later read would carry
    forward;
  * so every ticker ends on the target, which tools/check_price_histories.py confirms;
  * every new dividend's ex-date is after its ticker's last stored close;
  * afterwards only `prices` and `dividends` differ from the book as it was (and
    `splits`, when the fetch recorded one), by exactly the delta's row counts, and every
    row it held is still there unchanged but for the rows a split explains.

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


def _splits(path: Path) -> "list[tuple]":
    """Every split a book has recorded, as (ticker, ex_date, ratio, state, applied_on);
    [] when it has no splits table."""
    con = _ro(path)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'splits'").fetchone() is None:
            return []
        return con.execute("SELECT ticker, ex_date, ratio, state, applied_on FROM splits "
                           "ORDER BY ticker, ex_date").fetchall()
    finally:
        con.close()


def _split_factor(rescaled: "dict[str, list[tuple[str, float]]]", ticker: str,
                  day: str) -> float:
    """What the splits applied to ``ticker`` divided a row dated ``day`` by: the product
    of the ratios of those that went ex after it (1.0 for none)."""
    factor = 1.0
    for ex_date, ratio in rescaled.get(ticker, []):
        if day < ex_date:
            factor *= ratio
    return factor


def _explained(old, new, factor: float) -> bool:
    if factor == 1.0 or old is None or new is None:
        return False
    return abs(new - old / factor) <= 1e-9 * abs(old / factor)


def _delta(book: Path, work: Path, through: str):
    """(new price rows, new dividend rows, existing rows the copy changed that no split
    explains)."""
    new_prices, new_divs, changed, _by_split = _delta_and_splits(book, work, through)
    return new_prices, new_divs, changed


def _delta_and_splits(book: Path, work: Path, through: str):
    """_delta, and the existing rows a split explains, as (table, ticker, key, new value,
    new adj_close)."""
    from src.prices import RESCALED
    rescaled: "dict[str, list[tuple[str, float]]]" = {}
    for ticker, ex_date, ratio, state, _on in _splits(work):
        if state == RESCALED:
            rescaled.setdefault(ticker, []).append((ex_date, float(ratio)))
    con = _ro(work)
    try:
        con.execute("ATTACH DATABASE ? AS book", (f"file:{Path(book).resolve().as_posix()}?mode=ro",))
        moved = con.execute(
            "SELECT 'prices', b.ticker, b.price_date, b.close, w.close, b.adj_close, w.adj_close "
            "FROM book.prices b LEFT JOIN main.prices w "
            "ON w.ticker = b.ticker AND w.price_date = b.price_date "
            "WHERE w.ticker IS NULL OR w.close IS NOT b.close OR w.adj_close IS NOT b.adj_close "
            "UNION ALL "
            "SELECT 'dividends', b.ticker, b.ex_date, b.amount, w.amount, NULL, NULL "
            "FROM book.dividends b LEFT JOIN "
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
    changed, by_split = [], []
    for table, ticker, key, old, new, old_adj, new_adj in moved:
        factor = _split_factor(rescaled, ticker, key)
        if _explained(old, new, factor) and (
                (old_adj is None and new_adj is None) or _explained(old_adj, new_adj, factor)):
            by_split.append((table, ticker, key, new, new_adj))
        else:
            changed.append((table, ticker, key))
    return new_prices, new_divs, changed, by_split


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
        new_prices, new_divs, changed, by_split = _delta_and_splits(DEMO_DB, work, target)
        _check(ends, target, new_prices, new_divs, changed)
        held = _splits(DEMO_DB)
        splits = [row for row in _splits(work) if row not in held]

        con = sqlite3.connect(DEMO_DB)
        try:
            with con:
                if splits:
                    from src.prices import SPLITS_DDL
                    con.execute(SPLITS_DDL)
                    con.executemany("INSERT OR REPLACE INTO splits (ticker, ex_date, ratio, "
                                    "state, applied_on) VALUES (?, ?, ?, ?, ?)", splits)
                con.executemany("UPDATE prices SET close = ?, adj_close = ? WHERE ticker = ? "
                                "AND price_date = ?",
                                [(new, adj, t, k) for table, t, k, new, adj in by_split
                                 if table == "prices"])
                con.executemany("UPDATE dividends SET amount = ? WHERE ticker = ? AND "
                                "ex_date = ?",
                                [(new, t, k) for table, t, k, new, _adj in by_split
                                 if table == "dividends"])
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
        added = set(after) - set(before)
        if set(before) - set(after) or added - ({"splits"} if splits else set()):
            wrong.append(f"the table list changed: {sorted(set(after) ^ set(before))}")
        moved = sorted(t for t in before if after.get(t) != before[t])
        if set(moved) - {"prices", "dividends", "splits"}:
            wrong.append(f"tables other than prices, dividends and splits changed: {moved}")
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
    from src.demo_refresh import holiday_table_refusal
    refusal = holiday_table_refusal()
    if refusal:
        print(f"ABORT: {refusal}")
        return 1
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
