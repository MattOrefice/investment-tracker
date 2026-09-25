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
# without the burst that draws a 429.
_WORKERS = 4


class RefreshState(NamedTuple):
    """One attempt's outcome, as the banner reports it."""

    status: str                            # "fetched" | "failed"
    attempted_at: datetime                 # UTC
    next_attempt_at: datetime              # UTC: nothing fetches before this
    served_through: Optional[str]          # committed_price_frontier after the attempt
    fetched_before: Optional[str]          # a previous success on this seed, ISO UTC, or None
    failed: tuple = ()                     # (ticker, reason) for each fetch that raised


_STATE: Optional[RefreshState] = None
_LOCK = threading.Lock()


def enabled() -> bool:
    """False in tests (DEMO_DAILY_FETCH=0), or before the runtime cache is in use:
    fetching into demo.db itself is the one thing this must never do."""
    import src.db as db
    return os.environ.get("DEMO_DAILY_FETCH", "1") != "0" and db._RUNTIME_CACHE is not None


def state() -> Optional[RefreshState]:
    """The latest attempt in this process, or None if none has run."""
    return _STATE


def next_close_after(t: datetime) -> datetime:
    """The next weekday New York close (plus the publish margin) after ``t``, in UTC.
    A holiday is not skipped: an attempt on one finds nothing new and succeeds."""
    ny = t.astimezone(_NY)
    c = ny.replace(hour=16, minute=0, second=0, microsecond=0) + _PUBLISH_MARGIN
    if c <= ny:
        c += timedelta(days=1)
    while c.weekday() >= 5:
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
    )


def refresh_if_due(now: Optional[datetime] = None) -> Optional[RefreshState]:
    """Fetch if the last attempt's wait has passed; return the current state.

    Once running, the price layer's own gap fills are off for the rest of the process:
    this is the only thing that fetches on a schedule."""
    global _STATE
    if not enabled():
        return _STATE
    from src import prices
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        prices.set_gap_fetch(False)
        if _STATE is not None and now < _STATE.next_attempt_at:
            return _STATE
        _STATE = _attempt(now)
        return _STATE
