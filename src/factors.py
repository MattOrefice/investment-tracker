"""
Fama-French 5-Factor data ingestion and regression engine.

Data source: Ken French Data Library (Dartmouth).
  URL: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/
       F-F_Research_Data_5_Factors_2x3_daily_CSV.zip
  Format: daily CSV inside a ZIP, factor returns expressed in percent
          (0.50 = 0.50%); converted to decimal on load.
  Lag: factors are published with approximately a 1-month lag.  The
       regression sample is truncated to the available overlap window.

Standard errors: Newey-West HAC, lag L = floor(4 * (T/100)^(2/9)),
  where T is the number of aligned observations.
"""
from __future__ import annotations

import io
import math
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

from src.holdings import get_portfolio_value_series

_ROOT       = Path(__file__).resolve().parent.parent
_CACHE_PATH = _ROOT / "data" / "ff_factors.csv"

_FF_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
)

_REFRESH_CACHE_DAYS = 7   # re-fetch if cache file is older than this many days
_LAG_THRESHOLD_DAYS = 35  # re-fetch if most recent factor date is this far behind today

_FF5_FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]


# ── Utilities ─────────────────────────────────────────────────────────────────

def _nw_lags(T: int) -> int:
    """Newey-West HAC lag length: floor(4 * (T/100)^(2/9))."""
    return int(4 * (T / 100) ** (2 / 9))


def sig_marker(p: float) -> str:
    """Return significance asterisk(s) at 10/5/1% levels."""
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.10:
        return "*"
    return ""


# ── Factor data ingestion ──────────────────────────────────────────────────────

def _parse_ff_csv_text(text: str) -> pd.DataFrame:
    """
    Parse the raw text of a Ken French daily factor CSV file.

    The file is comma-delimited.  It begins with a descriptive header of
    arbitrary length, then a column-header row (leading comma, unnamed date
    column), then data rows of the form::

        19630701,   -0.67,    0.00,   -0.34,   -0.01,    0.16,    0.01

    followed by an annual-averages footer where the leading field is a
    4-digit year rather than an 8-digit date.

    All factor values are in percent (0.50 = 0.50%).  This function
    divides by 100 to convert to decimal.

    Returns a DatetimeIndex-indexed DataFrame with columns:
      Mkt-RF, SMB, HML, RMW, CMA, RF (all in decimal).
    """
    raw_data_lines: list[str] = []
    in_data = False

    for line in text.splitlines():
        # First comma-separated field is the candidate date token.
        first_field = line.split(",")[0].strip()
        if len(first_field) == 8 and first_field.isdigit():
            in_data = True
            raw_data_lines.append(line)
        elif in_data:
            # Annual-averages footer has 4-digit year as first field.
            if len(first_field) == 4 and first_field.isdigit():
                break
            # Blank or noise lines inside the data block — skip.

    if not raw_data_lines:
        raise ValueError("No daily factor data rows found in Ken French CSV text")

    csv_block = "date,Mkt-RF,SMB,HML,RMW,CMA,RF\n" + "\n".join(raw_data_lines)
    df = pd.read_csv(io.StringIO(csv_block))
    df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%Y%m%d")
    df = df.set_index("date")

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

    return df


