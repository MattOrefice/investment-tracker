"""Render-aware regression tests for the Tax Lots page (pages/12_Tax_Lots.py).

Pinned at Phase 49 — protects the framing additions (subtitle, How to read
expander, Methodology expander, Sleeve Summary subtitle) from accidental reversion.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def tax_lots_app() -> AppTest:
    """Run the Tax Lots page."""
    at = AppTest.from_file("pages/12_Tax_Lots.py", default_timeout=60)
    at.run()
    return at


def _has_lot_data(app: AppTest) -> bool:
    """Return False if the page stopped early due to no lot data."""
    for info_el in app.info:
        if hasattr(info_el, "value") and "No lot data available" in (info_el.value or ""):
            return False
    return True


def test_tax_lots_runs_without_exception(tax_lots_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not tax_lots_app.exception, (
        f"Tax Lots page raised: {tax_lots_app.exception}"
    )


def test_subtitle_present(tax_lots_app: AppTest) -> None:
    """One-line subtitle must appear directly under the H1.
    Pinned: Phase 49 Item A1."""
    all_captions = " ".join(
        c.value for c in tax_lots_app.caption
        if hasattr(c, "value") and c.value
    )
    assert "Per-lot cost basis and unrealized gains" in all_captions, (
        "Subtitle not found — Phase 49 Item A1 regression: "
        "'Per-lot cost basis and unrealized gains' must appear as a caption under the title."
    )


def test_how_to_read_expander_present(tax_lots_app: AppTest) -> None:
    """'How to read this page' expander must be present, collapsed by default.
    Pinned: Phase 49 Item A2."""
    labels = [e.label for e in tax_lots_app.expander]
    assert any("How to read this page" in lbl for lbl in labels), (
        "'How to read this page' expander not found — Phase 49 Item A2 regression. "
        f"Expander labels found: {labels}"
    )


def test_how_to_read_drip_bullet(tax_lots_app: AppTest) -> None:
    """How to read expander must describe DRIP lot cost basis convention.
    Pinned: Phase 49 Item A2."""
    if not _has_lot_data(tax_lots_app):
        pytest.skip("No lot data available — data-dependent test skipped.")
    all_md = " ".join(m.value for m in tax_lots_app.markdown if hasattr(m, "value") and m.value)
    assert "payment-date closing price as cost basis" in all_md, (
        "DRIP bullet not found — Phase 49 Item A2 regression: "
        "'payment-date closing price as cost basis' must appear in How to read expander."
    )


def test_methodology_expander_present(tax_lots_app: AppTest) -> None:
    """'Methodology' expander must be present at the bottom of the page.
    Pinned: Phase 49 Item B1."""
    if not _has_lot_data(tax_lots_app):
        pytest.skip("No lot data available — data-dependent test skipped.")
    labels = [e.label for e in tax_lots_app.expander]
    assert any(lbl == "Methodology" for lbl in labels), (
        "'Methodology' expander not found — Phase 49 Item B1 regression. "
        f"Expander labels found: {labels}"
    )


def test_methodology_marginal_rate(tax_lots_app: AppTest) -> None:
    """Methodology expander must state the marginal tax rate assumptions.
    Pinned: Phase 49 Item B1."""
    if not _has_lot_data(tax_lots_app):
        pytest.skip("No lot data available — data-dependent test skipped.")
    all_md = " ".join(m.value for m in tax_lots_app.markdown if hasattr(m, "value") and m.value)
    assert "22% federal marginal rate" in all_md, (
        "Marginal rate assumption not found — Phase 49 Item B1 regression: "
        "'22% federal marginal rate' must appear in the Methodology expander."
    )


def test_methodology_wash_sale(tax_lots_app: AppTest) -> None:
    """Methodology expander must state the wash sale rule.
    Pinned: Phase 49 Item B1."""
    if not _has_lot_data(tax_lots_app):
        pytest.skip("No lot data available — data-dependent test skipped.")
    all_md = " ".join(m.value for m in tax_lots_app.markdown if hasattr(m, "value") and m.value)
    assert "within 30 days before or after the loss sale" in all_md, (
        "Wash sale rule not found — Phase 49 Item B1 regression: "
        "'within 30 days before or after the loss sale' must appear in the Methodology expander."
    )


def test_sleeve_summary_subtitle(tax_lots_app: AppTest) -> None:
    """Sleeve Summary section must have a descriptive subtitle caption.
    Pinned: Phase 49 Item C1."""
    if not _has_lot_data(tax_lots_app):
        pytest.skip("No lot data available — data-dependent test skipped.")
    all_captions = " ".join(
        c.value for c in tax_lots_app.caption
        if hasattr(c, "value") and c.value
    )
    assert "Tax-lot exposure aggregated by SAA sleeve" in all_captions, (
        "Sleeve Summary subtitle not found — Phase 49 Item C1 regression: "
        "'Tax-lot exposure aggregated by SAA sleeve' must appear as a caption "
        "under the Sleeve Summary subheader."
    )
