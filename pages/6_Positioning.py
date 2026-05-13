"""Active Positioning — tilts, effective duration, scenario analysis."""
import streamlit as st

st.set_page_config(page_title="Active Positioning", layout="wide")

from src.asof import as_of_banner
from src.config import get_demo_banner_text, IS_DEMO
from src.ui_helpers import render_footer, render_sidebar_footer
from src.positioning import (
    build_style_box_figure,
    get_active_tilts,
    get_effective_duration,
    get_non_us_equity_data,
    get_scenario_triggers,
    get_style_box_data,
)
from src.style_box import STYLE_BOX_CAPTION

if IS_DEMO:
    st.info(get_demo_banner_text())

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Active Positioning")
    st.caption(
        "Auto-derived from current sleeve weights vs SAA targets. "
        "No hand-written text — updates every quarter as the portfolio evolves."
    )
    st.caption(as_of_banner())
    st.divider()

    from datetime import date
    end_date = date.today().isoformat()

    # ── Block A: Active Tilts ────────────────────────────────────────────────
    st.subheader("Active Tilts")
    tilts = get_active_tilts(end_date)

    if tilts:
        st.caption(
            "Sleeves with drift ≥50 bps absolute **or** ≥10% of target weight, "
            "sorted by absolute drift."
        )
        for t in tilts:
            direction_color = "🟢" if t["direction"] == "overweight" else "🔴"
            st.markdown(
                f"{direction_color} **{t['sleeve']}** — "
                f"{t['direction']} vs target "
                f"({t['actual_pct']:.1f}% vs {t['target_pct']:.1f}%, "
                f"Δ {t['drift_bps']:+.0f} bps) — *{t['descriptor']}*"
            )
    else:
        st.info("All sleeves within tolerance — no meaningful active tilts.")

    st.divider()

    # ── Block B: Fixed Income Effective Duration ─────────────────────────────
    st.subheader("Fixed Income Effective Duration")
    dur      = get_effective_duration(end_date)
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
        help="Weighted average duration of Core Fixed Income (VGIT) and TIPS (SCHP) only. "
             "Cash/SPAXX is excluded — it carries zero duration and is not in the Bloomberg Agg.",
    )
    st.caption(
        f"FI weight (Core FI + TIPS): {fi_wt}% of portfolio. "
        f"Cash/SPAXX: {cash_wt}% (excluded from duration calculation and from Bloomberg Agg). "
        f"FI sleeve duration is {dur_vs_caption}. "
        "Duration also flows through equity via discount-rate effects — it's a whole-portfolio consideration. "
        "Duration sourced from ETF fact-sheet values (VGIT: 5.5 yrs, SCHP: 6.8 yrs per Vanguard/Schwab Q1 2026)."
    )

    st.divider()

    # ── Block C: Scenarios That Benefit This Positioning ────────────────────
    st.subheader("Scenarios That Benefit This Positioning")
    scenarios = get_scenario_triggers(end_date)

    if scenarios:
        for s in scenarios:
            with st.expander(s["name"], expanded=True):
                st.write(s["text"])
    else:
        st.info("No scenario triggers based on current positioning.")

    st.divider()

    # ── Block D: Equity Style Profile ───────────────────────────────────────
    st.subheader("Equity Style Profile")
    style_data = get_style_box_data(end_date)
    non_us     = get_non_us_equity_data(end_date)

    if style_data:
        fig = build_style_box_figure(style_data)
        box_col, _ = st.columns([3, 2])
        with box_col:
            st.plotly_chart(fig, width='stretch')
        st.caption(STYLE_BOX_CAPTION)
        st.caption(
            "Empty cells reflect deliberate construction: the SAA does not include mid-cap or "
            "pure-growth tilts. Quality (SPHQ) and value (VTV, AVUV) are the chosen factor exposures."
        )

        if non_us:
            st.markdown("**Non-US Equity Sleeve**")
            for item in non_us:
                st.markdown(
                    f"- **{item['ticker']}** ({item['region_label']}) — "
                    f"{item['weight_pct']:.1f}% of portfolio"
                )
            st.caption(
                "Non-US holdings are not directly comparable to US value/growth and "
                "market-cap distributions. See Morningstar regional style boxes for "
                "international placement methodology."
            )
    else:
        st.info("No equity holdings found.")
    render_footer()
render_sidebar_footer()
