"""Render-aware tests for the Macro Dashboard.

These tests assert on rendered widget content using streamlit.testing.v1.AppTest.
They complement the math tests in tests/ — these confirm each panel actually
renders with the expected text and structure on first paint.

Regression pins document which Phase introduced each panel so that future
regressions surface a clear attribution in the failure message.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def macro_app() -> AppTest:
    """Run the Macro page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/3_Macro.py", default_timeout=60)
    at.run()
    return at


@pytest.mark.live_data
def test_macro_runs_without_exception(macro_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not macro_app.exception, f"Macro page raised: {macro_app.exception}"


@pytest.mark.live_data
def test_cape_panel_renders(macro_app: AppTest) -> None:
    """CAPE panel renders with heading. Pinned: always present."""
    headings = [m.value for m in macro_app.markdown]
    assert any("CAPE" in h for h in headings), (
        "CAPE panel heading not found in rendered markdown"
    )


@pytest.mark.live_data
def test_ecy_panel_renders(macro_app: AppTest) -> None:
    """ECY panel renders between CAPE and yield curve. Pinned: Phase 8k."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Excess CAPE Yield" in h or "ECY" in h for h in headings), (
        "ECY panel heading not found — possible Phase 8k regression"
    )


@pytest.mark.live_data
def test_yield_curve_panel_renders(macro_app: AppTest) -> None:
    """2/10 yield curve spread panel renders. Pinned: Phase 5."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Yield Curve" in h or "2/10" in h for h in headings), (
        "Yield Curve panel heading not found"
    )


@pytest.mark.live_data
def test_fed_funds_panel_renders(macro_app: AppTest) -> None:
    """Federal Funds Rate panel renders. Pinned: Phase 5."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Federal Funds" in h or "Fed Funds" in h for h in headings), (
        "Fed Funds panel heading not found"
    )


@pytest.mark.live_data
def test_hy_oas_panel_renders(macro_app: AppTest) -> None:
    """HY credit spreads panel renders. Pinned: Phase 5."""
    headings = [m.value for m in macro_app.markdown]
    assert any("HY" in h and ("OAS" in h or "Credit" in h) for h in headings), (
        "HY OAS panel heading not found"
    )


@pytest.mark.live_data
def test_force_refresh_button_present(macro_app: AppTest) -> None:
    """Force refresh button renders in personal mode; hidden in demo mode (write guard)."""
    from src.config import IS_DEMO
    button_labels = [b.label for b in macro_app.button]
    if IS_DEMO:
        assert not any("refresh" in label.lower() for label in button_labels), (
            "Force refresh button must be hidden in demo mode — demo write guard."
        )
    else:
        assert any("refresh" in label.lower() for label in button_labels), (
            "Force refresh button missing in personal mode — possible Phase 8l regression."
        )


@pytest.mark.live_data
def test_data_freshness_disclosure_present(macro_app: AppTest) -> None:
    """Data-source freshness expander must render. Pinned: Phase 5."""
    expander_labels = [e.label for e in macro_app.expander]
    assert any("freshness" in lbl.lower() or "data source" in lbl.lower()
               for lbl in expander_labels), (
        f"Data-source freshness disclosure not found in expanders: {expander_labels}"
    )


@pytest.mark.live_data
def test_build_caption_suppressed_without_env(macro_app: AppTest) -> None:
    """Build caption must be absent when SHOW_BUILD_HASH env var is not set (Phase 15.1)."""
    import os
    if os.environ.get("SHOW_BUILD_HASH"):
        pytest.skip("SHOW_BUILD_HASH is set — caption gating not active in this environment")
    captions = [c.value for c in macro_app.caption]
    assert not any("Build" in c for c in captions), (
        f"Build caption should be suppressed but found: {[c for c in captions if 'Build' in c]}"
    )


# ── Phase 33 — new indicator panels ─────────────────────────────────────────

@pytest.mark.live_data
def test_ig_oas_panel_renders(macro_app: AppTest) -> None:
    """IG credit spreads panel renders. Pinned: Phase 33."""
    headings = [m.value for m in macro_app.markdown]
    assert any("IG" in h and "OAS" in h for h in headings), (
        "IG OAS panel heading not found — possible Phase 33 regression"
    )


@pytest.mark.live_data
def test_ccc_oas_panel_renders(macro_app: AppTest) -> None:
    """CCC credit spreads panel renders. Pinned: Phase 33."""
    headings = [m.value for m in macro_app.markdown]
    assert any("CCC" in h for h in headings), (
        "CCC OAS panel heading not found — possible Phase 33 regression"
    )


@pytest.mark.live_data
def test_breakeven_panel_renders(macro_app: AppTest) -> None:
    """10-Year Breakeven Inflation (T10YIE) panel renders. Pinned: Phase 33."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Breakeven" in h or "T10YIE" in h for h in headings), (
        "Breakeven inflation panel heading not found — possible Phase 33 regression"
    )


@pytest.mark.live_data
def test_real_10y_panel_renders(macro_app: AppTest) -> None:
    """Real 10-Year Treasury Yield (DFII10) panel renders. Pinned: Phase 33."""
    headings = [m.value for m in macro_app.markdown]
    assert any("DFII10" in h or "Real 10-Year" in h for h in headings), (
        "Real 10Y yield panel heading not found — possible Phase 33 regression"
    )


@pytest.mark.live_data
def test_pmi_proxy_panel_renders(macro_app: AppTest) -> None:
    """Manufacturing activity / PMI proxy (CFNAIDIFF) panel renders. Pinned: Phase 33."""
    headings = [m.value for m in macro_app.markdown]
    assert any("CFNAIDIFF" in h or "PMI" in h or "Manufacturing" in h for h in headings), (
        "PMI proxy panel heading not found — possible Phase 33 regression"
    )


