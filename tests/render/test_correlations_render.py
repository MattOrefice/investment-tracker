"""Render-aware regression tests for the Correlations page (pages/9_Correlations.py).

Pinned at Phase 45 — protects highest-leverage prose from accidental reversion.

The page fetches live price data. Tests that check data-driven prose are guarded
with skip conditions when data is unavailable. Structural/prose pins below check
strings that appear regardless of the specific correlation values.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def corr_app() -> AppTest:
    """Run the Correlations page in default heatmap view."""
    at = AppTest.from_file("pages/9_Correlations.py", default_timeout=120)
    at.run()
    return at


@pytest.fixture(scope="module")
def corr_pair_app() -> AppTest:
    """Run the Correlations page switched to pair time-series view."""
    at = AppTest.from_file("pages/9_Correlations.py", default_timeout=120)
    at.run()
    at.radio[1].set_value("Pair time-series")
    at.run()
    return at


def test_correlations_runs_without_exception(corr_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not corr_app.exception, f"Correlations page raised: {corr_app.exception}"


def test_how_to_read_expander_present(corr_app: AppTest) -> None:
    """'How to read this page' expander must be present (collapsed by default).
    Pinned: Phase 45A Item A1."""
    expander_labels = [e.label for e in corr_app.expander]
    assert any("How to read this page" in lbl for lbl in expander_labels), (
        "'How to read this page' expander not found — Phase 45A Item A1 regression. "
        f"Expander labels found: {expander_labels}"
    )


def test_stationarity_caveat_present(corr_app: AppTest) -> None:
    """Stationarity caveat must appear below the heatmap as a caption.
    Pinned: Phase 45A Item A2."""
    all_text = " ".join(
        c.value for c in corr_app.caption
        if hasattr(c, "value") and c.value
    )
    assert "Rolling correlations are not stationary" in all_text, (
        "Stationarity caveat caption not found — Phase 45A Item A2 regression: "
        "'Rolling correlations are not stationary: during equity drawdowns...' "
        "must appear below the heatmap."
    )


def test_notable_pairs_high_sentence(corr_app: AppTest) -> None:
    """Notable pairs prose must open with the intra-equity conclusion (em-dash form).
    Pinned: Phase 45B Item B5."""
    if corr_app.exception:
        pytest.skip("Page raised an exception — data-dependent test skipped.")
    all_md = " ".join(m.value for m in corr_app.markdown)
    assert "High intra-equity co-movement" in all_md, (
        "Notable pairs high sentence not found — Phase 45B Item B5 regression: "
        "'High intra-equity co-movement — {pairs} — means the equity sleeves "
        "largely share a single global market beta.' must be present."
    )


def test_bond_equity_regime_lead(corr_app: AppTest) -> None:
    """Bond-equity regime block must lead with the post-2020 shift sentence.
    Pinned: Phase 45B Item B3b."""
    if corr_app.exception:
        pytest.skip("Page raised an exception — data-dependent test skipped.")
    all_md = " ".join(m.value for m in corr_app.markdown)
    assert "Bond–equity correlations have shifted materially since 2020" in all_md, (
        "Bond-equity regime lead not found — Phase 45B Item B3b regression: "
        "'Bond–equity correlations have shifted materially since 2020.' "
        "must open the bond-equity regime block."
    )


def test_asset_evaluation_cross_reference(corr_app: AppTest) -> None:
    """Page must contain a cross-reference to the Asset Evaluation page.
    Pinned: Phase 45B Item B4."""
    if corr_app.exception:
        pytest.skip("Page raised an exception — data-dependent test skipped.")
    all_text = " ".join(
        c.value for c in corr_app.caption
        if hasattr(c, "value") and c.value
    )
    assert "Asset Evaluation page" in all_text, (
        "Asset Evaluation cross-reference not found — Phase 45B Item B4 regression: "
        "'The same rolling-window methodology underlies the correlation analysis "
        "in the Asset Evaluation page...' must be present."
    )


def test_pair_reference_line_phrasing(corr_pair_app: AppTest) -> None:
    """Pair time-series caption must use 'reference line' framing, not 'threshold'.
    Pinned: Phase 45A Item A3."""
    if corr_pair_app.exception:
        pytest.skip("Page raised an exception — data-dependent test skipped.")
    all_text = " ".join(
        c.value for c in corr_pair_app.caption
        if hasattr(c, "value") and c.value
    )
    assert "diversification threshold" not in all_text, (
        "Old 'diversification threshold' phrasing still present — Phase 45A Item A3 "
        "regression. Should be 'ρ = 0 is a reference; values above zero co-move, "
        "values below offset.'"
    )
    assert "values above zero co-move" in all_text, (
        "'values above zero co-move' reference-line phrasing not found — "
        "Phase 45A Item A3 regression."
    )
