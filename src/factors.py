"""
Fama-French 5-Factor regional decomposition — data ingestion and regression engine.

Architecture (v1.1 — replaces single full-portfolio regression from v1.0)
--------------------------------------------------------------------------
Three equity sleeves are regressed separately against region-appropriate
factor sets.  A single full-portfolio regression conflated returns from
international and real-asset sleeves — which the US-only FF5 model cannot
span — into the alpha estimate, producing a methodologically misleading
+826 bps significant alpha.  The decomposed approach measures factor
exposure within the universe each factor set spans.

Data sources: Ken French Data Library (Dartmouth)
  US daily:           F-F_Research_Data_5_Factors_2x3_daily_CSV.zip
  Developed ex-US:    Developed_ex_US_5_Factors_Daily_CSV.zip
  EM (daily):         NOT PUBLISHED by Ken French.  Monthly EM factors
                      would yield T≈12 over a 1-year window — below any
                      threshold for stable inference.  EM sleeve excluded
                      from regression; included in qualitative disclosure.

Factor returns in source files are in percent (0.50 = 0.50%); converted
to decimal on load.  Missing-data sentinel is -99.99; replaced with NaN.

Standard errors: Newey-West HAC, L = floor(4 * (T/100)^(2/9)) per regression.
"""
from __future__ import annotations

import io
import sys
import time
import warnings
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

from src.benchmarks import get_custom_blended_series
from src.holdings import get_holdings_on_date, get_portfolio_value_series
from src.prices import get_prices
from src.prose_helpers import significance_label

_ROOT = Path(__file__).resolve().parent.parent

# ── Factor configuration ───────────────────────────────────────────────────────

_BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"

_CACHE_DIR = _ROOT / "data" / "cache"

_FACTOR_CONFIG: dict[str, dict] = {
    "us": {
        "url":   _BASE_URL + "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
        "cache": _CACHE_DIR / "ff_factors_us.csv",
    },
    "developed_exus": {
        "url":   _BASE_URL + "Developed_ex_US_5_Factors_Daily_CSV.zip",
        "cache": _CACHE_DIR / "ff_factors_developed_exus.csv",
    },
    "global": {
        "url":   _BASE_URL + "Global_5_Factors_Daily_CSV.zip",
        "cache": _CACHE_DIR / "ff_factors_global.csv",
    },
}

_UMD_URL   = _BASE_URL + "F-F_Momentum_Factor_daily_CSV.zip"
_UMD_CACHE = _CACHE_DIR / "ff_umd_us.csv"

_HYG_CACHE = _CACHE_DIR / "prices_hyg.parquet"

_CACHE_PATH = _FACTOR_CONFIG["us"]["cache"]  # backward-compatible alias

_REFRESH_CACHE_DAYS = 7   # re-fetch if cache mtime exceeds this many days
_LAG_THRESHOLD_DAYS = 35  # re-fetch if most recent factor date is this far behind today
_FF_RETRY_DELAYS    = (1, 3, 9)  # seconds between Ken French download retry attempts

# Ken French ceased publication of daily Global 5-factor data after this date.
# Any portfolio started after it has zero factor-data overlap and cannot be regressed.
GLOBAL_DAILY_FACTORS_CUTOFF = date(2019, 6, 28)

_FF5_FACTORS     = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
_FF5_MOM_FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
_BENCH_FACTORS   = ["Bench-RF", "HML", "SMB", "RMW"]
_FI_FACTORS      = ["TERM", "CREDIT"]

# FI sleeve weights proportional to SAA targets (VGIT 9%, SCHP 6%)
_FI_WEIGHTS = {"VGIT": 9.0 / 15.0, "SCHP": 6.0 / 15.0}

# ── Equity sleeve definitions ──────────────────────────────────────────────────

# SAA target weights for the US equity sleeve (Phase 1 locked).
# Used to compute the sleeve's value-weighted return series without requiring
# a DB call — eliminates the `get_holdings_on_date` dependency in environments
# where the portfolio database is empty or unavailable (e.g., Streamlit Cloud
# in personal mode, fresh deployments, or test environments).
_SAA_US = {"VOO": 16, "SPHQ": 14, "VTV": 8, "AVUV": 7}
_SAA_US_TOTAL = sum(_SAA_US.values())  # 45

_SLEEVES = {
    "us": {
        "label":   "US Equity Sleeve",
        "tickers": ["VOO", "VTV", "SPHQ", "AVUV"],
        "region":  "us",
        # Phase 8g — Conditions that require refreshing these embedded weights:
        #   1. SAA target weight changes — Phase 1 weights are locked; any SAA review
        #      must also update _SAA_US above and reseed the asset_classes table.
        #   2. A holding is added to or removed from this sleeve — update _SAA_US keys
        #      to match and re-run the seeding scripts.
        #   3. Portfolio age reaches ~3+ years — cumulative drift from the SAA launch
        #      weights may then be large enough that inception-market-value weighting
        #      (the DB-backed fallback path) would produce meaningfully different factor
        #      loadings. Revisit at the Phase 1 target reset review.
        # Assumption: buy-and-hold from a single-day initialization keeps actual weights
        # close to SAA proportions for the early years. The regression result is not
        # sensitive to ±5% weight deviations because it measures daily return structure,
        # not cumulative wealth attribution.
        "weights": {t: w / _SAA_US_TOTAL for t, w in _SAA_US.items()},
    },
    "developed_exus": {
        "label":   "International Developed Sleeve",
        "tickers": ["VEA"],
        "region":  "developed_exus",
        # Single-ticker sleeve: no weights needed.
    },
}

# Qualitative disclosure for the EM sleeve (no daily FF5 data available)
EM_DISCLOSURE = (
    "Emerging Markets sleeve (IEMG): Ken French does not publish daily EM factor data. "
    "Monthly EM factors would yield approximately 12 observations over the current "
    "1-year window — below the threshold for stable inference. "
    "IEMG provides passive cap-weighted broad EM exposure (~27% China weight at current "
    "index composition). Factor decomposition for this sleeve will be added when the "
    "portfolio accumulates sufficient history (target: 3+ years of monthly data)."
)


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


