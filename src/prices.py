"""Yahoo Finance price fetcher with SQLite caching.

Uses the v8/chart API directly (not yfinance) so browser User-Agent headers
are applied to every request, avoiding the 429 rate-limit that yfinance's
internal timezone-fetch triggers.
"""
import math
import re
import sqlite3
import threading
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, NamedTuple, Optional
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

from src.db import after_reseed, get_connection

# Shared session with a browser User-Agent so Yahoo Finance doesn't 429-block us
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
)

_YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class YahooResponseError(ValueError):
    """Yahoo Finance answered, and the answer holds no usable prices: a rate limit, an
    HTTP error, no result, no timestamps, no usable rows. A ValueError, as these were
    before they had a class, so a caller that catches ValueError still catches them.
    ``reason`` says what went wrong in words that follow "Yahoo Finance" (#637)."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


class YahooNoData(YahooResponseError):
    """Yahoo Finance's HTTP 400 that says no data exists for the window asked ("Data
    doesn't exist for startDate = ..."), as for a window before a fund's listing. An
    ANSWER that no close exists there, not a failure (#644, decided by the owner on
    October 17, 2026): get_prices' gap fetch remembers it like a success and never
    records it. A YahooResponseError still, so every other caller reads it as before."""


class YahooUnreadDividend(YahooResponseError):
    """Yahoo Finance's answer held a dividend event fetch_prices cannot read, so none of
    the answer was stored, neither its closes nor its dividends (#658, decided by the
    owner on October 18, 2026). ``unread`` holds each such event (UnreadDividend). A
    YahooResponseError, so get_prices' gap fetch records it as a failed fetch, with
    ``reason`` naming the events, and every other caller reads it as a failed fetch."""

    def __init__(self, message: str, reason: str, unread: "list[UnreadDividend]"):
        super().__init__(message, reason)
        self.unread = unread


# How Yahoo Finance's 400 describes a window that holds no data.
_NO_DATA_DESCRIPTION = "Data doesn't exist for startDate"

# A FORMAT check, deliberately not a symbol registry: letters, digits, and the
# punctuation Yahoo actually uses — '.' (BRK.B), '-' (BTC-USD), '=' (EURUSD=X),
# and a leading '^' (^GSPC). Case-insensitive so a lowercase caller behaves
# exactly as before. Whether a well-formed symbol EXISTS is not knowable here
# and stays the fetcher's job — this only rejects input that cannot be a symbol
# at all, before it becomes an outbound request or a URL.
_TICKER_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.=-]{0,14}$", re.IGNORECASE)


def is_valid_ticker(ticker) -> bool:
    """True if ``ticker`` could be an exchange-listed symbol.

    Cheap format/length sanity check. Rejects empty input, over-long strings,
    non-strings, and anything carrying URL metacharacters ('/', '?', '#', '%',
    whitespace) that would otherwise be interpolated into the request path.
    """
    return isinstance(ticker, str) and bool(_TICKER_RE.match(ticker.strip()))


def _to_iso(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def unsettled_bar_date(result: dict) -> Optional[date]:
    """The date of the bar still forming in the CURRENT trading session, or None.

    A bar fetched while its session is open carries an intraday quote, not a close.
    Persisting it writes a permanent wrong mark: ``get_prices`` only gap-fills
    *beyond* ``cached_end``, so that row is never revisited and the error is frozen
    into the cache. demo.db carries ~96 such rows from ~10 mid-session runs (VOO
    off 0.51% on 2026-07-20, QQQ 0.79%) — that is the defect this prevents.

    Read from the response's own ``meta``, so it is per-request and per-ticker for
    free: an equity's 09:30–16:00 session and BTC-USD's 24/7 one each answer for
    themselves, with no exchange calendar, holiday table, or timezone arithmetic
    here. A bar is unsettled when the current regular session has NOT yet ended
    (``now < regular.end``); its date is taken from ``regular.start``, which is the
    session's own opening timestamp rather than a local-clock guess at "today".

    DEGRADES TOWARD NOT PERSISTING. Returns ``regularMarketTime``'s date — i.e.
    "treat the newest bar as unsettled" — whenever the response does not let us
    prove settlement: ``currentTradingPeriod`` absent, ``regular`` absent, a
    non-numeric or missing ``end``/``start``. The two failure directions are not
    symmetric. Defaulting to "settled" silently reintroduces permanent partial
    rows and makes this whole function inert — the exact failure it exists to stop,
    and undetectable once written. Defaulting to "unsettled" costs one skipped row
    that the next run writes correctly, because a bar absent from the cache is
    re-fetched while a wrong bar in the cache is forever. Missing data is a reason
    to write less, not to write confidently.

    Returns None only when settlement is affirmatively established (the session has
    ended) or when there is no newest-bar timestamp to be unsure about.
    """
    meta = result.get("meta") or {}

    def _newest_bar_date() -> Optional[date]:
        """Fallback anchor: the date of the latest quote, treated as in-flight."""
        rmt = meta.get("regularMarketTime")
        if isinstance(rmt, (int, float)) and rmt > 0:
            return datetime.fromtimestamp(rmt, tz=timezone.utc).date()
        # No quote timestamp either. Fall back to the last bar in the payload —
        # still the conservative answer, since it is the only row that can be
        # mid-session.
        ts = result.get("timestamp") or []
        if ts and isinstance(ts[-1], (int, float)):
            return datetime.fromtimestamp(ts[-1], tz=timezone.utc).date()
        return None

    period = meta.get("currentTradingPeriod")
    if not isinstance(period, dict):
        return _newest_bar_date()
    regular = period.get("regular")
    if not isinstance(regular, dict):
        return _newest_bar_date()

    start, end_ts = regular.get("start"), regular.get("end")
    if not isinstance(end_ts, (int, float)) or not isinstance(start, (int, float)):
        return _newest_bar_date()

    now = datetime.now(tz=timezone.utc).timestamp()
    if now >= end_ts:
        return None  # session closed — the bar is a settled close, persist it
    return datetime.fromtimestamp(start, tz=timezone.utc).date()


def _date_to_unix(date_str: str) -> int:
    """Convert ISO date string to UTC midnight Unix timestamp."""
    dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# A DIVIDEND THE PROVIDER SENT THAT CANNOT BE READ IS RECORDED, NOT DROPPED (#642,
# decided by the owner on October 18, 2026). An event whose date or amount fetch_prices
# cannot read is recorded here with the ticker, the date as sent and why:
#   * a date that is not a timestamp, or one out of the platform's range (on Windows
#     datetime.fromtimestamp raises OSError for it, which a gap fetch used to record as
#     "a network error");
#   * an amount missing, not a number, or not above zero;
#   * an event that is not a record at all.
# AND THE ANSWER HOLDING IT IS NOT STORED AT ALL (#658, decided by the owner on October
# 18, 2026, reversing #642's "the closes are still stored"): neither its closes nor its
# dividends. fetch_prices records the events and raises YahooUnreadDividend. Stored, the
# closes made the window look fetched, get_prices never asked for it again, and the
# dividend was missing for good while this record died with the process. Not stored,
# the window stays a gap, so every process asks for it again, meets the event and
# records it again, until an answer reads cleanly; that answer is stored whole and
# clears the records its window holds. This is how a failed leading fetch survives a
# restart (#639). The ticker's prices stop where the store stops, and its price status
# (holdings._price_and_status) and the banner (asof.as_of_live_line) say why, naming
# the event. The lock's end and interior rules, and so the reportable-quarter cap, see
# the missing closes in the store; a lock whose window holds a record for a ticker it
# reads still refuses on it (cache._unread_dividends), the capture's last guard.
class UnreadDividend(NamedTuple):
    ticker: str
    raw_date: str              # the event's date as the provider sent it
    ex_date: "str | None"      # ISO, when the date could be read
    why: str
    start: str                 # the window asked for, which holds the event
    end: str

    def words(self) -> str:
        """"VTV, dated 2026-07-01 (its amount is not a number)": the date as read, or
        as sent when it could not be read."""
        return f"{self.ticker}, dated {self.ex_date or self.raw_date} ({self.why})"

    def holds(self, start: str, end: str) -> bool:
        """Whether [start, end] holds the event: by its date, or, when the date could
        not be read, by the window asked for, which holds it."""
        if self.ex_date:
            return start <= self.ex_date <= end
        return self.start <= end and start <= self.end


_UNREAD_DIVIDENDS: "dict[tuple[str, str], UnreadDividend]" = {}
_UNREAD_LOCK = threading.Lock()


def _reset_unread_dividends() -> None:
    """Forget the unread dividends (tests; a re-seed, after which their windows are
    asked for again, as after a restart)."""
    with _UNREAD_LOCK:
        _UNREAD_DIVIDENDS.clear()


after_reseed(_reset_unread_dividends)


def _read_dividend(ts_key, event) -> "tuple[str | None, float | None, str | None]":
    """(ex-date, amount, None) for a dividend event fetch_prices can read, else
    (ex-date or None, None, why it cannot be read)."""
    try:
        ts = int(ts_key)
    except (TypeError, ValueError):
        return None, None, "its date is not a timestamp"
    try:
        ex = _to_iso(datetime.fromtimestamp(ts, tz=timezone.utc).date())
    except (OverflowError, OSError, ValueError):
        return None, None, "its date is out of range"
    if not isinstance(event, dict):
        return ex, None, "it is not a dividend record"
    raw = event.get("amount")
    if raw is None:
        return ex, None, "it has no amount"
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return ex, None, "its amount is not a number"
    if not math.isfinite(amount):
        return ex, None, "its amount is not a number"
    if amount <= 0:
        return ex, None, f"its amount is {amount:g}, not above zero"
    return ex, amount, None


def _note_dividends(ticker: str, start: str, end: str, read: "list[str]",
                    unread: "list[UnreadDividend]") -> None:
    """Record the events an answer for [start, end] could not read, and clear any it
    now reads. An answer with none, which is stored whole, also clears every record of
    the ticker its window holds (#658): the window is stored now, so the record no
    longer says why the store stops."""
    with _UNREAD_LOCK:
        for raw in read:
            _UNREAD_DIVIDENDS.pop((ticker, raw), None)
        if not unread:
            for key in [k for k, u in _UNREAD_DIVIDENDS.items() if u.ticker == ticker
                        and (start <= u.ex_date <= end if u.ex_date
                             else start <= u.start and u.end <= end)]:
                del _UNREAD_DIVIDENDS[key]
        for u in unread:
            _UNREAD_DIVIDENDS[(u.ticker, u.raw_date)] = u


def _unread_reason(unread: "list[UnreadDividend]") -> str:
    """What the answer held, in words that follow "Yahoo Finance": "sent a dividend
    dated 2026-07-01 that could not be read: its amount is not a number"."""
    if len(unread) == 1:
        u = unread[0]
        return f"sent a dividend dated {u.ex_date or u.raw_date} that could not be read: {u.why}"
    return (f"sent {len(unread)} dividends that could not be read: "
            + "; ".join(f"dated {u.ex_date or u.raw_date}, {u.why}" for u in unread))


def unread_dividends() -> "list[UnreadDividend]":
    """Every dividend event the provider sent in this process that could not be read."""
    with _UNREAD_LOCK:
        return sorted(_UNREAD_DIVIDENDS.values(),
                      key=lambda u: (u.ticker, u.ex_date or u.start, u.raw_date))


def unread_dividends_for(ticker: str, start: str, end: "str | None" = None
                         ) -> "list[UnreadDividend]":
    """The unread dividends of ``ticker`` in [start, end]: by their date, or, when the
    date could not be read, by the window asked for, which holds the event."""
    end = end or date.today().isoformat()
    return [u for u in unread_dividends() if u.ticker == ticker and u.holds(start, end)]


# One store at a time in this process (#580). The demo's refresh fetches on four
# threads (src.demo_refresh._WORKERS), and each fetch stored through its own connection
# to the runtime cache, so two stores at once meant one waited on the other's write
# lock. In one of twenty CI runs under -n 4 the wait outlasted sqlite3's 5-second busy
# timeout: "database is locked", a holding's fetch failed, and the refresh with it.
# fetch_prices stores under this lock for every caller, so the fetches still run in
# parallel and the stores take turns. The connection's 5-second wait is unchanged.
_STORE_LOCK = threading.Lock()


def fetch_prices(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
    *,
    only_missing: "Optional[Iterable[str]]" = None,
) -> pd.DataFrame:
    """
    Pull daily close + adj_close from Yahoo Finance v8/chart API, cache the CLOSE
    and the dividends, and return a DataFrame indexed by datetime.date with columns:
    close, adj_close (the provider's own adjustment, as fetched).

    The cache stores UNADJUSTED prices (#304): adj_close is written NULL and derived
    on read by get_prices from close and the dividends table (dividend_adjusted).
    The provider re-anchors adj_close at every new dividend, so rows cached on
    different days carried different anchors and a return spanning two fetch dates
    crossed a seam. A raw close does not move after it settles.

    ``only_missing`` (ISO dates) makes this the re-ask for closes the book lacks inside
    a ticker's history (#501, src.price_gaps). A close is then stored only on one of
    those dates and only where the book holds none, and a dividend only where the book
    holds none, so NO STORED ROW CHANGES whatever else the response carries. Without it
    every settled bar in the response replaces the stored one, as before. The dates
    stored are returned in ``attrs["stored"]``.

    AN ANSWER HOLDING A DIVIDEND IT CANNOT READ IS NOT STORED AT ALL (#658, decided by
    the owner on October 18, 2026): neither its closes nor its dividends. The events are
    recorded (unread_dividends) and YahooUnreadDividend is raised, so the window stays
    a gap and is asked for again until an answer reads cleanly.

    Raises ValueError for malformed, delisted, or unrecognised tickers.
    """
    # Validate BEFORE the request. This is the choke point every caller shares,
    # so the check lives here rather than at each entry point: the Candidate
    # Screen's free-text box is the reachable one today, but the guarantee that
    # nothing malformed becomes an outbound call or a URL path should not depend
    # on remembering to re-check at every future call site.
    if not is_valid_ticker(ticker):
        raise ValueError(
            f"{ticker!r} is not a valid ticker format. Use an exchange-listed "
            "symbol of up to 15 characters — letters, digits, and . - = ^ only."
        )

    end = end_date or date.today().isoformat()

    # API period2 is inclusive — add one day to ensure end date is included
    period1 = _date_to_unix(start_date)
    period2 = _date_to_unix(
        _to_iso(date.fromisoformat(end) + timedelta(days=1))
    )

    # Percent-encode the path segment: the symbol is data, not URL syntax. '^'
    # and '=' are marked safe so that every symbol the validator admits passes
    # through byte-identical — this must not change what we ask Yahoo for. It is
    # therefore a no-op today and exists purely as belt-and-braces should a
    # future caller reach the URL without going through is_valid_ticker.
    url = _YF_CHART_URL.format(ticker=quote(ticker.strip(), safe="^="))
    params = {
        "interval": "1d",
        "period1": period1,
        "period2": period2,
        "events": "div,splits",
    }

    resp = _SESSION.get(url, params=params, timeout=15)
    if resp.status_code == 429:
        raise YahooResponseError(
            f"Yahoo Finance rate-limited (HTTP 429) for '{ticker}'. "
            "Try again in a few seconds.",
            "rate-limited the request (HTTP 429)",
        )
    if resp.status_code == 400:
        try:
            description = str(((resp.json().get("chart") or {}).get("error") or {})
                              .get("description") or "")
        except ValueError:                                       # not JSON
            description = ""
        if description.startswith(_NO_DATA_DESCRIPTION):
            raise YahooNoData(
                f"Yahoo Finance has no data for '{ticker}' ({start_date} to {end}): "
                f"{description}",
                "answered that no price data exists for the window",
            )
    if resp.status_code != 200:
        raise YahooResponseError(
            f"Yahoo Finance returned HTTP {resp.status_code} for '{ticker}'.",
            f"returned HTTP {resp.status_code}",
        )

    data = resp.json()
    result = (data.get("chart") or {}).get("result")
    if not result:
        error_msg = (data.get("chart") or {}).get("error") or "unknown error"
        raise YahooResponseError(
            f"No price data returned for '{ticker}' ({start_date} to {end}). "
            f"Yahoo Finance error: {error_msg}",
            "returned no price data",
        )

    result = result[0]
    timestamps = result.get("timestamp", [])
    if not timestamps:
        raise YahooResponseError(
            f"No price data returned for '{ticker}' ({start_date} to {end}). "
            "Ticker may be delisted or not recognised.",
            "returned no price data",
        )

    closes    = result["indicators"]["quote"][0].get("close", [])
    adjcloses = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose", [])

    # Pad adjcloses to same length as closes if missing
    if len(adjcloses) < len(timestamps):
        adjcloses = adjcloses + [None] * (len(timestamps) - len(adjcloses))

    rows = []
    for ts, c, ac in zip(timestamps, closes, adjcloses):
        if c is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        rows.append({"price_date": dt, "close": float(c),
                     "adj_close": float(ac) if ac is not None else None})

    if not rows:
        raise YahooResponseError(f"No usable price rows after cleaning for '{ticker}'.",
                                 "returned no usable price rows")

    df = pd.DataFrame(rows).set_index("price_date")
    df = df[df["close"] > 0]

    if df.empty:
        raise YahooResponseError(f"No usable price rows after cleaning for '{ticker}'.",
                                 "returned no usable price rows")

    # Extract dividend events from the API response
    events = result.get("events") or {}
    div_rows: list[tuple] = []
    read: "list[str]" = []
    unread: "list[UnreadDividend]" = []
    for ts_key, div_data in (events.get("dividends") or {}).items():
        ex_date, amount, why = _read_dividend(ts_key, div_data)
        if why is None:
            div_rows.append((ex_date, amount))
            read.append(str(ts_key))
        else:
            unread.append(UnreadDividend(ticker, str(ts_key), ex_date, why, start_date, end))
    if unread:
        _note_dividends(ticker, start_date, end, read, unread)
        reason = _unread_reason(unread)
        raise YahooUnreadDividend(
            f"Nothing stored for '{ticker}' ({start_date} to {end}): Yahoo Finance {reason}.",
            reason, unread)

    # The bar still forming in an open session is RETURNED but never CACHED — see
    # unsettled_bar_date(). Callers get the live mark (get_prices concatenates this
    # frame into its result), while the cache holds settled closes only. Skipping
    # it costs a re-fetch until the session closes; writing it costs a permanently
    # wrong row, because nothing ever re-reads a date at or below cached_end.
    skip_date = unsettled_bar_date(result)
    if skip_date is not None and any(dt >= skip_date for dt in df.index):
        # The frame carries the open session's bar: say so, with the time of its quote,
        # so whatever serves it can tell the reader (#160, audit item 4f).
        rmt = (result.get("meta") or {}).get("regularMarketTime")
        df.attrs["unsettled"] = {
            "date": skip_date.isoformat(),
            "quoted_at": (datetime.fromtimestamp(rmt, tz=timezone.utc).isoformat()
                          if isinstance(rmt, (int, float)) and rmt > 0 else None),
        }

    wanted = None if only_missing is None else set(only_missing)
    stored: "list[str]" = []
    with _STORE_LOCK, get_connection() as conn:
        # Auto-migrate: ensure dividends table exists in pre-existing DBs
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dividends (
                ticker TEXT NOT NULL, ex_date TEXT NOT NULL, amount REAL NOT NULL,
                PRIMARY KEY (ticker, ex_date))"""
        )
        for dt, row in df.iterrows():
            if skip_date is not None and dt >= skip_date:
                continue
            day = _to_iso(dt)
            if wanted is None:
                conn.execute(
                    """INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close)
                       VALUES (?, ?, ?, NULL)""",
                    (ticker, day, float(row["close"])),
                )
            elif day in wanted and conn.execute(
                    "SELECT 1 FROM prices WHERE ticker = ? AND price_date = ?",
                    (ticker, day)).fetchone() is None:
                # The re-ask: insert-only. The guard is in the statement too, so a row
                # another connection stored meanwhile is still not replaced: in the
                # demo `prices` is a view whose insert trigger replaces (src.db).
                conn.execute(
                    """INSERT INTO prices (ticker, price_date, close, adj_close)
                       SELECT ?, ?, ?, NULL WHERE NOT EXISTS
                       (SELECT 1 FROM prices WHERE ticker = ? AND price_date = ?)""",
                    (ticker, day, float(row["close"]), ticker, day),
                )
                stored.append(day)
        # Dividends are ex-date events, not intraday quotes — an ex-date announced
        # during an open session is already final, so they are not gated here.
        for ex_date, amount in div_rows:
            if wanted is None:
                conn.execute(
                    "INSERT OR REPLACE INTO dividends (ticker, ex_date, amount) VALUES (?, ?, ?)",
                    (ticker, ex_date, amount),
                )
            else:
                conn.execute(
                    """INSERT INTO dividends (ticker, ex_date, amount)
                       SELECT ?, ?, ? WHERE NOT EXISTS
                       (SELECT 1 FROM dividends WHERE ticker = ? AND ex_date = ?)""",
                    (ticker, ex_date, amount, ticker, ex_date),
                )

    _note_dividends(ticker, start_date, end, read, [])
    if wanted is not None:
        df.attrs["stored"] = stored
    _count_fetch()
    return df


# How many fetches the provider has answered in this process, with their rows stored.
# Personal mode reads it to tell a page load that refreshed the book from one that
# read only what was stored (src.price_gaps.reask_after_refresh, #501).
_FETCHES = 0
_FETCHES_LOCK = threading.Lock()


def _count_fetch() -> None:
    global _FETCHES
    with _FETCHES_LOCK:
        _FETCHES += 1


def fetch_count() -> int:
    """Fetches answered and stored so far in this process (see _FETCHES)."""
    return _FETCHES


# Whether a read may go to the network to fill a gap the cache does not cover. Off
# only once the demo's daily refresh has taken over fetching (src.demo_refresh):
# then the refresh fetches on its own schedule and a read serves what the cache
# holds, so a failed fetch is retried on a timer and never on every render. A
# ticker with no cached rows at all (a candidate typed on the Research page) is
# still fetched on read: nothing else will fetch it.
_GAP_FETCH = True

# Open-session bars get_prices has SERVED in this process, by ticker: {"date", "quoted_at"}.
# #160 keeps them out of the cache but hands them to the caller, so in personal mode
# during market hours a current value can include today's unsettled price. The banner
# reads this to say so, with the quote's time (src.asof.as_of_live_line).
_LIVE_MARKS: dict[str, dict] = {}


def live_marks() -> dict:
    """Open-session bars served so far in this process, by ticker (see _LIVE_MARKS)."""
    return dict(_LIVE_MARKS)


def _note_live_mark(ticker: str, frame: pd.DataFrame) -> None:
    mark = frame.attrs.get("unsettled")
    if mark:
        _LIVE_MARKS[ticker] = dict(mark)


def set_gap_fetch(enabled: bool) -> None:
    global _GAP_FETCH
    _GAP_FETCH = bool(enabled)


# Process-local memo for the TRAILING gap fetch only, keyed on (ticker, start, end).
# Once the in-flight bar stopped being cached, `cached_end < end` stays true for the
# rest of an open session, so every get_prices call re-issued the same request — 14
# per last_real_price_date() call, repeated per call, measured. The DB cache cannot
# absorb this by design (that is the fix), so it is absorbed here instead.
#
# Deliberately unbounded in time and cleared only by process exit: entries are keyed
# on an explicit end date, so a later date is a different key and a new fetch. It
# holds one small frame per (ticker, window) touched — bounded by the ~47-ticker
# universe — and a Streamlit rerun reuses the process, which is exactly the repeat
# this exists to stop. Reset it in tests via _reset_trailing_memo().
_TRAILING_MEMO: dict[tuple[str, str, str], pd.DataFrame] = {}


def _reset_trailing_memo() -> None:
    """Clear the trailing-fetch memo (tests; also useful in a long-lived REPL)."""
    _TRAILING_MEMO.clear()


# A re-seeded runtime cache no longer holds what these fetches stored, their dividends
# included, so the demo forgets them with the seed (src.db, #480).
after_reseed(_reset_trailing_memo)


def _fetch_trailing_memoized(ticker: str, start: str, end: str) -> pd.DataFrame:
    """fetch_prices for a trailing gap, memoized per process.

    A miss still calls fetch_prices, so the settled rows land in the DB exactly as
    before; only the repeated network round-trip for the unsettled tail is avoided.
    Failures are NOT memoized — a transient 429 must not pin an empty result for the
    life of the process.
    """
    key = (ticker, start, end)
    hit = _TRAILING_MEMO.get(key)
    if hit is not None:
        return hit.copy()
    df = fetch_prices(ticker, start, end)
    _TRAILING_MEMO[key] = df.copy()
    return df


# A FAILED GAP FETCH NEVER READS AS A QUIET OLD DATE (#637, decided by the owner on
# October 17, 2026): the rule #368 set for the demo, wherever the gap fetch runs, and the
# page stays up. get_prices's two gap fetches catch only the fetch's own failures
# (gap_fetch_cause): the network, Yahoo's answer, a read-only store. A code error
# raises. Each caught failure is recorded here with its cause, per ticker and window,
# and that window is not fetched again for demo_refresh.RETRY_AFTER, the demo's retry:
# a down network costs one attempt per ticker per half hour, not one per render. A
# later success clears it. The ticker's coverage status (holdings._price_and_status)
# and the banner (asof.as_of_live_line) read the record. An answer holding a dividend
# that cannot be read is one of these since #658: none of it is stored
# (YahooUnreadDividend), and the cause names the event.
class GapFetchFailure(NamedTuple):
    ticker: str
    start: str
    end: str
    cause: str
    attempted_at: datetime                 # UTC
    retry_after: datetime                  # UTC: this window is not fetched before this


_GAP_FAILURES: "dict[tuple[str, str, str], GapFetchFailure]" = {}
_GAP_FAILURES_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_only_store(exc: BaseException) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF == sqlite3.SQLITE_READONLY
    return "readonly database" in str(exc)


def gap_fetch_cause(exc: BaseException) -> "str | None":
    """Why a gap fetch failed, in words that follow "the price update failed", when
    ``exc`` is one of the fetch's own failures: the network (an OSError, which every
    requests exception is), Yahoo's answer (YahooResponseError) or a read-only store.
    None for anything else: a code error (KeyError, TypeError and the like) is not a
    fetch failure, and get_prices raises it."""
    if isinstance(exc, YahooResponseError):
        return f"Yahoo Finance {exc.reason}"
    if isinstance(exc, sqlite3.OperationalError) and _read_only_store(exc):
        return "the price store is read-only"
    if isinstance(exc, OSError):
        return f"a network error: {type(exc).__name__}"
    return None


# YAHOO'S "NO DATA HERE" ANSWERS ARE ANSWERS, NOT FAILURES (#644, decided by the owner on
# October 17, 2026; the holiday table they rest on extended back on October 18, #644):
#   * a window with no NYSE session (demo_refresh.is_session) holds no close, so it is
#     never fetched and never recorded. A series that trades every day
#     (price_gaps.DAILY) has a session every day;
#   * the provider's HTTP 400 "Data doesn't exist for startDate = ..." (YahooNoData), as
#     for a window before a fund's listing, means no close exists there. It is
#     remembered for the process like a success (_NO_DATA), clears the window's
#     failures as a success does, and is never recorded or shown;
#   * an empty answer for a window that has a session is still a failure.
_NO_DATA: "set[tuple[str, str, str]]" = set()


def _reset_no_data_memo() -> None:
    """Forget the windows the provider said hold no data (tests; a re-seed)."""
    with _GAP_FAILURES_LOCK:
        _NO_DATA.clear()


after_reseed(_reset_no_data_memo)


def _has_session(ticker: str, start: str, end: str) -> bool:
    """Whether [start, end] holds a session of ``ticker``'s market: an NYSE session by
    the holiday table, or any day for a series that trades every day."""
    from src.demo_refresh import is_session
    from src.price_gaps import DAILY
    if ticker in DAILY:
        return True
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    while day <= last:
        if is_session(day):
            return True
        day += timedelta(days=1)
    return False


def _clear_failures(ticker: str, start: str, end: str) -> None:
    with _GAP_FAILURES_LOCK:
        for k in [k for k in _GAP_FAILURES
                  if k[0] == ticker and start <= k[1] and k[2] <= end]:
            del _GAP_FAILURES[k]


def _gap_fetch(ticker: str, start: str, end: str, fetch: Callable) -> "pd.DataFrame | None":
    """One of get_prices's gap fetches: the rows fetched, or None when nothing was
    added: the window holds no session or the provider said it holds no data (#644),
    or the fetch failed now or failed for this window within RETRY_AFTER (#637), in
    which case nothing is asked."""
    from src.demo_refresh import RETRY_AFTER
    key, now = (ticker, start, end), _now()
    if not _has_session(ticker, start, end):
        return None
    with _GAP_FAILURES_LOCK:
        answered = key in _NO_DATA
        failed = _GAP_FAILURES.get(key)
    if answered or (failed is not None and now < failed.retry_after):
        return None
    try:
        frame = fetch(ticker, start, end)
    except YahooNoData:
        with _GAP_FAILURES_LOCK:
            _NO_DATA.add(key)
        _clear_failures(ticker, start, end)
        return None
    except Exception as exc:                                     # noqa: BLE001
        cause = gap_fetch_cause(exc)
        if cause is None:
            raise
        with _GAP_FAILURES_LOCK:
            _GAP_FAILURES[key] = GapFetchFailure(ticker, start, end, cause, now,
                                                 now + RETRY_AFTER)
        return None
    _clear_failures(ticker, start, end)
    return frame


def gap_fetch_failures() -> "list[GapFetchFailure]":
    """Every gap fetch whose last attempt failed, in this process, oldest first."""
    with _GAP_FAILURES_LOCK:
        return sorted(_GAP_FAILURES.values(), key=lambda f: f.attempted_at)


def gap_fetch_failure_for(ticker: str, start: str, end: "str | None" = None
                          ) -> "GapFetchFailure | None":
    """The latest failed gap fetch for ``ticker`` inside [start, end]: what a
    get_prices call for that window last failed to fetch, or None."""
    end = end or date.today().isoformat()
    found = [f for f in gap_fetch_failures()
             if f.ticker == ticker and start <= f.start and f.end <= end]
    return found[-1] if found else None


def dividend_adjusted(ticker: str, frame: pd.DataFrame, *,
                      through: Optional[str] = None) -> pd.Series:
    """Total-return prices for ``frame`` (indexed by date, with a ``close`` column),
    derived from close and the stored dividends (#304).

    ``through`` (ISO date) admits only dividends with an ex-date on or before it. A
    quarter lock passes its own last day, so the lock's levels cannot move when a
    later dividend arrives: without it, every ex-date after the quarter rescales the
    quarter's levels (#368). Omitted, every stored dividend counts, as before.

    The provider's own method: every close BEFORE an ex-date is scaled by
    ``1 - amount / close_on_the_last_trading_day_before_the_ex_date``, compounded
    over every later ex-date. Reproduces the provider's adjclose to under 5e-7
    relative (measured on SPY, SCHP, VEA, BIL, TIP and HYG, 2024 onward).

    Anchored on EVERY stored dividend for the ticker, not only those inside the
    frame, so two reads with different windows return the same level for the same
    date. The previous close for an ex-date is taken from ``frame`` when it holds
    that day and from the cache otherwise; an ex-date with no earlier close
    anywhere cannot affect any row of ``frame`` and is skipped.

    Only as complete as the dividends table: a missing dividend understates total
    return, silently. That is why #304 checked completeness for every held ticker
    before the cache stopped storing the provider's adjustment.
    """
    closes = frame["close"].astype(float)
    if closes.empty:
        return closes.copy()
    first = _to_iso(closes.index.min())
    # Each ex-date with the cache's last close before it, in one query. A book with
    # no dividends table has recorded no dividends: nothing to adjust for. Checked,
    # not created — a read must not write.
    with get_connection() as conn:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dividends'"
        ).fetchone()
        divs = [] if not has_table else conn.execute(
            """SELECT d.ex_date, d.amount,
                      (SELECT MAX(p.price_date) FROM prices p
                        WHERE p.ticker = d.ticker AND p.price_date < d.ex_date) AS pd,
                      (SELECT p.close FROM prices p
                        WHERE p.ticker = d.ticker AND p.price_date < d.ex_date
                        ORDER BY p.price_date DESC LIMIT 1) AS pc
                 FROM dividends d WHERE d.ticker = ? AND d.ex_date > ?
                  AND (? IS NULL OR d.ex_date <= ?)
                ORDER BY d.ex_date""",
            (ticker, first, through, through),
        ).fetchall()
    # Vectorised: each ex-date's multiplier lands at the position of its first row ON
    # or after the ex-date, and a row's factor is the product of every multiplier at a
    # LATER position (a reverse cumulative product). O(n + k) rather than a scan of
    # the frame per dividend; get_prices runs this on every read.
    dates = np.array(closes.index, dtype="datetime64[D]")
    vals = closes.to_numpy()
    g = np.ones(len(vals) + 1)
    for ex_iso, amount, cache_date, cache_close in divs:
        pos = int(np.searchsorted(dates, np.datetime64(ex_iso, "D"), side="left"))
        # The last close before the ex-date, from the frame or the cache, whichever
        # is later (the frame may end before this ex-date); the frame wins a tie.
        cands = []
        if pos > 0:
            cands.append((_to_iso(closes.index[pos - 1]), 1, float(vals[pos - 1])))
        if cache_date is not None:
            cands.append((cache_date, 0, float(cache_close)))
        if not cands:
            continue
        prev = max(cands)[2]
        if prev > 0:
            g[pos] *= 1.0 - float(amount) / prev
    factor = np.cumprod(g[::-1])[::-1][1:]
    factor = pd.Series(factor, index=closes.index)
    return closes * factor


# The price lock in force for the current context, or None. Set only by
# cache.snapshot_price_context, as a reader that returns a frame, or None for "not
# locked: read the cache as usual".
#
# CONSULTED INSIDE get_prices, NOT BY REPLACING IT. Every consumer module does
# `from src.prices import get_prices` and so holds this function object; swapping
# the module attribute, which is how the lock used to work, reached only callers
# that looked the name up on the module at call time. No report section did, so a
# PDF read live prices under a lock nothing read (#368: corrupting VOO in the DB
# after capture moved 86 "locked" figures). A ContextVar is also per-session:
# the attribute swap was process-global, visible to every concurrent visitor.
_PRICE_LOCK: ContextVar[Optional[Callable]] = ContextVar("price_lock", default=None)


def get_prices(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Return cached prices, fetching only gaps from yfinance.
    Returns DataFrame indexed by datetime.date with columns: close, adj_close.

    adj_close is DERIVED here, on read, from close and the dividends table
    (dividend_adjusted, #304), for cached and freshly fetched rows alike, so every
    row a caller sees shares one anchor.

    The bar of a currently-open session is served but never cached (see
    fetch_prices / unsettled_bar_date), so a live mark still reaches the caller
    while the DB holds settled closes only.

    Inside cache.snapshot_price_context, a locked ticker is served from the lock
    and never from the cache (see _PRICE_LOCK).
    """
    lock = _PRICE_LOCK.get()
    if lock is not None:
        locked = lock(ticker, start_date, end_date)
        if locked is not None:
            return locked

    end = end_date or date.today().isoformat()

    # Closes only. adj_close is NULL in storage since #339 and derived below from the
    # whole frame, so reading it only carried an all-NULL column into the gap-fill
    # concats, where pandas warns on an all-NA entry: 604 of 774 suite warnings came
    # from the trailing one (#356). A column the cache simply lacks does not warn.
    # The column stays in the schema; dropping it would migrate the committed demo.db.
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT price_date, close
               FROM prices
               WHERE ticker = ? AND price_date >= ? AND price_date <= ?
               ORDER BY price_date""",
            (ticker, start_date, end),
        ).fetchall()

    if not rows:
        # Fetch CONTIGUOUSLY with what is cached (#304). A window that touches no
        # cached row would otherwise store a disjoint block and leave a hole between
        # it and the cache: the hole's dividends are never stored, and a dividend
        # just after it is scaled against the stale close before the hole instead of
        # the true prior close. So widen the fetch to meet the cache on whichever
        # side it lies, then return only the requested window.
        with get_connection() as conn:
            before, after = conn.execute(
                "SELECT (SELECT MAX(price_date) FROM prices WHERE ticker = ? AND price_date < ?),"
                "       (SELECT MIN(price_date) FROM prices WHERE ticker = ? AND price_date > ?)",
                (ticker, start_date, ticker, end),
            ).fetchone()
        lo = _to_iso(date.fromisoformat(before) + timedelta(days=1)) if before else start_date
        hi = _to_iso(date.fromisoformat(after) - timedelta(days=1)) if after else end
        fresh = fetch_prices(ticker, min(lo, start_date), max(hi, end))
        _note_live_mark(ticker, fresh)
        fresh["adj_close"] = dividend_adjusted(ticker, fresh)
        in_window = [(_to_iso(d) >= start_date) and (_to_iso(d) <= end) for d in fresh.index]
        return fresh[in_window]

    cached = pd.DataFrame(
        [(r["price_date"], r["close"]) for r in rows],
        columns=["price_date", "close"],
    )
    cached.index = pd.to_datetime(cached["price_date"]).dt.date
    cached = cached.drop(columns=["price_date"])

    cached_start = _to_iso(cached.index.min())
    cached_end   = _to_iso(cached.index.max())

    # Fetch any missing leading data. A failure is recorded, not raised (#637).
    if cached_start > start_date and _GAP_FETCH:
        pre_end = _to_iso(cached.index.min() - timedelta(days=1))
        pre = _gap_fetch(ticker, start_date, pre_end, fetch_prices)
        if pre is not None:
            cached = pd.concat([pre, cached]).sort_index()

    # Fetch any missing trailing data. A failure is recorded, not raised (#637).
    if cached_end < end and _GAP_FETCH:
        post_start = _to_iso(cached.index.max() + timedelta(days=1))
        post = _gap_fetch(ticker, post_start, end, _fetch_trailing_memoized)
        if post is not None:
            _note_live_mark(ticker, post)
            cached = pd.concat([cached, post]).sort_index()

    # Dedup index: trailing/leading fetches can return dates that overlap the
    # cached range when the Yahoo Finance API's UTC-midnight period boundary
    # maps to the previous US trading day (e.g., requesting "2026-05-06"
    # returns May-5 data already in the cache).  Keep the last occurrence so
    # the most-recently fetched value wins.
    if cached.index.duplicated().any():
        cached = cached[~cached.index.duplicated(keep="last")]

    cached["adj_close"] = dividend_adjusted(ticker, cached)
    return cached


def total_return_series(frame: pd.DataFrame) -> pd.Series:
    """The total-return basis from a price frame: adj_close, filled from close.

    Fourteen sites open-code ``p["adj_close"].fillna(p["close"])`` today
    (benchmarks.py:93, holdings.py:423/443/596/708, drip.py:283, factors.py x5,
    asset_evaluation.py:104). This is the same expression in one place, so that:

    * a caller wanting TOTAL RETURN never touches ``close`` and therefore keeps
      working against a legacy single-basis snapshot, which serves no ``close``
      column at all (#193). Only a caller wanting close as its PRIMARY basis
      raises there — which is the distinction the DataFrame API cannot express and
      the reason this helper exists rather than a basis flag.
    * the fourteen per-site verdicts get a single home. They are DEFERRED, not
      settled, except drip.py's: a DRIP reinvestment executes at the raw market
      close, so drip.py reads ``close`` directly and no longer calls this (#406
      item 12). risk.py:135 manufactures a spurious return spike on
      the dividend date by filling from close. Whoever takes that up changes it
      here, per caller, instead of re-deriving the question fourteen times.

    Behaviour is pinned to what those sites do today, verbatim: adj_close where
    present, close where adj_close is null, and adj_close alone when there is no
    close column to fall back to.
    """
    adj = frame["adj_close"]
    if "close" in frame.columns:
        return adj.fillna(frame["close"])
    return adj


def _no_such_table(exc: BaseException, table: str) -> bool:
    """Whether ``exc`` is sqlite's error for a missing ``table``."""
    return isinstance(exc, sqlite3.OperationalError) and str(exc) == f"no such table: {table}"


def _cached_row_count(ticker: str, start_date: str, end: str) -> int:
    """How many settled rows the cache already holds for this window.

    Returns 0 when the prices table does not exist (a minimal fixture DB, a
    freshly created file): "no cached rows" is the honest answer to the question
    asked, not a swallowed error — it is also exactly what the caller would
    conclude from an empty table.

    ANY OTHER ERROR RAISES (#642, decided by the owner on October 18, 2026). It used to
    return 0 for every error, so a store that could not be read (locked, unopenable,
    malformed) and may hold rows was reported as a cold one, ``no_cached_rows``.
    """
    try:
        with get_connection() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM prices WHERE ticker = ? AND price_date >= ? "
                "AND price_date <= ?",
                (ticker, start_date, end),
            ).fetchone()[0])
    except sqlite3.OperationalError as exc:
        if _no_such_table(exc, "prices"):
            return 0
        raise


def classify_miss(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
    *,
    error: Optional[BaseException] = None,
) -> str:
    """Why a price lookup produced nothing usable, as a coverage reason code.

    THIS IS THE WHOLE REASON COVERAGE IS BUILT IN TWO LAYERS. ``get_prices``
    raises when the window holds no cached row and its fetch fails, so from a
    caller's ``except`` clause a blocked network and an empty cache arrive as the
    same ValueError. Only the price layer can consult its own cache first and tell
    them apart. Where cached rows exist, a gap fetch's own failure (the network,
    Yahoo's answer, a read-only store) is not raised: it is recorded with its cause
    (``gap_fetch_failure_for``) and the cached rows are served (#637). A code error
    raises on either path. A caller that classified
    misses from its own except clause would report every one of them as
    ``fetch_failed``, and the reason code is not decoration — it decides whether a
    gap is a legitimate degradation (a delisted holding) or a defect (#188).

    Deliberately a classifier rather than a fetching wrapper: the fetch stays on
    whichever ``get_prices`` the caller resolves, so a test that monkeypatches it
    keeps working and there is still exactly one fetch implementation.
    """
    end = end_date or date.today().isoformat()
    if _cached_row_count(ticker, start_date, end) > 0:
        # Rows exist for the window, so a gap fetch's own failure was recorded rather
        # than raised (gap_fetch_failure_for). An error that reached the caller anyway
        # came from somewhere else, and is still not a cold cache.
        return "fetch_failed" if error is not None else "empty_window"
    return "no_cached_rows"


def get_dividends(ticker: str, start_date: str, end_date: str) -> pd.Series:
    """
    Return dividend-per-share amounts indexed by ex_date for ticker in [start_date, end_date],
    from what is stored. Returns an empty Series if none is stored in the range.

    IT NEVER FETCHES (#494, decided by the owner on October 16, 2026). Dividends arrive
    with the price fetches that store them: fetch_prices stores a ticker's dividends
    with its closes. A window with no stored dividend used to fetch that window's prices
    to load them, and fetch_prices stores every close it receives, so a start later than
    the day after the ticker's last stored close left the closes in between unfetched: a
    gap inside the history (shown on a copy of the frozen book on #494).

    IT NEVER WRITES, AND ONLY A MISSING TABLE READS AS NONE (#642, decided by the owner
    on October 18, 2026). A book with no dividends table has recorded no dividends. Any
    other error raises: it used to read as "no dividend stored", so a quarter lock taken
    while the store was locked held an empty dividend history for good. The read also
    created the table, a write that failed on a read-only store.
    """
    end = end_date or date.today().isoformat()

    # A quarter lock serves the dividends it locked, through the quarter's last day
    # (#382), for every ticker it holds; others read the cache as usual.
    from src.input_lock import DIVIDENDS, locked
    held = locked(DIVIDENDS)
    if held is not None and ticker in held:
        s = held[ticker]
        keep = [(d.isoformat() >= start_date) and (d.isoformat() <= end) for d in s.index]
        return s[keep].copy()

    def _query_cache() -> list:
        with get_connection() as conn:
            try:
                return conn.execute(
                    """SELECT ex_date, amount FROM dividends
                       WHERE ticker = ? AND ex_date >= ? AND ex_date <= ?
                       ORDER BY ex_date""",
                    (ticker, start_date, end),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if _no_such_table(exc, "dividends"):
                    return []
                raise

    rows = _query_cache()
    if not rows:
        return pd.Series(dtype=float)

    s = pd.Series({r["ex_date"]: float(r["amount"]) for r in rows})
    s.index = pd.to_datetime(s.index).date  # DatetimeIndex → array of datetime.date
    return s.sort_index()


def _last_stored_close(ticker: str) -> "str | None":
    """The ticker's newest stored close date, or None when none is stored."""
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(price_date) FROM prices WHERE ticker = ?",
                           (ticker,)).fetchone()
    return row[0] if row and row[0] else None


def stored_span(ticker: str) -> "tuple[str, str] | None":
    """The ticker's first and newest stored close dates, or None when none is stored."""
    with get_connection() as conn:
        row = conn.execute("SELECT MIN(price_date), MAX(price_date) FROM prices "
                           "WHERE ticker = ?", (ticker,)).fetchone()
    return (row[0], row[1]) if row and row[0] else None


def bulk_refresh(tickers: list, start_date: str) -> None:
    """
    Pre-warm the price cache for multiple tickers from start_date to today.
    Skips individual failures with a warning rather than raising.

    REFUSES A START THAT WOULD LEAVE A GAP (#494, decided by the owner on October 16,
    2026): a start later than the day after any ticker's last stored close raises
    ValueError, naming each such ticker and its last close, and fetches nothing.
    fetch_prices stores every close it receives, so a later start would leave the
    closes in between unstored, a gap inside the history that nothing fills.
    """
    end = date.today().isoformat()
    errors = []

    late = []
    for ticker in tickers:
        last = _last_stored_close(ticker)
        if last is not None and start_date > _to_iso(date.fromisoformat(last) + timedelta(days=1)):
            late.append(f"{ticker} (last stored close {last})")
    if late:
        raise ValueError(
            f"bulk_refresh refuses start {start_date}: it is later than the day after the "
            f"last stored close of {', '.join(late)}, so the closes in between would be "
            "left unstored")

    for i, ticker in enumerate(tickers, 1):
        if i > 1:
            time.sleep(0.5)  # brief pause to avoid 429 rate-limiting
        print(f"  [{i:2d}/{len(tickers)}] {ticker:<6} ...", end=" ", flush=True)
        try:
            df = fetch_prices(ticker, start_date, end)
            print(f"{len(df)} rows  ({_to_iso(df.index.min())} to {_to_iso(df.index.max())})")
        except Exception as exc:
            print(f"SKIP  ({exc})")
            errors.append((ticker, str(exc)))

    print(f"\nDone: {len(tickers) - len(errors)}/{len(tickers)} tickers cached.")
    if errors:
        print("Skipped:")
        for t, e in errors:
            print(f"  {t}: {e}")


if __name__ == "__main__":
    bulk_refresh(
        ["VOO", "SPHQ", "VTV", "AVUV", "VEA", "IEMG", "VGIT", "SCHP", "VNQ", "PDBC"],
        "2025-04-25",
    )
