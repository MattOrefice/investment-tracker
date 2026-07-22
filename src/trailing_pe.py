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

Local disk cache: data/trailing_pe.csv, refreshed every 30 days (the
shiller_cape.csv pattern). Force-invalidate via clear_trailing_pe_cache().

No fallback source: unlike CAPE there is no secondary publisher worth
trusting, so on a failed refresh the loader serves the stale cache (age
visible to the caller via the obs_date column) or raises if none exists.
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

_REFRESH_DAYS = 30

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


def download_trailing_pe() -> pd.DataFrame:
    """Download the trailing P/E table and cache as CSV.

    On failure serves the existing disk cache (staleness is the caller's to
    surface via obs_date); raises only when there is nothing to serve.
    """
    try:
        resp = requests.get(
            _MULTPL_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; investment-tracker/1.0)"},
        )
        resp.raise_for_status()
        df = _parse_multpl_pe(resp.text)
        _CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(_CACHE_CSV, index=False)
        return df
    except Exception:
        if _CACHE_CSV.exists():
            return pd.read_csv(_CACHE_CSV, parse_dates=["date", "obs_date"])
        raise


def get_trailing_pe() -> pd.DataFrame:
    """Full-history trailing P/E DataFrame (date, pe, obs_date), ascending.

    Refreshes from multpl.com when the local cache is >= 30 days old,
    mirroring get_cape_series() in src/shiller.py.
    """
    needs_refresh = True
    if _CACHE_CSV.exists():
        mtime_date = date.fromtimestamp(_CACHE_CSV.stat().st_mtime)
        needs_refresh = (date.today() - mtime_date).days >= _REFRESH_DAYS

    if needs_refresh:
        df = download_trailing_pe()
    else:
        df = pd.read_csv(_CACHE_CSV, parse_dates=["date", "obs_date"])

    df = df.dropna(subset=["pe"])
    return df.sort_values("date").reset_index(drop=True)


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


def clear_trailing_pe_cache() -> None:
    """Delete the local CSV cache so the next load forces a fresh download."""
    if _CACHE_CSV.exists():
        _CACHE_CSV.unlink()
