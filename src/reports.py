"""
Quarterly PDF report generator.

Primary renderer: WeasyPrint (Linux / Streamlit Cloud).
Windows fallback: xhtml2pdf (no GTK required).
Charts: Plotly + kaleido 0.2.1.
"""
import base64
import io
import json
import logging
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from src.attribution import benchmark_gap_notice, brinson_fachler_period, price_gap_notice
from src.cache import (
    capture_quarter_snapshot,
    get_quarter_snapshot,
    is_quarter_complete,
    label_to_quarter_id,
    snapshot_price_context,
)
from src.benchmarks import get_custom_blended_series, get_sp500_series
from src.config import IS_DEMO
from src.db import get_connection
from src.drip import distribution_gaps_for_holdings, drip_distribution_gap_notice
from src.sleeve_config import sleeve_holdings as _sleeve_holdings
from src.factors import (
    EM_DISCLOSURE,
    build_benchmark_methodology, build_benchmark_prose,
    build_factor_methodology_notes, build_factor_prose,
    regress_fi_sleeve, run_benchmark_attribution_regression,
    run_intl_global_regression, run_sleeve_regressions, sig_marker,
)
from src.holdings import (
    get_current_market_value,
    get_external_cashflow_series,
    get_inception_date,
    get_portfolio_value_series,
    get_sleeve_weights_on_date,
)
from src.macro import compute_cape_implied_return, get_series, percentile
from src.positioning import (
    build_style_box_figure, get_effective_duration,
    get_non_us_equity_data, get_style_box_data,
)
from src.style_box import STYLE_BOX_CAPTION
from src.returns import period_return, twr_daily_linked
from src.shiller import current_cape, get_cape_series

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

# Phase 39 — the developed-international book, as four sleeves. Public so the
# pages that need a combined international figure sum THESE rather than each
# hardcoding the list. Every consumer resolves each name through a _require_*
# helper that raises on an unknown sleeve, so a later rename fails loudly here
# instead of silently dropping a leg from the total.
# Holding / benchmark ticker per sleeve, DERIVED from the DB so the maps describe
# whatever book this process is pointed at (9 sleeves personal, 12 demo) rather
# than freezing a taxonomy. Benchmark comes straight from asset_classes.
# benchmark_ticker; holdings from the securities table (see sleeve_holdings —
# Real Assets resolves to its commodity holding since VNQ is dual-role).
def sleeve_holding_ticker() -> dict[str, str]:
    return {s: " / ".join(t) for s, t in _sleeve_holdings().items()}


def sleeve_bench_ticker() -> dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, benchmark_ticker FROM asset_classes "
            "WHERE parent_id IS NOT NULL AND benchmark_ticker IS NOT NULL"
        ).fetchall()
    return {r["name"]: r["benchmark_ticker"] for r in rows}

def build_bf_cross_reference(bf_df: pd.DataFrame, n: int = 3) -> list[dict]:
    """Top-n Brinson-Fachler selection effects for prose cross-reference.

    Ranked by selection_effect (portfolio-weighted, w_p * (r_p - r_b)) but
    reported as raw_diff = r_p - r_b, so prose built from this list reads
    "AVUV outperformed IWM by 768 bps" — the actual return differential
    matching the port_ret/bench_ret columns on the Brinson-Fachler attribution
    table, not the weight-scaled selection effect. Single source for both the
    PDF report and the Benchmark Attribution page so the two can't diverge.
    """
    if bf_df.empty:
        return []
    df = bf_df.copy()
    _no_bm = set(bf_df.attrs.get("no_benchmark_sleeves", []))
    if _no_bm:
        df = df[~df["sleeve"].isin(_no_bm)]
    df["raw_diff"] = df["r_p"] - df["r_b"]
    top = df.nlargest(n, "selection_effect")
    _hold, _bench = sleeve_holding_ticker(), sleeve_bench_ticker()
    out = []
    for _, row in top.iterrows():
        sleeve = row["sleeve"]
        out.append({
            "holding": _hold.get(sleeve, sleeve),
            "bench":   _bench.get(sleeve, sleeve),
            "sel_bps": row["raw_diff"] * 10_000,
        })
    return out


_PERIODS = ["1M", "3M", "YTD", "1Y", "SI"]
_PERIOD_LABELS = {
    "1M": "1 Month", "3M": "3 Months",
    "YTD": "YTD", "1Y": "1 Year", "SI": "Since Inception",
}

REPORT_DISCLAIMER = (
    "This report documents the author's personal investment portfolio "
    "and is provided for informational and educational purposes only. "
    "Nothing in this report constitutes investment advice, a "
    "recommendation to buy or sell any security, or an offer to provide "
    "advisory services. Past performance does not predict future "
    "results, and all return figures are time-weighted historical "
    "calculations subject to data and methodology limitations. No "
    "fiduciary, advisory, or client relationship is created by accessing "
    "this report or the underlying analytics system. Price data sourced "
    "from public market data feeds; macro data from FRED; valuation data "
    "from Robert Shiller’s public datasets. Calculations are best-effort "
    "and may contain methodological simplifications."
)

# Demo-mode variant of REPORT_DISCLAIMER — mirrors the app's demo-banner wording
# (src/config.py: get_demo_banner_text) so a report generated from the public
# demo cannot be read as documenting a real account.
DEMO_REPORT_DISCLAIMER = (
    "This report documents a simulated paper-trade demo portfolio, "
    "illustrating the methodology of the author's personal investment "
    "analytics system, and is provided for informational and educational "
    "purposes only. Positions and trades are illustrative, not real "
    "holdings. Nothing in this report constitutes investment advice, a "
    "recommendation to buy or sell any security, or an offer to provide "
    "advisory services. Past performance does not predict future "
    "results, and all return figures are time-weighted historical "
    "calculations subject to data and methodology limitations. No "
    "fiduciary, advisory, or client relationship is created by accessing "
    "this report or the underlying analytics system. Price data sourced "
    "from public market data feeds; macro data from FRED; valuation data "
    "from Robert Shiller’s public datasets. Calculations are best-effort "
    "and may contain methodological simplifications."
)

REAL_ACCOUNT_LABEL = "Personal Brokerage Account"
DEMO_ACCOUNT_LABEL = "Demo Mode — Simulated Paper-Trade Portfolio"


def _account_label(is_demo: bool) -> str:
    """Cover-page account-status label, branched on mode so a demo-generated
    report is never labeled as a real personal account."""
    return DEMO_ACCOUNT_LABEL if is_demo else REAL_ACCOUNT_LABEL


