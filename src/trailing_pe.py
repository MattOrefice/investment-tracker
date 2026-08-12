"""S&P 500 trailing-twelve-month P/E sourced from multpl.com.

Primary source: https://www.multpl.com/s-p-500-pe-ratio/table/by-month
  - Full history 1871-present. Same site and parser pattern as the Shiller
    CAPE loader (src/shiller.py), so the two valuation lenses share one
    provenance style.
  - multpl marks recent months with a leading estimate dagger ("† 28.89")
    while the underlying earnings are still preliminary. src/shiller.py's
    strict float() would silently DROP those rows (the CAPE table carries no
    daggers, so it never needed to care); here the marker is stripped and the
    value kept — the recent, provisional readings are exactly the ones the
    Valuation page displays, flagged as provisional in the caption.

data/trailing_pe.csv is a COMMITTED INPUT (the shiller_cape.csv pattern):
get_trailing_pe() reads it and never fetches or writes;
tools/refresh_market_data.py is the only writer (fetch_trailing_pe_dataframe()
is its fetch half). No fallback source: unlike CAPE there is no secondary
publisher worth trusting, so the tool's fetch raises on failure and the
committed file stays as-is. Staleness is surfaced via trailing_pe_frontier() +
asof.staleness_note, never silently repaired at read time.
"""
import io
import re
from datetime import date
from pathlib import Path

import pandas as pd
import requests

_ROOT      = Path(__file__).resolve().parent.parent
_CACHE_CSV = _ROOT / "data" / "trailing_pe.csv"

_MULTPL_URL = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"

# A P/E cell is "28.89", "† 28.89", or similar marker + number. Extract the
# first decimal number; reject cells with none.
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _parse_pe_value(val) -> float | None:
    """'† 28.89' / '28.89' -> 28.89; None when no positive number present."""
    m = _NUM.search(str(val).replace(",", ""))
    if not m:
        return None
    f = float(m.group())
    return f if f > 0 else None


def _parse_multpl_pe(html_content: str) -> pd.DataFrame:
    """Parse the multpl.com S&P 500 P/E monthly table.

    Returns DataFrame with columns:
      date     — month-start Timestamp (normalised, one row per month)
      pe       — trailing-twelve-month P/E
      obs_date — the table's actual observation date for that row (the top
                 row is the current partial month, e.g. 'Jul 21, 2026'; kept
                 so the page can show a precise as-of instead of 'Jul 2026')
    Sorted ascending. Where the current-month reading and the month-start row
    would collide after normalisation, the fresher reading wins.
    """
    try:
        tables = pd.read_html(io.StringIO(html_content))
    except ValueError as exc:
        raise RuntimeError(f"No tables found in multpl.com response: {exc}") from exc
    if not tables:
        raise RuntimeError("No tables found in multpl.com response.")

    t = tables[0]
    if "Date" not in t.columns or "Value" not in t.columns:
        raise RuntimeError(f"Unexpected multpl.com columns: {list(t.columns)}")

    rows = []
    for _, row in t.iterrows():
        dt = pd.to_datetime(row["Date"], format="%b %d, %Y", errors="coerce")
        if pd.isna(dt):
            dt = pd.to_datetime(row["Date"], errors="coerce")
        if pd.isna(dt):
            continue
        pe_val = _parse_pe_value(row["Value"])
        if pe_val is None:
            continue
        rows.append({"date": dt.replace(day=1), "pe": pe_val, "obs_date": dt})

    if not rows:
        raise RuntimeError("No usable rows parsed from multpl.com response.")

    df = pd.DataFrame(rows)
    # Table is newest-first; keep the freshest reading per normalised month.
    df = df.drop_duplicates(subset="date", keep="first")
    return df.sort_values("date").reset_index(drop=True)


def fetch_trailing_pe_dataframe() -> pd.DataFrame:
    """Fetch and parse the trailing P/E table. TOOL-ONLY — never writes.

    Raises on failure; tools/refresh_market_data.py owns the write and reports
    fetch and write outcomes separately, so a blocked write can never
    masquerade as a network failure (the old combined path did exactly that).
    """
    resp = requests.get(
        _MULTPL_URL,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; investment-tracker/1.0)"},
    )
    resp.raise_for_status()
    return _parse_multpl_pe(resp.text)


def get_trailing_pe() -> pd.DataFrame:
    """Full-history trailing P/E DataFrame (date, pe, obs_date), ascending.

    Reads the COMMITTED CSV only — never fetches, never writes (same policy as
    get_cape_series in src/shiller.py; tools/refresh_market_data.py is the
    only writer).
    """
    if not _CACHE_CSV.exists():
        raise FileNotFoundError(
            f"Committed trailing-P/E data missing: {_CACHE_CSV}. Restore it "
            "from git, or regenerate it with tools/refresh_market_data.py."
        )
    df = pd.read_csv(_CACHE_CSV, parse_dates=["date", "obs_date"])
    df = df.dropna(subset=["pe"])
    return df.sort_values("date").reset_index(drop=True)


def trailing_pe_frontier() -> "date | None":
    """Last committed P/E month (the ``date`` column's max — month-start basis,
    matching cape_frontier so the two valuation lenses read alike; the fresher
    intra-month ``obs_date`` is still shown on the panel itself). None if
    unreadable."""
    try:
        df = get_trailing_pe()
        return pd.Timestamp(df["date"].iloc[-1]).date() if len(df) else None
    except Exception:
        return None


def get_trailing_pe_series() -> pd.Series:
    """Date-indexed (month-start) Series of trailing P/E values."""
    df = get_trailing_pe()
    s = pd.Series(df["pe"].values, index=pd.DatetimeIndex(df["date"]), name="PE_TTM")
    return s.sort_index()


def current_trailing_pe() -> tuple[float, str]:
    """(most recent trailing P/E, ISO date of that observation)."""
    df = get_trailing_pe()
    last = df.iloc[-1]
    return float(last["pe"]), pd.Timestamp(last["obs_date"]).date().isoformat()


# clear_trailing_pe_cache was removed with the auto-refresh: deleting a
# COMMITTED input so "the next load re-downloads" is exactly the write-from-
# runtime pattern this module no longer permits.
