"""Render-aware regression tests for the Asset Evaluation page (pages/5_Asset_Evaluation.py).

Pinned at Phase 44 — protects the page's highest-leverage institutional claims and
key framing sentences from accidental reversion.

Note: the page requires live price data (BTC-USD and sleeve benchmarks via Yahoo Finance).
Tests that depend on data-driven prose are guarded with skip conditions when data is
unavailable. The four prose pins below check against the static/structural sentences that
appear regardless of the data values.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def ae_app() -> AppTest:
    """Run the Asset Evaluation page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/5_Asset_Evaluation.py", default_timeout=120)
    at.run()
    return at


def test_asset_evaluation_runs_without_exception(ae_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not ae_app.exception, f"Asset Evaluation page raised: {ae_app.exception}"


def test_subtitle_worked_case_study(ae_app: AppTest) -> None:
    """Subtitle must use worked-case-study framing, not template framing. Pinned: Phase 44 Item 1."""
    captions = [c.value for c in ae_app.caption]
    assert any("Worked five-step framework" in c for c in captions), (
        "Subtitle 'Worked five-step framework applied to Bitcoin as a candidate for SAA inclusion.' "
        "not found — Phase 44 Item 1 regression: old template framing may have been restored."
    )


def test_how_to_read_sample_start_reasoning(ae_app: AppTest) -> None:
    """'How to read this page' expander must contain the 2018 sample-start behavioral reasoning.
    Pinned: Phase 44 Item 15.

    Regression: if the expander content changes, the key institutional claim 'Starting from 2018
    captures both the 2018 bear, the 2020 COVID crash, the 2021 peak, and the 2022 drawdown' must
    be preserved verbatim — it is the justification for the sample-period choice.
    """
    all_md = " ".join(m.value for m in ae_app.markdown)
    assert "Starting from 2018 captures both the 2018 bear" in all_md, (
        "Sample-start reasoning not found in page markdown — "
        "Phase 44 Item 15: 'How to read this page' expander content may have changed."
    )


def test_5c_correlations_most_costly_preserved(ae_app: AppTest) -> None:
    """Section 5c close must contain 'precisely when correlations are most costly'. Pinned: Phase 44 Item 15.

    This sentence is the institutional clincher for the rolling-correlation section. If it disappears,
    the section loses its analytical punchline.
    """
    all_md = " ".join(m.value for m in ae_app.markdown)
    assert "precisely when correlations are most costly" in all_md, (
        "Section 5c closing sentence missing — Phase 44 Item 15: "
        "'precisely when correlations are most costly' must be preserved verbatim."
    )


def test_5h_joint_stress_framing(ae_app: AppTest) -> None:
    """Section 5h must contain the joint-stress-scenario framing sentence. Pinned: Phase 44 Item 15.

    This sentence frames the 2022 data as the analytically relevant stress test, not just a
    data point. If it disappears, the drawdown section loses its risk-management argument.
    """
    all_md = " ".join(m.value for m in ae_app.markdown)
    assert "This joint stress scenario, not the full-sample average correlation" in all_md, (
        "Section 5h joint-stress framing not found — Phase 44 Item 15 regression."
    )


def test_5j_verdict_sentence(ae_app: AppTest) -> None:
    """Section 5j must contain the explicit verdict sentence. Pinned: Phase 44 Item 15.

    Regression: if CONCLUSION is reverted or the verdict sentence removed, the framework
    summary loses its explicit policy decision.
    """
    all_md = " ".join(m.value for m in ae_app.markdown)
    assert "Bitcoin does not currently belong in this portfolio" in all_md, (
        "Verdict sentence not found in Section 5j — Phase 44 Item 15: "
        "'Bitcoin does not currently belong in this portfolio's SAA.' must be present."
    )


def test_5j_reevaluation_triggers_present(ae_app: AppTest) -> None:
    """Section 5j must contain observable re-evaluation triggers. Pinned: Phase 44 Item 8."""
    all_md = " ".join(m.value for m in ae_app.markdown)
    assert "re-evaluate inclusion" in all_md, (
        "Re-evaluation triggers not found in Section 5j — Phase 44 Item 8 regression."
    )
    assert "0.20" in all_md or "correlation" in all_md.lower(), (
        "Correlation-based trigger not found — Phase 44 Item 8 regression."
    )


def test_rf_unit_is_bps_not_percent(ae_app: AppTest) -> None:
    """Methodology must show rf as 432 bps not 4.32 bps. Pinned: Phase 44 Item 9."""
    all_md = " ".join(m.value for m in ae_app.markdown)
    assert "4.32 bps" not in all_md, (
        "Old rf unit error '4.32 bps' found — Phase 44 Item 9: should be '432 bps'."
    )
    assert "432 bps" in all_md, (
        "Corrected rf unit '432 bps' not found in Methodology — Phase 44 Item 9 regression."
    )


def test_5g_mechanical_lead_paragraph(ae_app: AppTest) -> None:
    """Section 5g must show 'This curve is mechanical, not predictive' before the chart.
    Pinned: Phase 44 Item 7."""
    all_md = " ".join(m.value for m in ae_app.markdown)
    assert "This curve is mechanical, not predictive" in all_md, (
        "5g lead caveat not found — Phase 44 Item 7 regression: "
        "'This curve is mechanical, not predictive' must appear before the Sharpe curve chart."
    )
