"""
Quarterly PDF report generator.

Primary renderer: WeasyPrint (Linux / Streamlit Cloud).
Windows fallback: xhtml2pdf (no GTK required).
Charts: Plotly + kaleido 0.2.1.
"""
import base64
import io
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

try:
    from src.attribution import brinson_fachler_period
    from src.benchmarks import get_custom_blended_series, get_sp500_series
    from src.db import get_connection
    from src.holdings import get_portfolio_value_series, get_sleeve_weights_on_date
    from src.macro import get_series, percentile
    from src.returns import period_return, twr_daily_linked
    from src.shiller import current_cape, get_cape_series
except ImportError:
    from attribution import brinson_fachler_period
    from benchmarks import get_custom_blended_series, get_sp500_series
    from db import get_connection
    from holdings import get_portfolio_value_series, get_sleeve_weights_on_date
    from macro import get_series, percentile
    from returns import period_return, twr_daily_linked
    from shiller import current_cape, get_cape_series

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_REPORTS_DIR  = Path(__file__).parent.parent / "data" / "reports"

_PALETTE = {
    "portfolio": "#2E4057",
    "sp500":     "#8C9AA6",
    "blended":   "#5C7A5C",
    "alloc":     "#5B7FA6",
    "selection": "#A67B5B",
}

# Phase 2 locked picks — holding ticker and benchmark ticker per sleeve
_SLEEVE_HOLDING_TICKER: dict[str, str] = {
    "US Large Core":           "VOO",
    "US Large Quality":        "SPHQ",
    "US Large Value":          "VTV",
    "US Small Cap":            "AVUV",
    "International Developed": "VEA",
    "Emerging Markets":        "IEMG",
    "Core Fixed Income":       "VGIT",
    "TIPS":                    "SCHP",
    "Real Assets":             "VNQ / PDBC",
    "Cash / SPAXX":            "SPAXX",
}
_SLEEVE_BENCH_TICKER: dict[str, str] = {
    "US Large Core":           "SPY",
    "US Large Quality":        "QUAL",
    "US Large Value":          "IWD",
    "US Small Cap":            "IWM",
    "International Developed": "EFA",
    "Emerging Markets":        "EEM",
    "Core Fixed Income":       "IEF",
    "TIPS":                    "TIP",
    "Real Assets":             "VNQ / DBC",
    "Cash / SPAXX":            "BIL",
}

_PERIODS = ["1M", "3M", "YTD", "1Y", "SI"]
_PERIOD_LABELS = {
    "1M": "1 Month", "3M": "3 Months",
    "YTD": "YTD", "1Y": "1 Year", "SI": "Since Inception",
}


# ── PDF rendering ─────────────────────────────────────────────────────────────

def _strip_page_margin_boxes(html: str) -> str:
    """Remove @page margin-box rules (e.g. @bottom-center) that xhtml2pdf can't parse."""
    import re
    return re.sub(
        r'@(?:top|bottom|left|right)-(?:center|left|right|middle)\s*\{[^}]*\}',
        '',
        html,
        flags=re.DOTALL,
    )


def _render_pdf(html_content: str) -> bytes:
    """Try WeasyPrint (Linux/Cloud — full CSS support), fall back to xhtml2pdf (Windows)."""
    try:
        from weasyprint import HTML
        result = HTML(string=html_content).write_pdf()
        if result:
            return result
    except Exception:
        pass  # Fall through to xhtml2pdf (handles Windows/no-GTK cases)

    # xhtml2pdf doesn't support @page margin boxes — strip them first
    try:
        from xhtml2pdf import pisa
        buf = io.BytesIO()
        pisa_result = pisa.CreatePDF(_strip_page_margin_boxes(html_content), dest=buf)
        if pisa_result.err:
            raise RuntimeError(f"xhtml2pdf reported errors (code {pisa_result.err})")
        return buf.getvalue()
    except ImportError:
        raise RuntimeError(
            "No PDF renderer available. "
            "On Linux: install weasyprint + packages.txt system libs. "
            "On Windows: pip install xhtml2pdf"
        )


# ── Chart helpers ─────────────────────────────────────────────────────────────

