"""Investment Analytics Tracker — landing page."""
import streamlit as st

st.set_page_config(
    page_title="Investment Analytics Tracker",
    page_icon="📈",
    layout="wide",
)

from src.asof import as_of_banner
from src.db import initialize_db
from src.ui_helpers import render_footer, render_sidebar_footer

initialize_db()

st.title("Investment Analytics Tracker")
st.markdown(
    "**Multi-asset portfolio analytics with institutional-grade "
    "performance attribution, factor regression, and macro regime "
    "monitoring.**"
)
st.caption(
    "Built by Matt Orefice, CFA · Available for buy-side "
    "allocator and investment due diligence roles"
)

st.markdown(
    "This system maintains a 10-sleeve strategic asset allocation as policy, "
    "then measures deviation, attribution, and factor exposure against that policy. "
    "Performance is tracked time-weighted against a SAA-target-weighted blended "
    "benchmark. Per-sleeve Fama-French 5-factor regressions with Newey-West HAC "
    "standard errors decompose excess return into factor exposures and residual "
    "selection. A macro regime dashboard with dynamic interpretations frames whether "
    "current conditions warrant any tactical tilt."
)

st.caption(as_of_banner())

st.divider()

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.markdown("**Strategic Asset Allocation**")
    st.markdown(
        "10-sleeve SAA policy: target weights, tolerance bands, "
        "and the drift thresholds that define when rebalancing is warranted."
    )
    st.page_link("pages/1_SAA.py", label="Open SAA")

with c2:
    st.markdown("**Performance**")
    st.markdown(
        "Time-weighted return vs SAA-target-weighted blended benchmark. "
        "Cover narrative, cumulative return chart, and period-by-period BHB attribution."
    )
    st.page_link("pages/2_Performance.py", label="Open Performance")

with c3:
    st.markdown("**Macro**")
    st.markdown(
        "Regime classification with dynamic interpretations of CAPE, ECY, "
        "yield curve, credit spreads, labor, and growth indicators against "
        "historical percentile bands."
    )
    st.page_link("pages/3_Macro.py", label="Open Macro")

with c4:
    st.markdown("**Factor Profile**")
    st.markdown(
        "Per-sleeve Fama-French 5-factor regressions with Newey-West HAC "
        "standard errors. Region-appropriate factor universes; alpha "
        "decomposition with universe-mismatch disclosures."
    )
    st.page_link("pages/4_Factor_Profile.py", label="Open Factor Profile")

render_footer()
render_sidebar_footer()
