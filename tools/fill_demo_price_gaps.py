"""Fill the closes the committed books lack inside their own price histories (#491).

data/demo.db held no close on six NYSE sessions of May 2026 for ten of the funds it
holds, and none for BTC-USD on July 8, 2026: 33 closes, each between two stored closes
of its ticker. tests/fixtures/frozen_book.db, built from that book, lacked the same 33.
Nothing re-reads a date between two stored closes: get_prices fetches before the first
and after the last, and the snapshot advance starts after the last. A missing close is
carried forward on read, and a dividend just after it is scaled against the close before
the hole.

This fills exactly those 33 from the provider, in both books, and changes no row either
holds (the #283 data-path shape):
  * every ticker's whole stored span is fetched into a scratch copy emptied of its prices
    and dividends first, through the price layer's own fetch (src.prices.fetch_prices).
    The copy then holds what the provider reports and nothing else, so a row the provider
    no longer reports shows as missing;
  * the delta is read off the copy, and nothing is written unless it passes every check
    below. Then the 33 rows go into each book in one transaction, as plain INSERTs, and
    the result is asserted per table against the book as it was. A failed assertion puts
    both books back.

The checks:
  * the closes the provider reports and the book lacks, inside each ticker's stored span,
    are exactly the 33 named here, each positive;
  * the provider reports every close the book holds, and none differs by more than half
    a cent. Those that differ by less stay as they are: they are listed, never written;
  * the provider's dividends are the book's, row for row;
  * every price row the frozen book holds is demo.db's, and it lacks the same 33;
  * afterwards only `prices` differs in each book, by 33 rows, and every row it held is
    still there unchanged.

A second run finds the closes there and writes nothing. Targets the two books explicitly
(never get_db_path). main() only, so bootstrap never runs it. The quarter lock that froze
32 of these gaps is restated by tools/restate_demo_filled_closes.py.

Usage:  python tools/fill_demo_price_gaps.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
FROZEN_BOOK = ROOT / "tests" / "fixtures" / "frozen_book.db"
sys.path.insert(0, str(ROOT))

# The closes the books lacked: (tickers, dates), every pairing of the two.
GAPS = (
    (("IEMG", "PDBC", "VNQ"),
     ("2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15")),
    (("AVUV", "IEMG", "PDBC", "SCHP", "SPHQ", "VEA", "VGIT", "VNQ", "VOO", "VTV"),
     ("2026-05-28", "2026-05-29")),
    (("BTC-USD",), ("2026-07-08",)),
)
CELLS = tuple(sorted((t, d) for tickers, days in GAPS for t in tickers for d in days))
# A stored close may differ from the provider's by this much and stay as it is: nine did,
# by half a cent or less (#491). A larger difference refuses the fill.
TOLERANCE = 0.005
# Between requests, so 47 of them do not draw a 429.
PAUSE_SECONDS = 0.25


class FillError(RuntimeError):
    """The fill was refused, or failed a check. Both books are as they were."""


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)


def _closes(path: Path) -> "dict[tuple[str, str], float]":
    con = _ro(path)
    try:
        return {(t, d): c for t, d, c in con.execute(
            "SELECT ticker, price_date, close FROM prices")}
    finally:
        con.close()


def _dividends(path: Path) -> "dict[tuple[str, str], float]":
    con = _ro(path)
    try:
        return {(t, d): a for t, d, a in con.execute(
            "SELECT ticker, ex_date, amount FROM dividends")}
    finally:
        con.close()


def _spans(closes: dict) -> "dict[str, tuple[str, str]]":
    """{ticker: (first stored date, last stored date)}."""
    out: "dict[str, tuple[str, str]]" = {}
    for ticker, day in closes:
        lo, hi = out.get(ticker, (day, day))
        out[ticker] = (min(lo, day), max(hi, day))
    return out


def _provider_view(book: Path, spans: dict):
    """(closes, dividends, failed fetches) as the provider reports them today for each
    ticker's stored span, read off a scratch copy of ``book`` emptied of both tables."""
    import src.db as db
    import src.prices as prices
    scratch = Path(tempfile.mkdtemp(prefix="fill_demo_"))
    try:
        work = scratch / "work.db"
        shutil.copyfile(book, work)
        os.chmod(work, 0o644)
        con = sqlite3.connect(work)
        try:
            with con:
                con.execute("DELETE FROM prices")
                con.execute("DELETE FROM dividends")
        finally:
            con.close()
        if _closes(work) or _dividends(work):
            raise FillError("the scratch copy was not emptied before the fetch.")
        saved = (db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE)
        db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = work, set(), None
        failed = []
        try:
            for ticker, (first, last) in sorted(spans.items()):
                try:
                    prices.fetch_prices(ticker, first, last)
                except Exception as exc:                             # noqa: BLE001
                    failed.append((ticker, f"{type(exc).__name__}: {exc}"[:200]))
                time.sleep(PAUSE_SECONDS)
        finally:
            db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = saved
        return _closes(work), _dividends(work), failed
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _check(held: dict, held_divs: dict, got: dict, got_divs: dict,
           frozen: dict) -> "list[tuple[str, str, float, float]]":
    """The stored closes the provider reports differently, each within TOLERANCE:
    (ticker, date, stored, provider). Raises FillError unless the delta is exactly the
    33 named closes and nothing else is wrong."""
    problems = []
    spans = _spans(held)
    new = sorted(k for k in got if k not in held and k[0] in spans
                 and spans[k[0]][0] <= k[1] <= spans[k[0]][1])
    missing, unexpected = sorted(set(CELLS) - set(new)), sorted(set(new) - set(CELLS))
    if missing or unexpected:
        problems.append(f"the closes the provider reports and the book lacks are not the "
                        f"{len(CELLS)} named: not reported {missing[:5]}, not named "
                        f"{unexpected[:5]} ({len(missing)} and {len(unexpected)})")
    for key in new:
        if not got[key] or got[key] <= 0:
            problems.append(f"{key[0]} {key[1]}: close {got[key]!r} is not positive")
    gone = sorted(k for k in held if k not in got)
    if gone:
        problems.append(f"the provider no longer reports {len(gone)} close(s) the book "
                        f"holds, e.g. {gone[:3]}")
    differing = sorted((t, d, held[(t, d)], got[(t, d)]) for (t, d) in held
                       if (t, d) in got and got[(t, d)] != held[(t, d)])
    over = [r for r in differing if abs(r[3] - r[2]) > TOLERANCE]
    if over:
        problems.append(f"{len(over)} stored close(s) differ from the provider's by more "
                        f"than {TOLERANCE}, e.g. {over[:3]}")
    if got_divs != held_divs:
        added = sorted(k for k in got_divs if k not in held_divs)
        lost = sorted(k for k in held_divs if k not in got_divs)
        moved = sorted(k for k in held_divs if k in got_divs and got_divs[k] != held_divs[k])
        problems.append(f"the provider's dividends are not the book's: {len(added)} added "
                        f"{added[:3]}, {len(lost)} gone {lost[:3]}, {len(moved)} changed "
                        f"{moved[:3]}")
    stray = sorted(k for k, c in frozen.items() if held.get(k) != c)
    if stray:
        problems.append(f"{FROZEN_BOOK.name} holds {len(stray)} price row(s) that are not "
                        f"{DEMO_DB.name}'s, e.g. {stray[:3]}")
    f_spans = _spans(frozen)
    outside = [c for c in CELLS if c[0] not in f_spans
               or not f_spans[c[0]][0] <= c[1] <= f_spans[c[0]][1]]
    if outside:
        problems.append(f"{len(outside)} of the named closes fall outside "
                        f"{FROZEN_BOOK.name}'s histories, e.g. {outside[:3]}")
    if problems:
        raise FillError("the fetched delta failed its checks, nothing was written:\n  "
                        + "\n  ".join(problems))
    return differing


