"""Closes a book lacks inside its tickers' histories: the check, and the re-ask.

THE CHECK (#491). A book can end on the right date and still lack a close between two
it stores: the demo book lacked 33, and a quarter lock froze 32 of them. An end date
says nothing about what lies before it, so each ticker's whole span is checked:
  * SPY is the session reference. An exchange-traded ticker must hold a close on every
    date SPY holds between its own first and last close, and none on a date SPY lacks;
  * a series that trades every day (DAILY) must hold a close on every calendar day
    between its first and last;
  * the reference must itself hold exactly the NYSE sessions of the years the holiday
    table covers (demo_refresh.NYSE_HOLIDAYS), so a session that every ticker lacks, SPY
    included, still shows there.
tools/check_price_histories.py runs it on a committed book by path; the app runs it on
the book it serves (book_gaps).

THE RE-ASK (#501). A provider response that lacks one session's close and holds later
settled ones leaves such a gap, and every later fetch starts after the ticker's newest
stored date, so nothing asked for that session again. Every refresh now does:
  * the demo, after its daily fetch (demo_refresh._attempt);
  * personal mode, after each page load whose reads fetched (app.py calls
    reask_after_refresh once the page has run: there a book refreshes through the pages'
    own reads, prices.get_prices fetching past the newest stored close).
The check runs on the book, and each close it names is fetched again through
prices.fetch_prices, which stores those closes and nothing else: no stored row changes.
A close the provider still lacks stays named by the check, and by a page wherever a
lock would cover it (cache.quarter_lock_gaps), and is asked for again after the next
refresh. The refresh itself is unchanged: a ticker's later closes keep arriving.

In tests the re-ask never runs: conftest sets PRICE_GAP_REASK=0, and its own tests turn
it on.
"""
from __future__ import annotations

import os
import threading
from datetime import date, timedelta
from typing import NamedTuple, Optional

# The session reference: an exchange-traded ticker's sessions are the dates it holds.
REFERENCE = "SPY"
# Series that trade every calendar day. Every other ticker trades NYSE sessions.
DAILY = frozenset({"BTC-USD"})

MISSING = "no close"
OFF_SESSION = "a close on a date that is not a session"


class NoReference(ValueError):
    """The book holds no close for the reference, so no ticker's sessions can be checked."""


def _days(first: str, last: str):
    day, end = date.fromisoformat(first), date.fromisoformat(last)
    while day <= end:
        yield day
        day += timedelta(days=1)


def gaps_in(stored: "dict[str, list[str]]") -> "list[tuple[str, str, str]]":
    """Every (ticker, ISO date, what) wrong inside a stored history, ``what`` being
    MISSING or OFF_SESSION, by the rules in the module docstring. ``stored`` is
    {ticker: its stored price dates, ascending}. Raises NoReference when it holds no
    close for the reference."""
    from src.demo_refresh import NYSE_HOLIDAYS, is_session

    if REFERENCE not in stored:
        raise NoReference(f"no {REFERENCE} closes, so no ticker's sessions can be checked.")
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


def stored_dates() -> "dict[str, list[str]]":
    """{ticker: its stored price dates, ascending} in the book the app serves: the
    personal book, or the demo's runtime cache over its snapshot. DB only."""
    from src.db import get_connection
    out: "dict[str, list[str]]" = {}
    with get_connection() as conn:
        for ticker, day in conn.execute(
                "SELECT ticker, price_date FROM prices ORDER BY ticker, price_date"):
            out.setdefault(ticker, []).append(day)
    return out


def book_gaps() -> "list[tuple[str, str, str]]":
    """gaps_in for the book the app serves."""
    return gaps_in(stored_dates())


class Reask(NamedTuple):
    """One re-ask: what the check named, and what came of asking."""

    named: tuple = ()        # (ticker, ISO date): each close the book lacked before asking
    filled: tuple = ()       # those the provider supplied this time, now stored
    failed: tuple = ()       # (ticker, reason) for each request that raised
    unchecked: Optional[str] = None      # why the check could not run, or None

    @property
    def left(self) -> tuple:
        """The closes still lacking: named, asked for, and not supplied."""
        got = set(self.filled)
        return tuple(c for c in self.named if c not in got)


def enabled() -> bool:
    """False in tests (PRICE_GAP_REASK=0), which read fixed books."""
    return os.environ.get("PRICE_GAP_REASK", "1") != "0"


def reask() -> Reask:
    """Run the check on the book the app serves and fetch every close it names.

    One request per ticker, from its first missing close to its last, through the price
    layer's own fetch. It stores a close only on a date the check named and only where
    the book holds none (prices.fetch_prices, ``only_missing``), so no stored row
    changes whatever the response holds. Never raises: a request that fails is recorded,
    and its closes stay named for the next refresh."""
    from src import prices
    try:
        found = book_gaps()
    except NoReference as exc:
        return Reask(unchecked=str(exc))
    except Exception as exc:                                     # noqa: BLE001
        return Reask(unchecked=f"{type(exc).__name__}: {exc}"[:160])
    by_ticker: "dict[str, list[str]]" = {}
    for ticker, day, what in found:
        if what == MISSING:
            by_ticker.setdefault(ticker, []).append(day)
    filled, failed = [], []
    for ticker, days in sorted(by_ticker.items()):
        try:
            frame = prices.fetch_prices(ticker, days[0], days[-1], only_missing=days)
            filled += [(ticker, d) for d in frame.attrs.get("stored", ())]
        except Exception as exc:                                 # noqa: BLE001
            failed.append((ticker, f"{type(exc).__name__}: {exc}"[:160]))
    named = tuple((t, d) for t, days in sorted(by_ticker.items()) for d in days)
    return Reask(named=named, filled=tuple(filled), failed=tuple(failed))


# Personal mode's half. The fetch count (prices.fetch_count) at this process's last
# re-ask, and that re-ask.
_SEEN = 0
_LAST: Optional[Reask] = None
_LOCK = threading.Lock()


def last() -> Optional[Reask]:
    """The latest re-ask reask_after_refresh ran in this process, or None."""
    return _LAST


def reask_after_refresh() -> Optional[Reask]:
    """Re-ask if the book was refreshed since the last re-ask; else do nothing.

    Personal mode has no scheduled refresh: a page's reads fetch what the book lacks
    past its newest stored close. So app.py calls this after each page has run, and a
    load during which the provider answered at least one fetch is a refresh. A load
    that fetched nothing costs one integer comparison. The re-ask's own fetches are
    not a refresh. Returns the re-ask it ran, or None."""
    global _SEEN, _LAST
    if not enabled():
        return None
    from src import prices
    with _LOCK:
        if prices.fetch_count() == _SEEN:
            return None
        _LAST = reask()
        _SEEN = prices.fetch_count()
        return _LAST
