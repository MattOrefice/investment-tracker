"""Active Positioning — tilts, effective duration, scenario analysis."""
import streamlit as st

from src.config import IS_DEMO
from src.positioning import (
    get_active_tilts,
    get_effective_duration,
    get_scenario_triggers,
)

st.set_page_config(page_title="Active Positioning", layout="wide")

if IS_DEMO:
    st.info(
        "**Demo mode** — data reflects a paper-trade portfolio. "
        "Positioning analysis derives automatically from live portfolio state."
    )

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Active Positioning")
    st.caption(
        "Auto-derived from current sleeve weights vs SAA targets. "
        "No hand-written text — updates every quarter as the portfolio evolves."
    )
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

    # ── Block B: FI Sleeve Effective Duration ───────────────────────────────
    st.subheader("FI Sleeve Effective Duration")
    dur = get_effective_duration(end_date)
    fi_dur   = dur["fi_sleeve_duration"]
    agg_dur  = dur["agg_benchmark"]
    delta_yr = round(fi_dur - agg_dur, 1)
    st.metric(
        label="FI Sleeve Duration",
        value=f"{fi_dur} yrs",
        delta=f"{delta_yr:+.1f} yrs vs Bloomberg US Agg ({agg_dur} yrs)",
        help="Weighted average duration of the FI sleeve (Core Fixed Income, TIPS, Cash), "
             "using actual sleeve weights only — not diluted by equity.",
    )
    st.caption(
        f"FI weight: {dur['fi_weight_pct']}% of portfolio. "
        "Intermediate-Treasury focus keeps duration below the Agg benchmark, limiting rate sensitivity. "
        "Duration also flows through equity via discount-rate effects — it's a whole-portfolio consideration. "
        "Duration sourced from static ETF fact-sheet values — TODO: pull live."
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
