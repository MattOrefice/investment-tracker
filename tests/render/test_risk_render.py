"""Render-aware test for the Risk page (pages/7_Risk.py).

The page must render without raising in BOTH modes: demo (~1yr history →
factor decomposition) and personal (2-day portfolio → insufficient-history
empty state). This test asserts a clean render and that the section scaffolding
(title, the 'How to read' framing, the proxy disclosure) is present regardless
of which band the data falls into — the same render-import guard the other
page render tests provide.

No live fetch: the page reuses the committed price/factor cache (the same path
the Benchmark Attribution and Factor Profile render tests already rely on).
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def risk_app() -> AppTest:
    at = AppTest.from_file("pages/7_Risk.py", default_timeout=90)
    at.run()
    return at


def test_risk_page_runs_without_exception(risk_app: AppTest) -> None:
    """Page must complete render without an unhandled exception in either mode."""
    assert not risk_app.exception, f"Risk page raised: {risk_app.exception}"


def test_risk_page_title_present(risk_app: AppTest) -> None:
    """The Risk title and factor-decomposition section header must render."""
    all_text = " ".join(m.value for m in risk_app.markdown)
    titles = " ".join(t.value for t in risk_app.title)
    subheaders = " ".join(s.value for s in risk_app.subheader)
    assert "Risk" in (titles + subheaders + all_text)
    assert "Factor decomposition" in subheaders


def test_how_to_read_expander_present(risk_app: AppTest) -> None:
    """'How to read this section' framing expander must be present."""
    labels = [e.label for e in risk_app.expander]
    assert any("How to read this section" in lbl for lbl in labels), (
        f"'How to read this section' expander not found. Labels: {labels}"
    )


def test_marginal_exposure_framing_present(risk_app: AppTest) -> None:
    """The simultaneous/marginal-exposure framing must be present — guards the
    core methodological claim (not five univariate regressions)."""
    all_md = " ".join(m.value for m in risk_app.markdown)
    assert "marginal exposure" in all_md, (
        "Marginal-exposure framing missing — the 'controlling for the other "
        "four' simultaneous-regression explanation must be on the page."
    )


def test_proxy_disclosure_present(risk_app: AppTest) -> None:
    """Rates/credit proxy disclosure must render in either band."""
    all_md = " ".join(m.value for m in risk_app.markdown)
    assert "IEF" in all_md and "HYG" in all_md, (
        "Proxy disclosure (IEF rates / HYG−IEF credit) not found on the page."
    )


def test_scenario_section_present(risk_app: AppTest) -> None:
    """The Phase 2 scenario stress-test section header must render below the
    decomposition (in BOTH the decomposition and empty-state branches)."""
    subheaders = " ".join(s.value for s in risk_app.subheader)
    assert "Scenario stress test" in subheaders, (
        f"Scenario stress-test section header missing. Subheaders: {subheaders}"
    )


def _all_text(app: AppTest) -> str:
    """Concatenate text across element types (markdown, captions, and the
    info/warning alerts that carry the empty-state copy) so assertions work in
    both the decomposition and empty-state branches."""
    chunks = []
    for attr in ("markdown", "caption", "info", "warning", "success", "error"):
        for el in getattr(app, attr, []):
            val = getattr(el, "value", None)
            if val:
                chunks.append(str(val))
    return " ".join(chunks).lower()


def test_risk_contribution_section_present(risk_app: AppTest) -> None:
    """The Phase 3 risk-contribution section header must render (both branches)."""
    subheaders = " ".join(s.value for s in risk_app.subheader)
    assert "Risk contribution" in subheaders, (
        f"Risk-contribution section header missing. Subheaders: {subheaders}"
    )


def test_all_three_sections_present(risk_app: AppTest) -> None:
    """All THREE Risk page sections must render in order without exception."""
    subheaders = [s.value for s in risk_app.subheader]
    for needed in ("Factor decomposition", "Scenario stress test", "Risk contribution"):
        assert needed in subheaders, f"Section '{needed}' missing. Got: {subheaders}"


def test_risk_contribution_behaves_per_band(risk_app: AppTest) -> None:
    """Risk-contribution renders correctly in BOTH branches:
      - decomposition present (demo) → the Euler/weight≠risk framing;
      - insufficient-history (personal) → the inherited empty-state, NOT a
        garbage/singular-matrix result."""
    all_text = _all_text(risk_app)
    if "insufficient history for risk decomposition" in all_text:
        assert "stable sleeve covariance matrix" in all_text
    else:
        assert "euler" in all_text and "10% of the risk" in all_text, (
            "Risk-contribution Euler / weight≠risk framing missing in the "
            "decomposition branch."
        )


def test_risk_contribution_chart_gated_with_table(risk_app: AppTest) -> None:
    """The weight-vs-risk bar chart is additive and gated identically to the table:
      - decomposition present (demo) → the chart's caption renders (proxy for the
        chart, which AppTest does not expose as a typed element);
      - insufficient-history (personal) → NO chart caption, only the empty-state.
    AppTest renders st.plotly_chart without raising (covered by the no-exception
    test); the chart caption is a deterministic present/absent marker."""
    all_text = _all_text(risk_app)
    chart_marker = "share of risk (navy) beside its share of capital"
    if "insufficient history for risk decomposition" in all_text:
        assert chart_marker not in all_text, (
            "Chart must be suppressed in the risk empty-state, like the table."
        )
    else:
        assert chart_marker in all_text, (
            "Weight-vs-risk chart caption missing in the decomposition branch."
        )


def test_risk_contribution_low_confidence_caveat_absent_at_full_confidence(risk_app: AppTest) -> None:
    """The risk-contribution low-confidence caveat must NOT render when the
    decomposition is present (demo has ~270+ obs, well above the 120 band
    ceiling) — the band is additive and must not surface at full confidence.
    Personal mode never reaches this branch (insufficient-history empty state)."""
    all_text = _all_text(risk_app)
    if "insufficient history for risk decomposition" not in all_text:
        assert "stable covariance estimate" not in all_text, (
            "Risk-contribution low-confidence caveat rendered even though the "
            "decomposition is at full confidence — check the [60, 120) band gate."
        )


def test_scenario_section_behaves_per_band(risk_app: AppTest) -> None:
    """The scenario section must render correctly in BOTH branches:
      - decomposition present (demo) → the honesty-discipline framing
        (instantaneous, not forecasts) guards against implying prediction;
      - insufficient-history (personal) → the inherited empty-state, NOT garbage
        P&L (betas unavailable for stress testing).
    Exactly one branch is active; assert the active one is correct."""
    all_text = _all_text(risk_app)
    if "unavailable for stress testing" in all_text:
        # Phase-2 inherits Phase-1's empty state — no scenario P&L rendered.
        assert "factor betas unavailable for stress testing" in all_text
    else:
        assert "instantaneous" in all_text and "not forecasts" in all_text, (
            "Scenario instantaneous/not-forecasts framing missing in the "
            "decomposition branch."
        )