def _render_chart_to_png(fig: go.Figure, width: int = 700, height: int = 310) -> Optional[bytes]:
    """
    Render Plotly figure to PNG bytes via kaleido with a 25-second timeout.
    Uses a daemon thread so a hanging kaleido process (Windows sandbox/AV issue)
    does not block PDF generation — the chart renders as a placeholder instead.
    Charts render correctly on Linux/Streamlit Cloud.
    """
    import threading
    result: list = [None]

    def _render() -> None:
        try:
            result[0] = pio.to_image(fig, format="png", width=width, height=height, scale=1.5)
        except Exception:
            pass

    t = threading.Thread(target=_render, daemon=True)
    t.start()
    t.join(timeout=25)
    return result[0]


def _chart_b64(fig: Optional[go.Figure], width: int = 700, height: int = 310) -> Optional[str]:
    """Return base64-encoded PNG string for HTML embedding, or None."""
    if fig is None:
        return None
    raw = _render_chart_to_png(fig, width, height)
    return base64.b64encode(raw).decode() if raw else None


# ── Period / filename helpers ─────────────────────────────────────────────────

def _format_period_label(start_date: str, end_date: str) -> str:
    """'Q1 2026' for standard quarters, else 'Jan 1 to Mar 31, 2026'."""
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    quarters = [
        ((1, 1), (3, 31), "Q1"), ((4, 1), (6, 30), "Q2"),
        ((7, 1), (9, 30), "Q3"), ((10, 1), (12, 31), "Q4"),
    ]
    for (sm, sd), (em, ed), qname in quarters:
        if (s.month, s.day) == (sm, sd) and (e.month, e.day) == (em, ed) and s.year == e.year:
            return f"{qname} {s.year}"
    if s.year == e.year:
        return f"{s.strftime('%b')} {s.day} to {e.strftime('%b')} {e.day}, {e.year}"
    return f"{s.strftime('%b')} {s.day}, {s.year} to {e.strftime('%b')} {e.day}, {e.year}"


def _format_filename(start_date: str, end_date: str, period_label: str) -> str:
    """Orefice_Portfolio_2026Q1.pdf or Orefice_Portfolio_20260101_to_20260331.pdf"""
    if " " in period_label and period_label.split()[0].startswith("Q"):
        q, yr = period_label.split()
        return f"Orefice_Portfolio_{yr}{q}.pdf"
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    return f"Orefice_Portfolio_{s}_to_{e}.pdf"


def _drift_status(drift_bps: float, target_bps: float) -> str:
    """Return 'Within' or 'Drift'.

    Both thresholds must be satisfied for Within:
      abs(drift_bps) <= 200  AND  abs(drift_bps / target_bps) <= 0.20
    Either breach trips to Drift.
    """
    if target_bps == 0:
        return "Drift"
    return "Within" if abs(drift_bps) <= 200 and abs(drift_bps / target_bps) <= 0.20 else "Drift"


def _inception_date() -> str:
    """Return date of first trade, falling back to 2025-05-01."""
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
        return row[0] if row and row[0] else "2025-05-01"
    except Exception:
        return "2025-05-01"


# ── Benchmark period return helper ────────────────────────────────────────────

def _bm_period_return(series: pd.Series, period: str) -> float:
    if len(series) < 2:
        return 0.0
    last_d = series.index[-1].date() if hasattr(series.index[-1], "date") else series.index[-1]
    if period == "SI":
        start_ts = series.index[0]
    elif period == "1Y":
        start_ts = pd.Timestamp(last_d - timedelta(days=365))
    elif period == "YTD":
        start_ts = pd.Timestamp(date(last_d.year, 1, 1))
    elif period == "3M":
        start_ts = pd.Timestamp(last_d - timedelta(days=90))
    elif period == "1M":
        start_ts = pd.Timestamp(last_d - timedelta(days=30))
    else:
        return 0.0
    s = series[series.index >= start_ts]
    return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) >= 2 else 0.0


# ── Section builders ──────────────────────────────────────────────────────────

