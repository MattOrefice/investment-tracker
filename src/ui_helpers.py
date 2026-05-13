"""Shared Streamlit UI helpers used across all pages."""
import streamlit as st

from src.build_info import render_build_caption


def render_footer() -> None:
    """Render the standard page footer with deployed build SHA (skipped when SHOW_BUILD_HASH is unset)."""
    caption = render_build_caption()
    if caption:
        st.caption(caption)


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
