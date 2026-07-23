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

# Ken French NYSE book-to-market (BE/ME) breakpoints — annual, formed each June,
# "every 5th percentile" (5, 10, …, 100). Used for the value-spread valuation
# signal (Phase 30). Separate ingestion path from the 5-factor daily files.
_BEME_URL          = _BASE_URL + "BE-ME_Breakpoints_CSV.zip"
_BEME_CACHE        = _CACHE_DIR / "ff_beme_breakpoints.csv"
_BEME_PCTILES      = list(range(5, 101, 5))  # 20 columns: p5, p10, …, p100
_BEME_REFRESH_DAYS = 30  # annual data; refetch monthly at most

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

# FI sleeve weights proportional to SAA targets (VGIT 6%, SCHP 4%)
_FI_WEIGHTS = {"VGIT": 6.0 / 10.0, "SCHP": 4.0 / 10.0}

# ── Equity sleeve definitions ──────────────────────────────────────────────────

# SAA target weights for the US equity sleeve (Phase 1 locked).
# Used to compute the sleeve's value-weighted return series without requiring
# a DB call — eliminates the `get_holdings_on_date` dependency in environments
# where the portfolio database is empty or unavailable (e.g., Streamlit Cloud
# in personal mode, fresh deployments, or test environments).
_SAA_US = {"VOO": 17, "SPHQ": 15, "VTV": 9, "AVUV": 8}
_SAA_US_TOTAL = sum(_SAA_US.values())  # 49


def _intl_core_label() -> str:
    """Name of the cap-weighted developed-international (VEA) sleeve in this book.

    'International Developed' on the personal 9-sleeve book, 'International Core'
    on the demo 12-sleeve book — this VEA-only regression is exactly that sleeve
    either way. Derived from the DB so the factor-exhibit labels stay truthful in
    both modes instead of hardcoding one taxonomy's name.
    """
    from src.sleeve_config import international_sleeves
    intl = international_sleeves()
    for name in intl:
        if name in ("International Core", "International Developed"):
            return name
    return intl[0] if intl else "International Developed"


_INTL_CORE = _intl_core_label()

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
        # Phase 39: this regression has always been VEA-only, which is the
        # cap-weighted developed-international sleeve — International Developed on
        # the personal book, International Core on the demo book. Label derived
        # from the DB (_INTL_CORE) so it names the sleeve this book actually has.
        "label":   f"{_INTL_CORE} Sleeve",
        "tickers": ["VEA"],
        "region":  "developed_exus",
        # Single-ticker sleeve: no weights needed.
    },
}

# ── International tilt-sleeve regressions (Phase 40) ────────────────────────────
#
# The tilted (demo) book splits developed-international into four sleeves. VEA
# (International Core) is regressed above via _SLEEVES; this adds the three TILT
# sleeves — IDHQ (Quality), AVIV (Large Value), AVDV (Small Value) — each against
# the SAME Developed ex-US FF5 factor set, exactly like VEA, and each paired with
# a passive CONTROL fund so the residual can be read against an investable
# yardstick instead of the (un-investable) academic factors.
#
# CONTROL ROSTER — Canada-matched, deliberately NOT the SAA benchmarks.
# The Large/Small Value SAA benchmarks (EFV, SCZ) both track MSCI EAFE, which
# excludes BOTH Canada and Korea. The Ken French Developed ex-US factor universe
# excludes Korea but INCLUDES Canada, and the tilt funds hold Canada (~11-13%).
# Over the 2025-05 -> 2026-05 window Canada (EWC, +37.8%/yr) beat developed-ex-US
# (+27.7%/yr) by ~10 pts, so a Canada-holed control's residual is understated by
# ~90-250 bps (measured directly: EFV residual +23 bps vs the Canada-matched IVLU
# +267 bps, same large-value style, same window). Presenting EFV/SCZ as clean
# gauges would smuggle the missing-Canada return into the fund-vs-control gap.
# IQLT / IVLU / ISVL all match the factor universe on BOTH counts — Canada in,
# Korea out — so a fund-minus-control residual is Canada- and Korea-neutral by
# construction:
#   IDHQ (Quality)     <- IQLT  MSCI World ex USA Sector-Neutral Quality
#   AVIV (Large Value) <- IVLU  MSCI World ex USA Enhanced Value
#   AVDV (Small Value) <- ISVL  FTSE Developed ex US ex Korea Small Cap Focused Value
# ISVL + IQLT alone do NOT cover the roster: they match Quality and Small Value
# but leave AVIV (Large Value) with no style-matched Canada-matched comparator,
# which is why IVLU is added. Controls are NOT held securities and intentionally
# carry no `securities` row (one would make sleeve_holdings() treat them as
# holdings); they are priced on demand via get_prices, exactly like VEA.
#
# N-PORT as-of: the affirmative "no Korea position" clearance for the two Avantis
# funds is an OBSERVED snapshot from the most recent public holdings filing, not a
# mandate constraint. Refresh this date when a newer Avantis N-PORT is filed.
_NPORT_ASOF = "March 31, 2026"

