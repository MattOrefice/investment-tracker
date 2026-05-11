"""FRED macro data fetcher with 24-hour SQLite cache."""
import json
import math
import sys
import time
from datetime import date
from typing import Optional

import pandas as pd

from src.config import FRED_API_KEY as _FRED_KEY
from src.db import get_connection

_FRED_RETRY_DELAYS = (1, 3, 9)  # seconds between retry attempts (exponential backoff)


class FREDFetchError(Exception):
    """Raised when all FRED API retry attempts are exhausted."""
    def __init__(self, series_id: str, cause: Exception):
        self.series_id = series_id
        self.cause = cause
        super().__init__(
            f"FRED series '{series_id}' fetch failed after {len(_FRED_RETRY_DELAYS) + 1} attempts: {cause}"
        )

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS macro_cache (
    series_id  TEXT NOT NULL,
    fetch_date TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (series_id, fetch_date)
)
"""


def _ensure_cache_table() -> None:
    with get_connection() as conn:
        conn.execute(_CACHE_DDL)


def _get_fred():
    from fredapi import Fred
    if not _FRED_KEY:
        raise RuntimeError("FRED_API_KEY not set. Add it to .env (local) or Streamlit secrets (cloud).")
    return Fred(api_key=_FRED_KEY)


def fetch_fred_series(
    series_id: str,
    start_date: str,
    end_date: Optional[str] = None,
) -> pd.Series:
    """Fetch directly from FRED API with exponential backoff retry. Does not touch cache."""
    end  = end_date or date.today().isoformat()
    fred = _get_fred()
    last_exc: Exception = RuntimeError("no attempts made")
    delays = list(_FRED_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            return fred.get_series(series_id, observation_start=start_date, observation_end=end)
        except Exception as exc:
            last_exc = exc
            print(
                f"[INFO] FRED fetch attempt {attempt + 1}/{len(delays) + 1} "
                f"for '{series_id}' failed: {exc}",
                file=sys.stderr,
            )
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise FREDFetchError(series_id, last_exc)


def get_series(series_id: str, start_date: str = "1990-01-01") -> pd.Series:
    """
    Return series from 24h SQLite cache; fetches fresh from FRED if stale or
    if the cached window doesn't cover the requested start_date.
    """
    _ensure_cache_table()
    today = date.today().isoformat()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT data FROM macro_cache WHERE series_id = ? AND fetch_date = ?",
            (series_id, today),
        ).fetchone()

    if row:
        payload = json.loads(row["data"])
        s = pd.Series(
            [float(v) if v is not None else float("nan") for v in payload["values"]],
            index=pd.to_datetime(payload["dates"]),
            name=series_id,
        )
        # Only use the cache if it covers the requested start date
        cached_min = s.dropna().index.min()
        if not s.dropna().empty and str(cached_min.date()) <= start_date:
            return s[s.index >= start_date]
        # Cache hit but coverage is insufficient — re-fetch and overwrite

    raw = fetch_fred_series(series_id, start_date)
    payload = {
        "dates":  [str(d.date()) for d in raw.index],
        "values": [float(v) if pd.notna(v) else None for v in raw.values],
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO macro_cache (series_id, fetch_date, data) VALUES (?, ?, ?)",
            (series_id, today, json.dumps(payload)),
        )
    return raw


def clear_macro_cache() -> int:
    """Delete all macro_cache rows. Returns count of rows deleted."""
    _ensure_cache_table()
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM macro_cache")
        return cur.rowcount


def get_recession_periods(start_date: str, end_date: str) -> list:
    """
    Convert USREC monthly indicator into (start, end) date tuples for chart shading.
    USREC = 1 during NBER-dated recessions, 0 otherwise.
    """
    usrec = get_series("USREC", start_date="1945-01-01")
    window = usrec.loc[start_date:end_date].dropna()

    periods: list = []
    in_rec = False
    rec_start = None

    for dt, val in window.items():
        if val == 1 and not in_rec:
            in_rec = True
            rec_start = dt.date()
        elif val == 0 and in_rec:
            in_rec = False
            periods.append((rec_start, dt.date()))

    if in_rec and rec_start is not None:
        periods.append((rec_start, date.fromisoformat(end_date)))

    return periods


def percentile(series: pd.Series, current_value: float) -> float:
    """Return percentile rank of current_value in the series (0–100)."""
    clean = series.dropna()
    if clean.empty:
        return 50.0
    return float((clean <= current_value).mean() * 100)


def window_pctile(series: pd.Series, current_value: float, w_start: str) -> float:
    """Percentile of current_value within series windowed from w_start onward.

    Falls back to the full series when the windowed slice is empty (w_start
    predates available data or '1800-01-01' sentinel for 'Max' window).
    """
    if w_start == "1800-01-01":
        windowed = series.dropna()
    else:
        windowed = series.loc[w_start:].dropna()
    if windowed.empty:
        return percentile(series, current_value)
    return percentile(windowed, current_value)


_REGIME_LABELS = ("Recession", "Early-cycle", "Mid-cycle", "Late-cycle")


def classify_regime(
    usrec: float | None,
    t10y2y: float | None,
    unrate: float | None,
) -> str:
    """
    Classify the macro regime given three FRED indicator values.

    Rules applied in priority order (first match wins):
      1. Recession   — USREC = 1
      2. Early-cycle — USREC = 0, UNRATE > 5.5%, T10Y2Y > -0.25
      3. Late-cycle  — USREC = 0, T10Y2Y < -0.25 OR UNRATE < 4.2
      4. Mid-cycle   — default

    Missing signals (None) are treated as neutral for that rule.
    Returns one of: 'Recession', 'Early-cycle', 'Mid-cycle', 'Late-cycle'.
    See docs/regime_classifier.md for rationale and limitations.
    """
    if usrec is not None and usrec >= 0.5:
        return "Recession"

    unrate_high  = unrate is not None and unrate > 5.5
    curve_ok     = t10y2y is None or t10y2y > -0.25
    if unrate_high and curve_ok:
        return "Early-cycle"

    curve_inv    = t10y2y is not None and t10y2y < -0.25
    labor_tight  = unrate is not None and unrate < 4.2
    if curve_inv or labor_tight:
        return "Late-cycle"

    return "Mid-cycle"


def get_regime_signals(as_of_date: str | None = None) -> dict:
    """
    Fetch the most-recent USREC, T10Y2Y, and UNRATE values as of as_of_date.
    Returns a dict with keys: usrec, t10y2y, unrate, label (from classify_regime).
    """
    from datetime import date as _date
    end = as_of_date or _date.today().isoformat()

    def _latest(series_id: str, start: str) -> float | None:
        try:
            s = get_series(series_id, start_date=start)
            s = s.dropna()
            s = s.loc[:end]
            if s.empty:
                return None
            return float(s.iloc[-1])
        except Exception:
            return None

    usrec  = _latest("USREC",   "1945-01-01")
    t10y2y = _latest("T10Y2Y",  "1976-06-01")
    unrate = _latest("UNRATE",  "1948-01-01")

    return {
        "usrec":   usrec,
        "t10y2y":  t10y2y,
        "unrate":  unrate,
        "label":   classify_regime(usrec, t10y2y, unrate),
    }


def compute_cape_implied_return(cape: float) -> float:
    """
    Implied 10-year annualized real return from CAPE via log-linear regression.
    Formula: r ≈ −0.070 × ln(CAPE/16) + 0.066
    Calibration: fitted to Shiller long-run data anchored at (CAPE=16, r=6.6%)
    and (CAPE=35, r=1.1%), consistent with Campbell & Shiller (1998) and
    subsequent replications of the log-linear CAPE-to-forward-return relationship.
    """
    return -0.070 * math.log(cape / 16.0) + 0.066


def compute_ecy(cape: float, t10y_pct: float, t10yie_pct: float) -> float:
    """
    Excess CAPE Yield: equity earnings yield minus 10-year real bond yield.
    ECY = (100 / CAPE) − (T10Y% − T10YIE%)
    Positive = equities yield more than real bonds (equities cheaper relative to bonds).
    All inputs and output are in percent (e.g., 2.5 means 2.5%).
    """
    return (100.0 / cape) - (t10y_pct - t10yie_pct)


# ── Interpretation thresholds ──────────────────────────────────────────────────

# ECY (all in percent, same units as compute_ecy output)
EXCESS_CAPE_RICH_THRESHOLD: float = -2.0   # deeply negative → bonds substantially outyield equities
EXCESS_CAPE_RICH: float             =  0.0   # near parity → limited equity premium
EXCESS_CAPE_FAIR: float             =  2.0   # low-to-moderate premium → fair range
EXCESS_CAPE_CHEAP: float            =  4.0   # high premium → equities cheap vs bonds

# Yield curve (basis points)
CURVE_INVERTED: float =   0.0
CURVE_FLAT:     float =  50.0   # 0–50 bps
CURVE_NORMAL:   float = 150.0   # 50–150 bps

# HY credit spreads (basis points)
HY_SPREAD_TIGHT:        float = 300.0
HY_SPREAD_NORMAL:       float = 450.0
HY_SPREAD_WIDE:         float = 600.0
HY_SPREAD_RECESSIONARY: float = 800.0

# GDP growth rate (QoQ annualized, percent)
GDP_TREND:       float = 2.5   # CBO long-run potential output
GDP_ABOVE_TREND: float = 3.5   # solidly above trend


def interpret_excess_cape(value: float) -> str:
    """Dynamic 1–2 sentence interpretation of the Excess CAPE Yield (in percent)."""
    if value < EXCESS_CAPE_RICH_THRESHOLD:
        return (
            f"ECY of {value:.2f}% is deeply negative — real bonds yield substantially more than "
            f"equities by {abs(value):.2f} pp. This regime has historically preceded muted equity "
            "risk premiums; the Core Fixed Income and TIPS sleeves are competitively priced on "
            "a real-yield basis relative to equities."
        )
    elif value < EXCESS_CAPE_RICH:
        return (
            f"ECY of {value:.2f}% places equities near or below parity with real bond yields. "
            "The equity risk premium above real bonds is thin at current CAPE levels, "
            "suggesting forward equity returns may be below long-run averages."
        )
    elif value < EXCESS_CAPE_FAIR:
        return (
            f"ECY of {value:.2f}% sits in the long-run fair range "
            f"(0% to {EXCESS_CAPE_FAIR:.0f}%). "
            "Equities offer a modest real-yield premium over bonds — neither a strong buy "
            "nor a strong sell signal from valuation alone."
        )
    elif value < EXCESS_CAPE_CHEAP:
        return (
            f"ECY of {value:.2f}% indicates equities offer a meaningful real-yield premium "
            "over bonds, historically associated with above-average forward equity returns. "
            "The International Developed and US Large Value sleeves, trading at a discount "
            "to US CAPE, benefit most from wide ECY readings."
        )
    else:
        return (
            f"ECY of {value:.2f}% is unusually wide — equities offer a large real-yield premium "
            f"over bonds by historical standards (above {EXCESS_CAPE_CHEAP:.0f}%). "
            "This has historically signalled materially undervalued equity markets."
        )


def interpret_curve_spread(value_bps: float) -> str:
    """Dynamic 1–2 sentence interpretation of the 10Y−2Y yield curve spread (in bps)."""
    if value_bps < CURVE_INVERTED:
        return (
            f"The yield curve is inverted at {value_bps:+.0f} bps — short-term rates exceed long-term "
            "rates. Persistent inversion has preceded each of the last seven US recessions with a "
            "12–18 month lead time. Allocators watch the un-inversion (curve steepening back above "
            "zero) as the signal that a cutting cycle is underway, not the inversion itself."
        )
    elif value_bps < CURVE_FLAT:
        return (
            f"The yield curve is flat at {value_bps:+.0f} bps — the 10Y−2Y spread is positive but "
            "compressed. A flat curve reflects limited term premium and implies bond markets expect "
            "short rates to remain near current levels; it warrants monitoring for re-inversion."
        )
    elif value_bps < CURVE_NORMAL:
        return (
            f"The yield curve is modestly upward-sloping at {value_bps:+.0f} bps, consistent with a "
            "normal term structure. Duration earns its carry in this environment; "
            "the Core Fixed Income and TIPS sleeves operate as expected portfolio ballast."
        )
    else:
        return (
            f"The yield curve is steep at {value_bps:+.0f} bps. A steep curve historically reflects "
            "high term premium and often emerges in early-cycle recoveries as short rates are cut "
            "while long-end inflation expectations remain elevated — historically favorable for "
            "duration and early-cycle equity returns."
        )


def interpret_hy_spread(value_bps: float) -> str:
    """Dynamic 1–2 sentence interpretation of HY credit spreads (OAS, in bps)."""
    if value_bps < HY_SPREAD_TIGHT:
        return (
            f"HY spreads at {value_bps:.0f} bps are historically tight (below {HY_SPREAD_TIGHT:.0f} bps) — "
            "late-cycle credit market complacency. Tight spreads limit the cushion for further "
            "compression; historical episodes of sub-{HY_SPREAD_TIGHT:.0f} bps spreads have preceded "
            "equity peaks and subsequent spread blowouts."
        )
    elif value_bps < HY_SPREAD_NORMAL:
        return (
            f"HY spreads at {value_bps:.0f} bps are in the normal mid-cycle range "
            f"({HY_SPREAD_TIGHT:.0f}–{HY_SPREAD_NORMAL:.0f} bps), "
            "consistent with moderate default risk and a healthy credit environment."
        )
    elif value_bps < HY_SPREAD_WIDE:
        return (
            f"HY spreads at {value_bps:.0f} bps are elevated "
            f"(above the {HY_SPREAD_NORMAL:.0f} bps neutral threshold), "
            "signalling rising market concern about credit and default risk. "
            "This level has historically been associated with late-cycle or early recessionary "
            "conditions and has preceded equity market weakness."
        )
    elif value_bps < HY_SPREAD_RECESSIONARY:
        return (
            f"HY spreads at {value_bps:.0f} bps are in the stress zone "
            f"({HY_SPREAD_WIDE:.0f}–{HY_SPREAD_RECESSIONARY:.0f} bps). "
            "Credit markets are pricing meaningful recession risk; defensive positioning via "
            "Core Fixed Income and high-quality equity sleeves is warranted."
        )
    else:
        return (
            f"HY spreads at {value_bps:.0f} bps are at recessionary levels (above "
            f"{HY_SPREAD_RECESSIONARY:.0f} bps). Credit markets are pricing severe recession "
            "and elevated default rates — levels that have historically coincided with "
            "the most severe equity drawdowns."
        )


def interpret_gdp_growth(value: float) -> str:
    """Dynamic 1–2 sentence interpretation of real GDP growth (QoQ annualized, percent)."""
    if value < 0.0:
        return (
            f"Real GDP growth of {value:.1f}% is negative. Two consecutive quarters of negative "
            "growth satisfies the informal recession definition (NBER uses a broader indicator set). "
            "Negative growth is associated with rising unemployment, falling earnings, and "
            "widening credit spreads — conditions where duration and quality equity historically outperform."
        )
    elif value < GDP_TREND:
        return (
            f"Real GDP growth of {value:.1f}% is below the long-run potential output trend of "
            f"~{GDP_TREND:.1f}% (CBO estimate). Below-trend growth is consistent with a late-cycle "
            "slowdown or early recovery, where monetary easing becomes more likely and "
            "duration (Core Fixed Income, TIPS) gains defensive value."
        )
    elif value < GDP_ABOVE_TREND:
        return (
            f"Real GDP growth of {value:.1f}% is near the long-run potential output trend of "
            f"~{GDP_TREND:.1f}% — a mid-cycle Goldilocks range. On-trend growth is associated "
            "with stable corporate earnings and balanced equity risk premiums; "
            "the SAA is calibrated for this baseline environment."
        )
    else:
        return (
            f"Real GDP growth of {value:.1f}% is well above the long-run trend of ~{GDP_TREND:.1f}%. "
            "Above-trend growth typically drives strong corporate earnings momentum but "
            "also upward inflation pressure; cyclical sleeves (US Small Cap, Emerging Markets) "
            "tend to outperform, while TIPS provides inflation protection if growth is sustained."
        )


def interpret_us_vs_intl_spread(spread_pp: float, rolling_mean_pp: float) -> str:
    """
    Dynamic interpretation of the trailing 12-month US vs International return spread.

    Args:
        spread_pp:       12-month US minus Intl return, in percentage points (+ve = US leads).
        rolling_mean_pp: 5-year rolling mean of that spread, in pp.
    """
    direction = "outperformed" if spread_pp >= 0 else "underperformed"
    abs_spread = abs(spread_pp)
    delta = spread_pp - rolling_mean_pp

    if delta > 10:
        mean_context = (
            f"the spread is {delta:.1f} pp above its 5-year rolling average of "
            f"{rolling_mean_pp:.1f} pp — well into extended US-leadership territory. "
            "Historically such extremes have mean-reverted via valuation convergence "
            "and dollar cycle turns, supporting the case for the International Developed sleeve."
        )
    elif delta > 3:
        mean_context = (
            f"the spread is {delta:.1f} pp above its 5-year rolling average of "
            f"{rolling_mean_pp:.1f} pp, indicating continued US leadership."
        )
    elif delta > -3:
        mean_context = (
            f"the spread is near its 5-year rolling average of {rolling_mean_pp:.1f} pp — "
            "US and international relative performance is close to its recent historical norm."
        )
    elif delta > -10:
        mean_context = (
            f"the spread is {abs(delta):.1f} pp below its 5-year rolling average of "
            f"{rolling_mean_pp:.1f} pp, consistent with international narrowing the performance gap."
        )
    else:
        mean_context = (
            f"the spread is {abs(delta):.1f} pp below its 5-year rolling average of "
            f"{rolling_mean_pp:.1f} pp — a strong reversal in international's favor, "
            "consistent with the valuation mean-reversion thesis underlying "
            "the 19% International Developed sleeve weight."
        )

    return (
        f"Over the trailing 12 months, US equities (SPY) have {direction} international "
        f"developed (EFA) by {abs_spread:.1f} pp. On a 5-year rolling basis, {mean_context}"
    )