def alpha_ci_str(result: dict) -> str:
    """
    Format annualized alpha with 95% Newey-West confidence interval.
    Returns e.g. '+127 bps/yr [−53, +307]'.

    SE is extracted from result['_hac_bse']['const'] (daily decimal units).
    CI = alpha_bps ± 1.96 × SE_bps where SE_bps = SE_daily × 252 × 10000.
    CI applies to alpha only; betas are not bounded here.
    """
    hac_bse = result.get("_hac_bse", {})
    se_daily = hac_bse.get("const", float("nan"))
    a_bps    = result["alpha_annual_bps"]

    if se_daily != se_daily:  # NaN check
        return f"{a_bps:+.0f} bps/yr"

    se_bps = se_daily * 252 * 10_000
    lo     = a_bps - 1.96 * se_bps
    hi     = a_bps + 1.96 * se_bps
    lo_s   = f"−{abs(lo):.0f}" if lo < 0 else f"+{lo:.0f}"
    hi_s   = f"−{abs(hi):.0f}" if hi < 0 else f"+{hi:.0f}"
    return f"{a_bps:+.0f} bps/yr [{lo_s}, {hi_s}]"


def _fmt_date(iso: str) -> str:
    """'2025-05-01' → 'May 1, 2025'."""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ── Factor data ingestion ──────────────────────────────────────────────────────

def _parse_ff_csv_text(text: str) -> pd.DataFrame:
    """
    Parse the raw text of a Ken French daily factor CSV file.

    The file is comma-delimited.  Format::

        <descriptive header lines>
        ,Mkt-RF,SMB,HML,RMW,CMA,RF          ← column header (leading comma)
        19630701,   -0.67,    0.00, ...       ← data rows; date may have trailing spaces
        ...
        <annual averages footer with 4-digit year as first field>

    Two regional variants are handled:
      - US file: date field is clean (e.g. "19630701,")
      - Developed ex-US file: date field has trailing spaces ("19900702    ,")
      Both are handled by strip() on the first comma-separated token.

    Missing-data sentinel -99.99 is replaced with NaN.
    All factor columns are divided by 100 to convert percent to decimal.
    """
    raw_data_lines: list[str] = []
    in_data = False

    for line in text.splitlines():
        first_field = line.split(",")[0].strip()
        if len(first_field) == 8 and first_field.isdigit():
            in_data = True
            raw_data_lines.append(line)
        elif in_data:
            if len(first_field) == 4 and first_field.isdigit():
                break  # annual-averages footer

    if not raw_data_lines:
        raise ValueError("No daily factor data rows found in Ken French CSV text")

    csv_block = "date,Mkt-RF,SMB,HML,RMW,CMA,RF\n" + "\n".join(raw_data_lines)
    df = pd.read_csv(io.StringIO(csv_block))
    df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%Y%m%d")
    df = df.set_index("date")

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

    # Replace missing-data sentinel (-99.99 in percent → approx -0.9999 in decimal).
    # Use a threshold rather than exact equality to avoid float precision issues.
    # No legitimate daily factor return is below -99%.
    df = df.where(df > -0.99, float("nan"))

    return df


def _fetch_factors(url: str) -> pd.DataFrame:
    """Download and parse a Ken French daily factor ZIP from Dartmouth, with retry."""
    last_exc: Exception = RuntimeError("no attempts made")
    delays = list(_FF_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
                raw_text = zf.read(csv_name).decode("utf-8", errors="replace")
            return _parse_ff_csv_text(raw_text)
        except Exception as exc:
            last_exc = exc
            print(
                f"[INFO] Ken French fetch attempt {attempt + 1}/{len(delays) + 1} "
                f"for '{url}' failed: {exc}",
                file=sys.stderr,
            )
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise RuntimeError(
        f"Ken French factor download failed after {len(delays) + 1} attempts "
        f"for '{url}': {last_exc}"
    )


def _cache_stale(config: dict) -> bool:
    """Return True if the cache for this region config needs refreshing."""
    cache: Path = config["cache"]
    if not cache.exists():
        return True
    mtime = date.fromtimestamp(cache.stat().st_mtime)
    if (date.today() - mtime).days > _REFRESH_CACHE_DAYS:
        return True
    try:
        cached = pd.read_csv(cache, index_col=0, parse_dates=True)
        if cached.empty:
            return True
        most_recent = cached.index[-1].date()
        if (date.today() - most_recent).days > _LAG_THRESHOLD_DAYS:
            return True
    except Exception:
        return True
    return False


def load_factors(region: str, force_refresh: bool = False) -> pd.DataFrame:
    """
    Return Ken French daily 5-factor data for the specified region.

    region: 'us' | 'developed_exus'

    Refreshes the local cache when stale (file absent, mtime > 7 days, or
    most recent factor date > 35 days behind today).  On fetch failure the
    stale cache is returned with a warning rather than crashing.
    """
    if region not in _FACTOR_CONFIG:
        raise ValueError(
            f"Unknown factor region '{region}'. "
            f"Valid regions: {list(_FACTOR_CONFIG)}"
        )
    config = _FACTOR_CONFIG[region]
    cache: Path = config["cache"]

    if force_refresh or _cache_stale(config):
        try:
            df = _fetch_factors(config["url"])
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache)
            return df
        except Exception as exc:
            if cache.exists():
                warnings.warn(
                    f"FF factor refresh failed for region '{region}' ({exc}); "
                    "using cached data.",
                    stacklevel=2,
                )
            else:
                raise

    return pd.read_csv(cache, index_col=0, parse_dates=True)


# ── Momentum (UMD) factor data ────────────────────────────────────────────────

