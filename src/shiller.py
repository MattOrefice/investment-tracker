"""CAPE data sourced from Robert Shiller's dataset via multpl.com.

Primary source: https://www.multpl.com/shiller-pe/table/by-month
  - Full history 1871-present, updated monthly.
  - Yale's ie_data.xls has been stale at Sep 2023 since at least May 2026
    (no further updates to that file); multpl.com is the live replacement.

Fallback: http://www.econ.yale.edu/~shiller/data/ie_data.xls
  - Used only if multpl.com is unreachable; last reliable through Sep 2023.

data/shiller_cape.csv is a COMMITTED INPUT: get_cape_series() reads it and
never fetches or writes; tools/refresh_market_data.py is the only writer
(fetch_cape_dataframe() below is its fetch half). Staleness is surfaced via
cape_frontier() + asof.staleness_note, never silently repaired at read time.
"""
import io
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

_ROOT      = Path(__file__).resolve().parent.parent
_CACHE_CSV = _ROOT / "data" / "shiller_cape.csv"

_MULTPL_URL = "https://www.multpl.com/shiller-pe/table/by-month"
_YALE_URL   = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"


# ── date parsing (Shiller fractional-year format) ─────────────────────────────

def _parse_shiller_date(val) -> Optional[pd.Timestamp]:
    """Convert Shiller fractional-year date (e.g. 1881.01) to Timestamp."""
    try:
        val_f = round(float(val), 2)
        year  = int(val_f)
        frac  = round(val_f - year, 2)   # 0.01 … 0.12
        month = max(1, min(12, round(frac * 100)))
        return pd.Timestamp(year=year, month=month, day=1)
    except Exception:
        return None


# ── multpl.com parser (primary source) ───────────────────────────────────────

def _parse_multpl(html_content: str) -> pd.DataFrame:
    """
    Parse the multpl.com Shiller P/E monthly table.
    Returns DataFrame with columns: date (Timestamp), cape (float), sorted ascending.
    Dates are normalised to the 1st of each month.
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
        try:
            dt = pd.to_datetime(row["Date"], format="%b %d, %Y", errors="coerce")
            if pd.isna(dt):
                dt = pd.to_datetime(row["Date"], errors="coerce")
            if pd.isna(dt):
                continue
            dt = dt.replace(day=1)   # normalise to month start
            cape_val = float(row["Value"])
            if cape_val <= 0:
                continue
            rows.append({"date": dt, "cape": cape_val})
        except Exception:
            continue

    if not rows:
        raise RuntimeError("No usable rows parsed from multpl.com response.")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ── Yale Excel parser (fallback) ──────────────────────────────────────────────

def _parse_excel_bytes(content: bytes) -> pd.DataFrame:
    """Parse Shiller's ie_data.xls bytes into (date, cape) DataFrame."""
    raw = None
    for engine in ("xlrd", "openpyxl"):
        try:
            raw = pd.read_excel(
                io.BytesIO(content), sheet_name="Data", engine=engine, header=7
            )
            break
        except Exception:
            continue

    if raw is None:
        raise RuntimeError("Could not parse Shiller Excel with xlrd or openpyxl.")

    raw = raw.loc[:, ~raw.columns.astype(str).str.startswith("Unnamed")]
    raw = raw.dropna(how="all")

    cols_lower = {str(c).lower(): c for c in raw.columns}

    date_col = next(
        (cols_lower[k] for k in cols_lower if "date" in k),
        raw.columns[0],
    )
    cape_col = next(
        (cols_lower[k] for k in cols_lower if "cape" in k or "p/e10" in k),
        None,
    )
    if cape_col is None:
        raise RuntimeError(
            f"CAPE column not found. Available columns: {list(raw.columns)}"
        )

    real_price_col = next(
        (cols_lower[k] for k in cols_lower if "real" in k and "price" in k), None
    )
    real_earn_col = next(
        (cols_lower[k] for k in cols_lower if "real" in k and "earn" in k), None
    )

    rows = []
    for _, row in raw.iterrows():
        dt = _parse_shiller_date(row[date_col])
        if dt is None:
            continue
        cape_raw = row[cape_col]
        if pd.isna(cape_raw):
            continue
        try:
            cape_f = float(cape_raw)
        except (TypeError, ValueError):
            continue
        if cape_f <= 0:
            continue
        rows.append({
            "date":          dt,
            "cape":          cape_f,
            "sp500_real":    float(row[real_price_col]) if real_price_col and pd.notna(row[real_price_col]) else None,
            "earnings_real": float(row[real_earn_col])  if real_earn_col  and pd.notna(row[real_earn_col])  else None,
        })

    if not rows:
        raise RuntimeError("No usable rows found after parsing the Shiller Excel file.")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ── public API ────────────────────────────────────────────────────────────────

def fetch_cape_dataframe() -> pd.DataFrame:
    """Fetch and parse the CAPE series. TOOL-ONLY — never writes.

    Primary:  multpl.com HTML table (full history 1871-present, monthly updates).
    Fallback: Yale ie_data.xls (known stale at Sep 2023 as of May 2026).
    Raises when both fail; tools/refresh_market_data.py owns the write and
    reports fetch and write outcomes separately, so a blocked write can never
    masquerade as a network failure (the old combined path did exactly that).
    """
    try:
        resp = requests.get(
            _MULTPL_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; investment-tracker/1.0)"},
        )
        resp.raise_for_status()
        return _parse_multpl(resp.text)
    except Exception:
        pass

    resp = requests.get(_YALE_URL, timeout=30)
    resp.raise_for_status()
    return _parse_excel_bytes(resp.content)


def get_cape_series() -> pd.Series:
    """
    Date-indexed Series of CAPE values from 1881 through the committed frontier.

    Reads the COMMITTED CSV only — never fetches, never writes. The tracked
    file is an input, refreshed exclusively by tools/refresh_market_data.py;
    the old mtime-triggered auto-refresh rewrote a tracked file from ordinary
    renders, and its Force-refresh companion (clear_shiller_cache, removed)
    deleted it outright. Staleness is surfaced via cape_frontier() +
    asof.staleness_note on every consumer, not silently repaired here.
    """
    if not _CACHE_CSV.exists():
        raise FileNotFoundError(
            f"Committed CAPE data missing: {_CACHE_CSV}. Restore it from git, "
            "or regenerate it with tools/refresh_market_data.py."
        )
    df = pd.read_csv(_CACHE_CSV, parse_dates=["date"])
    df = df.dropna(subset=["cape"])
    s = pd.Series(df["cape"].values, index=pd.DatetimeIndex(df["date"]), name="CAPE")
    return s.sort_index()


def cape_frontier() -> "date | None":
    """Last committed CAPE month (data frontier, never mtime). None if unreadable."""
    try:
        s = get_cape_series()
        return s.index[-1].date() if len(s) else None
    except Exception:
        return None


def current_cape() -> float:
    """Return the most recent CAPE value."""
    return float(get_cape_series().dropna().iloc[-1])
