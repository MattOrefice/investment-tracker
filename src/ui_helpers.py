"""Shared Streamlit UI helpers used across all pages."""
import streamlit as st

from src.build_info import render_build_caption


def render_footer() -> None:
    """Render the standard page footer with deployed build SHA (skipped when SHOW_BUILD_HASH is unset)."""
    caption = render_build_caption()
    if caption:
        st.caption(caption)


def write_guard_toast() -> None:
    """Emit a demo-mode toast when a write action is blocked."""
    st.toast(
        "Demo mode — trades not written. "
        "This is a public demo; write actions are disabled.",
        icon="🔒",
    )