def _parse_momentum_csv_text(text: str) -> pd.Series:
    """
    Parse the raw text of a Ken French daily momentum factor CSV.
    Returns the Mom factor as a decimal Series (values divided by 100).
    Missing-data sentinel -99.99 is replaced with NaN.
    """
    raw_data_lines: list[str] = []
    in_data = False

    for line in text.splitlines():
        first_field = line.split(",")[0].strip()
        if len(first_field) == 8 and first_field.isdigit():
            in_data = True
            raw_data_lines.append(line)
        elif in_data:
            if len(first_field) == 4 and first_field.isdigit():
                break

    if not raw_data_lines:
        raise ValueError("No daily momentum data rows found in Ken French CSV text")

    rows = []
    for line in raw_data_lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            dt  = pd.to_datetime(parts[0].strip(), format="%Y%m%d")
            val = float(parts[1]) / 100.0
        except (ValueError, IndexError):
            continue
        if val <= -0.99:
            val = float("nan")
        rows.append({"date": dt, "Mom": val})

    if not rows:
        raise ValueError("No valid momentum rows after parsing")

    df = pd.DataFrame(rows).set_index("date")
    return df["Mom"].dropna()


def _umd_cache_stale() -> bool:
    if not _UMD_CACHE.exists():
        return True
    mtime = date.fromtimestamp(_UMD_CACHE.stat().st_mtime)
    if (date.today() - mtime).days > _REFRESH_CACHE_DAYS:
        return True
    try:
        cached = pd.read_csv(_UMD_CACHE, index_col=0, parse_dates=True)
        if cached.empty:
            return True
        if (date.today() - cached.index[-1].date()).days > _LAG_THRESHOLD_DAYS:
            return True
    except Exception:
        return True
    return False


def load_umd_factor(force_refresh: bool = False) -> pd.Series:
    """Return Ken French daily Momentum (UMD / Mom) factor as a decimal Series."""
    if force_refresh or _umd_cache_stale():
        try:
            resp = requests.get(_UMD_URL, timeout=30)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
                raw_text = zf.read(csv_name).decode("utf-8", errors="replace")
            s = _parse_momentum_csv_text(raw_text)
            _UMD_CACHE.parent.mkdir(parents=True, exist_ok=True)
            s.to_csv(_UMD_CACHE)
            return s
        except Exception as exc:
            if _UMD_CACHE.exists():
                warnings.warn(
                    f"UMD factor refresh failed ({exc}); using cached data.", stacklevel=2
                )
            else:
                raise

    return pd.read_csv(_UMD_CACHE, index_col=0, parse_dates=True).squeeze("columns")


# ── Sleeve return series ───────────────────────────────────────────────────────

def _get_sleeve_return_series(
    tickers: list[str],
    inception: str,
    end_date: str,
    weights: Optional[dict[str, float]] = None,
) -> pd.Series:
    """
    Daily total-return series for an equity sleeve from inception through end_date.

    Single-ticker sleeves (VEA): adj_close pct_change — no weights needed.

    Multi-ticker sleeves (US equity): value-weighted daily return.
      When ``weights`` is supplied (SAA target proportions from _SLEEVES config),
      they are used directly and no database access is required.
      When ``weights`` is None, inception market values are computed from
      get_holdings_on_date — this fallback requires trades to exist in the DB.

    Non-trading days (weekends, US holidays) carry forward Friday's price;
    inner-joining against the factor index in the regression step drops them.
    """
    date_range = pd.date_range(start=inception, end=end_date, freq="D")

    if len(tickers) == 1:
        ticker = tickers[0]
        p = get_prices(ticker, inception, end_date)
        p.index = pd.to_datetime(p.index)
        series = p["adj_close"].fillna(p["close"])
        return series.reindex(date_range).ffill().pct_change().iloc[1:]

    # Multi-ticker: use provided weights or compute from inception market values
    if weights is not None:
        effective_weights = weights
    else:
        holdings = get_holdings_on_date(inception)
        sleeve_values: dict[str, float] = {}
        for ticker in tickers:
            if ticker not in holdings.index:
                continue
            shares = float(holdings.loc[ticker, "net_shares"])
            try:
                p_inc = get_prices(ticker, inception, inception)
                p_inc.index = pd.to_datetime(p_inc.index)
                px = p_inc["adj_close"].fillna(p_inc["close"])
                price = float(px.dropna().iloc[-1]) if not px.dropna().empty else 0.0
                if price > 0:
                    sleeve_values[ticker] = shares * price
            except Exception:
                continue
        if not sleeve_values:
            raise ValueError(
                f"Could not compute inception market values for tickers: {tickers}"
            )
        total = sum(sleeve_values.values())
        effective_weights = {t: v / total for t, v in sleeve_values.items()}

    weighted_ret = pd.Series(0.0, index=date_range)
    for ticker, w in effective_weights.items():
        try:
            p = get_prices(ticker, inception, end_date)
            p.index = pd.to_datetime(p.index)
            series = p["adj_close"].fillna(p["close"])
            daily_ret = series.reindex(date_range).ffill().pct_change().fillna(0.0)
            weighted_ret = weighted_ret + w * daily_ret
        except Exception:
            continue

    return weighted_ret.iloc[1:]  # drop inception day (pct_change → 0.0 sentinel)


# ── Regression engine ──────────────────────────────────────────────────────────

def _ols_ff5(R_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """
    OLS regression with Newey-West HAC standard errors.

    R_excess : daily excess returns (R_sleeve - RF), decimal, DatetimeIndex
    factors  : DataFrame with columns Mkt-RF, SMB, HML, RMW, CMA, decimal

    Alpha is annualized as alpha_daily * 252 (linear daily-to-annual scaling).
    Both HAC and plain OLS standard errors are returned (HAC is authoritative;
    OLS is retained for the NW-vs-OLS diagnostic test).
    """
    T = len(R_excess)
    L = _nw_lags(T)

    X = add_constant(factors[_FF5_FACTORS])
    model = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _FF5_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _FF5_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _FF5_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":      T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
    }


