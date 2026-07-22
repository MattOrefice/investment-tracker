"""Investment Analytics Tracker — entry point and router."""
import streamlit as st

st.set_page_config(
    page_title="Investment Analytics Tracker",
    page_icon="📈",
    layout="wide",
)

from src.asof import as_of_banner
from src.config import IS_DEMO
from src.db import initialize_db
from src.ui_helpers import render_footer, render_sidebar_footer

if IS_DEMO:
    initialize_db()          # demo.db is committed/prebuilt — just ensure base schema
else:
    from src.bootstrap import bootstrap_personal_db
    bootstrap_personal_db()  # fresh personal DB: base schema + all migrations + all seeds


def _inject_global_styles():
    """Inject one site-wide mobile stylesheet, ONCE, at the router (runs before
    every page via app.py's top-to-bottom rerun).

    Mobile-only: every rule lives inside @media (max-width: 640px), so desktop
    rendering is pixel-identical — these rules override the desktop CSS only at
    small viewports. Selectors are STABLE (data-testid attributes, .block-container,
    and the landing .endow-* classes) — never st-emotion-cache-* hashes, which
    change every Streamlit release.
    """
    st.markdown(
        """
        <style>
        @media (max-width: 640px) {
            /* viewport breathing room — wide-layout padding eats a 390px screen */
            .block-container {
                padding-left: 1rem !important;
                padding-right: 1rem !important;
                padding-top: 3.5rem !important;
            }
            /* type scale — st.title h1 ~2.75rem is huge on a phone */
            h1 { font-size: 1.65rem !important; }
            h2 { font-size: 1.35rem !important; }
            h3 { font-size: 1.15rem !important; }
            /* metric rows stack natively; size the values for one column */
            [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
            [data-testid="stMetricLabel"] { font-size: 0.8rem !important; }
            /* dataframes: slightly denser so more columns fit before scroll */
            [data-testid="stDataFrame"] { font-size: 0.85rem; }
            /* landing endow classes (harmless globally — only exist on landing) */
            .endow-title { font-size: 1.75rem !important; }
            .endow-card-header { font-size: 1.25rem !important; }
            .endow-card { margin-bottom: 0.75rem; }
            .endow-intro { font-size: 0.95rem; }
            /* cards STACK on mobile (no columns to equalize), so the desktop
               3-line clamp just truncates body text mid-word — unclamp it */
            .endow-card-body-clamp {
                display: block;
                -webkit-line-clamp: unset;
                overflow: visible;
            }
            /* Plotly modebar: hover-only on desktop, but persistently visible on
               touch viewports — hide on mobile (charts remain fully rendered;
               zoom/pan chrome is unusable on a phone anyway) */
            .modebar, .modebar-container { display: none !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _sleeve_phrase() -> str:
    """'12-sleeve' / '9-sleeve' from the book this process is pointed at.

    The landing page renders in both modes, so the count must come from the DB
    (like the sleeve maps) rather than hardcoding either taxonomy's number.
    Falls back to 'multi-sleeve' if the DB is unavailable — a degraded phrase
    beats a wrong number.
    """
    try:
        from src.sleeve_config import strategic_sleeve_weights
        n = len(strategic_sleeve_weights())
        return f"{n}-sleeve" if n else "multi-sleeve"
    except Exception:
        return "multi-sleeve"


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

    _sleeves = _sleeve_phrase()
    st.markdown(
        f"""
        <p class="endow-intro">This system maintains a {_sleeves} strategic asset allocation as policy,
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
            f"""
            <div class="endow-card">
                <h3 class="endow-card-header">Strategic Asset Allocation</h3>
                <p class="endow-card-body endow-card-body-clamp">{_sleeves} SAA policy with target weights
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
                and period-by-period Brinson-Fachler attribution.</p>
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
                regressions for US Equity and International Core, TERM/CREDIT decomposition for
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


_page_groups = {
    "Portfolio": [
        st.Page(_landing_page_render, title="Home", default=True, url_path="", visibility="hidden"),
        st.Page("pages/1_SAA.py", title="SAA"),
        st.Page("pages/2_Performance.py", title="Performance"),
        st.Page("pages/6_Benchmark_Attribution.py", title="Benchmark Attribution"),
        st.Page("pages/4_Factor_Profile.py", title="Factor Profile"),
        st.Page("pages/7_Risk.py", title="Risk"),
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
}

# Household View is personal-mode only; st.navigation() suppresses it
# from the sidebar entirely in demo mode (no stub needed in the nav).
if not IS_DEMO:
    _page_groups["Household"] = [
        st.Page("pages/13_Household_View.py", title="Household View"),
        st.Page("pages/14_Asset_Location.py", title="Asset Location"),
        st.Page("pages/15_Liquidity.py", title="Liquidity"),
    ]

_inject_global_styles()

nav = st.navigation(_page_groups, expanded=True)

nav.run()
render_sidebar_footer()