def _report_disclaimer(is_demo: bool) -> str:
    """Legal disclaimer text, branched on mode to match _account_label."""
    return DEMO_REPORT_DISCLAIMER if is_demo else REPORT_DISCLAIMER


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
            logging.exception("kaleido chart render failed (width=%d, height=%d)", width, height)

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
    """'Q1 2026' for standard quarters, else 'Jan 1 to Mar 31, 2026'.

    Recognises both conventions:
      - Prior-quarter-end: Dec-31/2025 → Mar-31/2026 = Q1 2026  (preferred)
      - First-of-quarter:  Jan-1/2026  → Mar-31/2026 = Q1 2026  (legacy)
    """
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    # Prior-quarter-end start convention (Dec31→Mar31, Mar31→Jun30, etc.)
    for (sm, sd), (em, ed), qname in [
        ((12, 31), (3, 31),  "Q1"),  # Dec31 prior year → Mar31
        ((3, 31),  (6, 30),  "Q2"),
        ((6, 30),  (9, 30),  "Q3"),
        ((9, 30),  (12, 31), "Q4"),
    ]:
        if (s.month, s.day) == (sm, sd) and (e.month, e.day) == (em, ed):
            yr = e.year  # Q1 spans two calendar years; use end year as the label
            if qname == "Q1" and e.year != s.year + 1:
                continue  # Dec-31 same year as Mar-31 would be wrong
            if qname != "Q1" and s.year != e.year:
                continue
            return f"{qname} {yr}"
    # First-of-quarter start convention (Jan-1, Apr-1, Jul-1, Oct-1) — legacy
    for (sm, sd), (em, ed), qname in [
        ((1, 1),  (3, 31),  "Q1"), ((4, 1),  (6, 30),  "Q2"),
        ((7, 1),  (9, 30),  "Q3"), ((10, 1), (12, 31), "Q4"),
    ]:
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


def _drift_status(drift_bps: float, target_bps: float, band_bps: float = 200) -> str:
    """Return 'Within' or 'Drift'.

    Both thresholds must be satisfied for Within:
      abs(drift_bps) <= band_bps  AND  abs(drift_bps / target_bps) <= 0.20
    Either breach trips to Drift.
    band_bps defaults to 200 (±2%) for sleeves < 10% target; pass 300 for sleeves ≥ 10%.
    """
    if target_bps == 0:
        return "Drift"
    return "Within" if abs(drift_bps) <= band_bps and abs(drift_bps / target_bps) <= 0.20 else "Drift"


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


# ── NaN-safe formatting sinks ─────────────────────────────────────────────────
# A reference-benchmark price gap (see src/benchmarks.py) can carry a NaN
# sentinel through to a return value here — these render "—", never the
# literal string "nan%"/"nan bps".

def _fmt_pct(v: float, decimals: int = 2) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v * 100:.{decimals}f}%"