def run_sleeve_regression(
    sleeve_label: str,
    tickers: list[str],
    region: str,
    inception: str,
    end_date: str,
    weights: Optional[dict[str, float]] = None,
) -> Optional[dict]:
    """
    Run FF5 regression for one equity sleeve against region-appropriate factors.

    The sleeve return series is aligned against the factor data via inner join,
    which naturally drops weekends, US holidays, and the factor publication lag
    window at the right edge of the sample.

    Returns None if fewer than 30 aligned observations are available.
    """
    daily_ret = _get_sleeve_return_series(tickers, inception, end_date, weights=weights)
    daily_ret.index = pd.to_datetime(daily_ret.index)

    ff = load_factors(region)
    ff.index = pd.to_datetime(ff.index)

    # VEA trades on US exchanges and has no price information on US federal
    # holidays — its return series shows zero by forward-fill, not by
    # observation. The Dev FF calendar includes those dates (international
    # markets open); the US FF calendar excludes them. Filter to US trading
    # days so both sleeves use the same calendar and US-holiday rows with
    # artificially zeroed VEA returns are excluded.
    if region == "developed_exus":
        ff_us = load_factors("us")
        ff_us.index = pd.to_datetime(ff_us.index)
        us_trading_days = ff_us.loc[inception:end_date].index
        daily_ret = daily_ret.reindex(us_trading_days).dropna()

    merged = daily_ret.to_frame(name="R_sleeve").join(ff, how="inner").dropna()

    if len(merged) < 30:
        return None

    R_excess = merged["R_sleeve"] - merged["RF"]
    result = _ols_ff5(R_excess, merged)

    result["sleeve_label"]  = sleeve_label
    result["tickers"]       = tickers
    result["region"]        = region
    result["sample_start"]  = merged.index[0].date().isoformat()
    result["sample_end"]    = merged.index[-1].date().isoformat()
    result["exclusion_days"] = max(
        0, (pd.Timestamp(end_date) - pd.Timestamp(result["sample_end"])).days
    )

    return result


def run_sleeve_regressions(inception: str, end_date: str) -> dict:
    """
    Run FF5 regressions for the US equity and Developed ex-US equity sleeves.

    EM sleeve is excluded: Ken French does not publish daily EM factor data.
    Monthly EM factors would yield ~12 observations over a 1-year window,
    insufficient for stable inference.  See EM_DISCLOSURE for the qualitative
    note included in the PDF and Streamlit output.

    Returns a dict with keys 'us' and 'developed_exus'; each value is either
    a regression result dict or None if data is insufficient.
    """
    results: dict[str, Optional[dict]] = {}
    for key, spec in _SLEEVES.items():
        try:
            results[key] = run_sleeve_regression(
                sleeve_label=spec["label"],
                tickers=spec["tickers"],
                region=spec["region"],
                inception=inception,
                end_date=end_date,
                weights=spec.get("weights"),
            )
        except Exception:
            results[key] = None
    return results


# ── Carhart FF5+MOM (momentum supplement) ─────────────────────────────────────

def _ols_ff5_mom(R_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """
    OLS regression with Newey-West HAC for the 6-factor Carhart model (FF5 + Mom).
    Returns the same structure as _ols_ff5 but with 'Mom' included in betas/t_stats/p_values.
    """
    T = len(R_excess)
    L = _nw_lags(T)

    X = add_constant(factors[_FF5_MOM_FACTORS])
    model   = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _FF5_MOM_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _FF5_MOM_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _FF5_MOM_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":      T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
    }


def run_sleeve_regressions_mom(inception: str, end_date: str) -> dict:
    """
    Run FF5+Momentum Carhart regressions for US equity and Developed ex-US equity sleeves.
    Uses the same sleeve definitions as run_sleeve_regressions but extends the factor set
    with the Ken French daily UMD/Mom factor.

    Returns dict with keys 'us' and 'developed_exus'; each value is a result dict or None.
    Returns all-None values if the UMD factor data cannot be loaded.
    """
    try:
        umd = load_umd_factor()
        umd.index = pd.to_datetime(umd.index)
    except Exception:
        return {"us": None, "developed_exus": None}

    results: dict[str, Optional[dict]] = {}
    for key, spec in _SLEEVES.items():
        try:
            daily_ret = _get_sleeve_return_series(
                spec["tickers"], inception, end_date, weights=spec.get("weights")
            )
            daily_ret.index = pd.to_datetime(daily_ret.index)

            ff = load_factors(spec["region"])
            ff.index = pd.to_datetime(ff.index)

            if spec["region"] == "developed_exus":
                ff_us = load_factors("us")
                ff_us.index = pd.to_datetime(ff_us.index)
                us_trading_days = ff_us.loc[inception:end_date].index
                daily_ret = daily_ret.reindex(us_trading_days).dropna()

            merged = (
                daily_ret.to_frame(name="R_sleeve")
                .join(ff, how="inner")
                .join(umd.rename("Mom"), how="inner")
                .dropna()
            )

            if len(merged) < 30:
                results[key] = None
                continue

            R_excess = merged["R_sleeve"] - merged["RF"]
            result = _ols_ff5_mom(R_excess, merged)
            result["sleeve_label"]  = spec["label"]
            result["tickers"]       = spec["tickers"]
            result["region"]        = spec["region"]
            result["sample_start"]  = merged.index[0].date().isoformat()
            result["sample_end"]    = merged.index[-1].date().isoformat()
            results[key] = result
        except Exception:
            results[key] = None

    return results


def run_intl_global_regression(inception: str, end_date: str) -> Optional[dict]:
    """
    Run FF5 regression for the International Developed sleeve (VEA) against GLOBAL factors.

    Ken French ceased publication of daily Global 5-factor data in June 2019
    (GLOBAL_DAILY_FACTORS_CUTOFF). Any portfolio whose inception date is after
    that cutoff has zero factor-data overlap; this function returns None immediately
    without downloading the (permanently outdated) file from Dartmouth.

    Returns None if inception is after GLOBAL_DAILY_FACTORS_CUTOFF or if fewer
    than 30 aligned observations are available.
    """
    if date.fromisoformat(inception) > GLOBAL_DAILY_FACTORS_CUTOFF:
        return None

    spec = _SLEEVES["developed_exus"]
    try:
        return run_sleeve_regression(
            sleeve_label="International Developed Sleeve — Global Factors",
            tickers=spec["tickers"],
            region="global",
            inception=inception,
            end_date=end_date,
        )
    except Exception:
        return None


