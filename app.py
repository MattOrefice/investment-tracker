"""Investment Analytics Tracker — entry point and router."""
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


def _landing_page_render():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;600&display=swap');

        .endow-title {
            font-family: 'EB Garamond', Georgia, 'Times New Roman', serif !important;
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

        /* Equal-height cards: force Streamlit columns to be flex columns */
        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            display: flex;
            flex-direction: column;
        }

        .endow-card {
            background-color: #f8fafb;
            border-left: 3px solid #1e3a5f;
            padding: 1rem 1.25rem;
            flex: 1 1 auto;
            display: flex;
            flex-direction: column;
        }
        .endow-card-header {
            font-family: 'EB Garamond', Georgia, 'Times New Roman', serif !important;
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
            flex: 1 1 auto;
        }
        .endow-card-body-clamp {
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Open buttons (st.page_link) */
        a[data-testid="stPageLink-NavLink"] {
            background-color: #1e3a5f;
            color: #ffffff !important;
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
        /* Force label text white — st.page_link renders label inside <p> */
        a[data-testid="stPageLink-NavLink"] p,
        a[data-testid="stPageLink-NavLink"] span {
            color: #ffffff !important;
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
        excess return into factor exposures and residual selection. A macro regime dashboard provides
        reference context across growth, inflation, monetary policy, credit, valuation, and cross-asset
        performance — positioning remains policy-driven, not view-driven.</p>
        <hr class="endow-rule">
        """,
        unsafe_allow_html=True,
    )

    r1c1, r1c2, r1c3 = st.columns(3, vertical_alignment="top")

    with r1c1:
        st.markdown(
            """
            <div class="endow-card">
                <h3 class="endow-card-header">Strategic Asset Allocation</h3>
                <p class="endow-card-body endow-card-body-clamp">10-sleeve SAA policy with target weights
                and tolerance bands. Drift thresholds define when rebalancing is warranted; SAA is treated
                as policy, not a starting point for tactical tilts.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/1_SAA.py", label="Open")

    with r1c2:
        st.markdown(
            """
            <div class="endow-card">
                <h3 class="endow-card-header">Performance</h3>
                <p class="endow-card-body endow-card-body-clamp">Time-weighted return vs
                SAA-target-weighted blended benchmark. Cover narrative, cumulative return chart,
                and period-by-period BHB attribution.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/2_Performance.py", label="Open")

    with r1c3:
        st.markdown(
            """
            <div class="endow-card">
                <h3 class="endow-card-header">Benchmark Attribution</h3>
                <p class="endow-card-body endow-card-body-clamp">Portfolio excess return decomposed via
                OLS regression against the custom blended benchmark plus three style factors. Newey-West
                HAC standard errors; alpha is the institutional definition — return after controlling for
                benchmark beta and style tilts.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/6_Benchmark_Attribution.py", label="Open")

    r2c1, r2c2, r2c3 = st.columns(3, vertical_alignment="top")

    with r2c1:
        st.markdown(
            """
            <div class="endow-card">
                <h3 class="endow-card-header">Factor Profile</h3>
                <p class="endow-card-body endow-card-body-clamp">Per-sleeve Fama-French 5-factor
                regressions for US Equity and International Developed, TERM/CREDIT decomposition for
                Fixed Income. Equity Style Profile and FI Duration as supporting risk-characteristic
                views.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/4_Factor_Profile.py", label="Open")

    with r2c2:
        st.markdown(
            """
            <div class="endow-card">
                <h3 class="endow-card-header">Macro</h3>
                <p class="endow-card-body endow-card-body-clamp">Regime classification with dynamic
                interpretations of CAPE, ECY, yield curve, credit spreads, labor, and growth
                indicators against historical percentile bands.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/3_Macro.py", label="Open")

    with r2c3:
        st.markdown(
            """
            <div class="endow-card">
                <h3 class="endow-card-header">Asset Evaluation</h3>
                <p class="endow-card-body endow-card-body-clamp">Candidate-asset evaluation framework
                applied to Bitcoin: univariate statistics, regime-conditional correlation against SAA
                sleeves, and mean-variance contribution analysis.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/5_Asset_Evaluation.py", label="Open")

    render_footer()


nav = st.navigation(
    {
        "Portfolio": [
            st.Page(_landing_page_render, title="Home", default=True, url_path="", visibility="hidden"),
            st.Page("pages/1_SAA.py", title="SAA"),
            st.Page("pages/2_Performance.py", title="Performance"),
            st.Page("pages/6_Benchmark_Attribution.py", title="Benchmark Attribution"),
            st.Page("pages/4_Factor_Profile.py", title="Factor Profile"),
        ],
        "Markets & Macro": [
            st.Page("pages/3_Macro.py", title="Macro"),
            st.Page("pages/9_Correlations.py", title="Correlations"),
            st.Page("pages/8_Research.py", title="Research"),
            st.Page("pages/5_Asset_Evaluation.py", title="Asset Evaluation"),
        ],
        "Operations": [
            st.Page("pages/10_Trade_Log.py", title="Trade Log"),
            st.Page("pages/11_Capital_Deployment.py", title="Capital Deployment"),
            st.Page("pages/12_Tax_Lots.py", title="Tax Lots"),
        ],
    },
    expanded=True,
)

nav.run()
render_sidebar_footer()
