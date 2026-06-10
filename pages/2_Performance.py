"""Performance & Attribution page."""
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Performance & Attribution", layout="wide")

from src.asof import as_of_banner, most_recent_reportable_quarter, reportable_quarter_phrase, NO_COMPLETED_QUARTER
from src.config import get_demo_banner_text, IS_DEMO
from src.attribution import brinson_fachler_period, compute_two_stage_attribution
from src.benchmarks import get_custom_blended_series, get_naive_60_40_series, get_naive_series, get_sp500_series
from src.db import get_connection
from src.factors import run_sleeve_regressions
from src.holdings import (
    get_current_market_value,
    get_inception_date,
    get_portfolio_value_series,
    get_sleeve_weights_on_date,
)
from src.performance import compute_risk_metrics
from src.reports import generate_quarterly_report
from src.returns import annualize, period_return, twr_daily_linked
from src.positioning import get_effective_duration
from src.rebalance import compute_drift
from src.ui_helpers import render_footer, render_page_header
render_page_header()


_REPORTS_DIR = Path(__file__).parent.parent / "data" / "reports"

INCEPTION    = get_inception_date()
TODAY        = date.today().isoformat()
PERIODS      = ["1M", "3M", "YTD", "1Y", "SI"]
PERIOD_LABEL = {"1M": "1 Month", "3M": "3 Months", "YTD": "YTD",
                "1Y": "1 Year", "SI": "Since Inception"}

# Increment when a bug fix changes what get_portfolio_value_series returns so
# that Streamlit's @st.cache_data (keyed on function args) invalidates the old
# cached result automatically rather than serving the pre-fix stale value.
_PORTFOLIO_CACHE_V = 3

_PALETTE = {
    "portfolio": "#2E4057",   # deep navy
    "sp500":     "#8C9AA6",   # slate gray
    "blended":   "#5C7A5C",   # muted sage
    "alloc":     "#5B7FA6",   # steel blue
    "selection": "#A67B5B",   # warm tan
}


# ── Cached data loaders ───────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _load_portfolio(_v: int = _PORTFOLIO_CACHE_V):
    pv = get_portfolio_value_series(INCEPTION, TODAY)
    cf = pd.Series(0.0, index=pv.index)
    return pv, cf


@st.cache_data(ttl=3600, show_spinner=False)
def _load_benchmarks(start_val: float):
    sp  = get_sp500_series(INCEPTION, TODAY)      * start_val
    bl  = get_custom_blended_series(INCEPTION, TODAY) * start_val
    return sp, bl


@st.cache_data(ttl=3600, show_spinner=False)
def _load_naive_benchmark(start_val: float, kind: str = "60_40"):
    naive = get_naive_series(kind, INCEPTION, TODAY) * start_val
    return naive


@st.cache_data(ttl=3600, show_spinner=False)
def _load_attribution(period_key: str):
    if period_key == "SI":
        start = INCEPTION
    elif period_key == "1Y":
        start = _date_offset(TODAY, days=-365)
    elif period_key == "YTD":
        import datetime
        start = f"{TODAY[:4]}-01-01"
    elif period_key == "3M":
        start = _date_offset(TODAY, days=-90)
    else:   # 1M
        start = _date_offset(TODAY, days=-30)
    return brinson_fachler_period(start, TODAY)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_factor_results(inception_date: str, end: str) -> dict:
    try:
        return run_sleeve_regressions(inception_date, end)
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_drift():
    sw = get_sleeve_weights_on_date(TODAY)
    with get_connection() as conn:
        bands = conn.execute(
            "SELECT name, tolerance_band FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
    band_map = {r["name"]: r["tolerance_band"] for r in bands}
    return sw, band_map


@st.cache_data(ttl=3600, show_spinner=False)
def _load_sleeve_targets():
    """Return (parent_weights, sleeve_weights) dicts keyed by name."""
    with get_connection() as conn:
        parents = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NULL"
        ).fetchall()
        sleeves = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
    return (
        {r["name"]: r["target_weight"] for r in parents},
        {r["name"]: r["target_weight"] for r in sleeves},
    )


def _date_offset(iso: str, days: int) -> str:
    from datetime import timedelta
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def _pct(v: float, decimals: int = 2) -> str:
    return f"{v * 100:.{decimals}f}%"


def _bps(v: float) -> str:
    b = v * 10_000
    sign = "+" if b >= 0 else ""
    return f"{sign}{b:.0f} bps"


# ── Page ─────────────────────────────────────────────────────────────────────

if IS_DEMO:
    st.info(get_demo_banner_text())