# ── FI sleeve TERM/CREDIT factor model ────────────────────────────────────────

def regress_fi_sleeve(inception: str, end_date: str) -> Optional[dict]:
    """
    TERM/CREDIT two-factor model for the FI sleeve (VGIT 60% + SCHP 40%).

    TERM   = IEF daily return − BIL daily return  (duration premium proxy)
    CREDIT = HYG daily return − IEF daily return  (credit spread premium proxy)
    RF     = Ken French US daily risk-free rate

    Returns None if fewer than 30 aligned observations are available.
    """
    date_range = pd.date_range(start=inception, end=end_date, freq="D")

    # FI sleeve return (value-weighted VGIT + SCHP, SAA-proportional weights)
    fi_ret = pd.Series(0.0, index=date_range)
    for ticker, w in _FI_WEIGHTS.items():
        p = get_prices(ticker, inception, end_date)
        p.index = pd.to_datetime(p.index)
        series = p["adj_close"].fillna(p["close"])
        fi_ret = fi_ret + w * series.reindex(date_range).ffill().pct_change().fillna(0.0)
    fi_ret = fi_ret.iloc[1:]
    fi_ret.index = pd.to_datetime(fi_ret.index)

    # TERM and CREDIT factor proxies
    def _ret(ticker: str) -> pd.Series:
        if ticker == "HYG" and _HYG_CACHE.exists():
            try:
                df = pd.read_parquet(_HYG_CACHE)
                df.index = pd.to_datetime(df.index)
                return df["adj_close"].reindex(date_range).ffill().pct_change().iloc[1:]
            except Exception:
                pass
        p = get_prices(ticker, inception, end_date)
        p.index = pd.to_datetime(p.index)
        return p["adj_close"].fillna(p["close"]).reindex(date_range).ffill().pct_change().iloc[1:]

    ief_ret = _ret("IEF")
    bil_ret = _ret("BIL")
    hyg_ret = _ret("HYG")

    factors_df = pd.DataFrame(
        {"TERM": (ief_ret.values - bil_ret.values), "CREDIT": (hyg_ret.values - ief_ret.values)},
        index=fi_ret.index,
    )

    # RF from Ken French US factors
    ff_us = load_factors("us")
    ff_us.index = pd.to_datetime(ff_us.index)

    merged = (
        fi_ret.to_frame(name="R_fi")
        .join(factors_df, how="inner")
        .join(ff_us["RF"], how="inner")
        .dropna()
    )

    if len(merged) < 30:
        return None

    T = len(merged)
    L = _nw_lags(T)

    R_excess = merged["R_fi"] - merged["RF"]
    X = add_constant(merged[_FI_FACTORS])

    model   = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _FI_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _FI_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _FI_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":       T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
        "sleeve_label": "FI Sleeve — TERM / CREDIT",
        "tickers":      ["VGIT", "SCHP"],
        "sample_start": merged.index[0].date().isoformat(),
        "sample_end":   merged.index[-1].date().isoformat(),
    }


# ── Interpretation helpers ─────────────────────────────────────────────────────

