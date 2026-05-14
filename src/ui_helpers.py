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

    CSS strategy:
      - Forces all nav items visible (max-height/overflow on ul)
      - Hides "View N more" via defense-in-depth selectors (no :has())
      - Hides the auto-generated "app" entry (first <li>)
      - Makes sidebar a flex column so order: -2 pulls home link above nav
    """
    st.sidebar.markdown(
        """
        <style>
        /* Force all nav items visible — remove "View N more" collapse */
        [data-testid="stSidebarNav"] ul {
            max-height: none !important;
            overflow: visible !important;
        }
        /* Hide "View N more" — defense-in-depth for multiple DOM shapes */
        [data-testid="stSidebarNav"] details > summary {
            display: none !important;
        }
        [data-testid="stSidebarNavViewButton"] {
            display: none !important;
        }
        [data-testid="stSidebarNav"] > div > button {
            display: none !important;
        }
        [data-testid="stSidebarNav"] button {
            display: none !important;
        }
        /* If items are inside a <details>, force them all visible */
        [data-testid="stSidebarNav"] details {
            display: contents !important;
        }
        [data-testid="stSidebarNav"] details > *:not(summary) {
            display: block !important;
        }
        /* Hide the auto-generated "app" entry (first item in nav list) */
        [data-testid="stSidebarNav"] li:first-child {
            display: none !important;
        }
        /* Navy hover on sidebar nav links */
        [data-testid="stSidebarNav"] a:hover {
            color: #1e3a5f !important;
        }
        /* Sidebar content as flex column so we can reorder children */
        section[data-testid="stSidebar"] > div:first-child,
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            display: flex !important;
            flex-direction: column !important;
        }
        /* Home link pulled to top via order: -2 */
        .endow-sidebar-header {
            order: -2 !important;
            padding: 0.5rem 0 0.75rem 0;
            margin-bottom: 0.5rem;
            border-bottom: 1px solid #e5e7eb;
        }
        .endow-sidebar-header a {
            color: #1e3a5f !important;
            text-decoration: none;
            font-weight: 600;
            font-size: 0.95rem;
        }
        .endow-sidebar-header a:hover {
            text-decoration: underline;
        }
        /* Nav list immediately above default-order content */
        [data-testid="stSidebarNav"] {
            order: -1 !important;
        }
        </style>
        <div class="endow-sidebar-header">
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
