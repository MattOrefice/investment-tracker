"""Shared Streamlit UI helpers used across all pages."""
import streamlit as st

from src.build_info import render_build_caption


def render_footer() -> None:
    """Render the standard page footer with deployed build SHA (skipped when SHOW_BUILD_HASH is unset)."""
    caption = render_build_caption()
    if caption:
        st.caption(caption)


def render_sidebar_header() -> None:
    """Render nav CSS and home link at the top of the sidebar on every page.

    Call once near the TOP of each page script (after st.set_page_config and
    src imports) so the nav expansion and home link are always present.

    Injects CSS that:
      - removes the "View N more" collapse on the auto-nav list
      - hides the auto-generated "app" entry (first <li> in the nav)
    Renders a navy "← Investment Analytics Tracker" link above the page list.
    """
    st.sidebar.markdown(
        """
        <style>
        /* Expand all sidebar nav items — remove "View N more" collapse */
        [data-testid="stSidebarNav"] ul {
            max-height: none !important;
            overflow: visible !important;
        }
        /* Hide the collapse toggle button */
        [data-testid="stSidebarNav"] > div:has(> button) {
            display: none !important;
        }
        /* Hide the auto-generated "app" entry (first item in nav list) */
        [data-testid="stSidebarNav"] li:first-child {
            display: none !important;
        }
        /* Navy hover on sidebar nav links */
        [data-testid="stSidebarNav"] a:hover {
            color: #1e3a5f !important;
        }
        /* Home link */
        .endow-home-link a {
            color: #1e3a5f;
            text-decoration: none;
            font-weight: 600;
            font-size: 0.9rem;
        }
        .endow-home-link a:hover {
            text-decoration: underline;
        }
        </style>
        <div class="endow-home-link" style="padding:0.5rem 0 1rem 0; border-bottom:1px solid #e5e7eb; margin-bottom:0.5rem;">
            <a href="/" target="_self">&#8592; Investment Analytics Tracker</a>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_footer() -> None:
    """Persistent contact footer at the bottom of the sidebar on every page.

    Call once at the END of each page script so it renders below any
    page-specific sidebar widgets.
    """
    with st.sidebar:
        st.markdown("---")
        st.markdown(
            "**Matt Orefice, CFA**  \n"
            "[mattorefice0@gmail.com](mailto:mattorefice0@gmail.com)  \n"
            "[LinkedIn](https://www.linkedin.com/in/matthew-orefice-cfa-83536b190/)  \n"
            "[GitHub](https://github.com/MattOrefice/investment-tracker)"
        )
        st.caption(
            "Available for buy-side allocator and "
            "investment due diligence roles."
        )


def write_guard_toast() -> None:
    """Emit a demo-mode toast when a write action is blocked."""
    st.toast(
        "Demo mode — trades not written. "
        "This is a public demo; write actions are disabled.",
        icon="🔒",
    )