def _fmt_bps(v: float) -> str:
    """v is already in bps units (pre-multiplied by 10,000)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.0f} bps"


# ── Section builders ──────────────────────────────────────────────────────────

def _build_executive_summary(start_date: str, end_date: str) -> dict:
    # Portfolio and S&P 500 are loaded from inception so that the value series
    # is continuous and daily-linked TWR can be computed correctly.  Each is then
    # sliced to the report period start.  The custom blended benchmark is computed
    # fresh from start_date so it uses SAA target weights as of the period start
    # (buy-and-hold from inception drifts weights and produces a different result).
    # start_date is always a trading day (Dec-31 for Q1, Mar-31 for Q2, etc.) so
    # no holiday-bfill risk exists for the fresh blended series.
    inception = get_inception_date()

    pv_since_inception = get_portfolio_value_series(inception, end_date)
    # Displayed dollar value = true current MV (all shares incl DRIP × raw close).
    # The total-return series (pv, adj_close × non-DRIP) is used only for the TWR
    # below — the dollar figure must not come from the return series, which omits
    # the real DRIP shares' market worth.
    current_val = get_current_market_value(end_date)

    pv = pv_since_inception[pv_since_inception.index >= pd.Timestamp(start_date)]
    # Real external flows (contributions/withdrawals), aligned to the period
    # slice — flows before the slice drop out; one on its first date is the
    # starting NAV. End-of-day netting inside twr_daily_linked.
    cf = get_external_cashflow_series(inception, end_date).reindex(pv.index).fillna(0.0)
    portfolio_twr = twr_daily_linked(pv, cf) if len(pv) >= 2 else 0.0

    sp_full   = get_sp500_series(inception, end_date)
    # Captured immediately off .attrs, before any further slicing — pandas
    # doesn't reliably propagate .attrs through arithmetic/indexing.
    sp_gaps   = sp_full.attrs.get("benchmark_gaps", [])
    sp_period = sp_full[sp_full.index >= pd.Timestamp(start_date)]
    sp_return = float(sp_period.iloc[-1] / sp_period.iloc[0] - 1) if len(sp_period) >= 2 else 0.0

    bl = get_custom_blended_series(start_date, end_date)
    bl_gaps = bl.attrs.get("benchmark_gaps", [])
    bl_return = float(bl.iloc[-1] / bl.iloc[0] - 1) if len(bl) >= 2 else 0.0

    alpha_sp_bps = (portfolio_twr - sp_return) * 10_000
    alpha_bl_bps = (portfolio_twr - bl_return) * 10_000

    top_contributor = top_detractor = None
    _all_positive = False
    _bf_price_gap_note = None
    _bf_benchmark_gap_note = None
    _drip_gap_note = None
    try:
        _drip_gaps = distribution_gaps_for_holdings(inception, end_date)
        if _drip_gaps:
            _drip_gap_note = drip_distribution_gap_notice(_drip_gaps)
    except Exception:
        pass
    try:
        bf_df = brinson_fachler_period(start_date, end_date)
        if bf_df.attrs.get("price_gaps"):
            _bf_price_gap_note = price_gap_notice(bf_df.attrs["price_gaps"])
        if bf_df.attrs.get("benchmark_gaps"):
            _bf_benchmark_gap_note = benchmark_gap_notice(bf_df.attrs["benchmark_gaps"])
        if not bf_df.empty:
            best  = bf_df.loc[bf_df["total_effect"].idxmax()]
            worst = bf_df.loc[bf_df["total_effect"].idxmin()]
            top_contributor = (best["sleeve"],  best["total_effect"]  * 10_000)
            top_detractor   = (worst["sleeve"], worst["total_effect"] * 10_000)
            # Suppress detractor narrative when all sleeves are within noise threshold
            _all_positive = worst["total_effect"] * 10_000 > -5
    except Exception:
        pass

    cape_val = cape_pct = None
    try:
        cape_val = current_cape()
        cape_pct = percentile(get_cape_series(), cape_val)
    except Exception:
        pass

    period_label = _format_period_label(start_date, end_date)

    # A reference-benchmark gap (see src/benchmarks.py) can NaN-sentinel
    # alpha_sp_bps/alpha_bl_bps — "outperforming/underperforming by nan bps"
    # is never a sentence to construct, so each half degrades independently
    # to an explicit unavailable clause instead.
    sp_phrase = (
        f"{'outperforming' if alpha_sp_bps >= 0 else 'underperforming'} "
        f"the S&P 500 by {abs(alpha_sp_bps):.0f} bps"
        if not np.isnan(alpha_sp_bps)
        else "the S&P 500 comparison unavailable this period (benchmark data gap)"
    )
    bl_phrase = (
        f"{'outperforming' if alpha_bl_bps >= 0 else 'underperforming'} "
        f"the custom blended benchmark by {abs(alpha_bl_bps):.0f} bps"
        if not np.isnan(alpha_bl_bps)
        else "the custom blended benchmark comparison unavailable this period (benchmark data gap)"
    )

    narrative = [
        f"Portfolio returned {portfolio_twr*100:.2f}% in {period_label}, "
        f"{sp_phrase} and {bl_phrase}.",
    ]
    if sp_gaps or bl_gaps:
        narrative.append(benchmark_gap_notice(sp_gaps + bl_gaps))
    if top_contributor and top_contributor[1] >= 5:
        narrative.append(
            f"Top contributor: {top_contributor[0]} ({top_contributor[1]:+.0f} bps total effect)."
        )
    if _all_positive and top_contributor:
        narrative.append(
            "All sleeves contributed positively or were within ±5 bps of the neutral line."
        )
    elif top_detractor and (not top_contributor or top_detractor[0] != top_contributor[0]):
        narrative.append(
            f"Top detractor: {top_detractor[0]} ({top_detractor[1]:+.0f} bps total effect)."
        )
    if cape_val is not None and cape_pct is not None:
        narrative.append(
            f"CAPE stands at {cape_val:.1f}x, in the {cape_pct:.0f}th historical percentile, "
            f"supporting the diversification rationale across non-US and real asset sleeves."
        )
    if _bf_price_gap_note:
        narrative.append(_bf_price_gap_note)
    if _bf_benchmark_gap_note:
        narrative.append(_bf_benchmark_gap_note)
    if _drip_gap_note:
        narrative.append(_drip_gap_note)

    end = date.fromisoformat(end_date)
    formatted_end_date = f"{end.strftime('%B')} {end.day}, {end.year}"

    return {
        "period_label":         period_label,
        "portfolio_return_pct": f"{portfolio_twr*100:.2f}%",
        "sp500_return_pct":     _fmt_pct(sp_return),
        "blended_return_pct":   _fmt_pct(bl_return),
        # Neutralized to 0.0 for the NaN case so the template's sign-based CSS
        # class (positive/negative) doesn't evaluate against a nan; the
        # displayed text itself is the NaN-safe alpha_*_str below.
        "alpha_sp_bps":         0.0 if np.isnan(alpha_sp_bps) else alpha_sp_bps,
        "alpha_bl_bps":         0.0 if np.isnan(alpha_bl_bps) else alpha_bl_bps,
        "alpha_sp_str":         _fmt_bps(alpha_sp_bps),
        "alpha_bl_str":         _fmt_bps(alpha_bl_bps),
        "current_value":        f"${current_val:,.0f}",
        "end_date":             formatted_end_date,
        "narrative":            narrative,
    }


def _build_holdings_chart(sleeves: list, actuals: list, targets: list) -> go.Figure:
    """Pure figure construction — no DB, no rendering. Testable in isolation."""
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
        margin=dict(l=180, r=20, t=40, b=30), height=330,
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="sans-serif", size=9, color="#333"),
        xaxis=dict(gridcolor="#E8E8E8"),
        yaxis=dict(tickmode="array", tickvals=sleeves, ticktext=sleeves),
    )
    return fig


def _build_holdings_section(end_date: str) -> dict:
    sw = get_sleeve_weights_on_date(end_date)
    if sw.empty:
        return {"rows": [], "chart_b64": None}

    with get_connection() as conn:
        band_rows = conn.execute(
            "SELECT name, tolerance_band FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
    band_map = {r["name"]: r["tolerance_band"] * 10_000 for r in band_rows}

    rows = []
    for sleeve, row in sw.iterrows():
        drift       = row["Drift"]
        drift_bps   = drift * 10_000
        target_bps  = row["Target Weight"] * 10_000
        band_bps    = band_map.get(sleeve, 200)
        status      = _drift_status(drift_bps, target_bps, band_bps)
        rows.append({
            "sleeve":        sleeve,
            "market_value":  f"${row['Market Value']:,.0f}",
            "actual_weight": f"{row['Actual Weight']*100:.1f}%",
            "target_weight": f"{row['Target Weight']*100:.1f}%",
            "drift_bps":     f"{drift_bps:+.0f} bps",
            "status":        status,
            "status_class":  "status-within" if status == "Within" else "status-drift",
        })

    # Phase 38a — operational cash shown separately: untargeted SPAXX float,
    # weighted as a share of TOTAL (the strategic sleeves above are ex-cash).
    cash_mv = sw.attrs.get("cash_mv", 0.0)
    if cash_mv > 0:
        rows.append({
            "sleeve":        "Cash / SPAXX (operational)",
            "market_value":  f"${cash_mv:,.0f}",
            "actual_weight": f"{sw.attrs.get('cash_weight_of_total', 0.0)*100:.1f}% of total",
            "target_weight": "—",
            "drift_bps":     "—",
            "status":        "Operational",
            "status_class":  "status-within",
        })

    sleeves = sw.index.tolist()
    actuals = (sw["Actual Weight"] * 100).tolist()
    targets = (sw["Target Weight"] * 100).tolist()
    fig = _build_holdings_chart(sleeves, actuals, targets)
    return {"rows": rows, "chart_b64": _chart_b64(fig, 700, 330)}


def _build_cumulative_chart(
    pv_pct: pd.Series, sp_pct: pd.Series, bl_pct: pd.Series
) -> go.Figure:
    """Pure cumulative-return figure. Raises ValueError on empty input."""
    if pv_pct.empty:
        raise ValueError("pv_pct is empty — cannot build cumulative return chart")
    all_vals = np.concatenate([pv_pct.values, sp_pct.values, bl_pct.values])
    tick_min = int(np.nanmin(all_vals) // 10) * 10
    tick_max = int(np.nanmax(all_vals) // 10 + 1) * 10
    tick_vals = list(range(tick_min, tick_max + 1, 10))
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pv_pct.index, y=pv_pct, name="Portfolio",
        line=dict(color=_PALETTE["portfolio"], width=2),
    ))
    fig.add_trace(go.Scatter(
        x=sp_pct.index, y=sp_pct, name="S&P 500",
        line=dict(color=_PALETTE["sp500"], width=1.5, dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=bl_pct.index, y=bl_pct, name="Custom Blended",
        line=dict(color=_PALETTE["blended"], width=1.5, dash="dash"),
    ))
    fig.update_layout(
        yaxis_title="Cumulative Return (%)", xaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=60, r=0, t=40, b=0), height=290,
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="sans-serif", size=10, color="#333"),
        yaxis=dict(gridcolor="#E8E8E8", zeroline=True, zerolinecolor="#CCC",
                   tickmode="array", tickvals=tick_vals,
                   ticktext=[f"{v}%" for v in tick_vals]),
        xaxis=dict(gridcolor="#E8E8E8"),
    )
    return fig


def _build_performance_section(start_date: str, end_date: str) -> dict:
    inception = get_inception_date()
    pv = get_portfolio_value_series(inception, end_date)
    if pv.empty or float(pv.max()) == 0.0:
        return {"period_rows": [], "chart_b64": None, "benchmark_gaps": []}

    cf        = get_external_cashflow_series(inception, end_date).reindex(pv.index).fillna(0.0)
    start_val = float(pv.iloc[0])
    sp_raw = get_sp500_series(inception, end_date)
    bl_raw = get_custom_blended_series(inception, end_date)
    # Captured before the *start_val multiply — see _build_executive_summary.
    sp_gaps = sp_raw.attrs.get("benchmark_gaps", [])
    bl_gaps = bl_raw.attrs.get("benchmark_gaps", [])
    sp = sp_raw * start_val
    bl = bl_raw * start_val

    end_d   = date.fromisoformat(end_date)
    incep_d = date.fromisoformat(inception)

    def _bl_period_return(period: str) -> float:
        # Fresh blended series per period so weights reset at the period start
        # (no 7-month inception drift). Capped at inception for 1Y/SI so we
        # never extend the benchmark into pre-portfolio history.
        ps = {
            "SI":  inception,
            "1Y":  max(end_d - timedelta(days=365),  incep_d).isoformat(),
            "YTD": max(date(end_d.year - 1, 12, 31), incep_d).isoformat(),
            "3M":  max(end_d - timedelta(days=90),   incep_d).isoformat(),
            "1M":  max(end_d - timedelta(days=30),   incep_d).isoformat(),
        }.get(period, inception)
        s = get_custom_blended_series(ps, end_date)
        return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) >= 2 else 0.0

    period_rows = []
    for p in _PERIODS:
        pr = period_return("daily", pv, cf, p)
        sr = _bm_period_return(sp, p)
        br = _bl_period_return(p)
        period_rows.append({
            "period":    _PERIOD_LABELS[p],
            "portfolio": f"{pr*100:.2f}%",
            "sp500":     _fmt_pct(sr),
            "blended":   _fmt_pct(br),
            "vs_sp":     _fmt_bps((pr-sr)*10000),
            "vs_bl":     _fmt_bps((pr-br)*10000),
        })

    pv_norm = pv / float(pv.iloc[0])
    sp_norm = sp / float(sp.iloc[0])
    bl_norm = bl / float(bl.iloc[0])

    pv_pct = (pv_norm - 1) * 100
    sp_pct = (sp_norm - 1) * 100
    bl_pct = (bl_norm - 1) * 100

    fig = _build_cumulative_chart(pv_pct, sp_pct, bl_pct)
    _benchmark_gaps = [
        {"sleeve": s, "ticker": t, "date": d} for s, t, d in sp_gaps + bl_gaps
    ]
    return {
        "period_rows":     period_rows,
        "chart_b64":       _chart_b64(fig, 700, 290),
        "benchmark_gaps":  _benchmark_gaps,
    }


def _build_attribution_section(start_date: str, end_date: str) -> dict:
    _empty = {
        "rows": [], "chart_b64": None,
        "total_alloc": "+0.0", "total_sel": "+0.0", "total_total": "+0.0",
        "sel_commentary": [], "alloc_commentary": None,
        "price_gaps": [],
        "benchmark_gaps": [],
    }
    try:
        bf_df = brinson_fachler_period(start_date, end_date)
    except Exception:
        return _empty
    _price_gaps = [
        {"ticker": t, "date": d} for t, d in bf_df.attrs.get("price_gaps", [])
    ]
    _benchmark_gaps = [
        {"sleeve": s, "ticker": t, "date": d}
        for s, t, d in bf_df.attrs.get("benchmark_gaps", [])
    ]
    if bf_df.empty:
        return {
            **_empty,
            "price_gaps": _price_gaps,
            "benchmark_gaps": _benchmark_gaps,
        }

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
        margin=dict(l=180, r=20, t=40, b=30), height=320,
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

    # Sleeves with no real benchmark (w_b == 0, e.g. an unmapped ticker's
    # "Unknown" bucket): the r_b value is a placeholder that cancels in the
    # math — print N/A, not a fake 0.00%.
    _no_bm = set(bf_df.attrs.get("no_benchmark_sleeves", []))
    rows = []
    for _, row in bf_df.sort_values("total_effect", ascending=False).iterrows():
        rows.append({
            "sleeve":    row["sleeve"],
            "port_wt":   f"{row['w_p']*100:.1f}%",
            "bench_wt":  f"{row['w_b']*100:.1f}%",
            "port_ret":  f"{row['r_p']*100:.2f}%",
            "bench_ret": "N/A" if row["sleeve"] in _no_bm else f"{row['r_b']*100:.2f}%",
            "alloc":     f"{row['allocation_effect']*10000:+.1f}",
            "sel":       f"{row['selection_effect']*10000:+.1f}",
            "total":     f"{row['total_effect']*10000:+.1f}",
        })

    # Sleeves with no real benchmark are excluded from the prose rankings below
    # (not just N/A-labeled like the table above): a comparative sentence
    # ("returned X% vs benchmark Y%") is unconstructible without a real r_b,
    # and the sleeve's selection effect is placeholder-derived (r_b == 0.0).
    _bf_ranked = bf_df[~bf_df["sleeve"].isin(_no_bm)] if _no_bm else bf_df

    # Top 3 selection effect drivers (by absolute value)
    sel_commentary = []
    sel_sorted = _bf_ranked.reindex(
        _bf_ranked["selection_effect"].abs().sort_values(ascending=False).index
    ).head(3)
    _hold_map, _bench_map = sleeve_holding_ticker(), sleeve_bench_ticker()
    for _, r in sel_sorted.iterrows():
        sleeve  = r["sleeve"]
        port_t  = _hold_map.get(sleeve, "—")
        bench_t = _bench_map.get(sleeve, "—")
        sel_bps  = r["selection_effect"] * 10_000
        sel_commentary.append(
            f"{sleeve}: portfolio holding ({port_t}) returned {r['r_p']*100:.1f}% "
            f"vs benchmark {bench_t} {r['r_b']*100:.1f}%, "
            f"contributing {sel_bps:+.0f} bps selection effect"
        )

    # Top allocation effect driver (only if >15 bps absolute)
    alloc_commentary = None
    top_alloc = (
        _bf_ranked.loc[_bf_ranked["allocation_effect"].abs().idxmax()]
        if not _bf_ranked.empty else None
    )
    if top_alloc is not None and abs(top_alloc["allocation_effect"]) * 10_000 > 15:
        sleeve    = top_alloc["sleeve"]
        direction = "overweight" if top_alloc["w_p"] > top_alloc["w_b"] else "underweight"
        alloc_commentary = (
            f"{sleeve}: {direction} vs benchmark "
            f"({top_alloc['w_p']*100:.1f}% vs {top_alloc['w_b']*100:.1f}%), "
            f"contributing {top_alloc['allocation_effect']*10_000:+.0f} bps allocation effect"
        )

    return {
        "rows":             rows,
        "chart_b64":        _chart_b64(fig, 700, 320),
        "total_alloc":      f"{bf_df['allocation_effect'].sum()*10000:+.1f}",
        "total_sel":        f"{bf_df['selection_effect'].sum()*10000:+.1f}",
        "total_total":      f"{bf_df['total_effect'].sum()*10000:+.1f}",
        "sel_commentary":   sel_commentary,
        "alloc_commentary": alloc_commentary,
        # Phase 38b-2 — operational cash drag, broken out so the ex-cash strategic
        # attribution reconciles to the actual (incl-cash) total active return.
        "cash_drag_bps":    f"{bf_df.attrs.get('cash_drag', 0.0)*10000:+.1f}",
        "price_gaps":       _price_gaps,
        "benchmark_gaps":   _benchmark_gaps,
    }


def _build_factor_section(end_date: str) -> Optional[dict]:
    """
    Run per-sleeve FF5 regressions from inception through end_date and format for
    the template.  Returns None only if both sleeves fail; partial results (one
    sleeve None) are still returned so the template can render what it has.
    """
    inception = get_inception_date()
    try:
        results = run_sleeve_regressions(inception, end_date)
    except Exception:
        logging.exception("FF5 sleeve regressions failed — factor section omitted from PDF")
        return None

    if not any(v is not None for v in results.values()):
        return None

    fi_result: Optional[dict] = None
    try:
        fi_result = regress_fi_sleeve(inception, end_date)
    except Exception:
        pass

    global_result: Optional[dict] = None
    try:
        global_result = run_intl_global_regression(inception, end_date)
    except Exception:
        pass

    def _fmt_date_local(iso: str) -> str:
        d = date.fromisoformat(iso)
        return f"{d.strftime('%B')} {d.day}, {d.year}"

    def _format_sleeve(res: Optional[dict]) -> Optional[dict]:
        if res is None:
            return None
        rows = []
        for factor in ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]:
            p = res["p_values"][factor]
            rows.append({
                "factor":       factor,
                "beta":         f"{res['betas'][factor]:.3f}",
                "tstat":        f"{res['t_stats'][factor]:.2f}",
                "pvalue":       f"{p:.3f}",
                "significance": sig_marker(p),
            })
        p_alpha = res["p_alpha"]
        return {
            "label":            res["sleeve_label"],
            "tickers":          res["tickers"],
            "rows":             rows,
            "alpha_annual_str": f"{res['alpha_annual_bps']:+.0f} bps/yr",
            "t_alpha_str":      f"{res['t_alpha']:.2f}",
            "p_alpha_str":      f"{p_alpha:.3f}",
            "sig_alpha":        sig_marker(p_alpha),
            "r_squared":        f"{res['r_squared']:.3f}",
            "adj_r_squared":    f"{res['adj_r_squared']:.3f}",
            "T":                res["T"],
            "nw_lags":          res["nw_lags"],
            "sample_window": (
                f"{_fmt_date_local(res['sample_start'])} — "
                f"{_fmt_date_local(res['sample_end'])}"
            ),
            "_raw": res,
        }

    sleeves = [
        s for s in [
            _format_sleeve(results.get("us")),
            _format_sleeve(results.get("developed_exus")),
        ]
        if s is not None
    ]

    return {
        "sleeves":           sleeves,
        "em_note":           EM_DISCLOSURE,
        "prose":             build_factor_prose(results, fi_result=fi_result, global_result=global_result),
        "methodology_notes": build_factor_methodology_notes(results, fi_result=fi_result),
    }


def _build_benchmark_section(start_date: str, end_date: str) -> Optional[dict]:
    """
    Run the benchmark-relative attribution regression from inception through end_date
    and format results for the template.  Returns None if insufficient data or on failure.

    start_date is the report-period start (e.g. Dec-31 for Q1) and is used only
    for the Brinson-Fachler cross-reference window — the regression itself always covers the
    full inception-to-end_date window.  Cross-reference uses the raw return
    differential (r_p − r_b) so prose reads "AVUV outperformed IWM by 768 bps"
    rather than the portfolio-weighted selection effect.
    """
    inception = get_inception_date()
    try:
        result = run_benchmark_attribution_regression(inception, end_date)
    except Exception:
        logging.exception("Benchmark attribution regression failed — section omitted from PDF")
        return None

    if result is None:
        return None

    _BENCH_FACTORS = ["Bench-RF", "HML", "SMB", "RMW"]

    def _fmt_date_local(iso: str) -> str:
        d = date.fromisoformat(iso)
        return f"{d.strftime('%B')} {d.day}, {d.year}"

    rows = []
    p_alpha = result["p_alpha"]
    rows.append({
        "factor":       "Alpha (annualized)",
        "beta":         f"{result['alpha_annual_bps']:+.0f} bps/yr",
        "tstat":        f"{result['t_alpha']:.2f}",
        "pvalue":       f"{p_alpha:.3f}",
        "significance": sig_marker(p_alpha),
    })
    for f in _BENCH_FACTORS:
        p = result["p_values"][f]
        rows.append({
            "factor":       f,
            "beta":         f"{result['betas'][f]:.3f}",
            "tstat":        f"{result['t_stats'][f]:.2f}",
            "pvalue":       f"{p:.3f}",
            "significance": sig_marker(p),
        })

    # Top-3 Brinson-Fachler cross-reference — use report period (Q1) window.
    bhb_top = None
    try:
        bf_df = brinson_fachler_period(start_date, end_date)
        bhb_top = build_bf_cross_reference(bf_df) or None
    except Exception:
        pass

    return {
        "rows":              rows,
        "r_squared":         f"{result['r_squared']:.3f}",
        "adj_r_squared":     f"{result['adj_r_squared']:.3f}",
        "T":                 result["T"],
        "nw_lags":           result["nw_lags"],
        "sample_window": (
            f"{_fmt_date_local(result['sample_start'])} — "
            f"{_fmt_date_local(result['sample_end'])}"
        ),
        "prose":             build_benchmark_prose(result, bhb_top_selection=bhb_top),
        "methodology_notes": build_benchmark_methodology(result),
        "_raw":              result,
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
        try:
            _raw_pct = percentile(cape_s, cape_val)
            pct_str  = f"{_raw_pct:.0f}th"
            pct_int  = int(round(_raw_pct))
        except Exception:
            pct_str  = "N/A"
            pct_int  = 50
        if pct_int >= 80:
            _regime        = "Elevated"
            _regime_action = "support the SAA's diversification across non-US equity and real asset sleeves"
        elif pct_int >= 60:
            _regime        = "Above-average"
            _regime_action = "support modest diversification away from US large-cap"
        elif pct_int >= 40:
            _regime        = "Moderate"
            _regime_action = "are consistent with the long-run average; no strong tilt signal"
        else:
            _regime        = "Below-average"
            _regime_action = "support increased US equity exposure relative to SAA targets"
        cape_implied = compute_cape_implied_return(cape_val)
        macro["cape"] = {
            "value":          f"{cape_val:.1f}x",
            "percentile":     pct_str,
            "pct_int":        pct_int,
            "regime":         _regime,
            "regime_action":  _regime_action,
            "note":           f"{_regime} vs. history ({pct_str}); valuations {_regime_action}.",
            "implied_return": f"{cape_implied:+.2%}",
        }
    except Exception:
        macro["cape"] = {
            "value":          "N/A",
            "percentile":     "N/A",
            "pct_int":        50,
            "regime":         "Uncertain",
            "regime_action":  "suggest maintaining SAA diversification",
            "note":           "Data unavailable.",
            "implied_return": None,
        }

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
            """SELECT trade_date, ticker, action, shares, price, lot_source
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
    drip_count = 0
    drip_total = 0.0
    for t in tr_rows:
        price = float(t["price"]) if t["price"] else 0.0
        if t["lot_source"] == "drip":
            drip_count += 1
            drip_total += float(t["shares"]) * price
            continue
        td = date.fromisoformat(t["trade_date"])
        trades.append({
            "date":   f"{td.strftime('%b')} {td.day}, {td.year}",
            "ticker": t["ticker"],
            "action": t["action"].title(),
            "shares": f"{float(t['shares']):.0f}",
            "price":  f"${price:.2f}" if price else "N/A",
            "cost":   f"${float(t['shares']) * price:,.0f}",
        })

    drip_summary = (
        {"count": drip_count, "total": f"${drip_total:,.2f}"}
        if drip_count > 0 else None
    )
    return {"theses": theses, "trades": trades, "drip_summary": drip_summary}