_INTL_TILT_SLEEVES = [
    {
        "sleeve":        "International Quality",
        "fund":          "IDHQ",
        "fund_name":     "Invesco S&P International Developed Quality ETF",
        "control":       "IQLT",
        "control_name":  "iShares MSCI Intl Quality Factor ETF",
        "control_index": "MSCI World ex USA Sector-Neutral Quality",
        "disclosure":    "idhq",
    },
    {
        "sleeve":        "International Large Value",
        "fund":          "AVIV",
        "fund_name":     "Avantis International Large Cap Value ETF",
        "control":       "IVLU",
        "control_name":  "iShares MSCI Intl Value Factor ETF",
        "control_index": "MSCI World ex USA Enhanced Value",
        "disclosure":    "avantis",
        "israel_pct":    "1",
    },
    {
        "sleeve":        "International Small Value",
        "fund":          "AVDV",
        "fund_name":     "Avantis International Small Cap Value ETF",
        "control":       "ISVL",
        "control_name":  "iShares International Developed Small Cap Value Factor ETF",
        "control_index": "FTSE Developed ex US ex Korea Small Cap Focused Value",
        "disclosure":    "avantis",
        "israel_pct":    "4.5",
    },
]


# Qualitative disclosure for the EM sleeve (no daily FF5 data available)
def _em_disclosure() -> str:
    """EM-sleeve disclosure for the book this process is pointed at.

    Personal (single cap-weighted developed sleeve): the original framing —
    IEMG's composition plus the no-daily-EM-factor-data point.
    Demo (tilted international book): the SAA page's cap-weight-exception
    framing — the developed tilts are verifiable against Ken French's daily
    developed ex-US series, EM has no equivalent series, so the sleeve stays
    cap-weighted and carries no per-sleeve regression by design.
    Derived at import like _INTL_CORE; falls back to the untilted framing if
    the DB is unavailable (it is factually safe in either book).
    """
    try:
        from src.sleeve_config import international_sleeves
        tilted = len(international_sleeves()) > 1
    except Exception:
        tilted = False
    if not tilted:
        return (
            "Ken French does not publish daily EM factor data. "
            "Monthly EM factors would yield approximately 12 observations over the current "
            "1-year window — below the threshold for stable inference. "
            "IEMG provides passive cap-weighted broad EM exposure (~27% China weight at current "
            "index composition). Factor decomposition for this sleeve will be added when the "
            "portfolio accumulates sufficient history (target: 3+ years of monthly data)."
        )
    return (
        "This is the one equity region held at cap weight, and the only equity sleeve "
        "with no per-sleeve factor regression — by design, not omission. The developed "
        "book tilts toward quality, value, and small value because those exposures can "
        "be verified against Ken French's daily developed ex-US factor series; no "
        "equivalent daily series exists for emerging markets, and the monthly history "
        "is too short against this portfolio's inception for stable inference. A tilt "
        "here could be asserted but not shown, so the sleeve stays passive: IEMG "
        "provides cap-weighted broad EM exposure. The full cap-weight-exception "
        "argument is in the SAA sleeve rationale."
    )


EM_DISCLOSURE = _em_disclosure()


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


