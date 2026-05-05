"""Investment Analytics Tracker — homepage."""
import streamlit as st
from src.asof import as_of_banner
from src.config import DEMO_BANNER_TEXT, IS_DEMO
from src.db import initialize_db

st.set_page_config(
    page_title="Investment Analytics Tracker",
    page_icon="📈",
    layout="wide",
)

initialize_db()

_, col, _ = st.columns([1, 8, 1])
with col:
    if IS_DEMO:
        st.info(DEMO_BANNER_TEXT)

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