def _build_executive_summary(start_date: str, end_date: str) -> dict:
    pv = get_portfolio_value_series(start_date, end_date)
    cf = pd.Series(0.0, index=pv.index)
    portfolio_twr = twr_daily_linked(pv, cf) if len(pv) >= 2 else 0.0
    current_val   = float(pv.iloc[-1]) if not pv.empty else 0.0

    sp = get_sp500_series(start_date, end_date)
    sp_return = float(sp.iloc[-1] / sp.iloc[0] - 1) if len(sp) >= 2 else 0.0

    bl = get_custom_blended_series(start_date, end_date)
    bl_return = float(bl.iloc[-1] / bl.iloc[0] - 1) if len(bl) >= 2 else 0.0

    alpha_sp_bps = (portfolio_twr - sp_return) * 10_000
    alpha_bl_bps = (portfolio_twr - bl_return) * 10_000

    top_contributor = top_detractor = None
    try:
        bf_df = brinson_fachler_period(start_date, end_date)
        if not bf_df.empty:
            best  = bf_df.loc[bf_df["total_effect"].idxmax()]
            worst = bf_df.loc[bf_df["total_effect"].idxmin()]
            top_contributor = (best["sleeve"],  best["total_effect"]  * 10_000)
            top_detractor   = (worst["sleeve"], worst["total_effect"] * 10_000)
    except Exception:
        pass

    cape_val = cape_pct = None
    try:
        cape_val = current_cape()
        cape_pct = percentile(get_cape_series(), cape_val)
    except Exception:
        pass

    period_label = _format_period_label(start_date, end_date)
    vs_sp = "outperforming" if alpha_sp_bps >= 0 else "underperforming"
    vs_bl = "outperforming" if alpha_bl_bps >= 0 else "underperforming"

    narrative = [
        f"Portfolio returned {portfolio_twr*100:.2f}% in {period_label}, "
        f"{vs_sp} the S&P 500 by {abs(alpha_sp_bps):.0f} bps and "
        f"{vs_bl} the custom blended benchmark by {abs(alpha_bl_bps):.0f} bps.",
    ]
    if top_contributor:
        narrative.append(
            f"Top contributor: {top_contributor[0]} ({top_contributor[1]:+.0f} bps total effect)."
        )
    if top_detractor and (not top_contributor or top_detractor[0] != top_contributor[0]):
        narrative.append(
            f"Top detractor: {top_detractor[0]} ({top_detractor[1]:+.0f} bps total effect)."
        )
    if cape_val is not None and cape_pct is not None:
        narrative.append(
            f"CAPE stands at {cape_val:.1f}x, in the {cape_pct:.0f}th historical percentile, "
            f"supporting the diversification rationale across non-US and real asset sleeves."
        )

    end = date.fromisoformat(end_date)
    formatted_end_date = f"{end.strftime('%B')} {end.day}, {end.year}"

    return {
        "period_label":         period_label,
        "portfolio_return_pct": f"{portfolio_twr*100:.2f}%",
        "sp500_return_pct":     f"{sp_return*100:.2f}%",
        "blended_return_pct":   f"{bl_return*100:.2f}%",
        "alpha_sp_bps":         alpha_sp_bps,
        "alpha_bl_bps":         alpha_bl_bps,
        "alpha_sp_str":         f"{alpha_sp_bps:+.0f} bps",
        "alpha_bl_str":         f"{alpha_bl_bps:+.0f} bps",
        "current_value":        f"${current_val:,.0f}",
        "end_date":             formatted_end_date,
        "narrative":            narrative,
    }


def _build_holdings_section(end_date: str) -> dict:
    sw = get_sleeve_weights_on_date(end_date)
    if sw.empty:
        return {"rows": [], "chart_b64": None}

    rows = []
    for sleeve, row in sw.iterrows():
        drift       = row["Drift"]
        drift_bps   = drift * 10_000
        target_bps  = row["Target Weight"] * 10_000
        status      = _drift_status(drift_bps, target_bps)
        rows.append({
            "sleeve":        sleeve,
            "market_value":  f"${row['Market Value']:,.0f}",
            "actual_weight": f"{row['Actual Weight']*100:.1f}%",
            "target_weight": f"{row['Target Weight']*100:.1f}%",
            "drift_bps":     f"{drift_bps:+.0f} bps",
            "status":        status,
            "status_class":  "status-within" if status == "Within" else "status-drift",
        })

    sleeves = sw.index.tolist()
    actuals = (sw["Actual Weight"] * 100).tolist()
    targets = (sw["Target Weight"] * 100).tolist()

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Actual", y=sleeves, x=actuals, orientation="h",
        marker_color=_PALETTE["portfolio"],
    ))
    fig.add_trace(go.Bar(
        name="Target", y=sleeves, x=targets, orientation="h",
        marker_color=_PALETTE["sp500"], opacity=0.65,
    ))
    fig.update_layout(
        barmode="overlay",
        xaxis_title="Weight (%)", yaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=200, r=20, t=40, b=30), height=330,
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="sans-serif", size=9, color="#333"),
        xaxis=dict(gridcolor="#E8E8E8"),
        yaxis=dict(tickmode="array", tickvals=sleeves, ticktext=sleeves, automargin=True),
    )
    return {"rows": rows, "chart_b64": _chart_b64(fig, 700, 330)}


