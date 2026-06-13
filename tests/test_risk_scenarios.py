"""
Tests for the Phase 2 scenario / stress engine in src/risk.py.

All inputs are injected (betas, durations, current_mv, scenario defs): NO live
fetch, deterministic, demo-CI-safe. The engine CONSUMES a Phase 1
``run_portfolio_factor_regression`` result dict — these tests construct minimal
result dicts directly rather than running the regression, mirroring how the page
hands Phase 1's output to Phase 2.

Critical tests:
  - Translation-correctness (dimensional): a rate shock applies IEF DURATION,
    not raw bps × beta — the first thing a reviewer checks.
  - Empty-state INHERITANCE: a Phase-1 insufficient-history sentinel yields the
    scenario empty-state, never garbage P&L on non-existent betas (#38-analog).
  - 2022 vs risk-off directional pin: encodes the right correlations (hedge
    inverts vs flight-to-quality offset).
"""
from __future__ import annotations

import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.risk import (
    HY_SPREAD_DURATION,
    IEF_MODIFIED_DURATION,
    MIN_OBS_FOR_REGRESSION,
    STRESS_SCENARIOS,
    run_scenarios,
    scenario_insufficient_history_message,
)

# A representative "ok" Phase-1 result, betas roughly matching the demo book
# (positive market, small size/value, positive rates, small credit).
_OK_BETAS = {"Mkt-RF": 0.70, "SMB": 0.10, "HML": 0.15, "RATES": 0.35, "CREDIT": 0.08}
_DUR = {"rates": 7.5, "credit": 3.5}


def _ok_result(betas=None, low_confidence=False, n=250):
    return {
        "status": "ok",
        "low_confidence": low_confidence,
        "betas": dict(betas or _OK_BETAS),
        "n": n,
    }


def _scenario_by_name(result, needle):
    return next(s for s in result["scenarios"] if needle in s["name"])


def _leg(scenario, factor):
    return next(l for l in scenario["legs"] if l["factor"] == factor)


# ── Translation correctness: the dimensional pin ───────────────────────────────

def test_rates_leg_applies_duration_not_raw_bps():
    """+100bps rate shock → rates factor = −duration × Δy = −7.5 × 0.01 = −0.075;
    contribution = β_RATES × (−0.075). NOT raw 0.01 × β (that would be dimensional
    nonsense)."""
    res = run_scenarios(_ok_result(), scenarios=STRESS_SCENARIOS, durations=_DUR)
    rate_sc = _scenario_by_name(res, "Rate shock")
    leg = _leg(rate_sc, "RATES")

    assert leg["factor_return"] == pytest.approx(-7.5 * 0.01)        # −0.075
    assert leg["contribution"] == pytest.approx(0.35 * (-0.075))     # ≈ −0.026
    # Guard against the raw-bps bug: contribution must NOT equal β × 0.01.
    assert leg["contribution"] != pytest.approx(0.35 * 0.01)


def test_credit_leg_applies_spread_duration():
    """+150bps HY spread → credit factor = −3.5 × 0.015 = −0.0525; contribution =
    β_CREDIT × that."""
    res = run_scenarios(_ok_result(), durations=_DUR)
    credit_sc = _scenario_by_name(res, "Credit widening")
    leg = _leg(credit_sc, "CREDIT")

    assert leg["factor_return"] == pytest.approx(-3.5 * 0.015)       # −0.0525
    assert leg["contribution"] == pytest.approx(0.08 * (-0.0525))


def test_equity_leg_is_direct_market_realization():
    """−20% equity → Mkt-RF factor = −0.20 directly (no duration); contribution =
    β_Mkt × (−0.20). SMB/HML are NOT fabricated."""
    res = run_scenarios(_ok_result(), durations=_DUR)
    eq_sc = _scenario_by_name(res, "Equity drawdown")
    leg = _leg(eq_sc, "Mkt-RF")

    assert leg["factor_return"] == pytest.approx(-0.20)
    assert leg["contribution"] == pytest.approx(0.70 * -0.20)
    # Equity scenario shocks ONLY Mkt-RF — no invented SMB/HML legs.
    assert [l["factor"] for l in eq_sc["legs"]] == ["Mkt-RF"]


def test_translation_chain_is_shown():
    """Each leg carries a human-readable translation chain (the rigor signal)."""
    res = run_scenarios(_ok_result(), durations=_DUR)
    rate_leg = _leg(_scenario_by_name(res, "Rate shock"), "RATES")
    assert "IEF duration 7.5y" in rate_leg["chain"]
    assert "β" in rate_leg["chain"]


# ── Total P&L and dollar conversion ────────────────────────────────────────────