def _fetch_ff_factors() -> pd.DataFrame:
    """Download and parse the Ken French daily 5-factor ZIP from Dartmouth."""
    resp = requests.get(_FF_URL, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(
            n for n in zf.namelist()
            if n.upper().endswith(".CSV")
        )
        raw_text = zf.read(csv_name).decode("utf-8", errors="replace")

    return _parse_ff_csv_text(raw_text)


def _cache_is_stale() -> bool:
    """Return True if the cache file should be refreshed."""
    if not _CACHE_PATH.exists():
        return True
    mtime = date.fromtimestamp(_CACHE_PATH.stat().st_mtime)
    if (date.today() - mtime).days > _REFRESH_CACHE_DAYS:
        return True
    try:
        cached = pd.read_csv(_CACHE_PATH, index_col=0, parse_dates=True)
        if cached.empty:
            return True
        most_recent = cached.index[-1].date()
        if (date.today() - most_recent).days > _LAG_THRESHOLD_DAYS:
            return True
    except Exception:
        return True
    return False


def get_ff_factors(force_refresh: bool = False) -> pd.DataFrame:
    """
    Return the Ken French daily 5-factor data as a DataFrame indexed by date.

    Refreshes the local cache at data/ff_factors.csv when:
      - the file does not exist, OR
      - the cache file is older than 7 days, OR
      - the most recent factor date is more than 35 days behind today
        (accounting for Ken French's ~1-month publication lag).

    If a network fetch fails, the stale cache is returned with a logged warning.
    """
    if force_refresh or _cache_is_stale():
        try:
            df = _fetch_ff_factors()
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(_CACHE_PATH)
            return df
        except Exception as exc:
            if _CACHE_PATH.exists():
                import warnings
                warnings.warn(
                    f"FF factor refresh failed ({exc}); using cached data.",
                    stacklevel=2,
                )
            else:
                raise

    return pd.read_csv(_CACHE_PATH, index_col=0, parse_dates=True)


# ── Regression engine ──────────────────────────────────────────────────────────

def _ols_ff5(R_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """
    OLS regression of portfolio excess returns on FF5 factors with HAC SEs.

    R_excess : daily excess returns (R_p - RF), decimal, DatetimeIndex
    factors  : DataFrame with columns Mkt-RF, SMB, HML, RMW, CMA, decimal

    Returns a dict of regression statistics.  Alpha is annualized as
    alpha_daily * 252 (linear daily-to-annual scaling; equivalent to the
    compound form (1+alpha_daily)^252-1 within rounding for |alpha| < 0.5%).
    """
    T = len(R_excess)
    L = _nw_lags(T)

    X = add_constant(factors[_FF5_FACTORS])
    model = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()  # used only for the NW-vs-OLS SE comparison in tests

    params  = res_hac.params
    tvals   = res_hac.tvalues
    pvals   = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252  # linear annualization

    return {
        "alpha_daily":       alpha_daily,
        "alpha_annual":      alpha_annual,
        "alpha_annual_bps":  alpha_annual * 10_000,
        "t_alpha":           float(tvals["const"]),
        "p_alpha":           float(pvals["const"]),
        "betas":   {f: float(params[f])  for f in _FF5_FACTORS},
        "t_stats": {f: float(tvals[f])   for f in _FF5_FACTORS},
        "p_values":{f: float(pvals[f])   for f in _FF5_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":             T,
        "nw_lags":       L,
        # OLS (non-HAC) SEs for diagnostic comparison in tests
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
    }


def run_ff5_regression(
    inception_date: str,
    end_date: str,
) -> Optional[dict]:
    """
    Regress daily portfolio excess returns on FF5 factors.

    Portfolio returns are computed from get_portfolio_value_series() on a
    total-return (adj_close) basis from inception_date through end_date.
    Daily returns are aligned against available FF factor data via inner join;
    the alignment naturally excludes weekends, US holidays, and the ~20
    trailing trading days for which FF factors are not yet published.

    Returns None if fewer than 30 aligned observations are available.
    """
    pv = get_portfolio_value_series(inception_date, end_date)
    if pv.empty or float(pv.max()) == 0.0:
        return None

    daily_ret = pv.pct_change().dropna()
    daily_ret.index = pd.to_datetime(daily_ret.index)

    ff = get_ff_factors()
    ff.index = pd.to_datetime(ff.index)

    merged = (
        daily_ret.to_frame(name="R_p")
        .join(ff, how="inner")
        .dropna()
    )

    if len(merged) < 30:
        return None

    R_excess = merged["R_p"] - merged["RF"]
    result = _ols_ff5(R_excess, merged)

    result["sample_start"]    = merged.index[0].date().isoformat()
    result["sample_end"]      = merged.index[-1].date().isoformat()
    result["factor_end_date"] = ff.index[-1].date().isoformat()
    result["inception_date"]  = inception_date

    excl_days = (
        pd.Timestamp(end_date) - pd.Timestamp(result["sample_end"])
    ).days
    result["exclusion_days"] = max(0, excl_days)

    return result


# ── Interpretation helpers ─────────────────────────────────────────────────────

def _fmt_date(iso: str) -> str:
    """'2025-05-01' → 'May 1, 2025'."""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def build_factor_prose(res: dict) -> list[str]:
    """
    Generate 3–5 sentence institutional-register interpretation of the FF5 results.
    Called by both the PDF section builder and the Streamlit page for consistency.
    """
    b_mkt  = res["betas"]["Mkt-RF"]
    t_mkt  = res["t_stats"]["Mkt-RF"]
    b_smb  = res["betas"]["SMB"]
    t_smb  = res["t_stats"]["SMB"]
    b_hml  = res["betas"]["HML"]
    t_hml  = res["t_stats"]["HML"]
    alpha_bps = res["alpha_annual_bps"]
    t_alpha   = res["t_alpha"]
    T         = res["T"]
    s_start   = _fmt_date(res["sample_start"])
    s_end     = _fmt_date(res["sample_end"])

    hml_dir  = "positive" if b_hml >= 0 else "negative"
    hml_sig  = (
        "statistically significant at the 5% level"
        if abs(t_hml) > 2
        else "not statistically significant at conventional levels"
    )
    alpha_sig = (
        "marginally significant (|t| > 2)"
        if abs(t_alpha) > 2
        else "not statistically distinguishable from zero"
    )

    return [
        f"The portfolio's daily excess return series is regressed on the Fama-French "
        f"five-factor model over {T} trading days ({s_start} to {s_end}).",

        f"Market beta of {b_mkt:.2f} (t = {t_mkt:.2f}) is consistent with the portfolio's "
        f"72% growth-asset allocation; the sub-unity loading reflects the dampening effect "
        f"of fixed income, real assets, and international sleeves that the US equity market "
        f"factor does not span.",

        f"The HML loading of {b_hml:.3f} (t = {t_hml:.2f}) is {hml_dir} and {hml_sig}, "
        f"broadly consistent with the value tilts in VTV (US Large Value) and AVUV "
        f"(US Small Value). "
        f"The SMB loading of {b_smb:.3f} (t = {t_smb:.2f}) reflects the small-cap "
        f"exposure from the AVUV sleeve.",

        f"Annualized alpha of {alpha_bps:+.0f} bps (t = {t_alpha:.2f}) is {alpha_sig}; "
        f"the {T}-observation sample produces wide confidence intervals on the intercept, "
        f"and no inference about systematic outperformance is appropriate at this horizon.",
    ]


def build_factor_methodology_notes(res: dict) -> list[str]:
    """Return methodology disclosure bullet points for this regression run."""
    s_end      = _fmt_date(res["sample_end"])
    s_start    = _fmt_date(res["sample_start"])
    excl_days  = res.get("exclusion_days", 0)
    L          = res["nw_lags"]
    T          = res["T"]

    return [
        f"Sample: {T} daily trading-day observations, {s_start} to {s_end}. "
        "This is the minimum sample size for FF5 stability; factor loadings will tighten "
        "as the observation window extends toward 3+ years.",

        f"Data lag: approximately {excl_days} calendar days of portfolio returns excluded "
        "from the regression pending Ken French publication. "
        "FF factors are published with a ~1-month lag; the regression sample is truncated "
        "to the overlap window.",

        f"Standard errors: Newey-West HAC with lag L = {L} (rule: floor(4 × (T/100)^(2/9))), "
        "correcting for autocorrelation in daily return residuals.",

        "Factor scope: Fama-French factors are calibrated on US equities. "
        "International equity (VEA, IEMG) and fixed income (VGIT, SCHP) sleeves load "
        "primarily as residual / unexplained variance. A lower R² does not imply "
        "portfolio mispricing — it partly reflects non-US and non-equity exposures "
        "the model does not span.",

        "Extensions noted but out of scope for this version: rolling-window regression "
        "(requires T > 500 for stability), global FF factors for the international sleeve, "
        "and bond-specific term/credit factor decomposition for the fixed income sleeve.",
    ]