def _wrong_after(book: Path, image: Path, before: dict, rows: list) -> "list[str]":
    """What is wrong with ``book`` after the write, against its image from before."""
    from tools.fingerprint_db import fingerprint
    after = fingerprint(book)
    wrong = []
    if set(after) != set(before):
        wrong.append(f"the table list changed: {sorted(set(after) ^ set(before))}")
    moved = sorted(t for t in before if after.get(t) != before[t])
    if moved != ["prices"]:
        wrong.append(f"the tables that changed are {moved}, not prices alone")
    if after["prices"][0] != before["prices"][0] + len(rows):
        wrong.append(f"prices rows {before['prices'][0]} -> {after['prices'][0]}, "
                     f"expected +{len(rows)}")
    was, now = _closes(image), _closes(book)
    lost = sorted(k for k in was if now.get(k) != was[k])
    if lost:
        wrong.append(f"{len(lost)} close(s) the book held are gone or changed: {lost[:3]}")
    if {k: c for k, c in now.items() if k not in was} != {(t, d): c for t, d, c in rows}:
        wrong.append("the closes the book gained are not the ones written")
    con = _ro(book)
    try:
        stored_adj = con.execute("SELECT COUNT(adj_close) FROM prices").fetchone()[0]
    finally:
        con.close()
    if stored_adj:
        wrong.append(f"{stored_adj} price row(s) store an adjustment (#304: raw closes only)")
    return wrong


