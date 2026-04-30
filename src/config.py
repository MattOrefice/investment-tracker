"""
Dual-mode configuration for the investment tracker.

The app runs in two modes controlled by the TRACKER_MODE env var:
  personal (default) — local-only, real holdings, data/tracker.db, gitignored.
  demo              — public Streamlit Cloud deployment, fake trades, data/demo.db,
                      committed to the repo, password-protected.

All database access should go through get_db_path() (via src/db.py) so that both
modes stay in sync as new features are added.
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

VALID_MODES = ("personal", "demo")

MODE_LABELS = {
    "personal": "Personal Portfolio",
    "demo":     "Demo Portfolio",
}


def get_mode() -> str:
    mode = os.environ.get("TRACKER_MODE", "personal").lower()
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid TRACKER_MODE '{mode}'. Must be one of {VALID_MODES}.")
    return mode


def get_db_path() -> Path:
    return _ROOT / "data" / ("demo.db" if get_mode() == "demo" else "tracker.db")


def is_demo() -> bool:
    return get_mode() == "demo"
