"""Yahoo Finance price fetcher with SQLite caching.

Uses the v8/chart API directly (not yfinance) so browser User-Agent headers
are applied to every request, avoiding the 429 rate-limit that yfinance's
internal timezone-fetch triggers.
"""
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import pandas as pd
import requests

from src.db import get_connection

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


def fetch_prices(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Pull daily close + adj_close from Yahoo Finance v8/chart API, cache in the
    prices table, and return a DataFrame indexed by datetime.date with columns:
    close, adj_close.

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
        raise ValueError(
            f"Yahoo Finance rate-limited (HTTP 429) for '{ticker}'. "
            "Try again in a few seconds."
        )
    if resp.status_code != 200:
        raise ValueError(
            f"Yahoo Finance returned HTTP {resp.status_code} for '{ticker}'."
        )

    data = resp.json()
    result = (data.get("chart") or {}).get("result")
    if not result:
        error_msg = (data.get("chart") or {}).get("error") or "unknown error"
        raise ValueError(
            f"No price data returned for '{ticker}' ({start_date} to {end}). "
            f"Yahoo Finance error: {error_msg}"
        )

    result = result[0]
    timestamps = result.get("timestamp", [])
    if not timestamps:
        raise ValueError(
            f"No price data returned for '{ticker}' ({start_date} to {end}). "
            "Ticker may be delisted or not recognised."
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
        raise ValueError(f"No usable price rows after cleaning for '{ticker}'.")

    df = pd.DataFrame(rows).set_index("price_date")
    df = df[df["close"] > 0]

    if df.empty:
        raise ValueError(f"No usable price rows after cleaning for '{ticker}'.")

    # Extract dividend events from the API response
    events = result.get("events") or {}
    div_rows: list[tuple] = []
    for ts_key, div_data in (events.get("dividends") or {}).items():
        try:
            ex_dt = datetime.fromtimestamp(int(ts_key), tz=timezone.utc).date()
            amt   = float(div_data.get("amount", 0.0))
            if amt > 0:
                div_rows.append((_to_iso(ex_dt), amt))
        except (ValueError, TypeError, KeyError):
            pass

    # The bar still forming in an open session is RETURNED but never CACHED — see
    # unsettled_bar_date(). Callers get the live mark (get_prices concatenates this
    # frame into its result), while the cache holds settled closes only. Skipping
    # it costs a re-fetch until the session closes; writing it costs a permanently
    # wrong row, because nothing ever re-reads a date at or below cached_end.
    skip_date = unsettled_bar_date(result)

    with get_connection() as conn:
        # Auto-migrate: ensure dividends table exists in pre-existing DBs
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dividends (
                ticker TEXT NOT NULL, ex_date TEXT NOT NULL, amount REAL NOT NULL,
                PRIMARY KEY (ticker, ex_date))"""
        )
        for dt, row in df.iterrows():
            if skip_date is not None and dt >= skip_date:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close)
                   VALUES (?, ?, ?, ?)""",
                (
                    ticker,
                    _to_iso(dt),
                    float(row["close"]),
                    float(row["adj_close"]) if pd.notna(row["adj_close"]) else None,
                ),
            )
        # Dividends are ex-date events, not intraday quotes — an ex-date announced
        # during an open session is already final, so they are not gated here.
        for ex_date, amount in div_rows:
            conn.execute(
                "INSERT OR REPLACE INTO dividends (ticker, ex_date, amount) VALUES (?, ?, ?)",
                (ticker, ex_date, amount),
            )

    return df


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


def get_prices(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Return cached prices, fetching only gaps from yfinance.
    Returns DataFrame indexed by datetime.date with columns: close, adj_close.

    The bar of a currently-open session is served but never cached (see
    fetch_prices / unsettled_bar_date), so a live mark still reaches the caller
    while the DB holds settled closes only.
    """
    end = end_date or date.today().isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT price_date, close, adj_close
               FROM prices
               WHERE ticker = ? AND price_date >= ? AND price_date <= ?
               ORDER BY price_date""",
            (ticker, start_date, end),
        ).fetchall()

    if not rows:
        return fetch_prices(ticker, start_date, end)

    cached = pd.DataFrame(
        [(r["price_date"], r["close"], r["adj_close"]) for r in rows],
        columns=["price_date", "close", "adj_close"],
    )
    cached.index = pd.to_datetime(cached["price_date"]).dt.date
    cached = cached.drop(columns=["price_date"])

    cached_start = _to_iso(cached.index.min())
    cached_end   = _to_iso(cached.index.max())

    # Fetch any missing leading data
    if cached_start > start_date:
        pre_end = _to_iso(cached.index.min() - timedelta(days=1))
        try:
            pre = fetch_prices(ticker, start_date, pre_end)
            cached = pd.concat([pre, cached]).sort_index()
        except Exception:
            pass

    # Fetch any missing trailing data
    if cached_end < end:
        post_start = _to_iso(cached.index.max() + timedelta(days=1))
        try:
            post = _fetch_trailing_memoized(ticker, post_start, end)
            cached = pd.concat([cached, post]).sort_index()
        except Exception:
            pass

    # Dedup index: trailing/leading fetches can return dates that overlap the
    # cached range when the Yahoo Finance API's UTC-midnight period boundary
    # maps to the previous US trading day (e.g., requesting "2026-05-06"
    # returns May-5 data already in the cache).  Keep the last occurrence so
    # the most-recently fetched value wins.
    if cached.index.duplicated().any():
        cached = cached[~cached.index.duplicated(keep="last")]

    return cached


def _cached_row_count(ticker: str, start_date: str, end: str) -> int:
    """How many settled rows the cache already holds for this window.

    Returns 0 when the prices table does not exist (a minimal fixture DB, a
    freshly created file): "no cached rows" is the honest answer to the question
    asked, not a swallowed error — it is also exactly what the caller would
    conclude from an empty table.
    """
    try:
        with get_connection() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM prices WHERE ticker = ? AND price_date >= ? "
                "AND price_date <= ?",
                (ticker, start_date, end),
            ).fetchone()[0])
    except Exception:                                            # noqa: BLE001
        return 0


