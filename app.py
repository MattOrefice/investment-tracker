"""Investment Analytics Tracker — homepage."""
import streamlit as st
from src.config import DEMO_BANNER_TEXT, IS_DEMO
from src.db import initialize_db

st.set_page_config(
    page_title="Investment Analytics Tracker",
    page_icon="📈",
    layout="wide",
)

initialize_db()

if IS_DEMO:
    st.info(DEMO_BANNER_TEXT)

st.title("Investment Analytics Tracker")
st.caption("Personal portfolio · structured allocator workflow")

st.markdown(
    """
    This dashboard mirrors an institutional allocator workflow:

    1. **Strategic asset allocation** — set target weights and tolerance bands
    2. **Research** — compare candidate securities within each asset class
    3. **Thesis & execution** — log trades with documented views and exit conditions
    4. **Performance** — time-weighted returns, benchmarking, and attribution
    5. **Macro dashboard** — valuation, rates, and relative performance

    Use the sidebar to navigate.
    """
)
