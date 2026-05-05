"""
Dual-mode configuration for the investment tracker.

The app runs in two modes controlled by the TRACKER_MODE env var (or Streamlit secret):
  personal (default) — local-only, real holdings, data/tracker.db, gitignored.
  demo              — public Streamlit Cloud deployment, fake trades, data/demo.db,
                      committed to the repo, password-protected.

All database access should go through get_db_path() (via src/db.py) so that both
modes stay in sync as new features are added.

Secret resolution order: st.secrets (Streamlit Cloud) → .env (local dev) → default.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import streamlit as st
    FRED_API_KEY = st.secrets.get("FRED_API_KEY", os.getenv("FRED_API_KEY"))
    _resolved_mode = st.secrets.get("TRACKER_MODE", os.getenv("TRACKER_MODE", "personal")).lower()
except (ImportError, FileNotFoundError, AttributeError):
    FRED_API_KEY = os.getenv("FRED_API_KEY")
    _resolved_mode = os.getenv("TRACKER_MODE", "personal").lower()

_ROOT = Path(__file__).resolve().parent.parent

VALID_MODES = ("personal", "demo")

MODE_LABELS = {
    "personal": "Personal Portfolio",
    "demo":     "Demo Portfolio",
}


def get_mode() -> str:
    if _resolved_mode not in VALID_MODES:
        raise ValueError(f"Invalid TRACKER_MODE '{_resolved_mode}'. Must be one of {VALID_MODES}.")
    return _resolved_mode


def get_db_path() -> Path:
    return _ROOT / "data" / ("demo.db" if get_mode() == "demo" else "tracker.db")


def is_demo() -> bool:
    return get_mode() == "demo"


IS_DEMO = is_demo()

DEMO_BANNER_TEXT = (
    "**Demo mode** — analytics computed on a paper-trade portfolio simulated from May 2025. "
    "Methodology and inference are real; positions are illustrative."
)