def _build_performance_section(start_date: str, end_date: str) -> dict:
    inception = _inception_date()
    pv = get_portfolio_value_series(inception, end_date)
    if pv.empty or float(pv.max()) == 0.0:
        return {"period_rows": [], "chart_b64": None}

    cf        = pd.Series(0.0, index=pv.index)
    start_val = float(pv.iloc[0])
    sp = get_sp500_series(inception, end_date) * start_val
    bl = get_custom_blended_series(inception, end_date) * start_val

    period_rows = []
    for p in _PERIODS:
        pr = period_return("daily", pv, cf, p)
        sr = _bm_period_return(sp, p)
        br = _bm_period_return(bl, p)
        period_rows.append({
            "period":    _PERIOD_LABELS[p],
            "portfolio": f"{pr*100:.2f}%",
            "sp500":     f"{sr*100:.2f}%",
            "blended":   f"{br*100:.2f}%",
            "vs_sp":     f"{(pr-sr)*10000:+.0f} bps",
            "vs_bl":     f"{(pr-br)*10000:+.0f} bps",
        })

    pv_norm = pv / float(pv.iloc[0])
    sp_norm = sp / float(sp.iloc[0])
    bl_norm = bl / float(bl.iloc[0])

    pv_pct = (pv_norm - 1) * 100
    sp_pct = (sp_norm - 1) * 100
    bl_pct = (bl_norm - 1) * 100
    _all_pct = pd.concat([pv_pct, sp_pct, bl_pct])
    _tick_min = int((_all_pct.min() // 10) * 10)
    _tick_max = int((_all_pct.max() // 10 + 1) * 10)
    _tick_vals = list(range(_tick_min, _tick_max + 1, 10))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pv_norm.index, y=pv_pct, name="Portfolio",
        line=dict(color=_PALETTE["portfolio"], width=2),
    ))
    fig.add_trace(go.Scatter(
        x=sp_norm.index, y=sp_pct, name="S&P 500",
        line=dict(color=_PALETTE["sp500"], width=1.5, dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=bl_norm.index, y=bl_pct, name="Custom Blended",
        line=dict(color=_PALETTE["blended"], width=1.5, dash="dash"),
    ))
    fig.update_layout(
        yaxis_title="Cumulative Return (%)", xaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=60, r=0, t=40, b=0), height=290,
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="sans-serif", size=10, color="#333"),
        yaxis=dict(gridcolor="#E8E8E8", zeroline=True, zerolinecolor="#CCC",
                   tickmode="array", tickvals=_tick_vals,
                   ticktext=[f"{v}%" for v in _tick_vals]),
        xaxis=dict(gridcolor="#E8E8E8"),
    )
    return {"period_rows": period_rows, "chart_b64": _chart_b64(fig, 700, 290)}


def _build_attribution_section(start_date: str, end_date: str) -> dict:
    _empty = {
        "rows": [], "chart_b64": None,
        "total_alloc": "+0.0", "total_sel": "+0.0", "total_total": "+0.0",
        "sel_commentary": [], "alloc_commentary": None,
    }
    try:
        bf_df = brinson_fachler_period(start_date, end_date)
    except Exception:
        return _empty
    if bf_df.empty:
        return _empty

    bf_sorted = bf_df.sort_values("total_effect", ascending=True)
    sleeve_labels = bf_sorted["sleeve"].tolist()

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Allocation",
        y=sleeve_labels,
        x=(bf_sorted["allocation_effect"] * 10_000).tolist(),
        orientation="h", marker_color=_PALETTE["alloc"],
    ))
    fig.add_trace(go.Bar(
        name="Selection",
        y=sleeve_labels,
        x=(bf_sorted["selection_effect"] * 10_000).tolist(),
        orientation="h", marker_color=_PALETTE["selection"],
    ))
    fig.update_layout(
        barmode="stack", xaxis_title="Basis Points", yaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=180, r=20, t=40, b=30), height=240,
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="sans-serif", size=9, color="#333"),
        xaxis=dict(
            gridcolor="#E8E8E8", zeroline=True, zerolinecolor="#888",
            dtick=50, tickformat="+.0f",
        ),
        yaxis=dict(
            tickmode="array",
            tickvals=sleeve_labels,
            ticktext=sleeve_labels,
        ),
    )

    rows = []
    for _, row in bf_df.sort_values("total_effect", ascending=False).iterrows():
        rows.append({
            "sleeve":    row["sleeve"],
            "port_wt":   f"{row['w_p']*100:.1f}%",
            "bench_wt":  f"{row['w_b']*100:.1f}%",
            "port_ret":  f"{row['r_p']*100:.2f}%",
            "bench_ret": f"{row['r_b']*100:.2f}%",
            "alloc":     f"{row['allocation_effect']*10000:+.1f}",
            "sel":       f"{row['selection_effect']*10000:+.1f}",
            "total":     f"{row['total_effect']*10000:+.1f}",
        })

    # Top 3 selection effect drivers (by absolute value)
    sel_commentary = []
    sel_sorted = bf_df.reindex(
        bf_df["selection_effect"].abs().sort_values(ascending=False).index
    ).head(3)
    for _, r in sel_sorted.iterrows():
        sleeve  = r["sleeve"]
        port_t  = _SLEEVE_HOLDING_TICKER.get(sleeve, "—")
        bench_t = _SLEEVE_BENCH_TICKER.get(sleeve, "—")
        diff_bps = (r["r_p"] - r["r_b"]) * 10_000
        sel_bps  = r["selection_effect"] * 10_000
        sel_commentary.append(
            f"{sleeve}: portfolio holding ({port_t}) returned {r['r_p']*100:.1f}% "
            f"vs benchmark {bench_t} {r['r_b']*100:.1f}% "
            f"(Δ {diff_bps:+.0f} bps), contributing {sel_bps:+.0f} bps to active return"
        )

    # Top allocation effect driver (only if >15 bps absolute)
    alloc_commentary = None
    top_alloc = bf_df.loc[bf_df["allocation_effect"].abs().idxmax()]
    if abs(top_alloc["allocation_effect"]) * 10_000 > 15:
        sleeve    = top_alloc["sleeve"]
        direction = "overweight" if top_alloc["w_p"] > top_alloc["w_b"] else "underweight"
        alloc_commentary = (
            f"{sleeve}: {direction} vs benchmark "
            f"({top_alloc['w_p']*100:.1f}% vs {top_alloc['w_b']*100:.1f}%), "
            f"contributing {top_alloc['allocation_effect']*10_000:+.0f} bps allocation effect"
        )

    return {
        "rows":             rows,
        "chart_b64":        _chart_b64(fig, 700, 240),
        "total_alloc":      f"{bf_df['allocation_effect'].sum()*10000:+.1f}",
        "total_sel":        f"{bf_df['selection_effect'].sum()*10000:+.1f}",
        "total_total":      f"{bf_df['total_effect'].sum()*10000:+.1f}",
        "sel_commentary":   sel_commentary,
        "alloc_commentary": alloc_commentary,
    }