def _build_positioning_section(end_date: str) -> dict:
    """Build the positioning section (duration + style box) from live portfolio state."""
    dur        = get_effective_duration(end_date)
    style_data = get_style_box_data(end_date)
    non_us     = get_non_us_equity_data(end_date)
    fi_dur    = dur["fi_sleeve_duration"]
    agg_dur   = dur["agg_benchmark"]
    fi_wt     = dur["fi_weight_pct"]
    cash_wt   = dur["cash_weight_pct"]
    dur_diff  = abs(fi_dur - agg_dur)
    if dur_diff < 0.05:
        dur_vs = "in line with benchmark"
    else:
        vs_agg = "below" if fi_dur < agg_dur else "above"
        dur_vs = f"{vs_agg} benchmark by {dur_diff:.1f} yrs"
    duration_line = (
        f"Fixed Income sleeve (Core FI + TIPS) effective duration: {fi_dur} yrs "
        f"vs Bloomberg US Agg: {agg_dur} yrs ({dur_vs}). "
        f"FI weight: {fi_wt}% of portfolio. "
        f"Cash/SPAXX ({cash_wt}%) excluded — not a duration-bearing asset and excluded from Bloomberg Agg."
    )
    style_box_b64 = _chart_b64(build_style_box_figure(style_data), 520, 300) if style_data else None
    return {
        "duration_line":     duration_line,
        "style_box_b64":     style_box_b64,
        "style_box_caption": STYLE_BOX_CAPTION,
        "non_us":            non_us,
    }


