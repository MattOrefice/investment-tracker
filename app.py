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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;600&display=swap" rel="stylesheet">

    <style>
    .endow-title {
        font-family: 'EB Garamond', Georgia, serif;
        font-weight: 600;
        font-size: 2.75rem;
        color: #1e3a5f;
        margin-bottom: 0.5rem;
        line-height: 1.15;
    }
    .endow-tagline {
        font-weight: 600;
        color: #1f2937;
        margin-bottom: 1.25rem;
    }
    .endow-byline {
        color: #1e3a5f;
        font-weight: 600;
    }
    .endow-byline-suffix {
        color: #4b5563;
        font-weight: 400;
    }
    .endow-recency {
        color: #6b7280;
        font-style: italic;
        margin-top: 0.25rem;
    }
    .endow-rule {
        border: none;
        border-top: 1px solid #e5e7eb;
        margin: 1.75rem 0;
    }
    .endow-intro {
        color: #1f2937;
        line-height: 1.7;
    }
    .endow-card {
        background-color: #f8fafb;
        border-left: 3px solid #1e3a5f;
        padding: 1rem 1.25rem;
        height: 100%;
    }
    .endow-card-header {
        font-family: 'EB Garamond', Georgia, serif;
        font-weight: 600;
        font-size: 1.5rem;
        color: #1e3a5f;
        margin-top: 0;
        margin-bottom: 0.5rem;
        line-height: 1.2;
    }
    .endow-card-body {
        color: #374151;
        font-size: 0.9375rem;
        line-height: 1.55;
        margin-bottom: 0;
    }

    /* Open buttons (st.page_link) */
    a[data-testid="stPageLink-NavLink"] {
        background-color: #1e3a5f;
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
        background-color: #152a48;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <h1 class="endow-title">Investment Analytics Tracker</h1>
    <p class="endow-tagline">Multi-asset portfolio analytics with institutional-grade performance attribution, factor regression, and macro regime monitoring.</p>
    <p><span class="endow-byline">Built by Matt Orefice, CFA</span><span class="endow-byline-suffix"> · Available for buy-side allocator and investment due diligence roles</span></p>
    <p class="endow-recency"><em>{as_of_banner()}</em></p>
    <hr class="endow-rule">
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <p class="endow-intro">This system maintains a 10-sleeve strategic asset allocation as policy,
    then measures deviation, attribution, and factor exposure against that policy.
    Performance is tracked time-weighted against a SAA-target-weighted blended benchmark.
    Per-sleeve Fama-French 5-factor regressions with Newey-West HAC standard errors decompose
    excess return into factor exposures and residual selection. A macro regime dashboard with
    dynamic interpretations frames whether current conditions warrant any tactical tilt.</p>
    <hr class="endow-rule">
    """,
    unsafe_allow_html=True,
)

c1, c2, c3, c4 = st.columns(4, vertical_alignment="bottom")

with c1:
    st.markdown(
        """
        <div class="endow-card">
            <h3 class="endow-card-header">Strategic Asset Allocation</h3>
            <p class="endow-card-body">10-sleeve SAA policy with target weights and tolerance bands.
            Drift thresholds define when rebalancing is warranted; the framework treats SAA as
            policy, not as a starting point for tactical tilts.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("pages/1_SAA.py", label="Open")

with c2:
    st.markdown(
        """
        <div class="endow-card">
            <h3 class="endow-card-header">Performance</h3>
            <p class="endow-card-body">Time-weighted return vs SAA-target-weighted blended benchmark.
            Cover narrative, cumulative return chart, and period-by-period BHB attribution.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("pages/2_Performance.py", label="Open")

with c3:
    st.markdown(
        """
        <div class="endow-card">
            <h3 class="endow-card-header">Macro</h3>
            <p class="endow-card-body">Regime classification with dynamic interpretations of CAPE, ECY,
            yield curve, credit spreads, labor, and growth indicators against historical percentile
            bands.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("pages/3_Macro.py", label="Open")

with c4:
    st.markdown(
        """
        <div class="endow-card">
            <h3 class="endow-card-header">Asset Evaluation</h3>
            <p class="endow-card-body">Candidate-asset evaluation framework with worked Bitcoin
            case study. Univariate statistics, regime-conditional correlation, mean-variance
            contribution, and allocator-side tradeoffs.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("pages/5_Asset_Evaluation.py", label="Open")

render_footer()
render_sidebar_footer()
