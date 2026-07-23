"""
Positioning helpers: effective duration, equity style box.

All functions are pure computation over portfolio state.
No hand-written quarterly text — every number re-derives from live data.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import plotly.graph_objects as go

from src.holdings import get_holdings_on_date, get_portfolio_account_id, get_sleeve_weights_on_date
from src.prices import get_prices
from src.style_box import (
    US_EQUITY_TICKERS, NON_US_EQUITY_MAP,
    assign_textposition, compute_size_score, compute_value_growth_score,
    xy_to_category, _load_metadata,
)

# ── Static reference dicts ─────────────────────────────────────────────────

# Bloomberg US Aggregate Bond Index effective duration — used as the FI benchmark.
# Quarterly maintenance: update from Bloomberg Index Services fact sheet or FRED BFI series.
BLOOMBERG_AGG_DURATION_YEARS: float = 6.0

# Quarterly maintenance: update from ETF provider fact sheets (iShares, Vanguard, Schwab).
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
    # The international tilts land in the same cells as their US analogues —
    # IDHQ with SPHQ, AVIV with VTV, AVDV with AVUV — which is the point of
    # mirroring the US structure abroad.
    "IDHQ": ("Large", "Blend"),   # S&P Intl Dev Qual  — Foreign Large Blend
    "AVIV": ("Large", "Value"),   # Avantis Intl Value — Foreign Large Value
    "AVDV": ("Small", "Value"),   # Avantis Intl Sm Val— Foreign Small/Mid Value
    "IEMG": ("Large", "Blend"),   # MSCI EM            — Diversified Emerging Mkts
}

_STYLE_X: dict[str, int] = {"Value": 0, "Blend": 1, "Growth": 2}
_SIZE_Y:  dict[str, int] = {"Large": 2, "Mid": 1, "Small": 0}

# Within-cell dot layout for n simultaneous ETFs.
# Offsets in cell-coordinate units (each cell spans 1.0 unit).
# Chart is 520px wide / 3 cells ≈ 135px per unit (x); 390px / 3 ≈ 97px per unit (y).
#
# n=4 geometry: ±0.22 x (59px c-t-c), ±0.10 y (19px c-t-c).
#   Upper dots at y=2.10: 38.7px below plot ceiling at 520×390 → ample headroom for "top" label.
#   textpositions point outward from cell centre (top-left/right, bottom-left/right) so
#   labels radiate away from each other and away from adjacent cells.
_CELL_OFFSETS: dict[int, list[tuple[float, float]]] = {
    1: [(0.0,   0.0)],
    2: [(-0.22, 0.0),  (0.22,  0.0)],
    3: [(-0.22, 0.0),  (0.0,   0.0),  (0.22,  0.0)],
    4: [(-0.22, 0.10), (0.22,  0.10), (-0.22, -0.10), (0.22, -0.10)],
}
_CELL_TEXTPOS: dict[int, list[str]] = {
    1: ["top center"],
    2: ["top center",  "top center"],
    3: ["top center",  "top center",  "top center"],
    4: ["top left",    "top right",   "bottom left", "bottom right"],
}
# Per-cell dot size range (px). n=4 uses outward corner labels so text sits outside
# the dot; smaller max keeps markers from crowding the centre of the cell.
_CELL_MIN_SIZE: dict[int, float] = {1: 12.0, 2: 18.0, 3: 16.0, 4: 14.0}
_CELL_MAX_SIZE: dict[int, float] = {1: 50.0, 2: 38.0, 3: 30.0, 4: 24.0}


# ── Style box helpers ──────────────────────────────────────────────────────

def _portfolio_market_values(date_str: str) -> tuple[dict[str, float], float]:
    """Shared helper: returns (ticker→market_value, total_mv) for all holdings."""
    holdings = get_holdings_on_date(date_str, account_id=get_portfolio_account_id())
    if holdings.empty:
        return {}, 0.0

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

    return ticker_mv, total_mv


def get_style_box_data(date_str: str) -> list[dict]:
    """
    Return continuous-coordinate style entries for US equity ETFs.
    Each dict: ticker, x, y, weight_pct (with x, y as grid coordinates).
    Non-US equity excluded — see get_non_us_equity_data().
    """
    ticker_mv, total_mv = _portfolio_market_values(date_str)
    if total_mv == 0:
        return []

    meta = _load_metadata()
    result = []
    for ticker in US_EQUITY_TICKERS:
        mv = ticker_mv.get(ticker, 0.0)
        if mv == 0.0:
            continue
        x = compute_value_growth_score(ticker, meta)
        y = compute_size_score(ticker, meta)
        result.append({
            "ticker":     ticker,
            "x":          round(x, 4),
            "y":          round(y, 4),
            "weight_pct": round(mv / total_mv * 100, 2),
        })
    return sorted(result, key=lambda d: -d["weight_pct"])


def _non_us_equity_map() -> dict[str, str]:
    """{ticker -> sleeve label} for non-US equity holdings, derived from the DB.

    Covers whatever international structure the current book has — the single
    developed sleeve on the personal book; the Core/Quality/Large Value/Small
    Value split on the demo book — plus Emerging Markets. The hardcoded
    NON_US_EQUITY_MAP froze the two-ticker (VEA/IEMG) world and silently
    dropped the tilt holdings from the callout on the 12-sleeve book; it
    remains only as the fallback when the DB is unavailable.
    """
    try:
        from src.sleeve_config import international_sleeves, sleeve_holdings
        held = sleeve_holdings()
        derived = {
            ticker: sleeve
            for sleeve in (*international_sleeves(), "Emerging Markets")
            for ticker in held.get(sleeve, [])
        }
        return derived or dict(NON_US_EQUITY_MAP)
    except Exception:
        return dict(NON_US_EQUITY_MAP)


def get_non_us_equity_data(date_str: str) -> list[dict]:
    """
    Return weight data for non-US equity ETFs for the callout section.
    Each dict: ticker, region_label (the sleeve name), weight_pct.
    """
    ticker_mv, total_mv = _portfolio_market_values(date_str)
    if total_mv == 0:
        return []

    result = []
    for ticker, region_label in _non_us_equity_map().items():
        mv = ticker_mv.get(ticker, 0.0)
        if mv == 0.0:
            continue
        result.append({
            "ticker":       ticker,
            "region_label": region_label,
            "weight_pct":   round(mv / total_mv * 100, 2),
        })
    return sorted(result, key=lambda d: -d["weight_pct"])


def build_style_box_figure(style_data: list[dict]) -> go.Figure:
    """
    Morningstar 3×3 style grid. Dot position ∝ actual style/size tilt; dot size ∝ weight.

    Dual-mode input:
      Continuous (preferred): dicts contain 'x' and 'y' float coordinates
        computed from ETF fundamentals via src.style_box. Each dot placed at its
        data-derived position; textposition outward from cell centre via assign_textposition.
      Categorical (legacy, existing tests): dicts contain 'size' and 'style' strings.
        Uses cell-centre + _CELL_OFFSETS placement and the prior two-trace split.
    """
    fig = go.Figure()

    for i in range(4):
        fig.add_shape(type="line", x0=i - 0.5, x1=i - 0.5, y0=-0.5, y1=2.5,
                      line=dict(color="#CCCCCC", width=1), layer="below")
        fig.add_shape(type="line", x0=-0.5, x1=2.5, y0=i - 0.5, y1=i - 0.5,
                      line=dict(color="#CCCCCC", width=1), layer="below")

    if not style_data:
        pass  # empty grid

    elif "x" in style_data[0] and "y" in style_data[0]:
        # ── Continuous mode ─────────────────────────────────────────────────
        x_vals   = [d["x"] for d in style_data]
        y_vals   = [d["y"] for d in style_data]
        tickers  = [d["ticker"] for d in style_data]
        weights  = [d["weight_pct"] for d in style_data]
        textpos  = [assign_textposition(d["x"], d["y"]) for d in style_data]

        w_min, w_max = min(weights), max(weights)
        span = w_max - w_min if w_max > w_min else 1.0
        _MIN_SZ, _MAX_SZ = 14.0, 36.0
        sizes = [_MIN_SZ + (_MAX_SZ - _MIN_SZ) * (w - w_min) / span for w in weights]

        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            mode="markers+text",
            marker=dict(
                size=sizes, color="#2E4057", opacity=0.80,
                line=dict(width=1, color="white"),
            ),
            text=tickers,
            textfont=dict(size=8, color="#222222"),
            textposition=textpos,
            customdata=[[w] for w in weights],
            hovertemplate="%{text}: %{customdata[0]:.1f}%<extra></extra>",
        ))

    else:
        # ── Categorical mode (legacy — preserves all existing tests) ────────
        cell_items: dict = defaultdict(list)
        for i, d in enumerate(style_data):
            key = (_STYLE_X.get(d["style"], 1), _SIZE_Y.get(d["size"], 2))
            cell_items[key].append(i)

        x_vals      = [0.0] * len(style_data)
        y_vals      = [0.0] * len(style_data)
        textpos_out = ["top center"] * len(style_data)
        cell_n: dict[int, int] = {}

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

        weights = [d["weight_pct"] for d in style_data]
        w_min, w_max = min(weights), max(weights)
        span = w_max - w_min if w_max > w_min else 1.0
        sizes = []
        for i, w in enumerate(weights):
            capped = min(cell_n.get(i, 1), 4)
            min_s  = _CELL_MIN_SIZE.get(capped, 12.0)
            max_s  = _CELL_MAX_SIZE.get(capped, 24.0)
            sizes.append(min_s + (max_s - min_s) * (w - w_min) / span)

        normal_idx  = [i for i in range(len(style_data)) if cell_n.get(i, 1) < 4]
        crowded_idx = [i for i in range(len(style_data)) if cell_n.get(i, 1) >= 4]

        if normal_idx:
            fig.add_trace(go.Scatter(
                x=[x_vals[i] for i in normal_idx],
                y=[y_vals[i] for i in normal_idx],
                mode="markers+text",
                marker=dict(
                    size=[sizes[i] for i in normal_idx], color="#2E4057", opacity=0.80,
                    line=dict(width=1, color="white"),
                ),
                text=[style_data[i]["ticker"] for i in normal_idx],
                textfont=dict(size=8, color="#222222"),
                textposition=[textpos_out[i] for i in normal_idx],
                customdata=[[style_data[i]["weight_pct"]] for i in normal_idx],
                hovertemplate="%{text}: %{customdata[0]:.1f}%<extra></extra>",
            ))

        if crowded_idx:
            fig.add_trace(go.Scatter(
                x=[x_vals[i] for i in crowded_idx],
                y=[y_vals[i] for i in crowded_idx],
                mode="markers+text",
                marker=dict(
                    size=[sizes[i] for i in crowded_idx], color="#2E4057", opacity=0.80,
                    line=dict(width=1, color="white"),
                ),
                text=[style_data[i]["ticker"] for i in crowded_idx],
                textfont=dict(size=8, color="#222222"),
                textposition=[textpos_out[i] for i in crowded_idx],
                customdata=[[style_data[i]["weight_pct"]] for i in crowded_idx],
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
        height=390, width=520,
        plot_bgcolor="#FAFAFA", paper_bgcolor="white",
        font=dict(family="sans-serif", size=9, color="#333"),
        showlegend=False,
    )

    return fig


# ── Public API ─────────────────────────────────────────────────────────────

def get_effective_duration(end_date: str) -> dict:
    """
    Return effective duration metrics for the FI sleeves.

    Returns:
        duration               — portfolio-level duration contribution (Core FI + TIPS only)
        fi_sleeve_duration     — duration of Core FI + TIPS only (cash excluded)
        fi_weight_pct          — Core FI + TIPS weight as % of invested (ex-cash) portfolio
        cash_weight_pct        — operational SPAXX float as % of TOTAL portfolio
        fi_weight_incl_cash_pct — Core FI + TIPS + operational cash (for informational use)
        agg_benchmark          — Bloomberg US Agg duration for comparison

    Phase 38a — sleeve weights are ex-cash (sum to 1.0 over the 9 strategic
    sleeves); operational cash is read from the sleeve frame's .attrs, not as a
    sleeve row.
    """
    _empty = {
        "duration": 0.0, "fi_sleeve_duration": 0.0,
        "fi_weight_pct": 0.0, "cash_weight_pct": 0.0,
        "fi_weight_incl_cash_pct": 0.0, "agg_benchmark": BLOOMBERG_AGG_DURATION_YEARS,
    }
    sw = get_sleeve_weights_on_date(end_date)
    if sw.empty:
        return _empty

    total_portfolio_wt = float(sw["Actual Weight"].sum())
    if total_portfolio_wt == 0:
        return _empty

    weighted_dur    = 0.0
    fi_wt_excl_cash = 0.0
    fi_wt_incl_cash = 0.0
    cash_wt         = 0.0

    for sleeve, ticker in _FI_SLEEVE_HOLDING.items():
        if sleeve not in sw.index:
            continue
        actual_wt = float(sw.loc[sleeve, "Actual Weight"])
        duration  = ETF_DURATION.get(ticker, 0.0)
        fi_wt_incl_cash += actual_wt
        if sleeve == "Cash / SPAXX":
            cash_wt += actual_wt
        else:
            weighted_dur    += actual_wt * duration
            fi_wt_excl_cash += actual_wt

    eff_duration  = weighted_dur / total_portfolio_wt
    fi_sleeve_dur = weighted_dur / fi_wt_excl_cash if fi_wt_excl_cash > 0 else 0.0

    # Operational cash as a share of TOTAL portfolio, from the ex-cash frame's attrs.
    cash_of_total_pct = round(float(sw.attrs.get("cash_weight_of_total", 0.0)) * 100, 1)
    fi_excl_cash_pct  = round(fi_wt_excl_cash / total_portfolio_wt * 100, 1)

    return {
        "duration":                round(eff_duration, 1),
        "fi_sleeve_duration":      round(fi_sleeve_dur, 1),
        "fi_weight_pct":           fi_excl_cash_pct,
        "cash_weight_pct":         cash_of_total_pct,
        "fi_weight_incl_cash_pct": round(fi_excl_cash_pct + cash_of_total_pct, 1),
        "agg_benchmark":           BLOOMBERG_AGG_DURATION_YEARS,
    }


