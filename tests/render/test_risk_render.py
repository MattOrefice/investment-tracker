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


def test_risk_contribution_low_confidence_caveat_tracks_its_own_band(risk_app: AppTest) -> None:
    """The risk-contribution low-confidence caveat renders IF AND ONLY IF risk
    contribution's OWN result is in the [60, 120) band — derived from that result,
    two-sided.

    This test used to be "caveat absent at full confidence", resting on "personal
    mode never reaches this branch": the same inference #318 removed from the page.
    It passed in personal mode only because the page crashed before rendering any
    text. With the crash fixed, personal mode reaches the section at n ~ 70, which
    IS low confidence, so the caveat is correct there."""
    import src.risk as risk
    rc = risk.run_risk_contribution()
    all_text = _all_text(risk_app)
    shown = "stable covariance estimate" in all_text
    if rc["status"] == "insufficient_history":
        assert not shown, "caveat rendered with no decomposition to caveat"
    else:
        assert shown == bool(rc.get("low_confidence")), (
            f"caveat shown={shown} but risk contribution n={rc['n']} "
            f"low_confidence={rc.get('low_confidence')} — check the [60, 120) band gate")


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


# ── #318: risk contribution renders from its OWN status ────────────────────────
# The factor regression runs on the (lagging) Fama-French cache; risk contribution
# runs on prices. With current prices and a lagging factor file the regression is
# insufficient while risk contribution is `ok` — and the page used to INFER the
# latter's status from the former, reading an `ok` result's non-existent min_obs.

def _synthetic_rc(n_days: int):
    """The REAL run_risk_contribution over synthetic sleeve returns, so its result
    has the genuine shape for its band (ok at >= 60 days, insufficient below)."""
    import numpy as np
    import pandas as pd
    import src.risk as risk
    real = risk.run_risk_contribution
    rng = np.random.default_rng(318)
    sleeves = ["US Large Core", "Core Fixed Income", "Emerging Markets"]
    returns = pd.DataFrame(rng.normal(0, 0.01, (n_days, len(sleeves))), columns=sleeves,
                           index=pd.bdate_range("2026-06-09", periods=n_days))
    weights = {"US Large Core": 0.5, "Core Fixed Income": 0.3, "Emerging Markets": 0.2}
    return lambda *a, **k: real(sleeve_returns=returns, weights=weights)


def _render_with(monkeypatch, rc_days: int) -> AppTest:
    import streamlit as st
    import src.risk as risk
    lagging = {"status": "insufficient_history", "n": 14, "min_obs": 30}
    monkeypatch.setattr(risk, "run_portfolio_factor_regression", lambda *a, **k: lagging)
    monkeypatch.setattr(risk, "run_risk_contribution", _synthetic_rc(rc_days))
    st.cache_data.clear()
    return AppTest.from_file("pages/7_Risk.py", default_timeout=90).run()


def test_current_prices_with_a_lagging_factor_file_render_risk_contribution(monkeypatch):
    """The crash condition: regression insufficient (n=14, factor file lags),
    risk contribution ok (70 days of prices). The page must render the full risk
    contribution section, not raise KeyError: 'min_obs'."""
    at = _render_with(monkeypatch, rc_days=70)
    assert not at.exception, f"Risk page raised: {at.exception}"
    text = " ".join(str(m.value) for m in (*at.markdown, *at.caption, *at.info))
    assert "Factor betas are deliberately suppressed" in text      # regression: empty state
    assert any("Policy / SAA volatility" in str(m.label) for m in at.metric), \
        "risk contribution is ok, so its section must render in full"


def test_both_short_render_both_empty_states(monkeypatch):
    """Both insufficient: each section shows its own empty state, from its own n."""
    at = _render_with(monkeypatch, rc_days=40)
    assert not at.exception, f"Risk page raised: {at.exception}"
    assert not any("Policy / SAA volatility" in str(m.label) for m in at.metric)
    infos = " ".join(str(i.value) for i in at.info)
    assert "40" in infos, f"risk contribution's empty state must state ITS n (40): {infos}"
