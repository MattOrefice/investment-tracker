"""Check the committed price histories: every ticker's ends on the same date, and none
lacks a close inside its own span.

ENDS (#406 item 8). The demo's IWB and IWF closes ended 2026-06-08 while IWM, IWD and the
holdings ran to 2026-07-20, and nothing said so except one panel's "ETF proxy as of"
caption. The quarterly close-out's snapshot advance brings every ticker through the
quarter's end; this confirms it did, so no ticker lags silently again.

A series that trades every day (DAILY) is compared on its newest WEEKDAY close. On a
quarter that ends on a weekend it holds a Saturday or Sunday row that no exchange-traded
fund can have, and that is not a lag.

GAPS (#491). The book lacked 33 closes BETWEEN two stored closes of their tickers (ten
funds on six sessions of May 2026, and BTC-USD on one day), and a quarter lock froze 32 of
them. An end date says nothing about what lies before it, so each ticker's whole span is
checked:
  * SPY is the session reference. An exchange-traded ticker must hold a close on every
    date SPY holds between its own first and last close, and none on a date SPY lacks;
  * a series that trades every day must hold a close on every calendar day between its
    first and last;
  * the reference must itself hold exactly the NYSE sessions of the years the holiday
    table covers (demo_refresh.NYSE_HOLIDAYS), so a session that every ticker lacks, SPY
    included, still shows there.

THE ENDS CHECK IS A DEMO CLOSE-OUT STEP. The snapshot advance fetches every ticker the
demo book prices, so there a ticker that ends early is a ticker the advance missed. The
personal book is refreshed by the pages' own reads, and a ticker no page reads is never
refreshed: ^GSPC is read only for the Forward P/E panel, which fetches nothing while no
estimate is on file, so its history stops where that panel last ran and the check failed
on it alone. So in personal mode this lists the end dates and does not judge them, and
its result is the gap check's. Its first line says which mode it ran in, on which book,
and which checks it applied.

Reads the mode's own book by default (data/demo.db in demo mode, the personal book
otherwise), read-only; pass a path to check another book. Prints each end date with the
tickers that end on it, then every gap. Exits 1 on a gap in either mode, and in demo
mode also unless the histories end on one date.

Usage:  python tools/check_price_histories.py [path/to/book.db]
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

# The rule itself lives in src.price_gaps, where the app runs it on the book it serves
# after every refresh (#501). This tool runs the same rule on a committed book by path.
from src.price_gaps import DAILY, MISSING, OFF_SESSION, REFERENCE, NoReference, gaps_in  # noqa: E402,F401


def _stored(db_path: Path) -> "dict[str, list[str]]":
    """{ticker: its stored price dates, ascending}."""
    con = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        out: "dict[str, list[str]]" = {}
        for ticker, day in con.execute(
                "SELECT ticker, price_date FROM prices ORDER BY ticker, price_date"):
            out.setdefault(ticker, []).append(day)
        return out
    finally:
        con.close()


def history_ends(db_path: Path) -> "dict[str, list[str]]":
    """{end date: [tickers]}: each ticker's newest weekday close in ``db_path``."""
    newest: "dict[str, str]" = {}
    for ticker, days in _stored(db_path).items():
        for day in days:
            if date.fromisoformat(day).weekday() < 5:
                newest[ticker] = day
    ends: "dict[str, list[str]]" = {}
    for ticker, day in sorted(newest.items()):
        ends.setdefault(day, []).append(ticker)
    return dict(sorted(ends.items()))


def history_gaps(db_path: Path) -> "list[tuple[str, str, str]]":
    """Every (ticker, ISO date, what) wrong inside a stored history in ``db_path``,
    ``what`` being MISSING or OFF_SESSION, by the rules in the module docstring
    (price_gaps.gaps_in). Raises ValueError when the book holds no close for the
    reference."""
    try:
        return gaps_in(_stored(db_path))
    except NoReference:
        raise ValueError(f"{Path(db_path).name} holds no {REFERENCE} closes, so no ticker's "
                         f"sessions can be checked.") from None


def main(argv: "list[str]") -> int:
    from src import config, db
    demo = config.IS_DEMO
    path = Path(argv[1]) if len(argv) > 1 else (DEMO_DB if demo else Path(db.DB_PATH))
    if demo:
        print(f"Demo mode, {path.name}: end dates and gaps.")
    else:
        print(f"Personal mode, {path.name}: gaps only. The end dates are listed and not "
              f"judged: the alignment check is a demo close-out step.")
    ends = history_ends(path)
    for day, tickers in ends.items():
        print(f"{day}: {len(tickers)} ticker(s): {', '.join(tickers)}")
    try:
        gaps = history_gaps(path)
    except ValueError as exc:
        print(f"NO REFERENCE: {exc}")
        return 1
    for what in (MISSING, OFF_SESSION):
        by_day: "dict[str, list[str]]" = {}
        for ticker, day, kind in gaps:
            if kind == what:
                by_day.setdefault(day, []).append(ticker)
        for day, tickers in sorted(by_day.items()):
            print(f"{day}: {what} for {len(tickers)} ticker(s): {', '.join(tickers)}")
    aligned = len(ends) == 1
    if aligned:
        print(f"Every ticker's history in {path.name} ends on {next(iter(ends))}.")
    elif demo:
        print(f"NOT ALIGNED: {path.name}'s price histories end on {len(ends)} different "
              f"dates. Every ticker before {max(ends)} lags.")
    else:
        print(f"{path.name}'s price histories end on {len(ends)} different dates. Not "
              f"judged in personal mode: a ticker no page reads is not refreshed.")
    if gaps:
        print(f"GAPS: {path.name} holds {len(gaps)} gap(s) inside its tickers' histories, "
              f"listed above. No lock is taken from a book with a gap.")
    else:
        print(f"No ticker in {path.name} lacks a close inside its history.")
    return 0 if (aligned or not demo) and not gaps else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