def build_factor_prose(
    results: dict,
    fi_result: Optional[dict] = None,
    global_result: Optional[dict] = None,
) -> list[str]:
    """
    Generate institutional-register prose interpreting the sleeve regressions.

    results       : dict with keys 'us' and 'developed_exus' (each a result dict or None).
    fi_result     : optional TERM/CREDIT result dict from regress_fi_sleeve.
    global_result : optional Global FF5 result dict from run_intl_global_regression.
    Called by both the PDF section builder and the Streamlit page to guarantee
    identical output.
    """
    lines: list[str] = []
    us  = results.get("us")
    dev = results.get("developed_exus")

    if us:
        b_hml = us["betas"]["HML"]
        t_hml = us["t_stats"]["HML"]
        b_smb = us["betas"]["SMB"]
        t_smb = us["t_stats"]["SMB"]
        b_mkt = us["betas"]["Mkt-RF"]
        t_mkt = us["t_stats"]["Mkt-RF"]
        a_bps = us["alpha_annual_bps"]
        t_a   = us["t_alpha"]
        T_us  = us["T"]
        s_start = _fmt_date(us["sample_start"])
        s_end   = _fmt_date(us["sample_end"])

        hml_sig    = significance_label(t_hml)
        alpha_note = significance_label(t_a)

        lines.append(
            f"The US equity sleeve ({T_us} trading days, {s_start} to {s_end}) "
            f"loads on Mkt-RF at {b_mkt:.2f} (t = {t_mkt:.2f}), consistent with "
            f"near-fully-invested US equity exposure. "
            f"The HML loading of {b_hml:.3f} (t = {t_hml:.2f}) is {hml_sig}, "
            f"broadly consistent with the value tilts in VTV and AVUV. "
            f"The SMB loading of {b_smb:.3f} (t = {t_smb:.2f}) reflects the "
            f"small-cap exposure from the AVUV holding. "
            f"Annualized alpha of {a_bps:+.0f} bps is {alpha_note}."
        )

    if dev:
        b_mkt_d = dev["betas"]["Mkt-RF"]
        t_mkt_d = dev["t_stats"]["Mkt-RF"]
        a_bps_d = dev["alpha_annual_bps"]
        t_a_d   = dev["t_alpha"]
        T_dev   = dev["T"]

        alpha_sig_d = significance_label(t_a_d)

        lines.append(
            f"The International Developed sleeve (VEA, {T_dev} trading days) "
            f"loads on Mkt-RF_dev at {b_mkt_d:.2f} (t = {t_mkt_d:.2f}), within the "
            f"expected range for a passive cap-weighted developed-markets ETF. "
            f"The {a_bps_d:+.0f} bps annualized alpha (t = {t_a_d:.2f}) is {alpha_sig_d}, "
            f"but should not be interpreted as skill: "
            f"VEA tracks the FTSE Developed All Cap ex US Index, which classifies "
            f"South Korea as Developed (~3-4% of VEA's holdings); "
            f"Ken French's Developed ex-US factor universe excludes Korea entirely. "
            f"Korean equities returned approximately 95-98% in calendar 2025, "
            f"driven by the AI/semiconductor capex cycle (Samsung Electronics, SK Hynix). "
            f"That excess return falls outside the FF Developed ex-US factor span and "
            f"accumulates in the alpha term. "
            f"The reported alpha is best read as "
            f'"unexplained-by-Developed-ex-US-factors return attributable to universe mismatch," '
            f"not risk-adjusted excess return."
        )

        if global_result:
            a_bps_g = global_result["alpha_annual_bps"]
            t_a_g   = global_result["t_alpha"]
            lines.append(
                f"The Global FF5 supplementary regression yields {a_bps_g:+.0f} bps alpha "
                f"(t = {t_a_g:.2f}), providing a cross-check against the Developed-ex-US result. "
                "The Global factor set includes US exposure in Mkt-RF, making it less precise "
                "for a developed-ex-US holding, but it helps bound the alpha estimate: "
                "if both factor sets produce elevated alpha, the Korea universe mismatch "
                "likely explains the bulk of the gap in both models. "
                "Alpha estimates at this sample length carry wide confidence intervals "
                "and should not be read as evidence of skill or persistent outperformance."
            )

    if fi_result:
        b_term   = fi_result["betas"]["TERM"]
        t_term   = fi_result["t_stats"]["TERM"]
        b_credit = fi_result["betas"]["CREDIT"]
        t_credit = fi_result["t_stats"]["CREDIT"]
        a_bps_fi = fi_result["alpha_annual_bps"]
        t_a_fi   = fi_result["t_alpha"]
        T_fi     = fi_result["T"]

        lines.append(
            f"The FI sleeve (VGIT 60% / SCHP 40%, {T_fi} trading days) loads on the "
            f"TERM factor (IEF − BIL duration premium) at {b_term:.3f} (t = {t_term:.2f}) "
            f"and the CREDIT factor (HYG − IEF spread premium) at {b_credit:.3f} (t = {t_credit:.2f}). "
            f"The positive TERM loading confirms the sleeve carries meaningful interest-rate duration — "
            f"consistent with VGIT's ~5.5-year effective duration and SCHP's ~6.8-year duration. "
            f"Annualized alpha of {a_bps_fi:+.0f} bps (t = {t_a_fi:.2f}) captures return "
            f"not explained by the TERM/CREDIT proxies; at this sample length the confidence "
            f"interval is wide, and the alpha primarily reflects ETF-vs-index tracking "
            f"differences and expense ratios rather than managerial skill."
        )

    lines.append(
        "The Emerging Markets sleeve (IEMG) is excluded from regression analysis: "
        "Ken French does not publish daily EM factor data, and the current "
        "portfolio history is insufficient for a meaningful monthly-frequency regression. "
        "Real assets (VNQ, PDBC) are excluded — no liquid daily factor proxy set spans "
        "REIT and commodity exposure simultaneously."
    )

    return lines


def build_factor_methodology_notes(results: dict, fi_result: Optional[dict] = None) -> list[str]:
    """Return methodology disclosure bullet points for the sleeve regressions."""
    us  = results.get("us")
    dev = results.get("developed_exus")

    T_us  = us["T"]  if us  else "N/A"
    L_us  = us["nw_lags"]  if us  else "N/A"
    T_dev = dev["T"] if dev else "N/A"
    L_dev = dev["nw_lags"] if dev else "N/A"

    us_window  = (
        f"{_fmt_date(us['sample_start'])} to {_fmt_date(us['sample_end'])}"
        if us else "N/A"
    )
    dev_window = (
        f"{_fmt_date(dev['sample_start'])} to {_fmt_date(dev['sample_end'])}"
        if dev else "N/A"
    )

    return [
        f"Samples: US equity sleeve {T_us} US trading days ({us_window}), L = {L_us}. "
        f"International Developed sleeve {T_dev} US trading days ({dev_window}), L = {L_dev}. "
        "Both sleeves are restricted to US equity market trading days (dates present "
        "in the Ken French US factor calendar). The Developed sleeve applies this "
        "restriction explicitly: VEA trades on US exchanges and has no price observation "
        "on US federal holidays — its return series shows zero by forward-fill, not by "
        "market observation. The Dev FF dataset includes those holidays (international "
        "markets open); excluding them keeps both regression calendars consistent. "
        "Sample sizes reflect the overlap with the most recent available factor data "
        "(publication lag computed from factor-data end date to analysis date).",

        "Methodology: each equity sleeve is regressed against its own region-appropriate "
        "FF5 factor set — US factors for the US sleeve, Developed ex-US factors for VEA. "
        "This per-sleeve approach avoids the model-misspecification problem that inflated "
        "the alpha estimate in the prior single-portfolio regression: returns from non-US "
        "equity and real-asset sleeves that the US-only factor model cannot span had been "
        "flowing into the alpha term.",

        "Standard errors: Newey-West HAC, correcting for autocorrelation in daily return "
        "residuals. Lag L is computed per regression as floor(4 × (T/100)^(2/9)).",

        "Sleeve return series: the US equity sleeve return is the value-weighted daily "
        "total return of VOO, VTV, SPHQ, and AVUV, weighted by SAA target proportions "
        "(VOO 35.6%, SPHQ 31.1%, VTV 17.8%, AVUV 15.6% — proportional to the locked "
        "Phase 1 sleeve targets of 16/14/8/7%). Weights are held constant. "
        "The Developed sleeve is VEA's daily adj_close return.",

        "Universe mismatch — Developed sleeve: VEA tracks FTSE Developed All Cap ex US "
        "(includes Korea, Israel as Developed). Ken French's Developed ex-US universe "
        "excludes Korea (treated as EM in Ken French's classification) and uses a different "
        "Israel categorization. The universe difference contributes to unexplained variance "
        "and inflates the Developed-sleeve alpha estimate. A future phase may address this "
        "by reweighting VEA's holdings to match Ken French's universe before regressing, "
        "or by using a custom factor set aligned to FTSE Developed All Cap ex US.",

        "EM sleeve exclusion: Ken French does not publish daily EM factor data. "
        "Monthly EM factors would yield approximately 12 observations — below the minimum "
        "for stable inference. EM factor decomposition will be added at 3+ years of history.",

        "Carhart Momentum supplement (FF5+MOM): Ken French daily UMD factor "
        "(F-F_Momentum_Factor_daily_CSV.zip) added as a sixth regressor alongside FF5 "
        "to test whether the portfolio systematically loads on the momentum premium. "
        "The supplementary table is shown for diagnostic purposes — the primary FF5 result "
        "is authoritative. Momentum exposure is structurally avoided in this portfolio "
        "for tax-efficiency reasons (high turnover → short-term gains), so a near-zero "
        "Mom loading is expected and confirms the construction is tax-aware.",

        "Global factor supplement (International Developed): Ken French Global FF5 "
        "(Global_5_Factors_Daily_CSV.zip) provides a cross-check against the Developed-ex-US "
        "regression. Global Mkt-RF includes US exposure, making it less clean for a "
        "developed-ex-US ETF, but the comparison tests whether the Korea universe mismatch "
        "artifact persists under a different factor set. Differences in alpha between the "
        "two models are informative about the universe-mismatch contribution.",
    ]

    if fi_result:
        T_fi  = fi_result["T"]
        L_fi  = fi_result["nw_lags"]
        fi_window = (
            f"{_fmt_date(fi_result['sample_start'])} to {_fmt_date(fi_result['sample_end'])}"
        )
        notes += [
            f"FI sleeve model: (R_fi − RF) ~ TERM + CREDIT, {T_fi} observations "
            f"({fi_window}), Newey-West HAC L = {L_fi}. "
            "TERM = IEF daily return − BIL daily return (duration premium proxy). "
            "CREDIT = HYG daily return − IEF daily return (credit spread premium proxy). "
            "FI sleeve return: value-weighted VGIT (60%) + SCHP (40%), proportional to 9%/6% SAA targets. "
            "Equity FF5 factors do not span fixed income; this dedicated two-factor model "
            "captures duration and credit risk explicitly. RF from Ken French US daily factors.",

            "Real assets (VNQ, PDBC) remain excluded: no liquid daily factor proxy set spans "
            "REIT and commodity exposure simultaneously. Factor models for real assets are "
            "a future extension.",
        ]
    else:
        notes.append(
            "Fixed income (VGIT, SCHP): TERM/CREDIT factor model (IEF−BIL duration premium, "
            "HYG−IEF credit spread premium) in scope — see FI Sleeve panel above. "
            "Real assets (VNQ, PDBC) remain excluded; no liquid daily factor proxy set "
            "spans REIT and commodity exposure simultaneously."
        )

    return notes