def _build_asset_eval_section() -> dict:
    """Build the Asset Evaluation section — same source as pages/10_Asset_Evaluation.py."""
    import src.asset_evaluation as ae

    _empty: dict = {
        "sample_start":     ae.SAMPLE_START,
        "uni_rows":         [],
        "corr_chart_b64":   None,
        "corr_prose":       None,
        "rolling_chart_b64": None,
        "rolling_prose":    None,
        "con_rows":         [],
        "sharpe_con_no":    None,
        "sharpe_con_with":  None,
        "delta_bps_con":    None,
        "msc_chart_b64":    None,
        "dd_rows":          [],
        "args_for":         [],
        "args_against":     [],
        "conclusion": ae.CONCLUSION,
    }

    try:
        btc_ret = ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START)
        slv_ret = ae.get_sleeve_returns(ae.SAMPLE_START)
        spy_ret = ae.get_candidate_returns("SPY", ae.SAMPLE_START)
        if btc_ret.empty or slv_ret.empty:
            return _empty
    except Exception:
        logging.exception("asset_eval data load failed — section omitted from PDF")
        return _empty

    result = {k: (list(v) if isinstance(v, list) else v) for k, v in _empty.items()}

    # 5a: Univariate statistics
    try:
        uni_tbl = ae.build_univariate_table()
        if not uni_tbl.empty:
            for name, row in uni_tbl.iterrows():
                result["uni_rows"].append({
                    "asset":        name,
                    "ann_return":   f"{row['ann_return']:.1%}",
                    "ann_vol":      f"{row['ann_vol']:.1%}",
                    "sharpe":       f"{row['sharpe']:.2f}",
                    "max_drawdown": f"{row['max_drawdown']:.1%}",
                    "skewness":     f"{row['skewness']:.2f}",
                    "kurtosis":     f"{row['kurtosis']:.2f}",
                })
    except Exception:
        pass

    # 5b: Full-sample correlation heatmap
    corr: pd.Series = pd.Series(dtype=float)
    try:
        corr = ae.compute_full_sample_correlations(btc_ret, slv_ret)
        if not corr.empty:
            fig_heat = go.Figure(go.Heatmap(
                z=[[corr[s] for s in ae.SLEEVES]],
                x=ae.SLEEVES,
                y=["BTC"],
                colorscale="RdYlGn",
                zmid=0, zmin=-1, zmax=1,
                text=[[f"{corr[s]:.2f}" for s in ae.SLEEVES]],
                texttemplate="%{text}",
                showscale=True,
            ))
            fig_heat.update_layout(
                height=200,
                margin=dict(l=60, r=20, t=10, b=80),
                paper_bgcolor="white", plot_bgcolor="white",
                font=dict(family="sans-serif", size=9, color="#333"),
            )
            fig_heat.update_xaxes(tickangle=45)
            result["corr_chart_b64"] = _chart_b64(fig_heat, 700, 200)

            btc_spy_corr   = float(corr.get("US Large Core",     float("nan")))
            fi_corr        = float(corr.get("Core Fixed Income", float("nan")))
            highest_sleeve = corr.idxmax()
            highest_val    = float(corr.max())
            lowest_sleeve  = corr.idxmin()
            lowest_val     = float(corr.min())

            parts = []
            if not np.isnan(btc_spy_corr):
                parts.append(f"BTC full-sample correlation with US Large Core: {btc_spy_corr:.2f}.")
            parts.append(
                f"Highest sleeve: {highest_sleeve} ({highest_val:.2f}); "
                f"lowest: {lowest_sleeve} ({lowest_val:.2f})."
            )
            if not np.isnan(fi_corr):
                parts.append(f"Core Fixed Income: {fi_corr:.2f}.")
            result["corr_prose"] = " ".join(parts)
    except Exception:
        pass

    # 5c: Rolling 60-day correlation (BTC vs SPY)
    try:
        rolling_corr = ae.compute_rolling_correlation(btc_ret, spy_ret, window=60)
        if not rolling_corr.empty:
            fig_roll = go.Figure()
            fig_roll.add_hline(y=0, line_dash="dash", line_color="#9E9E9E", line_width=1)
            fig_roll.add_trace(go.Scatter(
                x=rolling_corr.index, y=rolling_corr.values,
                mode="lines",
                line=dict(color="#2E4057", width=1.5),
                name="BTC-SPY 60d",
            ))
            fig_roll.update_layout(
                height=200,
                margin=dict(l=45, r=10, t=8, b=30),
                paper_bgcolor="white", plot_bgcolor="#FAFAFA",
                showlegend=False,
                yaxis=dict(gridcolor="#EBEBEB", range=[-1.05, 1.05], title="Correlation"),
                xaxis=dict(gridcolor="#EBEBEB"),
                font=dict(family="sans-serif", size=9, color="#333"),
            )
            result["rolling_chart_b64"] = _chart_b64(fig_roll, 700, 200)

            post_2020    = rolling_corr.loc["2020-01-01":]
            pre_2020     = rolling_corr.loc[:"2019-12-31"]
            post_avg     = float(post_2020.mean()) if len(post_2020) > 0 else float("nan")
            pre_avg      = float(pre_2020.mean())  if len(pre_2020) > 0  else float("nan")
            prose_parts: list[str] = []
            if not np.isnan(pre_avg):
                prose_parts.append(
                    f"Pre-2020 average: {pre_avg:.2f} "
                    "(consistent with 'uncorrelated alternative' narrative)."
                )
            if not np.isnan(post_avg):
                prose_parts.append(
                    f"Post-2020 average: {post_avg:.2f} — persistently elevated after "
                    "institutionalization; correlation rises precisely when diversification "
                    "is most needed."
                )
            result["rolling_prose"] = " ".join(prose_parts)
    except Exception:
        pass

    # 5f: Constrained MV analysis
    mv_result: dict = {}
    msc: pd.DataFrame = pd.DataFrame()
    try:
        mv_result = ae.compute_mv_analysis(btc_ret, slv_ret, ae.RF_ANNUAL)
        if mv_result:
            sleeves_list    = mv_result["sleeves"]
            w_con_no        = mv_result["w_con_no"]
            w_con_with      = mv_result["w_con_with"]
            sharpe_con_no   = mv_result["sharpe_con_no"]
            sharpe_con_with = mv_result["sharpe_con_with"]
            delta_bps_con   = (sharpe_con_with - sharpe_con_no) * 10_000
            btc_wt_con      = float(w_con_with[-1])

            for i, s in enumerate(sleeves_list):
                result["con_rows"].append({
                    "sleeve": s,
                    "w_no":   f"{w_con_no[i]:.1%}",
                    "w_with": f"{w_con_with[i]:.1%}",
                })
            result["con_rows"].append({
                "sleeve": "BTC",
                "w_no":   "—",
                "w_with": f"{btc_wt_con:.1%}",
            })
            result["sharpe_con_no"]   = f"{sharpe_con_no:.3f}"
            result["sharpe_con_with"] = f"{sharpe_con_with:.3f}"
            result["delta_bps_con"]   = f"{delta_bps_con:+.0f}"
    except Exception:
        pass

    # 5g: Marginal Sharpe curve
    try:
        msc = ae.compute_marginal_sharpe_curve(btc_ret, slv_ret, ae.SLEEVE_WEIGHTS, ae.RF_ANNUAL)
        if not msc.empty:
            fig_msc = go.Figure()
            fig_msc.add_trace(go.Scatter(
                x=msc["btc_alloc"] * 100,
                y=msc["sharpe"],
                mode="lines+markers",
                line=dict(color="#2E4057", width=2),
                marker=dict(size=6),
                name="Portfolio Sharpe",
            ))
            fig_msc.update_layout(
                height=200,
                margin=dict(l=50, r=10, t=8, b=40),
                paper_bgcolor="white", plot_bgcolor="#FAFAFA",
                showlegend=False,
                xaxis=dict(title="BTC Allocation (%)", gridcolor="#EBEBEB"),
                yaxis=dict(title="Portfolio Sharpe",   gridcolor="#EBEBEB"),
                font=dict(family="sans-serif", size=9, color="#333"),
            )
            result["msc_chart_b64"] = _chart_b64(fig_msc, 700, 200)
    except Exception:
        pass

    # 5h: Drawdown sensitivity
    try:
        dd_sens = ae.compute_drawdown_sensitivity(
            btc_ret, slv_ret, ae.SLEEVE_WEIGHTS, rf_annual=ae.RF_ANNUAL
        )
        if not dd_sens.empty:
            for _, row in dd_sens.iterrows():
                mdd22_val = row["2022 MDD"]
                result["dd_rows"].append({
                    "alloc":  row["BTC Alloc"],
                    "cagr":   f"{row['CAGR']:.1%}",
                    "max_dd": f"{row['Max DD']:.1%}",
                    "sharpe": f"{row['Sharpe']:.2f}",
                    "mdd22":  f"{mdd22_val:.1%}" if not np.isnan(mdd22_val) else "—",
                })
    except Exception:
        pass

    # 5j: Decision framework
    try:
        args_for: list[str]     = []
        args_against: list[str] = []

        if not msc.empty:
            rows_0  = msc[msc["btc_alloc"] == 0.00]
            rows_10 = msc[msc["btc_alloc"] == 0.10]
            if not rows_0.empty and not rows_10.empty:
                saa_sh0  = float(rows_0["sharpe"].iloc[0])
                saa_sh10 = float(rows_10["sharpe"].iloc[0])
                if saa_sh10 > saa_sh0:
                    delta_saa = (saa_sh10 - saa_sh0) * 10_000
                    mvo_ok    = bool(
                        mv_result
                        and mv_result.get("sharpe_con_with", 0) > mv_result.get("sharpe_con_no", 0)
                    )
                    mvo_note = " — MVO sanity check directionally consistent" if mvo_ok else ""
                    args_for.append(
                        f"Sharpe-improving against SAA target weights at 10% allocation "
                        f"({saa_sh0:.3f} → {saa_sh10:.3f}, "
                        f"+{delta_saa:.0f} bps over {ae.SAMPLE_START}–present){mvo_note}"
                    )

        if not corr.empty:
            btc_spy_j = float(corr.get("US Large Core", float("nan")))
            if not np.isnan(btc_spy_j) and btc_spy_j > 0.3:
                args_against.append(
                    f"Equity-like correlation with US Large Core ({btc_spy_j:.2f}) post-2020 — "
                    "co-movement spikes during stress precisely when a hedge is most valuable"
                )

        args_against.extend([
            "Maximum historical drawdown exceeding 80% — 2022 coincided with equity and bond "
            "losses simultaneously (no diversification benefit when needed)",
            "Commodity tax treatment: short-term ordinary income / long-term capital gains, "
            "no qualified-dividend treatment — unfavorable vs. equity ETFs in taxable accounts",
            "No intrinsic cash flow or fundamental valuation anchor — expected return is "
            "purely sentiment-driven, making MV inputs unreliable for forward-looking allocation",
        ])

        result["args_for"]     = args_for
        result["args_against"] = args_against
    except Exception:
        pass

    return result


