"""Layer 3: metric tile arrow direction matches the sign of the comparison value.

Streamlit st.metric determines the arrow direction from the delta string:
  - starts with '-' → ↓ (red)
  - any other prefix  → ↑ (green)

Tiles q1 and m4 on the Performance page use format strings where the numeric
value now leads:
  f"{_pct(benchmark_val)} S&P 500"   (q1 delta)
  f"{_pct(port_si)} SI cumulative"   (m4 delta)

_pct(negative) produces "-X.XX%" which starts with '-' → correct ↓
_pct(positive) produces "X.XX%"  which starts with a digit → correct ↑
"""
from __future__ import annotations

import pytest


def _pct(v: float, decimals: int = 2) -> str:
    """Mirror of pages/2_Performance.py::_pct."""
    return f"{v * 100:.{decimals}f}%"


# ── delta format helpers ───────────────────────────────────────────────────────

def _q1_delta(sp_return: float) -> str:
    return f"{_pct(sp_return)} S&P 500"


def _m4_delta(si_cumulative: float) -> str:
    return f"{_pct(si_cumulative)} SI cumulative"


# ── arrow direction tests ──────────────────────────────────────────────────────

@pytest.mark.parametrize("sp_return", [-0.05, -0.001, -0.5, -0.0001])
def test_q1_delta_negative_starts_with_minus(sp_return):
    """Negative S&P 500 comparison → delta string starts with '-' → ↓ arrow."""
    delta = _q1_delta(sp_return)
    assert delta.startswith("-"), (
        f"q1 delta for S&P {sp_return:.4f} is {delta!r}; "
        "must start with '-' so Streamlit renders a ↓ arrow."
    )


@pytest.mark.parametrize("sp_return", [0.05, 0.001, 0.3, 0.0])
def test_q1_delta_nonnegative_does_not_start_with_minus(sp_return):
    """Non-negative S&P 500 comparison → delta string does NOT start with '-' → ↑ arrow."""
    delta = _q1_delta(sp_return)
    assert not delta.startswith("-"), (
        f"q1 delta for S&P {sp_return:.4f} is {delta!r}; "
        "must not start with '-' for a non-negative return."
    )


@pytest.mark.parametrize("si_return", [-0.10, -0.002, -0.999])
def test_m4_delta_negative_starts_with_minus(si_return):
    """Negative SI cumulative return → delta string starts with '-' → ↓ arrow."""
    delta = _m4_delta(si_return)
    assert delta.startswith("-"), (
        f"m4 delta for SI {si_return:.4f} is {delta!r}; "
        "must start with '-' so Streamlit renders a ↓ arrow."
    )


@pytest.mark.parametrize("si_return", [0.27, 0.001, 0.0])
def test_m4_delta_nonnegative_does_not_start_with_minus(si_return):
    """Non-negative SI cumulative return → delta string does NOT start with '-' → ↑ arrow."""
    delta = _m4_delta(si_return)
    assert not delta.startswith("-"), (
        f"m4 delta for SI {si_return:.4f} is {delta!r}; "
        "must not start with '-' for a non-negative return."
    )


def test_q1_delta_format_contains_sp500_label():
    """q1 delta string must contain 'S&P 500' as contextual label."""
    delta = _q1_delta(0.05)
    assert "S&P 500" in delta, f"q1 delta {delta!r} missing 'S&P 500' label."


def test_m4_delta_format_contains_si_label():
    """m4 delta string must contain 'SI cumulative' as contextual label."""
    delta = _m4_delta(0.27)
    assert "SI cumulative" in delta, f"m4 delta {delta!r} missing 'SI cumulative' label."
