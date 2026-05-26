"""Render-aware regression tests for the Trade Log page (pages/10_Trade_Log.py).

Pinned at Phase 47 — protects the framing additions (How to read expander,
Methodology expander, Themes tab framing paragraph) and the analytically
critical Exit/Invalidation distinction prose from accidental reversion.

The page reads thesis prose from SQLite (demo.db in CI, tracker.db locally).
T8 (Core Fixed Income cross-reference with VGIT Research rationale) verified
consistent — thesis prose and VGIT duration framing tell the same story from
two angles. T11 (em-dash sweep) confirmed no-op — all view_summary fields
clean.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def trade_log_app() -> AppTest:
    """Run the Trade Log page."""
    at = AppTest.from_file("pages/10_Trade_Log.py", default_timeout=60)
    at.run()
    return at


def test_trade_log_runs_without_exception(trade_log_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not trade_log_app.exception, (
        f"Trade Log page raised: {trade_log_app.exception}"
    )


def test_how_to_read_expander_present(trade_log_app: AppTest) -> None:
    """'How to read this page' expander must be present (collapsed by default).
    Pinned: Phase 47 Item B1."""
    labels = [e.label for e in trade_log_app.expander]
    assert any("How to read this page" in lbl for lbl in labels), (
        "'How to read this page' expander not found — Phase 47 Item B1 regression. "
        f"Expander labels found: {labels}"
    )


def test_hierarchy_bullet(trade_log_app: AppTest) -> None:
    """Hierarchy bullet must explain the trade→thesis→theme lineage.
    Pinned: Phase 47 Item B1."""
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "every trade is linked to a Position Thesis" in all_md, (
        "Hierarchy bullet not found — Phase 47 Item B1 regression: "
        "'every trade is linked to a Position Thesis (the vehicle-level rationale...)' "
        "must be present in the How to read expander."
    )


def test_cash_spaxx_five_star_claim(trade_log_app: AppTest) -> None:
    """Conviction-stars bullet must name Cash/SPAXX as the only 5-star thesis.
    Pinned: Phase 47 Item B1 (prose-data agreement: requires Commit A conviction fix)."""
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "Cash/SPAXX is the only 5-star" in all_md, (
        "Cash/SPAXX 5-star claim not found — Phase 47 Item B1 regression: "
        "'Cash/SPAXX is the only 5-star — not because it's the best return idea...' "
        "must be present. Also verify data/demo.db conviction=5 for Cash/SPAXX thesis."
    )


def test_exit_invalidation_distinction(trade_log_app: AppTest) -> None:
    """Active/Closed/Invalidated bullet must define Exit vs Invalidation as distinct.
    Pinned: Phase 47 Item B1 — the most important institutional nuance on this page."""
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "Exit conditions are expected unwind triggers" in all_md, (
        "Exit/Invalidation distinction not found — Phase 47 Item B1 regression: "
        "'Exit conditions are expected unwind triggers; invalidation conditions are "
        "the scenarios that prove the original case analytically wrong.' must be present."
    )


def test_themes_bullet_allocator_categories(trade_log_app: AppTest) -> None:
    """Themes bullet in 'How to read' must frame themes as allocator categories.
    Pinned: Phase 47 Item B1."""
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "allocator categories, not factor exposures" in all_md, (
        "'allocator categories, not factor exposures' not found — Phase 47 Item B1 "
        "regression: Themes bullet must be present in the How to read expander."
    )


def test_methodology_expander_present(trade_log_app: AppTest) -> None:
    """'Methodology' expander must be present at the bottom of the Theses tab.
    Pinned: Phase 47 Item B2."""
    labels = [e.label for e in trade_log_app.expander]
    assert any(lbl == "Methodology" for lbl in labels), (
        "'Methodology' expander not found — Phase 47 Item B2 regression. "
        f"Expander labels found: {labels}"
    )


def test_methodology_thesis_taxonomy_lead(trade_log_app: AppTest) -> None:
    """Methodology expander must open with the thesis taxonomy description.
    Pinned: Phase 47 Item B2."""
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "Thesis taxonomy" in all_md, (
        "Methodology thesis taxonomy lead not found — Phase 47 Item B2 regression: "
        "'Thesis taxonomy — three levels stored separately.' must be present."
    )


def test_themes_tab_framing_lead(trade_log_app: AppTest) -> None:
    """Themes tab framing paragraph must open with the allocator-categories lead.
    Pinned: Phase 47 Item B3."""
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "Themes are allocator categories, not return drivers" in all_md, (
        "Themes tab framing lead not found — Phase 47 Item B3 regression: "
        "'Themes are allocator categories, not return drivers.' must open the "
        "Themes tab framing paragraph."
    )


def test_themes_tab_reits_example(trade_log_app: AppTest) -> None:
    """Themes tab framing must include the REITs multi-tag example.
    Pinned: Phase 47 Item B3."""
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "inflation-correlated returns (Regime change) and equity-like diversification drag" in all_md, (
        "Themes tab REITs example not found — Phase 47 Item B3 regression: "
        "'REITs provide both inflation-correlated returns (Regime change) and "
        "equity-like diversification drag' must be present."
    )


def test_core_fi_thesis_lead(trade_log_app: AppTest) -> None:
    """Core Fixed Income thesis lead sentence must be present.
    Pinned: Phase 47 T8 verification — cross-reference with Research VGIT rationale
    confirmed consistent; pinning protects against future prose drift.
    DB-dependent: guards against regression in asset_classes.rationale."""
    if trade_log_app.exception:
        pytest.skip("Page raised an exception — DB-dependent test skipped.")
    all_md = " ".join(m.value for m in trade_log_app.markdown)
    assert "Duration as recession ballast, sized for an aggressive growth portfolio" in all_md, (
        "Core Fixed Income thesis lead not found — Phase 47 T8 regression anchor: "
        "'Duration as recession ballast, sized for an aggressive growth portfolio.' "
        "must be present in the Theses tab."
    )