# ── Methodology template variables ───────────────────────────────────────────

# SAA rule: sleeves with target weight ≥10% → ±300 bps; <10% → ±200 bps.
# Real Assets is the boundary case at exactly 10% and is assigned to the 200bps
# tier as a deliberate exception — so MIN(target_weight) for the 300bps DB group
# returns 0.14 (US Large Quality), not 0.10. Use the rule constant directly; do
# not infer the threshold from DB data.
_TOLERANCE_BAND_LARGE_CUTOFF_PCT = 10


def _build_methodology_vars() -> dict:
    """Return DB-derived values used in the PDF methodology section.

    Keeps the quarterly_report.html template free of hardcoded SAA numerics.
    Called once per report render; the DB queries are lightweight.
    """
    from src.benchmarks import _sleeve_benchmarks

    with get_connection() as conn:
        # Phase 38a — strategic SAA only (target_weight > 0). Cash / SPAXX and
        # any non-SAA residual are operational, excluded from the methodology.
        parent_rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes "
            "WHERE parent_id IS NULL AND target_weight > 0 ORDER BY asset_class_id"
        ).fetchall()
        sleeve_count = conn.execute(
            "SELECT COUNT(*) FROM asset_classes WHERE parent_id IS NOT NULL AND target_weight > 0"
        ).fetchone()[0]
        band_rows = conn.execute(
            "SELECT DISTINCT CAST(ROUND(tolerance_band * 10000) AS INTEGER) AS band_bps "
            "FROM asset_classes WHERE parent_id IS NOT NULL AND target_weight > 0 ORDER BY band_bps"
        ).fetchall()

    # "Equity 80% / Income 10% / Real Assets 10%" (ex-cash strategic parents)
    parent_weight_str = " / ".join(
        f"{r['name']} {round(r['target_weight'] * 100):.0f}%"
        for r in parent_rows
    )

    # "60% VNQ + 40% DBC" — derived from the DB benchmark for Real Assets
    ra_bench = _sleeve_benchmarks().get("Real Assets", [])
    ra_bench_str = " + ".join(f"{round(w * 100):.0f}% {t}" for t, w in ra_bench)

    # Drift band tiers: smallest band (200 bps) and largest (300 bps)
    band_bps_vals = [int(r["band_bps"]) for r in band_rows]
    if len(band_bps_vals) >= 2:
        drift_small_bps = band_bps_vals[0]
        drift_large_bps = band_bps_vals[-1]
    else:
        drift_small_bps, drift_large_bps = 200, 300

    return {
        "methodology_parent_weights":      parent_weight_str,
        "methodology_sleeve_count":        sleeve_count,
        "methodology_ra_bench":            ra_bench_str,
        "methodology_drift_large_bps":     drift_large_bps,
        "methodology_drift_small_bps":     drift_small_bps,
        "methodology_drift_large_min_pct": _TOLERANCE_BAND_LARGE_CUTOFF_PCT,
    }


