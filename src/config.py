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
    try:
        # st.secrets behavior varies across Streamlit versions when called outside a
        # Streamlit context (no secrets.toml). Catch any exception so env vars are
        # always the authoritative fallback in test / CI environments.
        FRED_API_KEY = st.secrets.get("FRED_API_KEY", os.getenv("FRED_API_KEY"))
        _resolved_mode = st.secrets.get("TRACKER_MODE", os.getenv("TRACKER_MODE", "personal")).lower()
    except Exception:
        FRED_API_KEY = os.getenv("FRED_API_KEY")
        _resolved_mode = os.getenv("TRACKER_MODE", "personal").lower()
except ImportError:
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


def get_demo_banner_text() -> str:
    """Return the demo-mode info banner, with inception month sourced from the DB."""
    inception_month = "May 2025"  # fallback if DB unavailable
    try:
        from src.db import get_connection  # lazy import — avoids circular at module level
        from datetime import date as _date
        with get_connection() as _conn:
            _row = _conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
        if _row and _row[0]:
            inception_month = _date.fromisoformat(_row[0]).strftime("%B %Y")
    except Exception:
        pass
    return (
        f"**Demo mode** — analytics computed on a paper-trade portfolio simulated from {inception_month}. "
        "Methodology and inference are real; positions are illustrative."
    )


# Kept for backward compatibility; pages should prefer get_demo_banner_text().
DEMO_BANNER_TEXT = (
    "**Demo mode** — analytics computed on a paper-trade portfolio simulated from May 2025. "
    "Methodology and inference are real; positions are illustrative."
)