_, col, _ = st.columns([1, 8, 1])
with col:

    st.markdown("## Performance & Attribution")
    st.caption(
        "Time-weighted return, benchmarking, and Brinson-Fachler decomposition."
    )
    st.caption(as_of_banner())

    # Load data
    with st.spinner("Loading performance data…"):
        pv, cf = _load_portfolio()

    # ── Generate Report expander ──────────────────────────────────────────
    with st.expander("Generate Quarterly Report", expanded=False):
        _existing_pdfs = sorted(_REPORTS_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if _existing_pdfs:
            _latest = _existing_pdfs[0]
            st.download_button(
                f"⬇ Latest report: {_latest.name}",
                _latest.read_bytes(),
                file_name=_latest.name,
                mime="application/pdf",
                key="latest_report_dl",
            )
            st.markdown("---")

        _rcol1, _rcol2 = st.columns([1, 1])
        with _rcol1:
            _period_choice = st.selectbox(
                "Period",
                ["Most recent completed quarter", "Custom date range"],
                key="report_period_choice",
            )
        with _rcol2:
            _recipient = st.text_input(
                "Recipient name", value="Matthew Orefice", key="report_recipient"
            )

        _can_generate = True
        if _period_choice == "Most recent completed quarter":
            _q_rep = most_recent_reportable_quarter(INCEPTION, date.fromisoformat(TODAY))
            if _q_rep is None:
                # Most-recent completed quarter entirely predates inception — no
                # quarter to report yet; suppress generation rather than emit a
                # zeros PDF for a span before the portfolio existed.
                st.info(NO_COMPLETED_QUARTER)
                _can_generate = False
            else:
                _r_start_d, _r_end_d, _r_qlabel = _q_rep
                _r_start, _r_end = _r_start_d.isoformat(), _r_end_d.isoformat()
                st.caption(f"Period: **{_r_qlabel}** &nbsp;({_r_start} to {_r_end})")
                _report_filename = (
                    f"Orefice_Portfolio_{_r_qlabel.replace(' ', '')[2:]}"
                    f"{_r_qlabel.split()[0]}.pdf"
                )
        else:
            _dc1, _dc2 = st.columns(2)
            with _dc1:
                _r_start = str(st.date_input("Start date", value=date(2025, 1, 1), key="report_start"))
            with _dc2:
                _r_end   = str(st.date_input("End date",   value=date.today(),       key="report_end"))
            _r_qlabel = None
            _report_filename = (
                f"Orefice_Portfolio_{_r_start.replace('-','')}_{_r_end.replace('-','')}.pdf"
            )

        if _can_generate and st.button("Generate Report", type="primary", key="gen_report_btn"):
            with st.spinner("Generating PDF — this takes ~30 seconds for chart rendering…"):
                try:
                    _pdf_path = generate_quarterly_report(
                        _r_start, _r_end, recipient_name=_recipient
                    )
                    _pdf_bytes = Path(_pdf_path).read_bytes()
                    st.success(f"Report generated ({len(_pdf_bytes):,} bytes)")
                    st.download_button(
                        "⬇ Download PDF",
                        _pdf_bytes,
                        file_name=_report_filename,
                        mime="application/pdf",
                        key="report_download",
                    )
                except Exception as _e:
                    st.error(f"Report generation failed: {_e}")

    # Empty-state guard — no trades yet
    if pv.empty or float(pv.max()) == 0.0:
        st.info(
            "No performance data yet. Log your first trade in the Trade Log "
            "to begin tracking returns and attribution."
        )
        st.stop()

    with st.spinner("Loading benchmark data…"):
        start_val = float(pv.iloc[0])
        sp, bl    = _load_benchmarks(start_val)

    _saa_parents, _saa_sleeves = _load_sleeve_targets()
    _non_eq_pct = 1.0 - _saa_parents.get("Equity", 0.78 / 0.98)
    _non_us_eq  = (_saa_sleeves.get("International Developed", 0.20 / 0.98)
                   + _saa_sleeves.get("Emerging Markets", 0.09 / 0.98))

    # Key scalars (Since Inception)
    si_days     = (pd.Timestamp(TODAY) - pd.Timestamp(INCEPTION)).days
    port_si     = period_return("daily", pv, cf, "SI")
    sp500_si    = float(sp.iloc[-1] / sp.iloc[0] - 1)
    blended_si  = float(bl.iloc[-1] / bl.iloc[0] - 1)
    alpha_sp    = port_si - sp500_si
    alpha_bl    = port_si - blended_si
    ytd_return  = period_return("daily", pv, cf, "YTD")
    # current_val: endpoint of the total-return series (adj_close × non-DRIP) —
    # used ONLY for the adj_close-basis absolute-return reconciliation below.
    current_val = float(pv.iloc[-1])
    # current_mv: true current account market value (ALL shares incl DRIP × raw
    # close) — the dollar figure to DISPLAY. Display-only; never feeds a return.
    current_mv  = get_current_market_value(TODAY)

    # ── Summary banner ────────────────────────────────────────────────────
    st.markdown(
        f"**${current_mv:,.0f}** current value &nbsp;·&nbsp; "
        f"**{si_days}**-day inception period &nbsp;·&nbsp; "
        f"**{port_si*100:.1f}%** cumulative TWR &nbsp;·&nbsp; "
        f"**{_bps(alpha_bl)}** vs blended &nbsp;·&nbsp; "
        f"**{_bps(alpha_sp)}** vs S&P 500",
        unsafe_allow_html=False,
    )

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 1a — Quarterly snapshot (most recently completed quarter)
    # ──────────────────────────────────────────────────────────────────────
    _q_rep = most_recent_reportable_quarter(INCEPTION, date.fromisoformat(TODAY))
    if _q_rep is None:
        # The most-recent completed quarter entirely predates inception (the
        # portfolio did not exist during it). Render the empty state instead of
        # an all-zero "locked" report for a span that precedes the portfolio.
        st.markdown("### Quarterly report")
        st.info(NO_COMPLETED_QUARTER)
    else:
        _q_start, _q_end, _q_label = _q_rep
        _q_ts_start = pd.Timestamp(_q_start)
        _q_ts_end   = pd.Timestamp(_q_end)

        def _q_ret(s: pd.Series) -> float:
            sliced = s[(s.index >= _q_ts_start) & (s.index <= _q_ts_end)]
            return float(sliced.iloc[-1] / sliced.iloc[0] - 1) if len(sliced) >= 2 else 0.0

        _q_port     = _q_ret(pv)
        _q_sp       = _q_ret(sp)
        _q_bl       = _q_ret(bl)
        _q_alpha_sp = _q_port - _q_sp
        _q_alpha_bl = _q_port - _q_bl

        st.markdown(f"### Quarterly report — {_q_label} (locked)")
        q1, q2, q3 = st.columns(3)
        q1.metric(f"{_q_label} return",             _pct(_q_port),     f"{_pct(_q_sp)} S&P 500")
        q2.metric(f"vs. S&P 500 — {_q_label}",      _bps(_q_alpha_sp), f"S&P 500: {_pct(_q_sp)}",  delta_color="off")
        q3.metric(f"vs. Custom Blended — {_q_label}", _bps(_q_alpha_bl), f"Blended: {_pct(_q_bl)}", delta_color="off")

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 1b — Since inception headline metrics
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("### Since inception")
    m1, m2, m3, m4 = st.columns(4)

    inception_delta_pct = f"{port_si*100:+.1f}% since inception"
    m1.metric("Portfolio value",              f"${current_mv:,.0f}", inception_delta_pct)
    m2.metric("vs. S&P 500 (since inception)",    _bps(alpha_sp),
              f"S&P 500: {_pct(sp500_si)} SI",
              delta_color="off")
    m3.metric("vs. Custom Blended (since inception)", _bps(alpha_bl),
              f"Blended: {_pct(blended_si)} SI",
              delta_color="off")
    m4.metric(f"YTD return ({TODAY[:4]})",    _pct(ytd_return),
              f"{_pct(port_si)} SI cumulative")

    st.caption(
        f"Underperformance vs. S&P 500 reflects intentional diversification: "
        f"{_non_eq_pct*100:.0f}% of the SAA is non-equity (Fixed Income + Real Assets + Cash), "
        f"{_non_us_eq*100:.0f}% is non-US equity. The Custom Blended benchmark — a target-weighted "
        "basket of cap-weighted indices in the same SAA — is the more meaningful "
        "to isolate implementation alpha from SAA-design effects."
    )

    # ── Reconciliation note ────────────────────────────────────────────────
    with get_connection() as _rc:
        _cost_row = _rc.execute(
            "SELECT SUM(shares * price) FROM trades WHERE LOWER(action) = 'buy'"
        ).fetchone()
    _cost_basis    = float(_cost_row[0] or 0.0)
    _series_start  = float(pv.iloc[0])
    if _cost_basis > 0 and _series_start > 0:
        _unrealized  = current_mv - _cost_basis
        _abs_ret_pct = (current_val / _series_start - 1) * 100
        _twr_pct     = port_si * 100
        st.caption(
            f"Reconciliation: **\\${_cost_basis:,.0f} cost basis** (all lots, incl "
            f"reinvested DRIP) → **\\${current_mv:,.0f} current value** (every share held, "
            f"incl DRIP, at market close; **\\${_unrealized:+,.0f}** unrealized gain). "
            f"Returns — absolute ({_abs_ret_pct:.1f}%) and cumulative TWR ({_twr_pct:.1f}%) — "
            f"use the dividend-adjusted total-return series (adj_close × actual non-DRIP "
            f"shares), which counts dividend income once and is restated retroactively as "
            f"dividends accrue. That return series is a different basis from the market-value "
            f"dollar above (which counts the real DRIP shares), so the two are not expected to "
            f"tie out dollar-for-dollar. TWR is the GIPS-correct measure for benchmark comparison."
        )
    # ── End reconciliation note ────────────────────────────────────────────

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 2 — TWR method toggle + period returns table
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("### Period Returns")

    c_left, c_right = st.columns([2, 5])
    with c_left:
        method = st.radio(
            "TWR method",
            ["Daily-linked", "Modified Dietz"],
            horizontal=True,
            help=(
                "**Daily-linked TWR** is the GIPS-compliant institutional standard, "
                "computed by chain-linking daily sub-period returns.  "
                "**Modified Dietz** is an approximation that weights cash flows by "
                "their time within the period — useful when daily valuations aren't "
                "available, but less precise when cash flows are large or volatile.  "
                "For a portfolio with one large initial deposit and no subsequent flows "
                "(this one currently), both methods produce nearly identical results."
            ),
        )
        st.caption(
            "For this single-flow portfolio, Daily-linked and Modified Dietz "
            "converge within 0 bps — both methods are shown for completeness."
        )
    method_key = "daily" if method == "Daily-linked" else "modified_dietz"

    def _benchmark_period_return(series: pd.Series, period: str) -> float:
        """Compute the return of a normalized benchmark series over the period."""
        last_ts  = series.index[-1]
        last_date = last_ts.date() if hasattr(last_ts, "date") else last_ts
        from datetime import date as _date, timedelta
        if period == "SI":
            start_ts = series.index[0]
        elif period == "1Y":
            start_ts = pd.Timestamp(last_date - timedelta(days=365))
        elif period == "YTD":
            start_ts = pd.Timestamp(_date(last_date.year, 1, 1))
        elif period == "3M":
            start_ts = pd.Timestamp(last_date - timedelta(days=90))
        elif period == "1M":
            start_ts = pd.Timestamp(last_date - timedelta(days=30))
        else:
            return 0.0
        sliced = series[series.index >= start_ts]
        if len(sliced) < 2:
            return 0.0
        return float(sliced.iloc[-1] / sliced.iloc[0] - 1)

    table_rows = []
    for p in PERIODS:
        pr = period_return(method_key, pv, cf, p)
        sr = _benchmark_period_return(sp, p)
        br = _benchmark_period_return(bl, p)
        table_rows.append({
            "":                "Portfolio",
            "Period":          PERIOD_LABEL[p],
            "Return":          _pct(pr),
            "_raw_port":       pr,
            "_raw_sp":         sr,
            "_raw_bl":         br,
        })

    # Build display dataframe
    display = {}
    for p in PERIODS:
        pr = period_return(method_key, pv, cf, p)
        sr = _benchmark_period_return(sp, p)
        br = _benchmark_period_return(bl, p)
        display[PERIOD_LABEL[p]] = {
            "Portfolio":       _pct(pr),
            "S&P 500":         _pct(sr),
            "Custom Blended":  _pct(br),
            "vs S&P 500":      _bps(pr - sr),
            "vs Blended":      _bps(pr - br),
        }

    tbl_df = pd.DataFrame(display).T
    tbl_df.index.name = "Period"
    styled = tbl_df.style.set_properties(
        subset=["vs S&P 500", "vs Blended"],
        **{"font-weight": "bold"},
    )
    st.dataframe(styled, width='stretch')

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 2b — Risk-adjusted metrics
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("### Risk-Adjusted Metrics")

    # Benchmark options for relative metrics (TE, IR, beta, active return)
    _RISK_BM_OPTIONS = {
        "Custom Blended SAA":        "blended",
        "S&P 500 (SPY)":             "spy",
        "60/40 (60% SPY / 40% AGG)": "60_40",
    }
    _risk_bm_sel = st.radio(
        "Compare against",
        list(_RISK_BM_OPTIONS.keys()),
        horizontal=True,
        key="risk_bm_sel",
        help=(
            "Tracking error, information ratio, beta, and active return recompute "
            "against the selected benchmark. Risk metrics (Std Dev, Sharpe, Sortino, "
            "Max DD, VaR, CVaR) are shown for both portfolio and benchmark."
        ),
    )
    _risk_bm_kind = _RISK_BM_OPTIONS[_risk_bm_sel]

    # Load the selected benchmark series (normalized to 1.0 for risk metric computation)
    _BM_ROW_LABELS = {
        "blended": "Custom Blended SAA",
        "spy":     "S&P 500 (SPY)",
        "60_40":   "60/40",
    }
    if _risk_bm_kind == "blended":
        _bl_for_metrics = bl / float(bl.iloc[0])
        _risk_bm_label  = "Custom Blended SAA"
    elif _risk_bm_kind == "spy":
        _bl_for_metrics = sp / float(sp.iloc[0])
        _risk_bm_label  = "S&P 500 (SPY)"
    else:
        _naive_60_40 = _load_naive_benchmark(start_val, "60_40")
        _bl_for_metrics = _naive_60_40 / float(_naive_60_40.iloc[0])
        _risk_bm_label  = "60/40 (60% SPY / 40% AGG)"
    _bm_row_label = _BM_ROW_LABELS[_risk_bm_kind]

    _m_si  = compute_risk_metrics(pv, _bl_for_metrics, window="SI")
    _m_1y  = compute_risk_metrics(pv, _bl_for_metrics, window="1Y")
    _m_ytd = compute_risk_metrics(pv, _bl_for_metrics, window="YTD")
    _m_3m  = compute_risk_metrics(pv, _bl_for_metrics, window="3M")
    _m_1m  = compute_risk_metrics(pv, _bl_for_metrics, window="1M")

    _RISK_WINDOW_LABELS = ["1 Month", "3 Months", "YTD", "1 Year", "Since Inception"]
    _RISK_WINDOW_MAP = {
        "1 Month":         _m_1m,
        "3 Months":        _m_3m,
        "YTD":             _m_ytd,
        "1 Year":          _m_1y,
        "Since Inception": _m_si,
    }

    def _fmt_ratio(v) -> str:
        return f"{v:.2f}" if v == v else "—"

    def _fmt_pct(v) -> str:
        return f"{v:.1f}%" if v == v else "—"

    if _m_si:
        _window_label = st.radio(
            "Window",
            _RISK_WINDOW_LABELS,
            index=4,
            horizontal=True,
            key="risk_metrics_window",
        )
        _m = _RISK_WINDOW_MAP.get(_window_label) or {}

        if not _m:
            st.caption(
                f"Insufficient data for {_window_label} window — requires ≥ 20 trading days."
            )
        else:
            # Row 1 — portfolio metrics
            st.caption("Portfolio")
            _c1, _c2, _c3, _c4, _c5, _c6 = st.columns(6)
            _c1.metric("Std Dev (ann.)", _fmt_pct(_m["annualized_vol_pct"]))
            _c2.metric("Sharpe",         _fmt_ratio(_m["sharpe"]))
            _c3.metric("Sortino",        _fmt_ratio(_m["sortino"]))
            _c4.metric("Max DD",         _fmt_pct(_m["max_drawdown_pct"]))
            _c5.metric("VaR (95%)",      _fmt_pct(_m["var_95_pct"]))
            _c6.metric("CVaR (95%)",     _fmt_pct(_m["cvar_95_pct"]))

            # Row 2 — benchmark metrics (same window and trading-day filter)
            st.caption(_bm_row_label)
            _b1, _b2, _b3, _b4, _b5, _b6 = st.columns(6)
            _b1.metric("Std Dev (ann.)", _fmt_pct(_m["bench_annualized_vol_pct"]))
            _b2.metric("Sharpe",         _fmt_ratio(_m["bench_sharpe"]))
            _b3.metric("Sortino",        _fmt_ratio(_m["bench_sortino"]))
            _b4.metric("Max DD",         _fmt_pct(_m["bench_max_drawdown_pct"]))
            _b5.metric("VaR (95%)",      _fmt_pct(_m["bench_var_95_pct"]))
            _b6.metric("CVaR (95%)",     _fmt_pct(_m["bench_cvar_95_pct"]))

            st.markdown("---")

            # Row 3 — benchmark-relative metrics
            st.caption(f"vs {_risk_bm_label}")
            _r1, _r2, _r3, _r4 = st.columns(4)
            _r1.metric("Track. Err",      _fmt_pct(_m["tracking_error_pct"]))
            _r2.metric("Info Ratio",       _fmt_ratio(_m["information_ratio"]))
            _r3.metric("Beta",             _fmt_ratio(_m["beta"]))
            _r4.metric("Active Ret (ann.)", _fmt_pct(_m["active_return_pct"]))

        st.caption(
            "Std Dev: annualized return volatility (trading days only, ddof=1). "
            "Sharpe and Sortino use RF = 4.5% (current cash yield). "
            "Benchmark metrics computed from the same return series used in the vs-benchmark "
            "statistics below, over the selected window. "
            f"Tracking error, information ratio, beta, and active return vs. {_risk_bm_label}. "
            "Max drawdown = peak-to-trough decline within the selected window. "
            "VaR(95%) = daily loss exceeded only 5% of trading days (historical simulation). "
            "CVaR(95%) = average daily loss on the worst 5% of trading days (Expected Shortfall). "
            "IR formula: (geometric annualized active return) / tracking error. "
            "Geometric annualization is used for consistency with the GIPS-linked TWR methodology "
            "(same compounding convention as the cumulative return chart above). "
            "Arithmetic annualization (mean daily active × 252) is the alternative institutional "
            "convention (CFA, GIPS IR supplement) and would yield a higher IR — arithmetic mean "
            "≥ geometric mean by Jensen's inequality."
        )
        _18mo_days = 548   # 18 × 30.44 calendar days
        _months_si = round(si_days / 30.44)
        _n_1m      = _m_1m.get("n_days", 0) if _m_1m else 0
        _n_3m      = _m_3m.get("n_days", 0) if _m_3m else 0

        _disc_parts = []
        if si_days < _18mo_days:
            _disc_parts.append(
                f"Inception period ({_months_si} months) overlaps substantially with the "
                "trailing 12 months. Max DD, TE, and IR will diverge from Since Inception "
                "once the portfolio crosses 18 months of history."
            )
        _disc_parts.append(
            f"Risk ratios at 1M and 3M windows reflect "
            f"{_n_1m or 21} and {_n_3m or 63} trading days respectively; "
            "interpret short-window Sharpe and Sortino as directional rather than "
            "statistically stable."
        )
        st.markdown("*" + " ".join(_disc_parts) + "*")

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 3 — Cumulative return chart
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("### Cumulative Return Since Inception")

    # Normalize all series to start at 1.0 for return comparison
    pv_norm = pv / float(pv.iloc[0])
    sp_norm = sp / float(sp.iloc[0])
    bl_norm = bl / float(bl.iloc[0])

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=pv_norm.index, y=(pv_norm - 1) * 100,
        name="Portfolio",
        line=dict(color=_PALETTE["portfolio"], width=2.5),
        hovertemplate="<b>Portfolio</b><br>%{x|%b %d, %Y}<br>Return: %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=sp_norm.index, y=(sp_norm - 1) * 100,
        name="S&P 500 (SPY)",
        line=dict(color=_PALETTE["sp500"], width=1.5, dash="dot"),
        hovertemplate="<b>S&P 500</b><br>%{x|%b %d, %Y}<br>Return: %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=bl_norm.index, y=(bl_norm - 1) * 100,
        name="Custom Blended",
        line=dict(color=_PALETTE["blended"], width=1.5, dash="dash"),
        hovertemplate="<b>Custom Blended</b><br>%{x|%b %d, %Y}<br>Return: %{y:.2f}%<extra></extra>",
    ))

    fig.update_layout(
        yaxis_title="Cumulative Return (%)",
        xaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=0, r=0, t=40, b=0),
        height=380,
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="sans-serif", size=12, color="#333"),
        yaxis=dict(gridcolor="#E8E8E8", zeroline=True, zerolinecolor="#CCCCCC",
                   dtick=10, ticksuffix="%"),
        xaxis=dict(gridcolor="#E8E8E8"),
    )
    st.plotly_chart(fig, width='stretch')

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 4 — Two-Stage Attribution + Brinson-Fachler decomposition
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("### Two-Stage Attribution")

    # Dynamic quarter label for the help text — sourced from the SAME
    # most_recent_reportable_quarter the quarterly report uses (single source of
    # truth), so it never goes stale and, in personal mode, never names a quarter
    # that predates inception.
    _rep_q_phrase = reportable_quarter_phrase(INCEPTION, date.fromisoformat(TODAY))
    bf_period = st.radio(
        "Attribution period",
        PERIODS,
        index=PERIODS.index("3M"),
        format_func=lambda p: PERIOD_LABEL[p],
        horizontal=True,
        key="bf_period",
        help=f"PDF quarterly report uses the most recent completed quarter {_rep_q_phrase}. "
             "Select SI to compare since-inception active return.",
    )

    _NAIVE_OPTIONS = {
        "60/40 (60% SPY / 40% AGG)": "60_40",
        "S&P 500 (SPY)": "spy",
    }
    _naive_sel = st.radio(
        "Naive benchmark",
        list(_NAIVE_OPTIONS.keys()),
        horizontal=True,
        key="naive_benchmark",
        help="Stage 1 measures the SAA design effect relative to this baseline.",
    )
    naive_kind   = _NAIVE_OPTIONS[_naive_sel]
    naive        = _load_naive_benchmark(start_val, naive_kind)
    _naive_label = (
        "60/40 naive baseline (60% SPY, 40% AGG)"
        if naive_kind == "60_40"
        else "S&P 500 baseline (SPY total return)"
    )
    _naive_short = "60/40" if naive_kind == "60_40" else "S&P 500"

    st.caption(
        "**What the two stages measure.** Stage 1 (SAA design) captures the strategic-tilt "
        f"contribution of the SAA itself — the value allocation (VTV at {_saa_sleeves.get('US Large Value', 0.09 / 0.98)*100:.0f}%), small-cap value "
        f"(AVUV at {_saa_sleeves.get('US Small Cap', 0.08 / 0.98)*100:.0f}%), emerging markets ({_saa_sleeves.get('Emerging Markets', 0.09 / 0.98)*100:.0f}%), "
        f"real assets ({_saa_sleeves.get('Real Assets', 0.10 / 0.98)*100:.0f}%), TIPS ({_saa_sleeves.get('TIPS', 0.04 / 0.98)*100:.0f}%), and the overall "
        f"~{_saa_parents.get('Equity', 0.78 / 0.98)*100:.0f}/{_non_eq_pct*100:.0f} equity-vs-other-assets risk posture — measured as the SAA-blended benchmark's "
        f"return spread over a {_naive_label}. This isolates what the "
        "allocation thesis itself contributed, separate from execution. Stage 2 (Implementation, "
        "decomposed via Brinson-Fachler below) captures two effects relative to the SAA's sleeve "
        "targets: **allocation effect** — over/underweights from SAA targets, primarily driven by "
        "drift since this portfolio is not rebalanced intra-quarter; and **selection effect** — the "
        "chosen ETF return vs. the sleeve benchmark return, typically near-zero for passive ETF "
        f"holdings. Stage 1 + Stage 2 = Portfolio return vs. {_naive_short} (algebra-checked in the summary tiles). The "
        "Factor Profile page provides an independent factor-loading view of the same strategic tilts "
        "(HML, SMB, RMW, CMA loadings on the US sleeve regression)."
    )

    with st.spinner("Computing attribution…"):
        bf_df = _load_attribution(bf_period)

    # ── Stage 1 + Stage 2 tiles ─────────────────────────────────────────────────────
    if not bf_df.empty:
        # BF-internal returns (price-appreciation only; used for BF chart detail)
        _r_p_bf  = float((bf_df["w_p"] * bf_df["r_p"]).sum())
        _r_b_bf  = float((bf_df["w_b"] * bf_df["r_b"]).sum())
        # Price-series returns (total return incl. dividends; drives Stage 1/2 tiles)
        _r_p_ps  = _benchmark_period_return(pv, bf_period)
        _r_b_ps  = _r_b_bf  # target weights x period returns; matches BF decomposition
        _naive_r = _benchmark_period_return(naive, bf_period)

        _ts = compute_two_stage_attribution(
            port_return              = _r_p_ps,
            saa_return               = _r_b_ps,
            naive_return             = _naive_r,
            sleeve_saa_weights       = dict(zip(bf_df["sleeve"], bf_df["w_b"])),
            sleeve_benchmark_returns = dict(zip(bf_df["sleeve"], bf_df["r_b"])),
        )

        st.caption(
            f"Window: {PERIOD_LABEL[bf_period]}. "
            f"Stages 1 and 2 are computed over the same window and sum to total active "
            f"return vs. the {_naive_label}."
        )

        _ts1_bps = _ts["stage1"] * 10_000
        _ts2_bps = _ts["stage2"] * 10_000
        _tot_bps = _ts["total"]  * 10_000
        _sign = lambda v: "+" if v >= 0 else ""

        _tc1, _tc2, _tc3 = st.columns(3)
        _tc1.metric(
            "Stage 1: SAA Design",
            f"{_sign(_ts1_bps)}{_ts1_bps:.0f} bps",
            "SAA blend vs. 60/40",
            delta_color="off",
        )
        _tc2.metric(
            "Stage 2: Implementation",
            f"{_sign(_ts2_bps)}{_ts2_bps:.0f} bps",
            "Portfolio vs. SAA blend",
            delta_color="off",
        )
        _tc3.metric(
            f"Total: Portfolio vs. {_naive_short}",
            f"{_sign(_tot_bps)}{_tot_bps:.0f} bps",
            "Stage 1 + Stage 2",
            delta_color="off",
        )

        # Stage1+Stage2=Total by construction (price-series throughout); residual is floating-point only
        _resid_bps = _ts["algebra_residual"] * 10_000
        st.caption(
            f"Reconciliation: {_sign(_ts1_bps)}{_ts1_bps:.0f} + "
            f"{_sign(_ts2_bps)}{_ts2_bps:.0f} = {_sign(_tot_bps)}{_tot_bps:.0f} bps "
            f"(price-series methodology; algebra residual: {_resid_bps:.2f} bps). "
            f"✓ reconciled"
        )

        # ── Stage 1 sleeve bar chart ─────────────────────────────────────────────────────
        _per_sleeve = _ts["per_sleeve"]
        _sleeve_order = sorted(_per_sleeve, key=lambda s: abs(_per_sleeve[s]), reverse=True)
        _sleeve_vals_bps = [_per_sleeve[s] * 10_000 for s in _sleeve_order]
        _bar_colors = [
            _PALETTE["alloc"] if v >= 0 else _PALETTE["selection"]
            for v in _sleeve_vals_bps
        ]

        _fig_s1 = go.Figure()
        _fig_s1.add_trace(go.Bar(
            name="SAA design contribution",
            y=_sleeve_order,
            x=_sleeve_vals_bps,
            orientation="h",
            marker_color=_bar_colors,
            hovertemplate="%{y}: %{x:.1f} bps<extra></extra>",
        ))
        for _si, (_slv, _val) in enumerate(zip(_sleeve_order, _sleeve_vals_bps)):
            _fig_s1.add_annotation(
                x=_val + (3 if _val >= 0 else -3),
                y=_slv,
                text=f"{_sign(_val)}{_val:.0f}",
                showarrow=False,
                font=dict(size=10, color="#555"),
                xanchor="left" if _val >= 0 else "right",
            )
        _fig_s1.update_layout(
            xaxis_title="Basis Points",
            yaxis_title=None,
            showlegend=False,
            margin=dict(l=0, r=80, t=20, b=0),
            height=380,
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(family="sans-serif", size=11, color="#333"),
            yaxis=dict(autorange="reversed"),
            xaxis=dict(gridcolor="#E8E8E8", zeroline=True, zerolinecolor="#888"),
        )
        st.plotly_chart(_fig_s1, width='stretch')

        _sleeve_sum_bps = sum(_sleeve_vals_bps)
        st.caption(
            f"Stage 1 sleeve contributions sum to {_sign(_sleeve_sum_bps)}{_sleeve_sum_bps:.0f} bps "
            f"(= Stage 1 {_sign(_ts1_bps)}{_ts1_bps:.0f} bps). "
            f"Each sleeve\u2019s contribution = SAA target weight \u00d7 (sleeve benchmark return \u2212 {_naive_short} return)."
        )

        st.caption(
            "Stage 2 implementation effect (drift + selection) is decomposed in the "
            "Brinson-Fachler chart below."
        )

    # ── Brinson-Fachler decomposition (Stage 2 detail) ──────────────────────────────────
    if bf_df.empty:
        st.info("Not enough data for the selected period.")
    else:
        bf_chart_col, bf_table_col = st.columns([4, 5])

        # — Horizontal stacked bar chart —
        with bf_chart_col:
            sleeves_sorted = bf_df.sort_values("total_effect")["sleeve"].tolist()
            alloc_vals = [
                bf_df.loc[bf_df["sleeve"] == s, "allocation_effect"].iloc[0] * 10_000
                for s in sleeves_sorted
            ]
            sel_vals = [
                bf_df.loc[bf_df["sleeve"] == s, "selection_effect"].iloc[0] * 10_000
                for s in sleeves_sorted
            ]
            total_vals = [a + b for a, b in zip(alloc_vals, sel_vals)]

            fig2 = go.Figure()
            fig2.add_trace(go.Bar(
                name="Allocation",
                y=sleeves_sorted, x=alloc_vals,
                orientation="h",
                marker_color=_PALETTE["alloc"],
                hovertemplate="Allocation: %{x:.1f} bps<extra></extra>",
            ))
            fig2.add_trace(go.Bar(
                name="Selection",
                y=sleeves_sorted, x=sel_vals,
                orientation="h",
                marker_color=_PALETTE["selection"],
                hovertemplate="Selection: %{x:.1f} bps<extra></extra>",
            ))

            # Annotate total effect per sleeve
            for i, (sleeve, total) in enumerate(zip(sleeves_sorted, total_vals)):
                sign = "+" if total >= 0 else ""
                fig2.add_annotation(
                    x=max(alloc_vals[i] + sel_vals[i], 0) + 2,
                    y=sleeve,
                    text=f"{sign}{total:.0f}",
                    showarrow=False,
                    font=dict(size=10, color="#555"),
                    xanchor="left",
                )

            fig2.update_layout(
                barmode="stack",
                xaxis_title="Basis Points",
                yaxis_title=None,
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="left", x=0),
                margin=dict(l=0, r=60, t=40, b=0),
                height=400,
                plot_bgcolor="white",
                paper_bgcolor="white",
                font=dict(family="sans-serif", size=11, color="#333"),
                yaxis=dict(autorange=True),
                xaxis=dict(gridcolor="#E8E8E8", zeroline=True,
                           zerolinecolor="#888"),
            )
            st.plotly_chart(fig2, width='stretch')

        # — Attribution table —
        with bf_table_col:
            tbl_data = []
            for _, row in bf_df.sort_values("total_effect", ascending=False).iterrows():
                tbl_data.append({
                    "Sleeve":       row["sleeve"],
                    "Port Wt":      row["w_p"] * 100,
                    "Bench Wt":     row["w_b"] * 100,
                    "Port Ret":     row["r_p"] * 100,
                    "Bench Ret":    row["r_b"] * 100,
                    "Alloc (bps)":  row["allocation_effect"] * 10_000,
                    "Sel (bps)":    row["selection_effect"] * 10_000,
                    "Total (bps)":  row["total_effect"] * 10_000,
                })
            st.dataframe(
                pd.DataFrame(tbl_data),
                hide_index=True,
                width='stretch',
                column_config={
                    "Port Wt":     st.column_config.NumberColumn("Port Wt",     format="%.1f%%"),
                    "Bench Wt":    st.column_config.NumberColumn("Bench Wt",    format="%.1f%%"),
                    "Port Ret":    st.column_config.NumberColumn("Port Ret",    format="%.2f%%"),
                    "Bench Ret":   st.column_config.NumberColumn("Bench Ret",   format="%.2f%%"),
                    "Alloc (bps)": st.column_config.NumberColumn("Alloc (bps)", format="%+.1f"),
                    "Sel (bps)":   st.column_config.NumberColumn("Sel (bps)",   format="%+.1f"),
                    "Total (bps)": st.column_config.NumberColumn("Total (bps)", format="%+.1f"),
                },
            )

        # — Algebra summary (Phase 38b-2: ex-cash strategic active + operational cash drag) —
        # BF weights are ex-cash (9 strategic sleeves sum to 1.0); the operational
        # SPAXX float is broken out as an explicit drag term, NOT erased. The total
        # active and reported TWR are unchanged — only the decomposition is new:
        #   strategic active (ex-cash) + cash drag = total active vs the SAA blend.
        sum_effects    = bf_df["total_effect"].sum() * 10_000
        r_p_total      = (bf_df["w_p"] * bf_df["r_p"]).sum() * 100   # ex-cash strategic return
        r_b_total      = (bf_df["w_b"] * bf_df["r_b"]).sum() * 100   # SAA blend
        ex_cash_active = r_p_total - r_b_total                        # strategic active (ex-cash)
        cash_drag_pct  = float(bf_df.attrs.get("cash_drag", 0.0)) * 100
        total_active   = ex_cash_active + cash_drag_pct              # = actual active (incl cash)
        reconciled     = abs(sum_effects - ex_cash_active * 100) < 1.0

        # Bridge: strategic active (ex-cash) + cash drag must equal the actual
        # portfolio active (Stage 2 basis) within 0.5 bps — keeps the reconciliation ✓.
        _bf_s2_gap_bps = (total_active / 100 - (_r_p_ps - _r_b_ps)) * 10_000
        _bf_reconciled = abs(_bf_s2_gap_bps) < 0.5
        st.caption(
            f"**BF decomposition:**  "
            f"Strategic active (ex-cash): {ex_cash_active:+.2f}%  &nbsp;·&nbsp;  "
            f"Operational cash drag: {cash_drag_pct:+.2f}%  &nbsp;·&nbsp;  "
            f"Total active vs SAA blend: {total_active:+.2f}%  &nbsp;·&nbsp;  "
            f"Sum of effects: {sum_effects:+.1f} bps  &nbsp;·&nbsp;  "
            f"Algebra check: {'✓' if reconciled else '⚠'}  &nbsp;·&nbsp;  "
            f"vs. Stage 2: {'✓' if _bf_reconciled else '⚠'} {_bf_s2_gap_bps:+.2f} bps"
        )

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 5 — Drift analysis
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("### Drift Analysis")

    sw, band_map = _load_drift()

    if sw.empty:
        st.info("No holdings found.")
    else:
        # Actual vs. target allocation bar chart
        _sleeves_ch = sw.index.tolist()
        _fig_alloc = go.Figure()
        # Trace order matches legend and z-order: Band (bottom) → Target → Actual (top)
        _fig_alloc.add_trace(go.Bar(
            name="Tolerance Band",
            y=_sleeves_ch,
            x=[band_map.get(s, 0.03) * 2 * 100 for s in _sleeves_ch],
            base=[(sw.loc[s, "Target Weight"] - band_map.get(s, 0.03)) * 100
                  for s in _sleeves_ch],
            orientation="h",
            marker_color="rgba(91, 127, 166, 0.15)",
            marker_line=dict(width=0),
            showlegend=True,
        ))
        _fig_alloc.add_trace(go.Bar(
            name="Target",
            y=_sleeves_ch,
            x=(sw["Target Weight"] * 100).tolist(),
            orientation="h",
            marker_color=_PALETTE["sp500"],
            opacity=0.65,
        ))
        _fig_alloc.add_trace(go.Bar(
            name="Actual",
            y=_sleeves_ch,
            x=(sw["Actual Weight"] * 100).tolist(),
            orientation="h",
            marker_color=_PALETTE["portfolio"],
        ))
        _fig_alloc.update_layout(
            barmode="overlay",
            xaxis_title="Weight (%)",
            yaxis_title=None,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=0, r=0, t=40, b=0),
            height=380,
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(family="sans-serif", size=11, color="#333"),
            xaxis=dict(gridcolor="#E8E8E8"),
        )
        st.plotly_chart(_fig_alloc, width='stretch')

        # Source drift from the canonical compute_drift helper (shared with the
        # Capital Deployment page) instead of recomputing inline. The display
        # layer below — ✓/⚠ status, abs-drift sort, bps/%% formatting — is
        # derived from its output and is unchanged.
        drift_df = compute_drift(
            sw["Actual Weight"].to_dict(),
            sw["Target Weight"].to_dict(),
            band_map,
        )
        outside_band_count = int((~drift_df["In Band"]).sum())

        drift_rows = []
        for sleeve, row in drift_df.iterrows():
            band = row["Band"]
            drift_rows.append({
                "Sleeve":        sleeve,
                "Target":        row["Target Weight"] * 100,
                "Actual":        row["Actual Weight"] * 100,
                "Drift (bps)":   row["Drift"] * 10_000,
                "Band (±bps)":   f"±{band*10000:.0f}",
                "Status":        "✓ Within" if row["In Band"] else "⚠ Outside",
            })

        # Sort by absolute drift descending
        drift_rows.sort(key=lambda r: abs(r["Drift (bps)"]), reverse=True)
        st.dataframe(
            pd.DataFrame(drift_rows),
            hide_index=True,
            width='stretch',
            column_config={
                "Target":      st.column_config.NumberColumn("Target",      format="%.1f%%"),
                "Actual":      st.column_config.NumberColumn("Actual",      format="%.1f%%"),
                "Drift (bps)": st.column_config.NumberColumn("Drift (bps)", format="%+.0f"),
            },
        )
        st.caption(
            f"Rebalance candidates: **{outside_band_count}** sleeve"
            f"{'s' if outside_band_count != 1 else ''} outside tolerance band"
        )

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Task 5 — Methodology validation expander
    # ──────────────────────────────────────────────────────────────────────
    with st.expander("Methodology validation"):
        if si_days < 365:
            st.info(
                f"**1Y window note:** Portfolio has {si_days} days of history (inception "
                f"{INCEPTION}). The 1Y return period is capped at inception and equals the "
                "Since Inception return. 1Y and SI windows will diverge once the portfolio "
                "crosses one year."
            )
        pv2, cf2 = _load_portfolio()
        daily_si  = period_return("daily",          pv2, cf2, "SI")
        dietz_si  = period_return("modified_dietz", pv2, cf2, "SI")
        spread_bps = abs(daily_si - dietz_si) * 10_000

        st.markdown(f"**Daily-linked TWR (SI):** {_pct(daily_si)}")
        st.markdown(f"**Modified Dietz (SI):**   {_pct(dietz_si)}")
        st.markdown(
            f"**Method spread:** {spread_bps:.2f} bps — "
            + ("negligible (expected: single initial flow, no subsequent CFs)"
               if spread_bps < 5 else "⚠ unexpectedly large, check CF data")
        )
        st.markdown("---")

        if not bf_df.empty:
            sum_eff  = bf_df["total_effect"].sum() * 10_000
            r_p_chk  = (bf_df["w_p"] * bf_df["r_p"]).sum() * 10_000
            r_b_chk  = (bf_df["w_b"] * bf_df["r_b"]).sum() * 10_000
            active_chk = r_p_chk - r_b_chk
            disc_chk   = abs(sum_eff - active_chk)
            st.markdown(
                f"**BF algebra check ({PERIOD_LABEL[bf_period]}):** "
                f"Σeffects = {sum_eff:+.2f} bps; "
                f"R_p − R_b = {active_chk:+.2f} bps; "
                f"discrepancy = {disc_chk:.3f} bps "
                + ("✓" if disc_chk < 1 else "⚠")
            )
            st.markdown("---")


    st.divider()

    # ── Fixed Income Effective Duration ───────────────────────────────────────
    st.subheader("Fixed Income Effective Duration")
    dur      = get_effective_duration(TODAY)
    fi_dur   = dur["fi_sleeve_duration"]
    agg_dur  = dur["agg_benchmark"]
    fi_wt    = dur["fi_weight_pct"]
    cash_wt  = dur["cash_weight_pct"]
    delta_yr  = round(fi_dur - agg_dur, 1)
    dur_diff  = abs(fi_dur - agg_dur)
    if dur_diff < 0.05:
        dur_vs_caption = "in line with the Bloomberg US Agg benchmark"
    else:
        vs_agg = "below" if delta_yr < 0 else "above"
        dur_vs_caption = f"{abs(delta_yr):.1f} yrs {vs_agg} the Bloomberg US Agg benchmark"
    st.metric(
        label="FI Sleeve Duration (Core FI + TIPS)",
        value=f"{fi_dur} yrs",
        delta=f"{delta_yr:+.1f} yrs vs Bloomberg US Agg ({agg_dur} yrs)",
        help=(
            "Weighted average duration of Core Fixed Income (VGIT) and TIPS (SCHP) only. "
            "Cash/SPAXX is excluded — it carries zero duration and is not in the Bloomberg Agg."
        ),
    )
    st.caption(
        f"FI weight (Core FI + TIPS): {fi_wt}% of portfolio. "
        f"Cash/SPAXX: {cash_wt}% (excluded from duration calculation and from Bloomberg Agg). "
        f"FI sleeve duration is {dur_vs_caption}. "
        "Duration also flows through equity via discount-rate effects — it's a whole-portfolio consideration. "
        "Duration sourced from ETF fact-sheet values (VGIT: 5.5 yrs, SCHP: 6.8 yrs per Vanguard/Schwab Q1 2026)."
    )
    render_footer()