def classify_miss(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
    *,
    error: Optional[BaseException] = None,
) -> str:
    """Why a price lookup produced nothing usable, as a coverage reason code.

    THIS IS THE WHOLE REASON COVERAGE IS BUILT IN TWO LAYERS. ``get_prices``
    raises on every failure path, so from a caller's ``except`` clause a blocked
    network and an empty cache arrive as the same ValueError. Only the price layer
    can consult its own cache first and tell them apart. A caller that classified
    misses from its own except clause would report every one of them as
    ``fetch_failed``, and the reason code is not decoration — it decides whether a
    gap is a legitimate degradation (a delisted holding) or a defect (#188).

    Deliberately a classifier rather than a fetching wrapper: the fetch stays on
    whichever ``get_prices`` the caller resolves, so a test that monkeypatches it
    keeps working and there is still exactly one fetch implementation.
    """
    end = end_date or date.today().isoformat()
    if _cached_row_count(ticker, start_date, end) > 0:
        # Rows exist for the window, so the gap fetches inside get_prices already
        # swallowed their own failures; an error reaching the caller past that is
        # a genuine fetch failure rather than a cold cache.
        return "fetch_failed" if error is not None else "empty_window"
    return "no_cached_rows"


def get_dividends(ticker: str, start_date: str, end_date: str) -> pd.Series:
    """
    Return dividend-per-share amounts indexed by ex_date for ticker in [start_date, end_date].
    Fetches from Yahoo Finance (and caches) if not already stored.
    Returns an empty Series if no dividends exist in the range.
    """
    end = end_date or date.today().isoformat()

    def _query_cache() -> list:
        with get_connection() as conn:
            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS dividends (
                        ticker TEXT NOT NULL, ex_date TEXT NOT NULL, amount REAL NOT NULL,
                        PRIMARY KEY (ticker, ex_date))"""
                )
                return conn.execute(
                    """SELECT ex_date, amount FROM dividends
                       WHERE ticker = ? AND ex_date >= ? AND ex_date <= ?
                       ORDER BY ex_date""",
                    (ticker, start_date, end),
                ).fetchall()
            except Exception:
                return []

    rows = _query_cache()
    if not rows:
        # Prices may be cached but dividends not yet (first run after migration).
        # Re-fetch prices — fetch_prices now also stores dividends as a side effect.
        try:
            fetch_prices(ticker, start_date, end)
            rows = _query_cache()
        except Exception:
            pass

    if not rows:
        return pd.Series(dtype=float)

    s = pd.Series({r["ex_date"]: float(r["amount"]) for r in rows})
    s.index = pd.to_datetime(s.index).date  # DatetimeIndex → array of datetime.date
    return s.sort_index()


def bulk_refresh(tickers: list, start_date: str) -> None:
    """
    Pre-warm the price cache for multiple tickers from start_date to today.
    Skips individual failures with a warning rather than raising.
    """
    end = date.today().isoformat()
    errors = []

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