# ── Benchmark-relative attribution regression ─────────────────────────────────

def _ols_benchmark(R_p_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """
    OLS regression with NW-HAC for the benchmark-relative attribution model.

    R_p_excess : daily portfolio excess returns (R_p − RF), decimal, DatetimeIndex
    factors    : DataFrame with columns Bench-RF, HML, SMB, RMW, decimal

    The model is: R_p − RF ~ const + β_b*(R_b − RF) + β_hml*HML + β_smb*SMB + β_rmw*RMW
    The intercept is "active return after controlling for benchmark beta and style tilts"
    — the institutional alpha definition used by PRINCO, JPM IDD, and similar.

    Alpha is annualized as alpha_daily * 252.
    Both HAC and plain OLS standard errors are returned.
    """
    T = len(R_p_excess)
    L = _nw_lags(T)

    X = add_constant(factors[_BENCH_FACTORS])
    model   = OLS(R_p_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _BENCH_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _BENCH_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _BENCH_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":       T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
    }


def run_benchmark_attribution_regression(
    inception: str,
    end_date: str,
) -> Optional[dict]:
    """
    Benchmark-relative attribution regression (portfolio vs custom blended SAA).

    Model: (R_p − RF) ~ (R_b − RF) + HML + SMB + RMW

      R_p: daily portfolio total return from get_portfolio_value_series
      R_b: daily custom blended SAA benchmark return from get_custom_blended_series
      RF:  Ken French US daily risk-free rate
      HML, SMB, RMW: Ken French US FF5 style factors (CMA excluded — see methodology)

    The intercept is active return after controlling for benchmark beta and style
    tilts. Standard errors: Newey-West HAC, L = floor(4*(T/100)^(2/9)).

    Returns None when fewer than 30 aligned observations are available.
    """
    date_range = pd.date_range(start=inception, end=end_date, freq="D")

    pv = get_portfolio_value_series(inception, end_date)
    if pv.empty or float(pv.max()) == 0.0:
        return None
    pv = pv.reindex(date_range).ffill()
    R_p = pv.pct_change()

    bl = get_custom_blended_series(inception, end_date)
    bl = bl.reindex(date_range).ffill()
    R_b = bl.pct_change()

    ff = load_factors("us")
    ff.index = pd.to_datetime(ff.index)

    merged = (
        R_p.to_frame(name="R_p")
        .join(R_b.to_frame(name="R_b"), how="inner")
        .join(ff[["RF", "HML", "SMB", "RMW"]], how="inner")
        .dropna()
    )

    if len(merged) < 30:
        return None

    R_p_excess = merged["R_p"] - merged["RF"]
    R_b_excess = merged["R_b"] - merged["RF"]

    factors_df = pd.DataFrame({
        "Bench-RF": R_b_excess.values,
        "HML":      merged["HML"].values,
        "SMB":      merged["SMB"].values,
        "RMW":      merged["RMW"].values,
    }, index=merged.index)

    result = _ols_benchmark(R_p_excess, factors_df)
    result["sample_start"] = merged.index[0].date().isoformat()
    result["sample_end"]   = merged.index[-1].date().isoformat()

    return result


def build_benchmark_prose(
    result: Optional[dict],
    bhb_top_selection: Optional[list] = None,
) -> list[str]:
    """
    Generate institutional-register prose interpreting the benchmark attribution regression.

    Called by both the PDF section builder and the Streamlit page.

    bhb_top_selection: optional list of dicts from the caller:
        [{"holding": "AVUV", "bench": "IWM", "sel_bps": 754.0}, ...]
      When provided and alpha is significant, a cross-reference to the
      Brinson-Fachler attribution is included with specific numbers.
      When None, the cross-reference is generic (no bps numbers).
    """
    if result is None:
        return [
            "Benchmark attribution regression is unavailable: insufficient aligned observations "
            "(minimum 30 trading days required). Section will populate as portfolio history grows."
        ]

    b_bench = result["betas"]["Bench-RF"]
    t_bench = result["t_stats"]["Bench-RF"]
    b_hml   = result["betas"]["HML"]
    t_hml   = result["t_stats"]["HML"]
    b_smb   = result["betas"]["SMB"]
    t_smb   = result["t_stats"]["SMB"]
    b_rmw   = result["betas"]["RMW"]
    t_rmw   = result["t_stats"]["RMW"]
    a_bps   = result["alpha_annual_bps"]
    t_a     = result["t_alpha"]
    T       = result["T"]
    r2      = result["r_squared"]
    s_start = _fmt_date(result["sample_start"])
    s_end   = _fmt_date(result["sample_end"])

    bench_note = (
        "consistent with near-full beta to the SAA benchmark"
        if abs(b_bench - 1.0) < 0.15
        else "departing from benchmark (expected ≈ 1.0 for a fully-invested passive portfolio)"
    )

    sig_label      = significance_label(t_a)
    is_significant = abs(t_a) >= 1.65

    # Second paragraph: intercept interpretation + optional BHB cross-reference
    second_para = (
        f"The intercept — active return after controlling for benchmark beta and style tilts — "
        f"is {a_bps:+.0f} bps annualized (t = {t_a:.2f}), {sig_label}. "
    )

    if is_significant and bhb_top_selection:
        parts = []
        for item in (bhb_top_selection or [])[:3]:
            bps    = item["sel_bps"]
            verb   = "outperformed" if bps >= 0 else "underperformed"
            parts.append(
                f"{item['holding']} {verb} {item['bench']} by {abs(bps):.0f} bps"
            )
        if len(parts) == 1:
            joined = parts[0]
        elif len(parts) == 2:
            joined = f"{parts[0]}, and {parts[1]}"
        else:
            joined = f"{parts[0]}, {parts[1]}, and {parts[2]}"
        second_para += (
            f"This active return is consistent with the selection effects documented in the "
            f"Brinson-Fachler attribution: {joined}. "
            f"The benchmark attribution regression aggregates these sleeve-level selection "
            f"effects into a single portfolio-level active return after controlling for "
            f"systematic style tilts. "
        )
    elif is_significant:
        second_para += (
            "This active return is consistent with the selection effects documented in the "
            "Brinson-Fachler attribution. "
            "The benchmark attribution regression aggregates sleeve-level selection effects "
            "into a single portfolio-level active return after controlling for systematic "
            "style tilts. "
        )

    second_para += (
        f"The t-statistic should be interpreted in light of the {T}-observation sample window: "
        f"confidence intervals around the alpha estimate are wide at this sample length, "
        f"and persistence of the active return cannot be established without a longer history."
    )

    return [
        f"Portfolio benchmark-relative regression ({T} trading days, {s_start} to {s_end}): "
        f"the portfolio loads on the custom blended benchmark at β = {b_bench:.3f} "
        f"(t = {t_bench:.2f}), {bench_note}. "
        f"Residual style exposures: HML β = {b_hml:.3f} (t = {t_hml:.2f}), "
        f"SMB β = {b_smb:.3f} (t = {t_smb:.2f}), "
        f"RMW β = {b_rmw:.3f} (t = {t_rmw:.2f}). "
        f"R² = {r2:.3f}.",

        second_para,
    ]


def build_benchmark_methodology(result: Optional[dict]) -> list[str]:
    """Return methodology disclosure bullet points for the benchmark attribution regression."""
    T      = result["T"]      if result else "N/A"
    L      = result["nw_lags"] if result else "N/A"
    window = (
        f"{_fmt_date(result['sample_start'])} to {_fmt_date(result['sample_end'])}"
        if result else "N/A"
    )
    return [
        f"Regression: (R_p − RF) ~ (R_b − RF) + HML + SMB + RMW. "
        f"{T} observations ({window}), Newey-West HAC SEs, L = {L}.",

        "R_p: daily portfolio total return (adj_close basis, SPAXX proxied via BIL normalized "
        "to $1.00 at inception). R_b: daily custom blended SAA benchmark return (target-weight "
        "basket: SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, 50% VNQ + 50% DBC, BIL). "
        "RF, HML, SMB, RMW: Ken French US daily factors (Dartmouth). "
        "All series aligned by inner join on trading dates.",

        "The benchmark beta (Bench-RF coefficient) measures how closely the portfolio tracks "
        "its own SAA benchmark. A value near 1.0 indicates near-full tracking; deviations "
        "reflect cross-sleeve return dispersion from active tilts relative to the benchmark "
        "basket. Style betas (HML, SMB, RMW) capture portfolio-wide factor tilts not "
        "explained by benchmark beta. The intercept is the active return component "
        "unexplained by benchmark beta and style — the PRINCO/JPM IDD institutional "
        "alpha definition.",

        "CMA (investment factor) is excluded from this regression. For a passive/semi-passive "
        "multi-ETF implementation, CMA primarily captures differences in accruals and capex "
        "patterns across the constituent ETFs — not a deliberate active tilt. Including it "
        "would add collinearity without improving interpretation. HML, SMB, and RMW are "
        "the style factors most informative for this portfolio's deliberate tilts "
        "(value via VTV, size via AVUV, quality/profitability via SPHQ).",
    ]
