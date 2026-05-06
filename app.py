"""Investment Analytics Tracker — homepage."""
import streamlit as st

st.set_page_config(
    page_title="Investment Analytics Tracker",
    page_icon="📈",
    layout="wide",
)

from src.asof import as_of_banner
from src.db import initialize_db
from src.ui_helpers import render_footer

initialize_db()

_, col, _ = st.columns([1, 8, 1])
with col:
    st.markdown("## Personal Investment Analytics")
    st.caption(as_of_banner())

    st.markdown(
        """
        Built by Matt Orefice — CFA charterholder, ex-MissionSquare Retirement.
        A live-data portfolio tracking system with GIPS-compliant time-weighted returns,
        Brinson-Fachler attribution, and automated quarterly PDF reporting against a
        custom-blended benchmark.

        The quarterly PDF report is the primary deliverable. Generate it from the **Performance** page.
        """
    )

    st.divider()

    st.caption(
        "SAA framework · thesis-driven execution · BF attribution · macro overlay"
    )
    render_footer()
