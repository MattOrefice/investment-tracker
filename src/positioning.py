"""
Active Positioning analysis: tilts, effective duration, scenario triggers.

All functions are pure computation over get_sleeve_weights_on_date() output —
no hand-written quarterly text.  Every number re-derives from live portfolio state.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from src.holdings import get_holdings_on_date, get_sleeve_weights_on_date
    from src.prices import get_prices
except ImportError:
    from holdings import get_holdings_on_date, get_sleeve_weights_on_date
    from prices import get_prices

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

# Morningstar 3×3 style box assignments for equity holdings.
# Source: official Morningstar category — update if holdings change.
ETF_STYLE_BOX: dict[str, tuple[str, str]] = {
    "VOO":  ("Large", "Blend"),   # S&P 500            — Large Blend
    "SPHQ": ("Large", "Blend"),   # S&P 500 Quality    — Large Blend
    "VTV":  ("Large", "Value"),   # CRSP Large Value   — Large Value
    "AVUV": ("Small", "Value"),   # Avantis US Sm Val  — Small Value
    "VEA":  ("Large", "Blend"),   # FTSE Dev ex-US     — Foreign Large Blend
    "IEMG": ("Large", "Blend"),   # MSCI EM            — Diversified Emerging Mkts
}

_STYLE_X: dict[str, int] = {"Value": 0, "Blend": 1, "Growth": 2}
_SIZE_Y:  dict[str, int] = {"Large": 2, "Mid": 1, "Small": 0}

# Within-cell dot layout for n simultaneous ETFs.
# Offsets in cell-coordinate units (each cell spans 1.0 unit).
# Chart is 320px wide / 3 cells = 107px per unit (x); 240px / 3 = 80px per unit (y).
# n=4: ±0.22 x (47px c-t-c) / ±0.20 y (32px c-t-c); max dot size 24px → min gap 23px x / 8px y.
_CELL_OFFSETS: dict[int, list[tuple[float, float]]] = {
    1: [(0.0,   0.0)],
    2: [(-0.22, 0.0),  (0.22,  0.0)],
    3: [(-0.22, 0.0),  (0.0,   0.0),  (0.22,  0.0)],
    4: [(-0.22, 0.20), (0.22,  0.20), (-0.22, -0.20), (0.22, -0.20)],
}
_CELL_TEXTPOS: dict[int, list[str]] = {
    1: ["top center"],
    2: ["top center",    "top center"],
    3: ["top center",    "top center",    "top center"],
    4: ["top center",    "top center",    "bottom center", "bottom center"],
}
# Per-cell maximum dot diameter (px) to prevent overlap in crowded cells.
_CELL_MAX_SIZE: dict[int, float] = {1: 50.0, 2: 38.0, 3: 30.0, 4: 24.0}


# ── Style box helpers ──────────────────────────────────────────────────────

def get_style_box_data(date_str: str) -> list[dict]:
    """
    Return per-equity-ETF style box entries with portfolio weights for date_str.
    Each dict: ticker, size, style, weight_pct.
    Only ETFs listed in ETF_STYLE_BOX are included; missing tickers get 0 weight.
    """
    holdings = get_holdings_on_date(date_str)
    if holdings.empty:
        return []

    look_back = (date.fromisoformat(date_str) - timedelta(days=7)).isoformat()
    total_mv = 0.0
    ticker_mv: dict[str, float] = {}

    for ticker, row in holdings.iterrows():
        shares = float(row["net_shares"])
        if ticker == "SPAXX":
            mv = shares
        else:
            try:
                p = get_prices(ticker, look_back, date_str)
                price = float(p["close"].iloc[-1]) if not p.empty else 0.0
                mv = shares * price
            except Exception:
                mv = 0.0
        ticker_mv[ticker] = mv
        total_mv += mv

    if total_mv == 0:
        return []

    result = []
    for ticker, (size, style) in ETF_STYLE_BOX.items():
        mv = ticker_mv.get(ticker, 0.0)
        result.append({
            "ticker":     ticker,
            "size":       size,
            "style":      style,
            "weight_pct": round(mv / total_mv * 100, 2),
        })
    return sorted(result, key=lambda x: -x["weight_pct"])


def build_style_box_figure(style_data: list[dict]) -> go.Figure:
    """
    Morningstar 3×3 style grid. Dot size ∝ portfolio weight.
    Multiple ETFs in the same cell are spread using _CELL_OFFSETS; dot sizes
    are capped per cell population (_CELL_MAX_SIZE) to prevent overlap.
    Bottom-row dots in 4-ETF cells use 'bottom center' label placement to
    avoid collision with the top-row dots above them.
    """
    cell_items: dict = defaultdict(list)
    for i, d in enumerate(style_data):
        key = (_STYLE_X.get(d["style"], 1), _SIZE_Y.get(d["size"], 2))
        cell_items[key].append(i)

    x_vals      = [0.0] * len(style_data)
    y_vals      = [0.0] * len(style_data)
    textpos_out = ["top center"] * len(style_data)
    cell_n      = {}  # style_data index → cell population count

    for (cx, cy), indices in cell_items.items():
        n = min(len(indices), 4)
        offs = _CELL_OFFSETS.get(n, [(0.0, 0.0)] * n)
        tpos = _CELL_TEXTPOS.get(n, ["top center"] * n)
        for j, idx in enumerate(indices):
            ox, oy = offs[j] if j < len(offs) else (0.0, 0.0)
            x_vals[idx]      = cx + ox
            y_vals[idx]      = cy + oy
            textpos_out[idx] = tpos[j] if j < len(tpos) else "top center"
            cell_n[idx]      = len(indices)

    if style_data:
        weights = [d["weight_pct"] for d in style_data]
        w_min, w_max = min(weights), max(weights)
        span = w_max - w_min if w_max > w_min else 1.0
        sizes = []
        for i, w in enumerate(weights):
            n      = cell_n.get(i, 1)
            max_s  = _CELL_MAX_SIZE.get(min(n, 4), 24.0)
            sizes.append(12.0 + (max_s - 12.0) * (w - w_min) / span)
    else:
        sizes = []

    fig = go.Figure()

    for i in range(4):
        fig.add_shape(type="line", x0=i - 0.5, x1=i - 0.5, y0=-0.5, y1=2.5,
                      line=dict(color="#CCCCCC", width=1), layer="below")
        fig.add_shape(type="line", x0=-0.5, x1=2.5, y0=i - 0.5, y1=i - 0.5,
                      line=dict(color="#CCCCCC", width=1), layer="below")

    if style_data:
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            mode="markers+text",
            marker=dict(
                size=sizes, color="#2E4057", opacity=0.80,
                line=dict(width=1, color="white"),
            ),
            text=[d["ticker"] for d in style_data],
            textfont=dict(size=8, color="#222222"),
            textposition=textpos_out,
            customdata=[[d["weight_pct"]] for d in style_data],
            hovertemplate="%{text}: %{customdata[0]:.1f}%<extra></extra>",
        ))

    fig.update_layout(
        xaxis=dict(
            tickmode="array", tickvals=[0, 1, 2],
            ticktext=["Value", "Blend", "Growth"],
            range=[-0.5, 2.5], showgrid=False, zeroline=False, side="top",
            tickfont=dict(size=9),
        ),
        yaxis=dict(
            tickmode="array", tickvals=[0, 1, 2],
            ticktext=["Small", "Mid", "Large"],
            range=[-0.5, 2.5], showgrid=False, zeroline=False,
            tickfont=dict(size=9),
        ),
        margin=dict(l=55, r=20, t=50, b=20),
        height=240, width=320,
        plot_bgcolor="#FAFAFA", paper_bgcolor="white",
        font=dict(family="sans-serif", size=9, color="#333"),
        showlegend=False,
    )

    return fig


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
