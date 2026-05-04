"""Performance & Attribution page."""
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.attribution import brinson_fachler_period
from src.benchmarks import get_custom_blended_series, get_sp500_series
from src.db import get_connection
from src.holdings import get_portfolio_value_series, get_sleeve_weights_on_date
from src.reports import generate_quarterly_report
from src.returns import annualize, period_return, twr_daily_linked

st.set_page_config(page_title="Performance & Attribution", layout="wide")

INCEPTION    = "2025-05-01"
TODAY        = "2026-05-01"
PERIODS      = ["1M", "3M", "YTD", "1Y", "SI"]
PERIOD_LABEL = {"1M": "1 Month", "3M": "3 Months", "YTD": "YTD",
                "1Y": "1 Year", "SI": "Since Inception"}

_PALETTE = {
    "portfolio": "#2E4057",   # deep navy
    "sp500":     "#8C9AA6",   # slate gray
    "blended":   "#5C7A5C",   # muted sage
    "alloc":     "#5B7FA6",   # steel blue
    "selection": "#A67B5B",   # warm tan
}


# ── Cached data loaders ───────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _load_portfolio():
    pv = get_portfolio_value_series(INCEPTION, TODAY)
    cf = pd.Series(0.0, index=pv.index)
    return pv, cf


@st.cache_data(ttl=3600, show_spinner=False)
def _load_benchmarks(start_val: float):
    sp  = get_sp500_series(INCEPTION, TODAY)      * start_val
    bl  = get_custom_blended_series(INCEPTION, TODAY) * start_val
    return sp, bl


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
def _load_drift():
    sw = get_sleeve_weights_on_date(TODAY)
    with get_connection() as conn:
        bands = conn.execute(
            "SELECT name, tolerance_band FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
    band_map = {r["name"]: r["tolerance_band"] for r in bands}
    return sw, band_map


def _date_offset(iso: str, days: int) -> str:
    from datetime import timedelta
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def _most_recent_completed_quarter():
    """Return (start_iso, end_iso, label) for the most recently completed quarter.

    Start = last trading day of the prior quarter (conventional quarterly return
    definition: Q1 return = Mar-31 close / Dec-31 prior-year close − 1).
    Using Jan-1 as Q1 start fails because Jan-1 is a US market holiday; the
    benchmark pipeline would bfill from Jan-2, creating a spurious shift vs the
    correct Dec-31 base price.
    """
    today = date.today()
    quarters = [
        (date(today.year - 1, 12, 31), date(today.year, 3, 31),  f"Q1 {today.year}"),
        (date(today.year, 3, 31),       date(today.year, 6, 30),  f"Q2 {today.year}"),
        (date(today.year, 6, 30),       date(today.year, 9, 30),  f"Q3 {today.year}"),
        (date(today.year, 9, 30),       date(today.year, 12, 31), f"Q4 {today.year}"),
    ]
    for q_start, q_end, q_label in reversed(quarters):
        if q_end < today:
            return q_start.isoformat(), q_end.isoformat(), q_label
    return f"{today.year-1}-09-30", f"{today.year-1}-12-31", f"Q4 {today.year-1}"


def _pct(v: float, decimals: int = 2) -> str:
    return f"{v * 100:.{decimals}f}%"


def _bps(v: float) -> str:
    b = v * 10_000
    sign = "+" if b >= 0 else ""
    return f"{sign}{b:.0f} bps"


# ── Page ─────────────────────────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:

    st.markdown("## Performance & Attribution")
    st.caption(
        "Time-weighted return, benchmarking, and Brinson-Fachler decomposition."
    )

    # Load data
    with st.spinner("Loading performance data…"):
        pv, cf = _load_portfolio()

    # ── Generate Report expander ──────────────────────────────────────────
    with st.expander("Generate Quarterly Report", expanded=False):
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

        if _period_choice == "Most recent completed quarter":
            _r_start, _r_end, _r_qlabel = _most_recent_completed_quarter()
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

        if st.button("Generate Report", type="primary", key="gen_report_btn"):
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

    # Key scalars (Since Inception)
    si_days     = (pd.Timestamp(TODAY) - pd.Timestamp(INCEPTION)).days
    port_si     = period_return("daily", pv, cf, "SI")
    sp500_si    = float(sp.iloc[-1] / sp.iloc[0] - 1)
    blended_si  = float(bl.iloc[-1] / bl.iloc[0] - 1)
    alpha_sp    = port_si - sp500_si
    alpha_bl    = port_si - blended_si
    ytd_return  = period_return("daily", pv, cf, "YTD")
    current_val = float(pv.iloc[-1])

    # ── Summary banner ────────────────────────────────────────────────────
    st.markdown(
        f"**${current_val:,.0f}** current value &nbsp;·&nbsp; "
        f"**{si_days}** day inception period &nbsp;·&nbsp; "
        f"**{port_si*100:.1f}%** cumulative TWR &nbsp;·&nbsp; "
        f"**{_bps(alpha_sp)}** vs S&P 500",
        unsafe_allow_html=False,
    )

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 1 — Headline metrics
    # ──────────────────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)

    inception_delta_pct = f"{port_si*100:+.1f}% since inception"
    m1.metric("Current Value",      f"${current_val:,.0f}", inception_delta_pct)
    m2.metric("vs. S&P 500",        _bps(alpha_sp),
              f"SI: {_pct(sp500_si)} S&P 500",
              delta_color="off")
    m3.metric("vs. Custom Blended", _bps(alpha_bl),
              f"SI: {_pct(blended_si)} blended",
              delta_color="off")
    m4.metric("YTD Return",         _pct(ytd_return),
              f"Since: {_pct(port_si)}")

    st.caption(
        "Underperformance vs. S&P 500 reflects intentional diversification: "
        "28% of the portfolio is non-equity (Income + Real Assets + Cash), "
        "27% is non-US equity. The Custom Blended benchmark — a target-weighted "
        "basket of cap-weighted indices in the same SAA — is the more meaningful "
        "comparison for security selection alpha."
    )

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 2 — TWR method toggle + period returns table
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("#### Period Returns")

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
    st.dataframe(tbl_df, width='stretch')

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 3 — Cumulative return chart
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("#### Cumulative Return Since Inception")

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
        height=360,
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
    # Section 4 — Brinson-Fachler attribution
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("#### Brinson-Fachler Attribution")

    bf_period = st.radio(
        "Attribution period",
        PERIODS,
        index=PERIODS.index("SI"),
        format_func=lambda p: PERIOD_LABEL[p],
        horizontal=True,
        key="bf_period",
    )

    with st.spinner("Computing attribution…"):
        bf_df = _load_attribution(bf_period)

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
                height=360,
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
                    "Sleeve":        row["sleeve"],
                    "Port Wt":       f"{row['w_p']*100:.1f}%",
                    "Bench Wt":      f"{row['w_b']*100:.1f}%",
                    "Port Ret":      _pct(row["r_p"]),
                    "Bench Ret":     _pct(row["r_b"]),
                    "Alloc (bps)":   f"{row['allocation_effect']*10000:+.1f}",
                    "Sel (bps)":     f"{row['selection_effect']*10000:+.1f}",
                    "Total (bps)":   f"{row['total_effect']*10000:+.1f}",
                })
            st.dataframe(pd.DataFrame(tbl_data), hide_index=True,
                         width='stretch')

        # — Algebra summary —
        sum_effects   = bf_df["total_effect"].sum() * 10_000
        r_p_total     = (bf_df["w_p"] * bf_df["r_p"]).sum() * 100
        r_b_total     = (bf_df["w_b"] * bf_df["r_b"]).sum() * 100
        active        = r_p_total - r_b_total
        reconciled    = abs(sum_effects - active * 100) < 1.0

        st.caption(
            f"**Total active return:** {active:+.2f}%  &nbsp;|&nbsp; "
            f"Portfolio: {r_p_total:.2f}%  &nbsp;·&nbsp; "
            f"Blended benchmark: {r_b_total:.2f}%  &nbsp;·&nbsp; "
            f"Sum of effects: {sum_effects:+.1f} bps  &nbsp;·&nbsp; "
            f"Algebra check: {'✓ reconciled' if reconciled else '⚠ discrepancy'}"
        )

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Section 5 — Drift analysis
    # ──────────────────────────────────────────────────────────────────────
    st.markdown("#### Drift Analysis")

    sw, band_map = _load_drift()

    if sw.empty:
        st.info("No holdings found.")
    else:
        # Actual vs. target allocation bar chart
        _sleeves_ch = sw.index.tolist()
        _fig_alloc = go.Figure()
        _fig_alloc.add_trace(go.Bar(
            name="Actual",
            y=_sleeves_ch,
            x=(sw["Actual Weight"] * 100).tolist(),
            orientation="h",
            marker_color=_PALETTE["portfolio"],
        ))
        _fig_alloc.add_trace(go.Bar(
            name="Target",
            y=_sleeves_ch,
            x=(sw["Target Weight"] * 100).tolist(),
            orientation="h",
            marker_color=_PALETTE["sp500"],
            opacity=0.65,
        ))
        _fig_alloc.update_layout(
            barmode="overlay",
            xaxis_title="Weight (%)",
            yaxis_title=None,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=0, r=0, t=40, b=0),
            height=320,
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(family="sans-serif", size=11, color="#333"),
            xaxis=dict(gridcolor="#E8E8E8"),
        )
        st.plotly_chart(_fig_alloc, width='stretch')

        drift_rows = []
        outside_band_count = 0
        for sleeve, row in sw.iterrows():
            target  = row["Target Weight"]
            actual  = row["Actual Weight"]
            drift   = row["Drift"]
            band    = band_map.get(sleeve, 0.03)
            outside = abs(drift) > band
            if outside:
                outside_band_count += 1
            drift_rows.append({
                "Sleeve":        sleeve,
                "Target":        f"{target*100:.1f}%",
                "Actual":        f"{actual*100:.1f}%",
                "Drift (bps)":   f"{drift*10000:+.0f}",
                "Band (±bps)":   f"±{band*10000:.0f}",
                "Status":        "⚠ Outside" if outside else "✓ Within",
            })

        # Sort by absolute drift descending
        drift_rows.sort(key=lambda r: abs(int(r["Drift (bps)"].replace("+", ""))), reverse=True)
        st.dataframe(pd.DataFrame(drift_rows), hide_index=True, width='stretch')
        st.caption(
            f"Rebalance candidates: **{outside_band_count}** sleeve"
            f"{'s' if outside_band_count != 1 else ''} outside tolerance band"
        )

    st.divider()

    # ──────────────────────────────────────────────────────────────────────
    # Task 5 — Methodology validation expander
    # ──────────────────────────────────────────────────────────────────────
    with st.expander("Methodology validation"):
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

        conn2 = get_connection()
        n_days = conn2.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        last_refresh = conn2.execute(
            "SELECT MAX(price_date) FROM prices"
        ).fetchone()[0]
        conn2.close()
        st.markdown(f"**Price cache rows:** {n_days:,}")
        st.markdown(f"**Last price date in cache:** {last_refresh}")
