"""
Dual-mode configuration for the investment tracker.

The app runs in two modes controlled by the TRACKER_MODE env var (or Streamlit secret):
  personal          — local-only, real holdings, data/tracker.db, gitignored.
  demo (default)    — public Streamlit Cloud deployment, fake trades, data/demo.db,
                      committed to the repo, password-protected.

All database access should go through get_db_path() (via src/db.py) so that both
modes stay in sync as new features are added.

Secret resolution order: st.secrets (Streamlit Cloud) → .env (local dev) → default.

FAIL-CLOSED: personal mode must be asked for EXPLICITLY. An unset, unreadable, or
unrecognised TRACKER_MODE resolves to demo, never personal. The two modes are not
symmetric in what they cost when wrong: demo on a local machine is a visibly wrong
portfolio and an obvious fix, whereas personal on the public Cloud deployment puts
the Household View in the nav (see app.py) and flips is_write_enabled() to True on
an anonymous, publicly-reachable app. The safe default is the one whose failure is
loud and harmless.
"""
import os
import warnings
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_ROOT = Path(__file__).resolve().parent.parent

VALID_MODES = ("personal", "demo")

# The mode a caller gets when they did not successfully ask for one.
DEFAULT_MODE = "demo"

MODE_LABELS = {
    "personal": "Personal Portfolio",
    "demo":     "Demo Portfolio",
}


def _read_secret(key: str) -> str | None:
    """Return a Streamlit secret, or None when secrets are genuinely unavailable.

    st.secrets RAISES when no secrets.toml exists — it does not return the
    `.get()` default — which is the normal case for local dev, pytest, and CI.
    That specific miss is expected and is swallowed here so the env var can take
    over. Anything else propagates: an unexpected failure while reading config
    must not be silently reinterpreted as "no secret set", because that is
    exactly how a mode resolution turns into a wrong-mode render.
    """
    try:
        import streamlit as st
        from streamlit.errors import StreamlitSecretNotFoundError
    except ImportError:
        # Not running under Streamlit at all (scripts, tests) — env vars only.
        return None

    try:
        return st.secrets.get(key)
    except (StreamlitSecretNotFoundError, FileNotFoundError):
        # No secrets file. FileNotFoundError is belt-and-braces: Streamlit is
        # pinned, but this is the one miss we never want to turn into a raise.
        return None


def _resolve_mode(raw: object | None) -> str:
    """Map a raw TRACKER_MODE value to a valid mode, failing closed to demo.

    Unset, blank, non-string, and unrecognised values all resolve to demo. An
    unrecognised value warns rather than raising: demo is a safe, correct
    render of a real portfolio, so taking the whole app down adds no safety —
    but a silent downgrade would be baffling to debug, hence the warning.
    """
    if raw is None:
        return DEFAULT_MODE
    mode = str(raw).strip().lower()
    if mode in VALID_MODES:
        return mode
    if mode:
        warnings.warn(
            f"Unrecognised TRACKER_MODE {str(raw)!r}; falling back to "
            f"{DEFAULT_MODE!r}. Valid modes: {VALID_MODES}.",
            RuntimeWarning,
            stacklevel=2,
        )
    return DEFAULT_MODE


FRED_API_KEY = _read_secret("FRED_API_KEY") or os.getenv("FRED_API_KEY")
_resolved_mode = _resolve_mode(_read_secret("TRACKER_MODE") or os.getenv("TRACKER_MODE"))


def get_mode() -> str:
    """The resolved mode. Fails closed at read time too, so that a caller
    monkeypatching _resolved_mode to something invalid still gets demo."""
    if _resolved_mode not in VALID_MODES:
        return DEFAULT_MODE
    return _resolved_mode


def get_db_path() -> Path:
    return _ROOT / "data" / ("demo.db" if get_mode() == "demo" else "tracker.db")


def is_demo() -> bool:
    return get_mode() == "demo"


def is_write_enabled() -> bool:
    """Return True when running locally (personal mode); False on the public demo."""
    return not is_demo()


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
