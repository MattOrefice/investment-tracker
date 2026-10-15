"""The public demo's daily price refresh (#368 item 3).

The demo serves a committed snapshot (demo.db) whose prices end on a fixed date. In
demo mode app.py calls refresh_if_due() at startup, before the landing page reads its
date. Every ticker the demo holds prices for is fetched from its last stored date to
the latest settled close, into the runtime cache (src.db, #368 item 2), and served
over the snapshot. demo.db is never written.

WHEN. After a success, the next attempt waits for the next New York close plus a
margin for the provider to publish it: no newer settled close can exist before then,
and a container that stays up still moves daily. After a failure, the next attempt
waits RETRY_AFTER. Between attempts nothing fetches, because the refresh switches off
the price layer's gap fills (prices.set_gap_fetch): a failed fetch is retried on a
timer, never on every render, which would slow every page and invite throttling.

SETTLED CLOSES ONLY. fetch_prices never caches a bar whose session is still open
(prices.unsettled_bar_date), so a mid-session attempt stores through the previous
close and the next attempt picks up the rest.

AND THE CLOSES IT LACKS. A response that leaves one session out and holds later ones
puts a gap inside a history, behind the date the next fetch starts from. After the
fetch, each attempt runs the gap check on the cache and asks again for every close it
names (src.price_gaps, #501).

What the banner reports (asof.as_of_live_line) comes from state(): the date served
and its basis, and, when the fetch failed, that it failed, what is served instead,
and when it retries. In tests the refresh never runs: conftest sets
DEMO_DAILY_FETCH=0, and tests keep reading fixed data.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
# A failed attempt is retried after this long, never sooner.
RETRY_AFTER = timedelta(minutes=30)
# The provider publishes a session's close a little after 16:00 New York.
_PUBLISH_MARGIN = timedelta(minutes=30)
# Four requests at a time: the whole set in a few seconds rather than half a minute,
# without the burst that draws a 429. Their stores take turns (prices._STORE_LOCK, #580).
_WORKERS = 4


class RefreshState(NamedTuple):
    """One attempt's outcome, as the banner reports it."""

    status: str                            # "fetched" | "failed"
    attempted_at: datetime                 # UTC
    next_attempt_at: datetime              # UTC: nothing fetches before this
    served_through: Optional[str]          # committed_price_frontier after the attempt
    fetched_before: Optional[str]          # a previous success on this seed, ISO UTC, or None
    failed: tuple = ()                     # (ticker, reason) for each fetch that raised
    gaps: tuple = ()                       # (ticker, ISO date): closes still lacking inside a
                                           # history after they were asked for again (#501)


_STATE: Optional[RefreshState] = None
_LOCK = threading.Lock()


def _forget_last_attempt() -> None:
    """A re-seed dropped every fetched row with the seed it was fetched into (src.db,
    #480), so the next refresh_if_due fetches again, as a restarted process would,
    instead of waiting out the last attempt's timer on the committed snapshot."""
    global _STATE
    _STATE = None


def enabled() -> bool:
    """False in tests (DEMO_DAILY_FETCH=0), or before the runtime cache is in use:
    fetching into demo.db itself is the one thing this must never do."""
    import src.db as db
    return os.environ.get("DEMO_DAILY_FETCH", "1") != "0" and db._RUNTIME_CACHE is not None


def state() -> Optional[RefreshState]:
    """The latest attempt in this process, or None if none has run."""
    return _STATE


# NYSE full-day closures. The scheduled ones for 2025 as NYSE Group's 2025-2027 holiday
# calendar listed them (announced November 11, 2024), and for 2026 to 2028 as
# nyse.com/markets/hours-calendars listed them on 2026-09-26 (observed dates where a
# holiday falls on a weekend; no New Year's closure in 2028, when January 1 is a
# Saturday). Static, so no calendar dependency. Early closes are sessions. Outside
# these years every weekday counts as a session, the rule before this table, so the
# close-out refuses to run (holiday_table_refusal), and tests/test_market_holidays.py
# fails, once the table does not reach the end of next year: extend it from the same
# page before then (#466).
NYSE_HOLIDAY_YEARS = (2025, 2028)
_SCHEDULED_CLOSURES = frozenset(date.fromisoformat(d) for d in (
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
    "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
))
# Closures no calendar schedules, each entered by hand from NYSE's notice. Nothing
# falls back when an expected session never trades: a lock waits on the close of its
# quarter's last session until the closure is entered here (#466). January 9, 2025:
# the national day of mourning for President Carter (NYSE Group's notice of December
# 30, 2024).
NYSE_UNSCHEDULED_CLOSURES = frozenset({date(2025, 1, 9)})
NYSE_HOLIDAYS = _SCHEDULED_CLOSURES | NYSE_UNSCHEDULED_CLOSURES