# ── BE/ME breakpoints (value-spread valuation, Phase 30) ───────────────────────
#
# This is an ADDITIVE Ken French ingestion path. It reuses the shared download
# constants (_BASE_URL, _CACHE_DIR, _FF_RETRY_DELAYS) and the same requests+zip
# mechanism, but has its own parser — the 5-factor parser (_parse_ff_csv_text)
# and loader (load_factors) are deliberately left untouched.

def _parse_beme_breakpoints_text(text: str) -> pd.DataFrame:
    """
    Parse the raw text of the Ken French BE/ME breakpoints CSV.

    File format (confirmed against the live file)::

        This file was created using the … CRSP database.  It contains every 5th
        NYSE BEME percentile.
        <blank>
          ,<= 0,>0                                   ← partial header (count cols)
        1926,   3,  429,  0.296, 0.404, …, 31.324     ← year, n(BE<=0), n(BE>0),
        …                                                then 20 breakpoints
        Copyright …                                   ← footer

    Each data row is a 4-digit formation year followed by two firm-count columns
    (number of firms with BE <= 0 and BE > 0) and 20 book-to-market breakpoints
    at the 5th, 10th, …, 100th NYSE percentiles. Breakpoints are formed each June;
    rows are indexed at June 30 of the formation year.

    Returns a DataFrame indexed by June-30 Timestamp with columns p5, p10, …, p100
    (book-to-market ratio at each percentile). Low percentiles are growth/expensive
    (low B/M); high percentiles are value/cheap (high B/M).
    """
    n_break = len(_BEME_PCTILES)
    rows: list[tuple[int, list[float]]] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        first = parts[0] if parts else ""
        # Data rows: 4-digit year + 2 count cols + the 20 breakpoint columns.
        if len(first) == 4 and first.isdigit() and len(parts) >= 3 + n_break:
            try:
                year = int(first)
                vals = [float(x) for x in parts[3:3 + n_break]]
            except ValueError:
                continue
            rows.append((year, vals))

    if not rows:
        raise ValueError("No BE/ME breakpoint data rows found in Ken French CSV text")

    idx = pd.DatetimeIndex([pd.Timestamp(year=y, month=6, day=30) for y, _ in rows])
    data = {f"p{p}": [vals[i] for _, vals in rows] for i, p in enumerate(_BEME_PCTILES)}
    return pd.DataFrame(data, index=idx).sort_index()


def _fetch_beme_breakpoints() -> pd.DataFrame:
    """Download and parse the Ken French BE/ME breakpoints ZIP, with retry."""
    last_exc: Exception = RuntimeError("no attempts made")
    delays = list(_FF_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            resp = requests.get(_BEME_URL, timeout=30)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
                raw_text = zf.read(csv_name).decode("utf-8", errors="replace")
            return _parse_beme_breakpoints_text(raw_text)
        except Exception as exc:
            last_exc = exc
            print(
                f"[INFO] Ken French BE/ME fetch attempt {attempt + 1}/{len(delays) + 1} "
                f"failed: {exc}",
                file=sys.stderr,
            )
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise RuntimeError(
        f"Ken French BE/ME breakpoint download failed after {len(delays) + 1} attempts: "
        f"{last_exc}"
    )


def load_beme_breakpoints(force_refresh: bool = False) -> pd.DataFrame:
    """
    Return Ken French NYSE BE/ME breakpoints (annual, June-formed).

    DataFrame indexed by June-30 Timestamp with columns p5…p100 (book-to-market
    ratio at each NYSE percentile). Refreshes the local cache when stale (file
    absent or mtime older than _BEME_REFRESH_DAYS). On fetch failure a stale
    cache is returned with a warning rather than crashing.
    """
    cache = _BEME_CACHE
    stale = True
    if cache.exists():
        mtime = date.fromtimestamp(cache.stat().st_mtime)
        stale = (date.today() - mtime).days > _BEME_REFRESH_DAYS

    if force_refresh or stale:
        try:
            df = _fetch_beme_breakpoints()
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache)
            return df
        except Exception as exc:
            if cache.exists():
                warnings.warn(
                    f"BE/ME breakpoint refresh failed ({exc}); using cached data.",
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
    Run FF5 regression for the International Core sleeve (VEA) against GLOBAL factors.

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
            sleeve_label=f"{_INTL_CORE} Sleeve — Global Factors",
            tickers=spec["tickers"],
            region="global",
            inception=inception,
            end_date=end_date,
        )
    except Exception:
        return None


