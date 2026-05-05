"""Shared Streamlit UI helpers used across all pages."""
import streamlit as st

from src.build_info import render_build_caption


def render_footer() -> None:
    """Render the standard page footer with deployed build SHA."""
    st.caption(render_build_caption())
