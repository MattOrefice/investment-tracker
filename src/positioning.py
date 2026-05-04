"""
Active Positioning analysis: tilts, effective duration, scenario triggers.

All functions are pure computation over get_sleeve_weights_on_date() output —
no hand-written quarterly text.  Every number re-derives from live portfolio state.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from src.holdings import get_sleeve_weights_on_date
except ImportError:
    from holdings import get_sleeve_weights_on_date

# ── Static reference dicts ─────────────────────────────────────────────────

TILT_DESCRIPTORS: dict[str, str] = {
    "Cash / SPAXX":           "defensive cash buffer",
    "Core Fixed Income":      "duration exposure",
    "TIPS":                   "real-rate / inflation hedge",
    "US Large Value":         "value tilt within US large-cap",
    "US Large Quality":       "quality factor tilt",
    "US Small Cap":           "small-cap risk premium",
    "Emerging Markets":       "EM growth / dollar-cycle exposure",
    "International Developed":"non-US developed equity exposure",
    "Real Assets":            "real assets / inflation hedge",
    "US Large Core":          "broad US large-cap beta",
}

# Bloomberg US Aggregate Bond Index effective duration — used as the FI benchmark.
# TODO: source this live from Bloomberg/FRED each quarter instead of hardcoding.
BLOOMBERG_AGG_DURATION_YEARS: float = 6.0

# TODO: source these from ETF fact sheets each quarter instead of hardcoding
ETF_DURATION: dict[str, float] = {
    "VGIT":  5.5,   # Vanguard Intermediate-Term Treasury (Core FI holding)
    "SCHP":  6.8,   # Schwab TIPS (TIPS holding) — verify from fact sheet
    "SPAXX": 0.0,   # Money market (Cash holding)
    "IEF":   7.5,   # iShares 7-10Y Treasury (Core FI benchmark)
    "TIP":   7.0,   # iShares TIPS (TIPS benchmark)
    "BIL":   0.1,   # SPDR 1-3 Month T-Bill (Cash benchmark)
}

# Sleeve → actual holding ticker (for duration lookup)
_FI_SLEEVE_HOLDING: dict[str, str] = {
    "Core Fixed Income": "VGIT",
    "TIPS":              "SCHP",
    "Cash / SPAXX":      "SPAXX",
}


# ── Public API ─────────────────────────────────────────────────────────────

def get_active_tilts(
    end_date: str,
    min_abs_bps: float = 50.0,
    min_rel: float = 0.10,
    max_tilts: int = 5,
) -> list[dict]:
    """
    Return sleeves with drift ≥ min_abs_bps OR ≥ min_rel of target weight,
    sorted by absolute drift descending, capped at max_tilts.

    Each dict contains:
        sleeve, direction, actual_pct, target_pct, drift_bps, abs_drift,
        rel_drift, descriptor, line (pre-formatted display string)
    """
    sw = get_sleeve_weights_on_date(end_date)
    if sw.empty:
        return []

    tilts: list[dict] = []
    for sleeve, row in sw.iterrows():
        drift_bps  = row["Drift"] * 10_000
        target_bps = row["Target Weight"] * 10_000
        abs_drift  = abs(drift_bps)
        rel_drift  = abs(drift_bps / target_bps) if target_bps != 0 else 0.0

        if abs_drift < min_abs_bps and rel_drift < min_rel:
            continue

        direction  = "overweight" if drift_bps > 0 else "underweight"
        descriptor = TILT_DESCRIPTORS.get(sleeve, "portfolio exposure")
        actual_pct = row["Actual Weight"] * 100
        target_pct = row["Target Weight"] * 100

        tilts.append({
            "sleeve":      sleeve,
            "direction":   direction,
            "actual_pct":  actual_pct,
            "target_pct":  target_pct,
            "drift_bps":   drift_bps,
            "abs_drift":   abs_drift,
            "rel_drift":   rel_drift,
            "descriptor":  descriptor,
            "line": (
                f"{sleeve}: {direction} vs target "
                f"({actual_pct:.1f}% vs {target_pct:.1f}%, "
                f"Δ {drift_bps:+.0f} bps) — {descriptor}"
            ),
        })

    tilts.sort(key=lambda t: t["abs_drift"], reverse=True)
    return tilts[:max_tilts]


def get_effective_duration(end_date: str) -> dict:
    """
    Return effective duration metrics for the FI sleeves.

    Returns:
        duration          — portfolio-level contribution (FI weighted by full portfolio)
        fi_sleeve_duration — duration of the FI sleeve itself (weighted by FI weight only)
        fi_weight_pct     — actual FI (Core FI + TIPS + Cash) weight as % of portfolio
        agg_benchmark     — Bloomberg US Agg duration for comparison (BLOOMBERG_AGG_DURATION_YEARS)
    """
    sw = get_sleeve_weights_on_date(end_date)
    if sw.empty:
        return {
            "duration": 0.0,
            "fi_sleeve_duration": 0.0,
            "fi_weight_pct": 0.0,
            "agg_benchmark": BLOOMBERG_AGG_DURATION_YEARS,
        }

    total_portfolio_wt = float(sw["Actual Weight"].sum())
    if total_portfolio_wt == 0:
        return {
            "duration": 0.0,
            "fi_sleeve_duration": 0.0,
            "fi_weight_pct": 0.0,
            "agg_benchmark": BLOOMBERG_AGG_DURATION_YEARS,
        }

    weighted_dur = 0.0
    fi_actual_wt = 0.0

    for sleeve, ticker in _FI_SLEEVE_HOLDING.items():
        if sleeve not in sw.index:
            continue
        actual_wt  = float(sw.loc[sleeve, "Actual Weight"])
        duration   = ETF_DURATION.get(ticker, 0.0)
        weighted_dur += actual_wt * duration
        fi_actual_wt  += actual_wt

    eff_duration     = weighted_dur / total_portfolio_wt
    fi_sleeve_dur    = (weighted_dur / fi_actual_wt) if fi_actual_wt > 0 else 0.0
    fi_weight_pct    = fi_actual_wt / total_portfolio_wt * 100

    return {
        "duration":           round(eff_duration, 1),
        "fi_sleeve_duration": round(fi_sleeve_dur, 1),
        "fi_weight_pct":      round(fi_weight_pct, 1),
        "agg_benchmark":      BLOOMBERG_AGG_DURATION_YEARS,
    }


def get_scenario_triggers(end_date: str, max_scenarios: int = 4) -> list[dict]:
    """
    Check current portfolio positioning against scenario conditions.
    Returns at most max_scenarios dicts with keys: name, text.
    Sorted by signal strength descending.
    """
    sw = get_sleeve_weights_on_date(end_date)
    if sw.empty:
        return []

    def _drift_bps(sleeve: str) -> float:
        return float(sw.loc[sleeve, "Drift"]) * 10_000 if sleeve in sw.index else 0.0

    def _actual_wt(sleeve: str) -> float:
        return float(sw.loc[sleeve, "Actual Weight"]) if sleeve in sw.index else 0.0

    def _target_wt(sleeve: str) -> float:
        return float(sw.loc[sleeve, "Target Weight"]) if sleeve in sw.index else 0.0

    dur = get_effective_duration(end_date)
    eff_duration  = dur["duration"]
    fi_weight_pct = dur["fi_weight_pct"]
    fi_target_pct = (
        _target_wt("Core Fixed Income") +
        _target_wt("TIPS") +
        _target_wt("Cash / SPAXX")
    ) * 100

    non_us_actual_pp = (_actual_wt("International Developed") + _actual_wt("Emerging Markets")) * 100
    non_us_target_pp = (_target_wt("International Developed") + _target_wt("Emerging Markets")) * 100
    non_us_drift_pp  = non_us_actual_pp - non_us_target_pp

    candidates: list[dict] = []

    if _drift_bps("US Large Quality") > 0:
        candidates.append({
            "name":   "Late-cycle slowdown",
            "text":   "Earnings revisions signal deteriorating growth — quality tilt rewards margin resilience and balance-sheet strength",
            "weight": abs(_drift_bps("US Large Quality")),
        })

    if non_us_drift_pp >= 2.0:
        candidates.append({
            "name":   "USD weakness",
            "text":   "Non-US earnings outperformance as the dollar reverses — international overweight captures the tailwind",
            "weight": non_us_drift_pp * 100,
        })

    if _drift_bps("Cash / SPAXX") >= 100:
        candidates.append({
            "name":   "Volatility shock",
            "text":   "Risk-off drawdown — cash buffer reduces depth and provides redeployment optionality at lower prices",
            "weight": _drift_bps("Cash / SPAXX"),
        })

    if eff_duration < 3.0 and fi_weight_pct < fi_target_pct - 0.5:
        candidates.append({
            "name":   "Higher-for-longer rates",
            "text":   "Short-duration positioning benefits as longer-duration peers underperform in a sticky-rate environment",
            "weight": (3.0 - eff_duration) * 100,
        })

    if eff_duration > 5.0 or _drift_bps("Core Fixed Income") > 0:
        candidates.append({
            "name":   "Rate cut acceleration",
            "text":   "Duration overweight rewards as yields fall and bond prices rise faster than the short end",
            "weight": max(eff_duration * 10, _drift_bps("Core Fixed Income")),
        })

    if _drift_bps("Real Assets") > 0:
        candidates.append({
            "name":   "Inflation surprise",
            "text":   "Commodity rally and unexpected CPI acceleration — real assets tilt captures upside that equities and bonds miss",
            "weight": _drift_bps("Real Assets"),
        })

    if _drift_bps("US Small Cap") > 0:
        candidates.append({
            "name":   "Small-cap rotation",
            "text":   "Cyclical rally and broadening risk appetite — small-cap risk premium and valuation discount get repriced",
            "weight": _drift_bps("US Small Cap"),
        })

    if _drift_bps("US Large Value") > 0:
        candidates.append({
            "name":   "Value rotation",
            "text":   "Multiple compression in expensive growth stocks — value tilt rewarded as market re-rates on fundamentals",
            "weight": _drift_bps("US Large Value"),
        })

    if _drift_bps("Emerging Markets") > 0:
        candidates.append({
            "name":   "EM-specific catalysts",
            "text":   "Commodity strength, China stimulus, or dollar weakness — EM overweight captures the upside",
            "weight": _drift_bps("Emerging Markets"),
        })

    candidates.sort(key=lambda c: c["weight"], reverse=True)
    return [{"name": c["name"], "text": c["text"]} for c in candidates[:max_scenarios]]