def run_intl_tilt_regressions(inception: str, end_date: str) -> dict:
    """Per-fund FF5 regressions for the international TILT sleeves + their controls.

    Runs only on the tilted (demo) book — the one that splits developed-
    international into Quality / Large Value / Small Value tilt sleeves. On the
    untilted (personal) book there is a single VEA sleeve, already regressed via
    run_sleeve_regressions, and this returns an empty dict.

    Each tilt fund (IDHQ, AVIV, AVDV) and its Canada-matched control (IQLT, IVLU,
    ISVL) is regressed against the Developed ex-US FF5 factors through the same
    run_sleeve_regression path VEA uses, so it inherits the US-trading-day
    reindex and the full result-key set. See _INTL_TILT_SLEEVES for the roster
    rationale (why the controls are Canada-matched and NOT the SAA benchmarks).

    Returns an ordered dict keyed by fund ticker; each value carries the fund and
    control result dicts (either may be None on insufficient data) plus the
    metadata the page needs to render and caption the pair.
    """
    try:
        from src.sleeve_config import international_sleeves
        book_sleeves = set(international_sleeves())
    except Exception:
        return {}

    # Untilted book: a single developed-international sleeve. Nothing to add.
    if len(book_sleeves) <= 1:
        return {}

    out: dict[str, dict] = {}
    for spec in _INTL_TILT_SLEEVES:
        if spec["sleeve"] not in book_sleeves:
            continue
        try:
            fund_res = run_sleeve_regression(
                sleeve_label=f"{spec['sleeve']} — {spec['fund']}",
                tickers=[spec["fund"]],
                region="developed_exus",
                inception=inception,
                end_date=end_date,
            )
        except Exception:
            fund_res = None
        try:
            control_res = run_sleeve_regression(
                sleeve_label=f"{spec['sleeve']} control — {spec['control']}",
                tickers=[spec["control"]],
                region="developed_exus",
                inception=inception,
                end_date=end_date,
            )
        except Exception:
            control_res = None

        out[spec["fund"]] = {
            "sleeve":         spec["sleeve"],
            "fund":           spec["fund"],
            "fund_name":      spec["fund_name"],
            "control":        spec["control"],
            "control_name":   spec["control_name"],
            "control_index":  spec["control_index"],
            "disclosure":     spec["disclosure"],
            "israel_pct":     spec.get("israel_pct"),
            "fund_result":    fund_res,
            "control_result": control_res,
        }
    return out


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
            f"The {_INTL_CORE} sleeve (VEA, {T_dev} trading days) "
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
        "Real assets (VNQ 60%, DBC 40%) are excluded — no liquid daily factor proxy set spans "
        "REIT and commodity exposure simultaneously."
    )

    return lines


# ── International tilt-sleeve residual reading + per-fund disclosures ───────────

def intl_residual_reading_order() -> str:
    """The explicit order in which a tilt-sleeve residual should be read.

    Skill is last and least: a non-zero residual is fully expected before any
    question of skill arises. This is the interpretation contract for the whole
    international tilt section and mirrors the 'How to read this page' expander.
    """
    return (
        "**Read a residual in this order — skill last, and least.** "
        "**(1) Sampling noise.** At about one year of daily data the 95% confidence "
        "interval spans hundreds of basis points; most of any single residual estimate "
        "is noise, and the interval shown with each number says so. "
        "**(2) Universe / classification mismatch.** The fund's index and the Ken French "
        "factor universe classify countries differently — South Korea is developed to "
        "S&P and FTSE-for-VEA but emerging to Ken French; Canada is in the factor "
        "universe but out of MSCI EAFE. Returns that fall outside the factor span "
        "accumulate in the residual with no skill involved. "
        "**(3) Construction differences** between an investable fund and the academic "
        "factors: the factors are computed gross of foreign dividend withholding while a "
        "fund reports net-of-withholding NAV; the factors rebalance costlessly and are "
        "long-short; the fund is long-only, reaches a microcap tail the NYSE-style "
        "breakpoints smooth over, and prices across time zones that close before the US "
        "tape (stale pricing). "
        "**(4) Skill** — considered last, and weighted least, because every item above "
        "produces a non-zero residual without it. Each fund is shown beside a passive, "
        "Canada-matched control so the residual has an investable yardstick: a control "
        "that carries a large residual too is measuring construction, not selection."
    )