def _build_macro_section() -> dict:
    def _pct_str(series, val):
        try:
            return f"{percentile(series, val):.0f}th"
        except Exception:
            return "N/A"

    macro: dict = {}

    try:
        cape_val = current_cape()
        cape_s   = get_cape_series()
        macro["cape"] = {
            "value":      f"{cape_val:.1f}x",
            "percentile": _pct_str(cape_s, cape_val),
            "note":       "Elevated vs. history; supports diversification into non-US and real assets.",
        }
    except Exception:
        macro["cape"] = {"value": "N/A", "percentile": "N/A", "note": "Data unavailable."}

    try:
        yc     = get_series("T10Y2Y", "1990-01-01")
        yc_val = float(yc.dropna().iloc[-1])
        note   = ("Positive slope — normalized rate environment."
                  if yc_val > 0 else
                  "Inverted — historically precedes recession; supports duration exposure.")
        macro["yield_curve"] = {
            "value": f"{yc_val:.2f}%", "percentile": _pct_str(yc, yc_val), "note": note,
        }
    except Exception:
        macro["yield_curve"] = {"value": "N/A", "percentile": "N/A", "note": "Data unavailable."}

    try:
        ff     = get_series("DFF", "1990-01-01")
        ff_val = float(ff.dropna().iloc[-1])
        macro["fed_funds"] = {
            "value": f"{ff_val:.2f}%", "percentile": _pct_str(ff, ff_val),
            "note":  "Current rate level relative to post-1990 history.",
        }
    except Exception:
        macro["fed_funds"] = {"value": "N/A", "percentile": "N/A", "note": "Data unavailable."}

    try:
        hy     = get_series("BAMLH0A0HYM2", "2023-05-01")
        hy_val = float(hy.dropna().iloc[-1])
        macro["hy_spread"] = {
            "value": f"{hy_val:.2f}%", "percentile": _pct_str(hy, hy_val),
            "note":  "ICE BofA HY OAS; percentile relative to May 2023+ window (FRED restriction).",
        }
    except Exception:
        macro["hy_spread"] = {"value": "N/A", "percentile": "N/A", "note": "Data unavailable."}

    return macro