# ── Main public function ──────────────────────────────────────────────────────

def _make_report_env() -> Environment:
    """Jinja environment for the PDF templates, with autoescape ON (audit #6).

    autoescape neutralizes HTML/script injection through any templated value —
    notably the visitor-supplied recipient name and any ticker / thesis / fund
    string. The trusted stylesheet is the one value that must pass through raw;
    the template marks it ``{{ css_content|safe }}`` so autoescape does not mangle
    the CSS.
    """
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def generate_quarterly_report_bytes(
    start_date: str,
    end_date: str,
    recipient_name: str = "Matthew Orefice",
    is_demo: Optional[bool] = None,
) -> bytes:
    """
    Render a quarterly report and return the PDF as in-memory bytes — no file is
    written. This is the session-safe entry point (audit #6): the Streamlit page
    serves these bytes straight to st.download_button, so on shared Cloud
    infrastructure one visitor's report never lands on a shared disk path where
    another visitor's session could glob and download it.

    When zero trades exist, produces a structural report (SAA + macro + theses only).
    `is_demo` defaults to the app's resolved TRACKER_MODE (src.config.IS_DEMO) so a
    report generated from the public demo is labeled demo/illustrative rather than
    as a real personal account; pass explicitly to override (e.g. in tests).
    """
    if is_demo is None:
        is_demo = IS_DEMO
    with get_connection() as conn:
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    has_trades = trade_count > 0

    period_label = _format_period_label(start_date, end_date)

    # Quarter-locking: use immutable snapshot prices for standard completed quarters so
    # that retroactive adj_close updates cannot shift historical report numbers.
    quarter_id = label_to_quarter_id(period_label)
    snap_df: Optional[pd.DataFrame] = None
    snapshot_captured_at: Optional[str] = None
    if quarter_id and is_quarter_complete(quarter_id):
        snap_df, snapshot_captured_at = get_quarter_snapshot(quarter_id)
        if snap_df is None:
            snap_df, snapshot_captured_at = capture_quarter_snapshot(quarter_id)

    ctx = snapshot_price_context(snap_df) if snap_df is not None else nullcontext()
    with ctx:
        exec_data        = _build_executive_summary(start_date, end_date) if has_trades else None
        hold_data        = _build_holdings_section(end_date)              if has_trades else {"rows": [], "chart_b64": None}
        perf_data        = _build_performance_section(start_date, end_date) if has_trades else None
        attr_data        = _build_attribution_section(start_date, end_date) if has_trades else None
        pos_data         = _build_positioning_section(end_date)             if has_trades else None
        factor_data      = _build_factor_section(end_date)                 if has_trades else None
        bench_attr_data  = _build_benchmark_section(start_date, end_date)  if has_trades else None
        macro_data       = _build_macro_section()
        thesis_data      = _build_thesis_section(start_date, end_date)
        asset_eval_data  = _build_asset_eval_section()

    css_content = (TEMPLATES_DIR / "report_styles.css").read_text(encoding="utf-8")

    env  = _make_report_env()
    tmpl = env.get_template("quarterly_report.html")

    # Cross-platform date formatting (%-d fails on Windows)
    today = date.today()
    gen_date = f"{today.strftime('%B')} {today.day}, {today.year}"

    snapshot_display: Optional[str] = None
    if snapshot_captured_at:
        snap_dt = datetime.fromisoformat(snapshot_captured_at)
        snapshot_display = f"{snap_dt.strftime('%B')} {snap_dt.day}, {snap_dt.year}"

    inception_str   = get_inception_date()
    si_days_report  = (date.fromisoformat(end_date) - date.fromisoformat(inception_str)).days

    html_content = tmpl.render(
        css_content          = css_content,
        period_label         = period_label,
        recipient_name       = recipient_name,
        generation_date      = gen_date,
        snapshot_captured_at = snapshot_display,
        has_trades           = has_trades,
        inception_date       = inception_str,
        si_days              = si_days_report,
        cover_sub_id         = _account_label(is_demo),
        report_disclaimer    = _report_disclaimer(is_demo),
        exec           = exec_data,
        hold           = hold_data,
        perf           = perf_data,
        attr           = attr_data,
        pos            = pos_data,
        factor         = factor_data,
        bench_attr     = bench_attr_data,
        macro          = macro_data,
        thesis         = thesis_data,
        asset_eval     = asset_eval_data,
        **_build_methodology_vars(),
    )

    return _render_pdf(html_content)


def generate_quarterly_report(
    start_date: str,
    end_date: str,
    recipient_name: str = "Matthew Orefice",
    output_path: Optional[Path] = None,
    is_demo: Optional[bool] = None,
) -> Path:
    """
    Generate a quarterly PDF report and WRITE it to disk, returning the Path.

    Retained for CLI / personal-mode / test use. The public Streamlit page does
    NOT call this — it uses generate_quarterly_report_bytes() and serves the bytes
    from memory, so no PDF is written to the shared Cloud filesystem (audit #6).
    When zero trades exist, produces a structural report (SAA + macro + theses only).
    `is_demo` defaults to the app's resolved TRACKER_MODE (src.config.IS_DEMO) so a
    report generated from the public demo is labeled demo/illustrative rather than
    as a real personal account; pass explicitly to override (e.g. in tests).
    """
    if is_demo is None:
        is_demo = IS_DEMO
    pdf_bytes = generate_quarterly_report_bytes(
        start_date, end_date, recipient_name=recipient_name, is_demo=is_demo
    )
    if output_path is None:
        period_label = _format_period_label(start_date, end_date)
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _REPORTS_DIR / _format_filename(start_date, end_date, period_label)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)
    return output_path
