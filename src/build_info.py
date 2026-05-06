"""Deployed build identifier for footer rendering."""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def get_build_sha() -> str:
    """Return the short git SHA of the deployed commit.

    Resolution order:
    1. STREAMLIT_BUILD_SHA env var (CI/manual override).
    2. git rev-parse --short HEAD from the deployed checkout.
    3. Literal string "unknown" if both fail.
    """
    env_sha = os.environ.get("STREAMLIT_BUILD_SHA")
    if env_sha:
        return env_sha[:7]
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return sha.decode("ascii").strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


def render_build_caption() -> str:
    """Return the caption string rendered in page footers."""
    sha = get_build_sha()
    if sha == "unknown":
        return "Build identifier unavailable"
    return f"Build {sha}"
