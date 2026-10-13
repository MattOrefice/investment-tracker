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

Reads data/demo.db by default, read-only; pass a path to check another book. Prints each
end date with the tickers that end on it, then every gap, and exits 1 unless the
histories end on one date and hold no gap.

Usage:  python tools/check_price_histories.py [path/to/book.db]
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

# The session reference: an exchange-traded ticker's sessions are the dates it holds.
REFERENCE = "SPY"
# Series that trade every calendar day. Every other ticker trades NYSE sessions.
DAILY = frozenset({"BTC-USD"})

MISSING = "no close"
OFF_SESSION = "a close on a date that is not a session"


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


def _days(first: str, last: str):
    day, end = date.fromisoformat(first), date.fromisoformat(last)
    while day <= end:
        yield day
        day += timedelta(days=1)


def history_gaps(db_path: Path) -> "list[tuple[str, str, str]]":
    """Every (ticker, ISO date, what) wrong inside a stored history, ``what`` being
    MISSING or OFF_SESSION, by the rules in the module docstring. Raises ValueError when
    the book holds no close for the reference."""
    from src.demo_refresh import NYSE_HOLIDAYS, is_session

    stored = _stored(db_path)
    if REFERENCE not in stored:
        raise ValueError(f"{Path(db_path).name} holds no {REFERENCE} closes, so no ticker's "
                         f"sessions can be checked.")
    sessions = stored[REFERENCE]
    session_set = set(sessions)
    found = []
    # The reference against the calendar, where the calendar is known.
    known = {d.year for d in NYSE_HOLIDAYS}
    for day in _days(sessions[0], sessions[-1]):
        if day.year in known and is_session(day) != (day.isoformat() in session_set):
            found.append((REFERENCE, day.isoformat(),
                          MISSING if is_session(day) else OFF_SESSION))
    for ticker, days in stored.items():
        held = set(days)
        if ticker in DAILY:
            found += [(ticker, d.isoformat(), MISSING) for d in _days(days[0], days[-1])
                      if d.isoformat() not in held]
        elif ticker != REFERENCE:
            found += [(ticker, d, MISSING) for d in sessions
                      if days[0] <= d <= days[-1] and d not in held]
            found += [(ticker, d, OFF_SESSION) for d in days if d not in session_set]
    return sorted(found)


def main(argv: "list[str]") -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEMO_DB
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
    else:
        print(f"NOT ALIGNED: {path.name}'s price histories end on {len(ends)} different "
              f"dates. Every ticker before {max(ends)} lags.")
    if gaps:
        print(f"GAPS: {path.name} holds {len(gaps)} gap(s) inside its tickers' histories, "
              f"listed above. No lock is taken from a book with a gap.")
    else:
        print(f"No ticker in {path.name} lacks a close inside its history.")
    return 0 if aligned and not gaps else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
