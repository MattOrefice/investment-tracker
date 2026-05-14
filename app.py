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

st.markdown(
    """
    <style>
    /* Open buttons: institutional blue, compact size */
    a[data-testid="stPageLink-NavLink"] {
        background-color: #2563eb;
        color: white !important;
        padding: 0.375rem 1rem;
        border-radius: 0.375rem;
        text-decoration: none;
        font-weight: 500;
        font-size: 0.875rem;
        line-height: 1.25;
        display: inline-block;
        transition: background-color 0.2s;
    }
    a[data-testid="stPageLink-NavLink"]:hover {
        background-color: #1d4ed8;
    }
    /* Equal-height cards: push Open button to bottom of each column */
    div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] {
        height: 100%;
        display: flex;
        flex-direction: column;
    }
    div[data-testid="stColumn"] .stPageLink {
        margin-top: auto;
        padding-top: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Investment Analytics Tracker")
st.markdown(
    "**Multi-asset portfolio analytics with institutional-grade "
    "performance attribution, factor regression, and macro regime "
    "monitoring.**"
)
st.markdown(
    "**Built by Matt Orefice, CFA** · Available for buy-side "
    "allocator and investment due diligence roles  \n"
    f"*{as_of_banner()}*"
)
st.markdown(
    "<hr style='margin: 1.5rem 0; border: none; border-top: 1px solid #e5e7eb;'>",
    unsafe_allow_html=True,
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

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.markdown("### Strategic Asset Allocation")
    st.markdown(
        "10-sleeve SAA policy: target weights, tolerance bands, "
        "and the drift thresholds that define when rebalancing is warranted."
    )
    st.page_link("pages/1_SAA.py", label="Open")

with c2:
    st.markdown("### Performance")
    st.markdown(
        "Time-weighted return vs SAA-target-weighted blended benchmark. "
        "Cover narrative, cumulative return chart, and period-by-period BHB attribution."
    )
    st.page_link("pages/2_Performance.py", label="Open")

with c3:
    st.markdown("### Macro")
    st.markdown(
        "Regime classification with dynamic interpretations of CAPE, ECY, "
        "yield curve, credit spreads, labor, and growth indicators against "
        "historical percentile bands."
    )
    st.page_link("pages/3_Macro.py", label="Open")

with c4:
    st.markdown("### Asset Evaluation")
    st.markdown(
        "Candidate-asset evaluation framework with worked Bitcoin "
        "case study: univariate statistics, regime-conditional "
        "correlation, mean-variance contribution, and a decision "
        "framework enumerating liquidity, tax, operational, and "
        "mandate-fit considerations."
    )
    st.page_link("pages/5_Asset_Evaluation.py", label="Open")

render_footer()
render_sidebar_footer()
