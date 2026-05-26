"""Render-aware regression tests for the Capital Deployment page (pages/11_Capital_Deployment.py).

Pinned at Phase 48 — protects the framing additions (How to read expander,
A3 caption above Execute and Log, Methodology expander) from accidental reversion.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def cap_deploy_app() -> AppTest:
    """Run the Capital Deployment page."""
    at = AppTest.from_file("pages/11_Capital_Deployment.py", default_timeout=60)
    at.run()
    return at


@pytest.fixture(scope="function")
def cap_deploy_app_with_cash() -> AppTest:
    """Run Capital Deployment with contrib_cash=1000 to enter the Execute and Log branch."""
    at = AppTest.from_file("pages/11_Capital_Deployment.py", default_timeout=60)
    at.session_state["contrib_cash"] = 1000.0
    at.run()
    return at


def test_capital_deployment_runs_without_exception(cap_deploy_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not cap_deploy_app.exception, (
        f"Capital Deployment page raised: {cap_deploy_app.exception}"
    )


def test_how_to_read_expander_present(cap_deploy_app: AppTest) -> None:
    """'How to read this page' expander must be present (collapsed by default).
    Pinned: Phase 48 Item A1."""
    labels = [e.label for e in cap_deploy_app.expander]
    assert any("How to read this page" in lbl for lbl in labels), (
        "'How to read this page' expander not found — Phase 48 Item A1 regression. "
        f"Expander labels found: {labels}"
    )


def test_deploy_new_cash_bullet(cap_deploy_app: AppTest) -> None:
    """How to read expander must describe the Deploy New Cash workflow.
    Pinned: Phase 48 Item A1."""
    all_md = " ".join(m.value for m in cap_deploy_app.markdown)
    assert "close drift first, then distribute the remainder" in all_md, (
        "'close drift first, then distribute the remainder' not found — "
        "Phase 48 Item A1 regression: Deploy New Cash bullet must be present."
    )


def test_rationale_tags_bullet(cap_deploy_app: AppTest) -> None:
    """How to read expander must define the four rationale tags.
    Pinned: Phase 48 Item A1."""
    all_md = " ".join(m.value for m in cap_deploy_app.markdown)
    assert "close drift" in all_md and "maintain target" in all_md, (
        "Rationale tags bullet not found — Phase 48 Item A1 regression: "
        "'close drift' and 'maintain target' tag definitions must be present."
    )


def _has_portfolio_data(app: AppTest) -> bool:
    """Return False if the page stopped early due to no holdings data."""
    for info_el in app.info:
        if hasattr(info_el, "value") and "No holdings found" in (info_el.value or ""):
            return False
    return True


def test_a3_caption_present(cap_deploy_app_with_cash: AppTest) -> None:
    """Caption above Execute and Log must instruct on editing Suggested $ column.
    Pinned: Phase 48 Item A3."""
    if not _has_portfolio_data(cap_deploy_app_with_cash):
        pytest.skip("No portfolio data available — data-dependent test skipped.")
    all_captions = " ".join(
        c.value for c in cap_deploy_app_with_cash.caption
        if hasattr(c, "value") and c.value
    )
    assert "recompute automatically from the edited amounts" in all_captions, (
        "A3 caption not found — Phase 48 Item A3 regression: "
        "'recompute automatically from the edited amounts' "
        "must appear as a caption above Execute and Log."
    )


def test_methodology_expander_present(cap_deploy_app: AppTest) -> None:
    """'Methodology' expander must be present at the bottom of the page.
    Pinned: Phase 48 Item A2."""
    if not _has_portfolio_data(cap_deploy_app):
        pytest.skip("No portfolio data available — data-dependent test skipped.")
    labels = [e.label for e in cap_deploy_app.expander]
    assert any(lbl == "Methodology" for lbl in labels), (
        "'Methodology' expander not found — Phase 48 Item A2 regression. "
        f"Expander labels found: {labels}"
    )


def test_methodology_tiered_bands(cap_deploy_app: AppTest) -> None:
    """Methodology expander must describe tiered SAA bands.
    Pinned: Phase 48 Item A2."""
    if not _has_portfolio_data(cap_deploy_app):
        pytest.skip("No portfolio data available — data-dependent test skipped.")
    all_md = " ".join(m.value for m in cap_deploy_app.markdown)
    assert "Tiered SAA bands" in all_md, (
        "Methodology tiered bands not found — Phase 48 Item A2 regression: "
        "'Tiered SAA bands' bullet must be present in the Methodology expander."
    )


def test_methodology_execute_and_log_mechanics(cap_deploy_app: AppTest) -> None:
    """Methodology expander must describe Execute and Log lot_source distinction.
    Pinned: Phase 48 Item A2."""
    if not _has_portfolio_data(cap_deploy_app):
        pytest.skip("No portfolio data available — data-dependent test skipped.")
    all_md = " ".join(m.value for m in cap_deploy_app.markdown)
    assert "lot_source='Manual'" in all_md, (
        "Execute and Log mechanics not found — Phase 48 Item A2 regression: "
        "lot_source='Manual' must be present in the Methodology expander."
    )
