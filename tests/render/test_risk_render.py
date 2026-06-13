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