def holiday_table_refusal(today: "date | None" = None) -> "str | None":
    """Why the quarterly close-out may not run, or None: the NYSE holiday table must
    reach the end of next year (#466, decided by the owner on September 28, 2026).

    A lock waits on the close of its quarter's last NYSE session
    (cache.last_session_on_or_before), and past the table every weekday counts as a
    session. So a closure the table does not hold is a session that never trades, and
    a quarter ending there would wait indefinitely: March 31, 2029 is a Saturday and the
    Friday before it is Good Friday. ``today`` is New York's date by default."""
    from src.asof import today_et
    needed = (today or today_et()).year + 1
    last = NYSE_HOLIDAY_YEARS[1]
    if last >= needed:
        return None
    return (f"the NYSE holiday table in src/demo_refresh.py ends with {last}, and the "
            f"close-out needs it through December 31, {needed}. Extend it from "
            f"nyse.com/markets/hours-calendars, and enter any unscheduled closure by "
            f"hand from NYSE's notice.")


def is_session(d: date) -> bool:
    """Whether New York holds a session on ``d``: a weekday that is not an NYSE
    holiday (NYSE_HOLIDAYS). The banner's freshness count (asof.as_of_live_line) uses
    the same calendar. Before the table, a market holiday counted as a session, so the
    day after one read a current frontier as a close behind (2026-09-25 audit, item 12).
    """
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def next_close_after(t: datetime) -> datetime:
    """The next New York session's close (plus the publish margin) after ``t``, in
    UTC. Holidays in NYSE_HOLIDAYS are skipped."""
    ny = t.astimezone(_NY)
    c = ny.replace(hour=16, minute=0, second=0, microsecond=0) + _PUBLISH_MARGIN
    if c <= ny:
        c += timedelta(days=1)
    while not is_session(c.date()):
        c += timedelta(days=1)
    return c.astimezone(timezone.utc)


def _frontier_tickers() -> "set[str]":
    """The tickers the banner's frontier is taken over: the portfolio's holdings, with
    SPAXX priced off BIL, as committed_price_frontier reads them."""
    from src.holdings import get_holdings_on_date, get_portfolio_account_id
    try:
        held = get_holdings_on_date(date.today().isoformat(),
                                    account_id=get_portfolio_account_id())
    except Exception:                                            # noqa: BLE001
        return set()
    return {"BIL" if t == "SPAXX" else t for t in held.index}


def _fetch_one(ticker: str, last: str, today: str) -> "Optional[tuple[str, str]]":
    """Fetch from the last stored date (inclusive, so the window always holds a
    bar) through today. None on success, else (ticker, reason)."""
    from src import prices
    try:
        prices.fetch_prices(ticker, last, today)
        return None
    except Exception as exc:                                     # noqa: BLE001
        return ticker, f"{type(exc).__name__}: {exc}"[:160]


def _attempt(now: datetime) -> RefreshState:
    import src.db as db
    from src import prices
    from src.holdings import committed_price_frontier

    today = now.astimezone(_NY).date().isoformat()
    with db.get_connection() as conn:
        tickers = conn.execute(
            "SELECT ticker, MAX(price_date) FROM prices GROUP BY ticker").fetchall()
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(lambda r: _fetch_one(r[0], r[1], today), tickers))
    failed = tuple(sorted(r for r in results if r is not None))

    # Then ask again for every close the cache lacks inside a ticker's history (#501).
    # Each fetch above began at its ticker's last stored date, so a session the
    # provider left out of an earlier response was never asked for again. What it
    # still lacks stays named (state().gaps, and cache.quarter_lock_gaps for a ticker
    # a lock covers) and is asked for at the next attempt. It does not make the fetch
    # a failure: the later closes arrived, and the banner's date is theirs.
    from src import price_gaps
    gaps = price_gaps.reask().left if price_gaps.enabled() else ()

    # A failure the banner must report: every ticker failed (the provider is out of
    # reach), or one the frontier is taken over did. A lone non-holding ticker that
    # failed is recorded and retried at the next close, without calling the whole
    # fetch failed.
    frontier_set = _frontier_tickers()
    failed_names = {t for t, _ in failed}
    ok = not (failed and (len(failed) == len(tickers) or failed_names & frontier_set))

    before = db.runtime_meta("last_fetch_success")
    served = committed_price_frontier(today)
    if ok:
        db.set_runtime_meta("last_fetch_success", now.isoformat(timespec="minutes"))
    return RefreshState(
        status="fetched" if ok else "failed",
        attempted_at=now,
        next_attempt_at=next_close_after(now) if ok else now + RETRY_AFTER,
        served_through=served,
        fetched_before=before,
        failed=failed,
        gaps=gaps,
    )


def refresh_if_due(now: Optional[datetime] = None) -> Optional[RefreshState]:
    """Fetch if the last attempt's wait has passed; return the current state.

    Once running, the price layer's own gap fills are off for the rest of the process:
    this is the only thing that fetches on a schedule."""
    global _STATE
    if not enabled():
        return _STATE
    import src.db as db
    from src import prices
    db.after_reseed(_forget_last_attempt)
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        prices.set_gap_fetch(False)
        if _STATE is not None and now < _STATE.next_attempt_at:
            return _STATE
        _STATE = _attempt(now)
        return _STATE