def test_total_pnl_is_sum_of_legs_and_dollars_scale_on_mv():
    """Total % == Σ leg contributions; $ == total% × current_mv."""
    mv = 200_000.0
    res = run_scenarios(_ok_result(), current_mv=mv, durations=_DUR)
    for sc in res["scenarios"]:
        assert sc["total_pct"] == pytest.approx(sum(l["contribution"] for l in sc["legs"]))
        assert sc["total_usd"] == pytest.approx(sc["total_pct"] * mv)
    assert res["current_mv"] == mv


def test_dollars_suppressed_without_mv():
    """No/zero MV → total_usd is None (percent-only), never a bogus $0 figure."""
    res = run_scenarios(_ok_result(), current_mv=None, durations=_DUR)
    assert res["current_mv"] is None
    assert all(s["total_usd"] is None for s in res["scenarios"])


def test_five_scenarios_returned():
    res = run_scenarios(_ok_result(), durations=_DUR)
    assert res["status"] == "ok"
    assert len(res["scenarios"]) == 5


# ── Empty-state inheritance: the #38-analog, gated on Phase 1 ──────────────────

def test_insufficient_history_inherited_no_scenarios():
    """Phase-1 insufficient_history input → scenario empty-state, NO scenarios,
    never shocks non-existent betas."""
    phase1 = {"status": "insufficient_history", "n": 0, "min_obs": MIN_OBS_FOR_REGRESSION}
    res = run_scenarios(phase1, current_mv=100_000.0)

    assert res["status"] == "insufficient_history"
    assert res["n"] == 0
    assert "scenarios" not in res


def test_insufficient_history_on_none_input():
    """A None/empty Phase-1 result degrades to the empty-state, not a crash."""
    assert run_scenarios(None)["status"] == "insufficient_history"
    assert run_scenarios({})["status"] == "insufficient_history"


def test_scenario_empty_state_copy_pinned():
    """Empty-state copy must lead with the betas-unavailable phrasing (literal)."""
    msg = scenario_insufficient_history_message(0)
    assert msg.startswith(
        "Insufficient history — factor betas unavailable for stress testing"
    )


# ── Low-confidence inheritance ─────────────────────────────────────────────────

def test_low_confidence_carried_through():
    """Phase-1 low_confidence → scenarios computed WITH the flag set."""
    res = run_scenarios(_ok_result(low_confidence=True, n=45), durations=_DUR)
    assert res["status"] == "ok"
    assert res["low_confidence"] is True
    assert len(res["scenarios"]) == 5


# ── 2022 vs risk-off: the directional / correlation pin ────────────────────────

def test_2022_regime_both_legs_negative_compounded_loss():
    """2022 regime (rates +200bps AND equity −20%): with positive β_RATES, the
    rates leg is NEGATIVE (rates up hurts the long-duration FI sleeve) AND the
    equity leg is negative → a compounded loss. This is the hedge-fails case."""
    res = run_scenarios(_ok_result(), durations=_DUR)
    sc = _scenario_by_name(res, "2022")

    rates_leg  = _leg(sc, "RATES")
    equity_leg = _leg(sc, "Mkt-RF")
    assert rates_leg["contribution"] < 0        # rates up → loss on FI exposure
    assert equity_leg["contribution"] < 0       # equity down → loss
    assert sc["total_pct"] < 0
    # Compounded: total is worse than either leg alone.
    assert sc["total_pct"] < rates_leg["contribution"]
    assert sc["total_pct"] < equity_leg["contribution"]


def test_risk_off_rate_rally_offsets_equity():
    """Risk-off: the −50bps rate RALLY produces a POSITIVE rates contribution
    (β_RATES > 0) that OFFSETS part of the equity/credit loss — distinct from the
    2022 case where rates compound the loss. This guards the diversification-works
    vs diversification-fails distinction."""
    res = run_scenarios(_ok_result(), durations=_DUR)
    risk_off = _scenario_by_name(res, "Risk-off")
    regime22 = _scenario_by_name(res, "2022")

    rates_leg = _leg(risk_off, "RATES")
    assert rates_leg["factor_return"] > 0       # rally → positive rates factor
    assert rates_leg["contribution"] > 0        # offsets, not compounds
    # The rates sign is OPPOSITE between the two scenarios.
    assert _leg(regime22, "RATES")["contribution"] < 0 < rates_leg["contribution"]


# ── Module-level duration sourcing ─────────────────────────────────────────────

def test_ief_duration_sourced_from_positioning_table():
    """IEF duration is the maintained ETF-table value (single source of truth)."""
    from src.positioning import ETF_DURATION
    assert IEF_MODIFIED_DURATION == ETF_DURATION["IEF"] == 7.5
    assert HY_SPREAD_DURATION == 3.5
