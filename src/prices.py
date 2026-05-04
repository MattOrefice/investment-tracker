"""Yahoo Finance price fetcher with SQLite caching.

Uses the v8/chart API directly (not yfinance) so browser User-Agent headers
are applied to every request, avoiding the 429 rate-limit that yfinance's
internal timezone-fetch triggers.
"""
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests

try:
    from src.db import get_connection
except ImportError:
    from db import get_connection

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


def _to_iso(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


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

    Raises ValueError for delisted / unrecognised tickers.
    """
    end = end_date or date.today().isoformat()

    # API period2 is inclusive — add one day to ensure end date is included
    period1 = _date_to_unix(start_date)
    period2 = _date_to_unix(
        _to_iso(date.fromisoformat(end) + timedelta(days=1))
    )

    url = _YF_CHART_URL.format(ticker=ticker)
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

    with get_connection() as conn:
        # Auto-migrate: ensure dividends table exists in pre-existing DBs
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dividends (
                ticker TEXT NOT NULL, ex_date TEXT NOT NULL, amount REAL NOT NULL,
                PRIMARY KEY (ticker, ex_date))"""
        )
        for dt, row in df.iterrows():
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
        for ex_date, amount in div_rows:
            conn.execute(
                "INSERT OR REPLACE INTO dividends (ticker, ex_date, amount) VALUES (?, ?, ?)",
                (ticker, ex_date, amount),
            )

    return df


def get_prices(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Return cached prices, fetching only gaps from yfinance.
    Returns DataFrame indexed by datetime.date with columns: close, adj_close.
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
            post = fetch_prices(ticker, post_start, end)
            cached = pd.concat([cached, post]).sort_index()
        except Exception:
            pass

    return cached


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