def build_intl_tilt_disclosure(entry: dict) -> list[str]:
    """Per-fund disclosure paragraphs for one international tilt sleeve.

    ASYMMETRIC by design (`entry['disclosure']`):
      - IDHQ ('idhq') guards against reading the residual as skill — its residual
        is largely a Korea universe artifact. The Korea mechanism is stated
        STRUCTURALLY (reconstitution-varying), never as a pinned weight.
      - AVDV / AVIV ('avantis') guard against OVER-applying the Korea excuse —
        they are actively managed and observed Korea-clear, so the residual is
        construction (Israel, costs, withholding, a window-specific materials /
        gold-miner tilt Avantis flags as unrepeatable), not universe.
    """
    fund    = entry["fund"]
    control = entry["control"]
    cidx    = entry["control_index"]
    kind    = entry["disclosure"]

    paras: list[str] = []

    if kind == "idhq":
        paras.append(
            f"**Do not read {fund}'s residual as skill — it is largely a universe "
            f"artifact.** {fund} tracks S&P's Developed ex-US quality universe, which "
            "classifies South Korea as **developed** and holds it; the Ken French "
            "Developed ex-US factor universe (and the "
            f"{control} control) classify Korea as emerging and exclude it entirely. "
            "Korean equities' return therefore falls outside the factor span and "
            f"lands in {fund}'s residual with no selection involved."
        )
        paras.append(
            "The exposure driving this is **reconstitution-varying, not a fixed "
            "weight**: the S&P screen's Korea weight stood near 9.5% in the first "
            "half of 2026 and steps down to roughly 2% after the June "
            "reconstitution. A residual channel that rises and falls with an index "
            "rebalance is the signature of a universe mismatch, not persistent "
            "skill — which is exactly why the weight is described as a mechanism "
            "here rather than pinned as a number the residual could be 'corrected' by."
        )
        paras.append(
            "One residual channel is genuinely about the screen, not the universe: "
            "S&P's quality score and the Fama-French **RMW** (profitability) factor "
            "are cousins, not the same construct, so the part of the quality "
            f"selection that RMW does not span also accrues to the residual. The "
            f"{control} control ({cidx}) — Canada-included, Korea-excluded on the "
            "same terms as the factors — removes the Korea channel, so its residual "
            "is the cleaner read on what an international quality screen actually "
            "costs against these factors."
        )
        return paras

    # Avantis funds (AVDV, AVIV) — the "not-Korea" side of the asymmetry.
    israel = entry.get("israel_pct") or "a few"
    paras.append(
        f"**The Korea excuse does not apply to {fund} — do not over-extend it here.** "
        f"Unlike IDHQ, the universe channel does not explain this residual. As of the "
        f"most recent Avantis N-PORT holdings disclosure (period ending {_NPORT_ASOF}), "
        f"{fund} held **no South Korea position** — an absence *observed* in the filing, "
        f"not one the mandate prohibits: {fund} is actively managed and could add Korea "
        f"at a future reconstitution. The {control} control ({cidx}) carves Korea out on "
        "the same terms as the factors, so the fund-minus-control residual is "
        "Korea-neutral by construction."
    )
    paras.append(
        f"What remains is **construction, not universe — and it is not a persistent "
        f"edge.** The fund-minus-control gap should not be read as a repeatable "
        f"{fund}-over-control advantage of this size. A material part of it in this "
        f"window is a sector bet: an overweight to materials and gold miners that "
        f"contributed disproportionately and that Avantis characterizes as cyclical "
        f"and **not repeatable** — as that tilt normalizes the gap should compress, "
        f"not compound. What is left is persistent but still not skill: an Israel "
        f"weight of roughly {israel}% (developed, so spanned by the factors, but a "
        f"source of idiosyncratic country return), the fund's expense ratio and "
        f"unrecovered foreign dividend-withholding drag on a net-NAV basis, and the "
        f"deeper microcap tail the NYSE-style breakpoints smooth over."
    )
    paras.append(
        f"Finally, a joint-metric caveat: Avantis integrates value and profitability "
        f"into a **single** selection metric, so {fund}'s **HML** and **RMW** loadings "
        "are not the separable, one-factor-at-a-time exposures a pure sort would show. "
        "The residual absorbs the part of that joint screen the two standalone factors "
        "do not span — again a construction property, read well before any question of "
        "skill."
    )
    return paras


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

    lag_str = "N/A"
    if us and us.get("sample_end"):
        try:
            _lag_days = (date.today() - date.fromisoformat(us["sample_end"])).days
            lag_str   = f"{_lag_days}-calendar-day publication lag (factor data ends {us['sample_end']})"
        except Exception:
            pass

    notes = [
        f"Samples: US equity sleeve {T_us} US trading days ({us_window}), L = {L_us}. "
        f"{_INTL_CORE} sleeve {T_dev} US trading days ({dev_window}), L = {L_dev}. "
        "Both sleeves are restricted to US equity market trading days (dates present "
        "in the Ken French US factor calendar). The Developed sleeve applies this "
        "restriction explicitly: VEA trades on US exchanges and has no price observation "
        "on US federal holidays — its return series shows zero by forward-fill, not by "
        "market observation. The Dev FF dataset includes those holidays (international "
        "markets open); excluding them keeps both regression calendars consistent. "
        f"Sample sizes reflect the overlap with the most recent available factor data ({lag_str}).",

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

        f"Global factor supplement ({_INTL_CORE}): Ken French ceased publication "
        "of the daily Global 5-factor file in June 2019. This portfolio's inception "
        "post-dates that cutoff; no daily-frequency Global FF5 regression can be produced. "
        "The Developed ex-US factor set is the primary decomposition for the international "
        "sleeve. Global Mkt-RF would include US exposure in any case, making it less precise "
        "for a developed-ex-US ETF — the Korea universe mismatch is better bounded via the "
        "Developed ex-US result alone.",
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

            "Real assets (VNQ 60%, DBC 40%) remain excluded: no liquid daily factor proxy set spans "
            "REIT and commodity exposure simultaneously. Factor models for real assets are "
            "a future extension.",
        ]
    else:
        notes.append(
            "Fixed income (VGIT, SCHP): TERM/CREDIT factor model (IEF−BIL duration premium, "
            "HYG−IEF credit spread premium) in scope — see FI Sleeve panel above. "
            "Real assets (VNQ 60%, DBC 40%) remain excluded; no liquid daily factor proxy set "
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

    # Compute 95% CI for alpha if HAC SE is available
    _hac_bse  = result.get("_hac_bse", {})
    _se_daily = _hac_bse.get("const", float("nan"))
    if _se_daily == _se_daily:  # not NaN
        _se_bps = _se_daily * 252 * 10_000
        _ci_lo  = a_bps - 1.96 * _se_bps
        _ci_hi  = a_bps + 1.96 * _se_bps
        _lo_s   = f"−{abs(_ci_lo):.0f}" if _ci_lo < 0 else f"+{_ci_lo:.0f}"
        _hi_s   = f"−{abs(_ci_hi):.0f}" if _ci_hi < 0 else f"+{_ci_hi:.0f}"
        _ci_part = f" (95% CI: [{_lo_s}, {_hi_s}]; t = {t_a:.2f})"
    else:
        _ci_part = f" (t = {t_a:.2f})"

    # Second paragraph: intercept interpretation + optional Brinson-Fachler cross-reference
    second_para = (
        f"The intercept — active return after controlling for benchmark beta and style tilts — "
        f"is {a_bps:+.0f} bps/yr{_ci_part}, {sig_label}. "
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

    cross_page_note = (
        "Note: this regression covers the since-inception window; the Brinson-Fachler "
        "attribution on the Performance page reports Q1 2026. The top selection drivers "
        "differ by design, reflecting the longer window captured here."
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

        cross_page_note,
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
        "R_p: daily portfolio total return (adj_close basis, SPAXX proxied via BIL normalized "
        "to $1.00 at inception). R_b: daily custom blended SAA benchmark return (target-weight "
        "basket: SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, 60% VNQ + 40% DBC, BIL). "
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


# ── Per-sleeve and per-regression dynamic interpretation ──────────────────────

_T_SIG = 2.0   # |t| threshold for "statistically significant"
_R2_LOW = 0.70  # R² below this flags a weak model fit


def interpret_sleeve_regression(res: dict, factor_names: list | None = None) -> str:
    """
    Generate 2–4 sentences interpreting a single sleeve's factor regression.

    Works for both FF5 equity regressions and TERM/CREDIT FI regressions.
    Identifies statistically significant factor loadings, flags alpha significance,
    and notes low R² when present.

    Args:
        res:          Result dict from run_sleeve_regressions / regress_fi_sleeve.
        factor_names: Factor names to inspect (defaults to all in res["betas"]).
    """
    if res is None:
        return "Regression result unavailable."

    betas    = res.get("betas", {})
    t_stats  = res.get("t_stats", {})
    a_bps    = res.get("alpha_annual_bps", 0.0)
    t_alpha  = res.get("t_alpha", 0.0)
    r2       = res.get("r_squared", 0.0)
    T        = res.get("T", 0)
    label    = res.get("sleeve_label", "This sleeve")
    factors  = factor_names or list(betas.keys())

    parts: list[str] = []

    # Significant factor loadings
    sig_factors = [f for f in factors if abs(t_stats.get(f, 0.0)) >= _T_SIG]
    for f in sig_factors:
        b = betas[f]
        t = t_stats[f]
        direction = "positive" if b > 0 else "negative"
        parts.append(
            f"{f} loading: {b:+.3f} (t = {t:.2f}, {direction}, significant)"
        )

    factor_sentence = ""
    if sig_factors:
        factor_sentence = (
            f"{label} shows significant loadings on: "
            + "; ".join(parts) + ". "
        )
    else:
        factor_sentence = (
            f"{label} has no statistically significant factor loadings "
            f"at the |t| > {_T_SIG:.0f} threshold across the {T} observed trading days. "
            "This may reflect a short sample period, not an absence of factor exposure. "
        )

    # Alpha interpretation
    alpha_sig = abs(t_alpha) >= _T_SIG
    if alpha_sig:
        alpha_dir = "positive" if a_bps > 0 else "negative"
        alpha_sentence = (
            f"Annualized alpha is {a_bps:+.0f} bps (t = {t_alpha:.2f}), "
            f"statistically significant — a {alpha_dir} active return not explained by "
            "the factor model. Given the short sample, this estimate carries wide uncertainty "
            "and should not be read as evidence of persistent skill."
        )
    else:
        alpha_sentence = (
            f"Annualized alpha of {a_bps:+.0f} bps (t = {t_alpha:.2f}) is not statistically "
            "significant — the sleeve's returns are consistent with factor exposures alone."
        )

    # R² note
    r2_sentence = ""
    if r2 < _R2_LOW:
        r2_sentence = (
            f" R² = {r2:.3f} is below {_R2_LOW:.0f}, indicating the factor model explains "
            "only a portion of this sleeve's variance — either a universe mismatch "
            "or factors outside the standard FF5 set are at play."
        )

    return factor_sentence + alpha_sentence + r2_sentence


def interpret_benchmark_attribution(result: dict) -> str:
    """
    Generate a focused interpretation of the benchmark attribution regression.

    This regression is a SELECTION-and-INTRA-SLEEVE-TILT model:
      (R_p − RF) ~ β_bench(R_b − RF) + β_HML·HML + β_SMB·SMB + β_RMW·RMW + α

    The blended benchmark (R_b) captures the SAA policy allocation.
    Style factor loadings ABOVE the benchmark capture residual tilts not
    explained by the SAA's target weights. Alpha is the active return
    after controlling for both the benchmark and those residual style tilts.
    This is NOT a Brinson-Fachler decomposition; that lives on the
    Performance page.

    Args:
        result: result dict from run_benchmark_attribution_regression.
    """
    if result is None:
        return "Benchmark attribution regression unavailable."

    b_bench = result["betas"]["Bench-RF"]
    t_bench = result["t_stats"]["Bench-RF"]
    b_hml   = result["betas"]["HML"]
    t_hml   = result["t_stats"]["HML"]
    b_smb   = result["betas"]["SMB"]
    t_smb   = result["t_stats"]["SMB"]
    b_rmw   = result["betas"]["RMW"]
    t_rmw   = result["t_stats"]["RMW"]
    a_bps   = result["alpha_annual_bps"]
    t_alpha = result["t_alpha"]
    r2      = result["r_squared"]

    # Benchmark beta sentence — meaning only, no specific coefficient values
    if abs(b_bench - 1.0) < 0.10:
        bench_sentence = (
            "The portfolio tracks its SAA policy benchmark closely — consistent with a "
            "fully-invested passive/semi-passive implementation."
        )
    else:
        bench_sentence = (
            "The portfolio's benchmark beta departs from the expected 1.0, reflecting "
            "cross-sleeve return dispersion or cash drag."
        )

    # Style tilt sentences — meaning only, no specific β or t-stat values
    style_parts = []
    for fname, beta, tstat, context in [
        ("HML", b_hml, t_hml, "value tilt (VTV, AVUV)"),
        ("SMB", b_smb, t_smb, "small-cap tilt (AVUV)"),
        ("RMW", b_rmw, t_rmw, "profitability tilt (SPHQ, AVUV)"),
    ]:
        if abs(tstat) >= _T_SIG:
            direction = "positive" if beta > 0 else "negative"
            style_parts.append(f"{fname}: {direction} {context}")

    if style_parts:
        style_sentence = (
            "Residual style tilts beyond the SAA benchmark: "
            + "; ".join(style_parts) + ". "
            "See the regression table above for loadings and significance."
        )
    else:
        style_sentence = (
            "No statistically significant residual style tilts beyond the SAA benchmark "
            "are detected at the current sample length. "
        )

    # Alpha sentence — significance and direction only, numbers live in the table
    alpha_sig = abs(t_alpha) >= _T_SIG
    if alpha_sig:
        alpha_dir = "positive" if a_bps > 0 else "negative"
        alpha_sentence = (
            f"The active return intercept is statistically significant — a {alpha_dir} return "
            "after accounting for both the SAA benchmark and residual style exposures. "
            "See the regression table and the Interpretation section below for the full "
            "alpha estimate with confidence interval."
        )
    else:
        alpha_sentence = (
            "The active return intercept is not statistically significant at the current "
            "sample length — the portfolio's return is consistent with its SAA benchmark "
            "exposure and residual style tilts alone."
        )

    return bench_sentence + " " + style_sentence + " " + alpha_sentence


def interpret_correlations(corr: pd.DataFrame) -> str:
    """
    Generate 2–3 sentences interpreting a correlation matrix of sleeve returns.

    Identifies highest and lowest off-diagonal pairs, cross-asset patterns,
    and flags any unexpected correlations. Returns an empty string if the
    matrix has fewer than 3 sleeves.

    Args:
        corr: square correlation DataFrame (sleeves as both index and columns).
    """
    if corr.empty or len(corr) < 3:
        return ""

    # Build sorted list of off-diagonal pairs
    pairs: list[tuple[str, str, float]] = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            pairs.append((cols[i], cols[j], float(corr.iloc[i, j])))

    if not pairs:
        return ""

    pairs_sorted = sorted(pairs, key=lambda x: x[2])
    lowest  = pairs_sorted[:2]
    highest = pairs_sorted[-3:][::-1]   # top 3, highest first

    # Highest correlations sentence — lead with allocator conclusion, pairs as evidence
    high_parts = [
        f"{a} × {b} (ρ = {r:.2f})" for a, b, r in highest if r < 0.999
    ]
    high_sentence = (
        f"High intra-equity co-movement — {', '.join(high_parts)} — "
        "means the equity sleeves largely share a single global market beta."
    ) if high_parts else ""

    # Lowest correlations sentence — frame as cross-asset structural difference
    low_parts = [
        f"{a} × {b} (ρ = {r:.2f})" for a, b, r in lowest
    ]
    low_sentence = (
        f"The most meaningful return offsets are {' and '.join(low_parts)}: "
        "the only pairs where structural differences in risk exposure, "
        "not just style tilts, drive genuine diversification."
    ) if low_parts else ""

    sentences = [s for s in [high_sentence, low_sentence] if s]
    return "  \n".join(sentences)
