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

from src.holdings import get_holdings_on_date
from src.prices import get_prices

_ROOT = Path(__file__).resolve().parent.parent

# ── Factor configuration ───────────────────────────────────────────────────────

_BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"

_FACTOR_CONFIG: dict[str, dict] = {
    "us": {
        "url":   _BASE_URL + "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
        "cache": _ROOT / "data" / "ff_factors_us.csv",
    },
    "developed_exus": {
        "url":   _BASE_URL + "Developed_ex_US_5_Factors_Daily_CSV.zip",
        "cache": _ROOT / "data" / "ff_factors_developed_exus.csv",
    },
}

# Backward-compatible alias used by the stale-cache check
_CACHE_PATH = _FACTOR_CONFIG["us"]["cache"]

_REFRESH_CACHE_DAYS = 7   # re-fetch if cache mtime exceeds this many days
_LAG_THRESHOLD_DAYS = 35  # re-fetch if most recent factor date is this far behind today

_FF5_FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]

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
        # Proportional SAA target weights; used instead of inception market values
        # so the regression requires no database access.
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
    """Download and parse a Ken French daily factor ZIP from Dartmouth."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
        raw_text = zf.read(csv_name).decode("utf-8", errors="replace")

    return _parse_ff_csv_text(raw_text)


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


# ── Interpretation helpers ─────────────────────────────────────────────────────

def build_factor_prose(results: dict) -> list[str]:
    """
    Generate institutional-register prose interpreting the two sleeve regressions.

    results: dict with keys 'us' and 'developed_exus' (each a result dict or None).
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

        hml_sig = (
            "statistically significant at the 5% level"
            if abs(t_hml) > 2
            else "not statistically significant at conventional levels"
        )
        alpha_note = (
            f"marginally significant (|t| = {abs(t_a):.2f} > 2.0)"
            if abs(t_a) > 2
            else "not statistically distinguishable from zero at conventional thresholds"
        )

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

        alpha_note_d = (
            f"statistically significant at the 5% level (|t| = {abs(t_a_d):.2f})"
            if abs(t_a_d) > 2
            else "not statistically distinguishable from zero"
        )

        lines.append(
            f"The International Developed sleeve (VEA, {T_dev} trading days) "
            f"loads on Mkt-RF_dev at {b_mkt_d:.2f} (t = {t_mkt_d:.2f}), within the "
            f"expected range for a passive cap-weighted developed-markets ETF. "
            f"Annualized alpha of {a_bps_d:+.0f} bps is {alpha_note_d}. "
            f"This unexplained return should not be interpreted as skill: "
            f"VEA tracks the FTSE Developed All Cap ex US index, which classifies "
            f"South Korea as a developed market and includes it at approximately "
            f"3–4% of the index. Ken French's Developed ex-US factor universe "
            f"excludes South Korea. Over the current sample window, Korean equities "
            f"(EWY) returned approximately 95–98% — an extraordinary period driven "
            f"by AI-semiconductor demand — producing a model-span gap that flows "
            f"into the alpha term. The alpha estimate is expected to revert as the "
            f"sample window extends and Korean equity returns normalize."
        )

    lines.append(
        "The Emerging Markets sleeve (IEMG) is excluded from regression analysis: "
        "Ken French does not publish daily EM factor data, and the current "
        "portfolio history is insufficient for a meaningful monthly-frequency regression. "
        "Fixed income (VGIT, SCHP) and real assets (VNQ, PDBC) are out of scope for "
        "equity factor models and are not regressed."
    )

    return lines


def build_factor_methodology_notes(results: dict) -> list[str]:
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
        f"Samples: US equity sleeve {T_us} trading days ({us_window}), L = {L_us}. "
        f"International Developed sleeve {T_dev} trading days ({dev_window}), L = {L_dev}. "
        "Sample sizes reflect the overlap between portfolio inception (May 1, 2025) and "
        "the most recent available factor data (~65 calendar-day publication lag "
        "at current Ken French release cadence).",

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

        "Universe mismatch — International Developed sleeve: VEA tracks the FTSE "
        "Developed All Cap ex US index, which classifies South Korea as Developed and "
        "includes it at ~3–4% of the index. Ken French's Developed ex-US factor "
        "universe excludes South Korea (following a different market-classification "
        "framework). This mismatch is the primary source of unexplained return (alpha) "
        "in the Developed sleeve regression; it is not indicative of active management "
        "skill. Caveats: (1) the alpha estimate carries meaningful sampling uncertainty "
        "at T ≈ 216; (2) Korean equity outperformance in the current sample "
        "(EWY ≈ +95–98% over calendar 2025) is exceptional and unlikely to persist.",

        "EM sleeve exclusion: Ken French does not publish daily EM factor data. "
        "Monthly EM factors would yield approximately 12 observations — below the minimum "
        "for stable inference. EM factor decomposition will be added at 3+ years of history.",

        "Fixed income (VGIT, SCHP) and real assets (VNQ, PDBC) are excluded; equity "
        "factor models do not span those asset classes. Term/credit factor models for FI "
        "and real-asset factor proxies are noted as future extensions.",
    ]