def fill() -> dict:
    """Fill the 33 closes in DEMO_DB and FROZEN_BOOK. Returns what it did."""
    from tools.fingerprint_db import fingerprint

    books = (DEMO_DB, FROZEN_BOOK)
    held, frozen = _closes(DEMO_DB), _closes(FROZEN_BOOK)
    have = {b.name: sum(1 for c in CELLS if c in rows)
            for b, rows in ((DEMO_DB, held), (FROZEN_BOOK, frozen))}
    report = {"cells": len(CELLS), "written": {b.name: 0 for b in books}, "kept": [],
              "prices": {}, "unchanged_tables": {}}
    if all(n == len(CELLS) for n in have.values()):
        return report                                    # already filled: nothing to write
    if any(have.values()):
        raise FillError(f"the books already hold some of the {len(CELLS)} closes "
                        f"({have}): neither the state this fills nor the one it leaves.")

    held_divs = _dividends(DEMO_DB)
    got, got_divs, failed = _provider_view(DEMO_DB, _spans(held))
    if failed:
        raise FillError("the fetch failed for " + "; ".join(f"{t} ({why})" for t, why in failed)
                        + ". Nothing was written.")
    report["kept"] = _check(held, held_divs, got, got_divs, frozen)
    rows = [(t, d, got[(t, d)]) for t, d in CELLS]

    scratch = Path(tempfile.mkdtemp(prefix="fill_demo_"))
    try:
        images = {b: scratch / f"before_{b.name}" for b in books}
        before = {}
        for b in books:
            shutil.copyfile(b, images[b])
            before[b] = fingerprint(b)
        try:
            for b in books:
                con = sqlite3.connect(b)
                try:
                    with con:
                        con.executemany("INSERT INTO prices (ticker, price_date, close, "
                                        "adj_close) VALUES (?, ?, ?, NULL)", rows)
                finally:
                    con.close()
            wrong = [f"{b.name}: {w}" for b in books
                     for w in _wrong_after(b, images[b], before[b], rows)]
        except Exception as exc:                                     # noqa: BLE001
            wrong = [f"the write raised {type(exc).__name__}: {exc}"]
        if wrong:
            for b in books:
                shutil.copyfile(images[b], b)
            raise FillError("the written result failed its assertions and both books were "
                            "put back:\n  " + "\n  ".join(wrong))
        for b in books:
            report["written"][b.name] = len(rows)
            report["prices"][b.name] = (before[b]["prices"][0], before[b]["prices"][0] + len(rows))
            report["unchanged_tables"][b.name] = len(before[b]) - 1
        return report
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    try:
        report = fill()
    except FillError as exc:
        print(f"ABORT: {exc}")
        return 1
    if not any(report["written"].values()):
        print(f"{DEMO_DB.name} and {FROZEN_BOOK.name} already hold all {report['cells']} "
              f"closes. Nothing was written.")
        return 0
    for ticker, day, stored, provider in report["kept"]:
        print(f"  kept      {ticker:<8} {day}  stored {stored!r}, provider {provider!r} "
              f"({abs(provider - stored):.4f})")
    print(f"{len(report['kept'])} stored close(s) differ from the provider's by "
          f"{TOLERANCE} or less and stay as they are.")
    for name, (p0, p1) in report["prices"].items():
        print(f"{name}: prices {p0} -> {p1} rows (+{p1 - p0}); every other table unchanged "
              f"({report['unchanged_tables'][name]} tables)")
    print(f"Filled {report['cells']} closes in {DEMO_DB.name} and {FROZEN_BOOK.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
