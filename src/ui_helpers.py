"""Shared Streamlit UI helpers used across all pages."""
import streamlit as st

from src.build_info import render_build_caption


def render_footer() -> None:
    """Render the standard page footer with deployed build SHA (skipped when SHOW_BUILD_HASH is unset)."""
    caption = render_build_caption()
    if caption:
        st.caption(caption)


def render_page_header() -> None:
    """Render the home-link header at the top of every inner page's main content area.

    Call once near the top of each page script in pages/ (after imports, before
    any st.* content calls). Do NOT call on the landing page — it would be self-referential.
    """
    st.markdown(
        """
        <style>
        .endow-page-header {
            margin: 0 0 1.25rem 0;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid #e5e7eb;
        }
        .endow-page-header a {
            color: #1e3a5f;
            text-decoration: none;
            font-weight: 600;
            font-size: 0.95rem;
        }
        .endow-page-header a:hover {
            text-decoration: underline;
        }
        </style>
        <div class="endow-page-header">
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