def _build_thesis_section(start_date: str, end_date: str) -> dict:
    with get_connection() as conn:
        th_rows = conn.execute(
            """SELECT title, conviction, horizon_months, target_sleeves
               FROM theses
               WHERE level = 'investment' AND status = 'active'
                 AND title NOT LIKE 'system:%'
               ORDER BY conviction DESC""",
        ).fetchall()
        tr_rows = conn.execute(
            """SELECT trade_date, ticker, action, shares, price
               FROM trades
               WHERE trade_date BETWEEN ? AND ?
               ORDER BY trade_date""",
            (start_date, end_date),
        ).fetchall()

    theses = []
    for t in th_rows:
        sleeves = ""
        if t["target_sleeves"]:
            try:
                sleeves = ", ".join(json.loads(t["target_sleeves"]))
            except Exception:
                sleeves = str(t["target_sleeves"])
        theses.append({
            "title":          t["title"],
            "conviction_int": int(t["conviction"] or 0),
            "horizon":        f"{t['horizon_months']} mo" if t["horizon_months"] else "N/A",
            "sleeves":        sleeves,
        })

    trades = []
    for t in tr_rows:
        price = float(t["price"]) if t["price"] else 0.0
        td = date.fromisoformat(t["trade_date"])
        trades.append({
            "date":   f"{td.strftime('%b')} {td.day}, {td.year}",
            "ticker": t["ticker"],
            "action": t["action"].title(),
            "shares": f"{float(t['shares']):.0f}",
            "price":  f"${price:.2f}" if price else "N/A",
            "cost":   f"${float(t['shares']) * price:,.0f}",
        })

    return {"theses": theses, "trades": trades}


# ── Main public function ──────────────────────────────────────────────────────

def generate_quarterly_report(
    start_date: str,
    end_date: str,
    recipient_name: str = "Matthew Orefice",
    output_path: Optional[Path] = None,
) -> Path:
    """
    Generate an 8-page quarterly PDF report.

    Returns the Path of the written PDF file.
    When zero trades exist, produces a structural report (SAA + macro + theses only).
    """
    with get_connection() as conn:
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    has_trades = trade_count > 0

    period_label = _format_period_label(start_date, end_date)

    exec_data   = _build_executive_summary(start_date, end_date) if has_trades else None
    hold_data   = _build_holdings_section(end_date)              if has_trades else {"rows": [], "chart_b64": None}
    perf_data   = _build_performance_section(start_date, end_date) if has_trades else None
    attr_data   = _build_attribution_section(start_date, end_date) if has_trades else None
    macro_data  = _build_macro_section()
    thesis_data = _build_thesis_section(start_date, end_date)

    css_content = (TEMPLATES_DIR / "report_styles.css").read_text(encoding="utf-8")

    env  = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    tmpl = env.get_template("quarterly_report.html")

    # Cross-platform date formatting (%-d fails on Windows)
    today = date.today()
    gen_date = f"{today.strftime('%B')} {today.day}, {today.year}"

    html_content = tmpl.render(
        css_content    = css_content,
        period_label   = period_label,
        recipient_name = recipient_name,
        generation_date= gen_date,
        has_trades     = has_trades,
        exec           = exec_data,
        hold           = hold_data,
        perf           = perf_data,
        attr           = attr_data,
        macro          = macro_data,
        thesis         = thesis_data,
    )

    pdf_bytes = _render_pdf(html_content)

    if output_path is None:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _REPORTS_DIR / _format_filename(start_date, end_date, period_label)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)
    return output_path