@pytest.mark.live_data
def test_rate_vol_panel_renders(macro_app: AppTest) -> None:
    """Rate volatility panel (DGS10 rolling 21-day) renders. Pinned: Phase 33."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Rate Volatility" in h or "DGS10" in h for h in headings), (
        "Rate volatility panel heading not found — possible Phase 33 regression"
    )


@pytest.mark.live_data
def test_usd_index_panel_renders(macro_app: AppTest) -> None:
    """Broad trade-weighted USD index (DTWEXBGS) panel renders. Pinned: Phase 33."""
    headings = [m.value for m in macro_app.markdown]
    assert any("DTWEXBGS" in h or "USD Index" in h or "Trade-Weighted" in h for h in headings), (
        "USD index panel heading not found — possible Phase 33 regression"
    )


# ── Phase 38 — licensing and hygiene pins ────────────────────────────────────

@pytest.mark.live_data
def test_ice_data_indices_disclosure_present(macro_app: AppTest) -> None:
    """Data-sources expander must contain the ICE Data Indices licensing disclosure. Pinned: Phase 38."""
    captions = [c.value for c in macro_app.caption]
    assert any("ICE Data Indices" in c for c in captions), (
        "ICE Data Indices licensing disclosure not found in macro page captions — "
        "possible Phase 38 regression"
    )


# ── Phase 35 — polish additions ──────────────────────────────────────────────

@pytest.mark.live_data
def test_treasury_yield_curve_panel_renders(macro_app: AppTest) -> None:
    """Treasury Yield Curve spot chart panel renders. Pinned: Phase 35."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Treasury Yield Curve" in h for h in headings), (
        "Treasury Yield Curve panel heading not found — possible Phase 35 regression"
    )


@pytest.mark.live_data
def test_fomc_calendar_renders(macro_app: AppTest) -> None:
    """2026 FOMC Meeting Calendar section renders. Pinned: Phase 35."""
    headings = [m.value for m in macro_app.markdown]
    assert any("FOMC" in h for h in headings), (
        "FOMC calendar heading not found — possible Phase 35 regression"
    )


@pytest.mark.live_data
def test_oas_methodology_expander_renders(macro_app: AppTest) -> None:
    """Credit section OAS methodology expander renders. Pinned: Phase 35."""
    expander_labels = [e.label for e in macro_app.expander]
    assert any("credit spread" in lbl.lower() or "OAS" in lbl or "about credit" in lbl.lower()
               for lbl in expander_labels), (
        f"OAS methodology expander not found — possible Phase 35 regression. Expanders: {expander_labels}"
    )


@pytest.mark.live_data
def test_regime_transition_paragraph_renders(macro_app: AppTest) -> None:
    """Regime 'How regimes transition' is a collapsed expander. Pinned: Phase 35; updated Phase 43."""
    expander_labels = [e.label for e in macro_app.expander]
    assert any("How regimes transition" in l for l in expander_labels), (
        "Regime transition expander not found — Phase 43 moved this from inline markdown "
        "to a collapsed expander; if the label changed this test needs updating"
    )


@pytest.mark.live_data
def test_regime_methodology_paragraph_renders(macro_app: AppTest) -> None:
    """Regime Methodology is a collapsed expander. Pinned: Phase 35; updated Phase 43."""
    expander_labels = [e.label for e in macro_app.expander]
    assert any(l == "Methodology" for l in expander_labels), (
        "Regime Methodology expander not found — Phase 43 moved this from inline markdown "
        "to a collapsed expander; if the label changed this test needs updating"
    )


# ── Sector Leadership — relocated from the former Market Snapshot page ────────

@pytest.mark.live_data
def test_sector_leadership_section_renders(macro_app: AppTest) -> None:
    """Sector Leadership subsection renders with its window radio. Relocated from
    the former Market Snapshot page (Sector Movers)."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Sector Leadership" in h for h in headings), (
        "Sector Leadership heading not found — relocation regression"
    )
    radio_labels = [r.label for r in macro_app.radio]
    assert any(lbl == "Window" for lbl in radio_labels), (
        "Sector Leadership window radio not found"
    )


@pytest.mark.live_data
def test_trailing_pe_panel_renders(macro_app: AppTest) -> None:
    """Trailing P/E panel renders beside CAPE. Pinned: valuation-lenses batch."""
    headings = [m.value for m in macro_app.markdown]
    assert any("Trailing P/E" in h for h in headings), (
        "Trailing P/E panel heading not found in rendered markdown"
    )


@pytest.mark.live_data
def test_trailing_pe_source_and_asof_caption(macro_app: AppTest) -> None:
    """Trailing P/E must credit its source and carry an as-of date."""
    caps = " ".join(c.value for c in macro_app.caption if getattr(c, "value", None))
    assert "multpl.com, S&P 500 trailing-twelve-month P/E" in caps, (
        "Trailing P/E source caption missing"
    )
    assert "Recent months are provisional" in caps, (
        "Provisional-estimate disclosure missing from trailing P/E caption"
    )


@pytest.mark.live_data
def test_cape_vs_trailing_contrast_prose(macro_app: AppTest) -> None:
    """The teaching contrast between trailing P/E and CAPE must render."""
    md = " ".join(m.value for m in macro_app.markdown)
    assert "Reading the gap." in md, "trailing-vs-CAPE contrast prose missing"
    assert "Each lens's weakness." in md, "per-lens weakness prose missing"
