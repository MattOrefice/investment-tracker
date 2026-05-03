"""CAPE data from Robert Shiller's Yale dataset (shillerdata.com).

Known fragility: the Excel URL and sheet structure can change without notice.
If download fails, the module falls back to the local CSV cache in data/.
To manually refresh: delete data/shiller_cape.csv and reload the page.
"""
import io
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

_ROOT      = Path(__file__).resolve().parent.parent
_CACHE_CSV = _ROOT / "data" / "shiller_cape.csv"

_URLS = [
    "http://www.econ.yale.edu/~shiller/data/ie_data.xls",  # primary — reliable direct download
    "https://shillerdata.com/ie_data.xls",                 # fallback — sometimes redirects to HTML
]

_REFRESH_DAYS = 30


# ── date parsing ─────────────────────────────────────────────────────────────

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


# ── Excel parsing ─────────────────────────────────────────────────────────────

def _parse_excel_bytes(content: bytes) -> pd.DataFrame:
    """Parse Shiller's ie_data.xls bytes into (date, sp500_real, earnings_real, cape)."""
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

    # Drop entirely-unnamed or entirely-NaN columns
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
            "sp500_real":    float(row[real_price_col]) if real_price_col and pd.notna(row[real_price_col]) else None,
            "earnings_real": float(row[real_earn_col])  if real_earn_col  and pd.notna(row[real_earn_col])  else None,
            "cape":          cape_f,
        })

    if not rows:
        raise RuntimeError("No usable rows found after parsing the Shiller Excel file.")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ── public API ────────────────────────────────────────────────────────────────

def download_shiller_data() -> pd.DataFrame:
    """Download ie_data.xls, parse, cache as CSV. Falls back to cached CSV on failure."""
    last_exc: Exception = RuntimeError("No download attempted.")
    for url in _URLS:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            df = _parse_excel_bytes(resp.content)
            _CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(_CACHE_CSV, index=False)
            return df
        except Exception as exc:
            last_exc = exc

    if _CACHE_CSV.exists():
        return pd.read_csv(_CACHE_CSV, parse_dates=["date"])

    raise RuntimeError(
        f"Shiller CAPE data unavailable. Download failed ({last_exc}). "
        "No local cache exists. Manually download ie_data.xls from "
        "https://shillerdata.com/ and place it in the project root."
    )


def get_cape_series() -> pd.Series:
    """
    Date-indexed Series of CAPE values from 1881 to present.
    Refreshes from Shiller's site when the local cache is ≥30 days old.
    """
    needs_refresh = True
    if _CACHE_CSV.exists():
        mtime_date = date.fromtimestamp(_CACHE_CSV.stat().st_mtime)
        needs_refresh = (date.today() - mtime_date).days >= _REFRESH_DAYS

    if needs_refresh:
        try:
            df = download_shiller_data()
        except Exception:
            if _CACHE_CSV.exists():
                df = pd.read_csv(_CACHE_CSV, parse_dates=["date"])
            else:
                raise
    else:
        df = pd.read_csv(_CACHE_CSV, parse_dates=["date"])

    df = df.dropna(subset=["cape"])
    s = pd.Series(df["cape"].values, index=pd.DatetimeIndex(df["date"]), name="CAPE")
    return s.sort_index()


def current_cape() -> float:
    """Return the most recent CAPE value."""
    return float(get_cape_series().dropna().iloc[-1])
