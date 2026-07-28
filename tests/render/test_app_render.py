"""Render smoke for app.py — the public landing page and router.

Every inner page had an AppTest render test; app.py did not. It is the first
thing a visitor to the public demo loads and the one file whose failure takes
down the whole artifact rather than a single page, so it had the widest blast
radius and the least coverage.

Deliberately a smoke test, not a content pin: app.py is mostly presentation CSS
and st.Page wiring, and pinning its copy here would duplicate what the inner
pages already assert. What is worth pinning is that it renders at all, and that
the demo build does not expose the personal-mode surface.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from src.config import IS_DEMO


@pytest.fixture(scope="module")
def app_root() -> AppTest:
    """Run the router once and return the rendered AppTest object."""
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()
    return at


def test_app_runs_without_exception(app_root: AppTest) -> None:
    """The landing page must render without an unhandled exception.

    This is the check that was missing: an ImportError or a module-level raise
    in app.py (or in the default page its router runs) breaks every page at
    once, and nothing in the suite would have caught it.
    """
    assert not app_root.exception, f"app.py raised: {app_root.exception}"


def test_app_renders_content(app_root: AppTest) -> None:
    """The router must actually paint something.

    Guards the degenerate pass: a page that raises st.stop() early, or renders
    an empty document, would satisfy the no-exception assertion above while
    shipping a blank artifact.
    """
    painted = len(app_root.markdown) + len(app_root.title) + len(app_root.header)
    assert painted > 0, "app.py rendered no markdown, title or header content"


@pytest.mark.skipif(not IS_DEMO, reason="Personal-surface suppression only applies in demo mode")
def test_demo_build_does_not_expose_household_view(app_root: AppTest) -> None:
    """Demo mode must not surface the Household View nav group.

    CLAUDE.md treats this as non-negotiable: personal-mode data and the
    Household View page must never deploy publicly. app.py gates the group
    behind `if not IS_DEMO`, and this asserts the gate actually holds at render
    time rather than trusting the branch by inspection.
    """
    rendered = " ".join(
        [m.value for m in app_root.markdown]
        + [str(getattr(el, "label", "")) for el in app_root.sidebar]
    )
    assert "Household View" not in rendered, (
        "Household View surfaced in a demo-mode render — personal-only page "
        "reachable on the public artifact."
    )
