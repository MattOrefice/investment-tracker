"""
Tests for the Phase 3 risk-contribution engine in src/risk.py.

All inputs are injected (sleeve returns DataFrame, weights): NO live fetch,
deterministic, demo-CI-safe. Fixtures mirror production shape — real-ish daily
sleeve return series — not degenerate all-equal columns (except where a singular
matrix is the thing under test).

Critical pins:
  - EULER summation (Σ RC == σ_p, RC% == 100%): THE correctness proof a risk
    reviewer checks — if contributions don't sum to total vol, the math is wrong.
  - weight ≠ risk: a high-vol/high-correlation sleeve's RISK% exceeds its
    WEIGHT%; a diversifier's is below — proves the decomposition captures
    correlation, not just weight (and that it is NOT weight×vol).
  - insufficient-history AND singular-covariance empty states (#38-analog).
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.risk import (
    LOW_CONFIDENCE_OBS_RC,
    MIN_OBS_FOR_COVARIANCE,
    risk_contribution_insufficient_history_message,
    risk_contribution_low_confidence_caveat,
    run_risk_contribution,
)


def _returns(n=250, seed=3, cols=None, vols=None, corr_to_first=None):
    """Build a daily sleeve-return DataFrame with controllable vols/correlation.

    cols: sleeve names. vols: per-sleeve daily vol. corr_to_first: if set for a
    column, that column is built as a correlated blend with the first column.
    """
    cols = cols or ["A", "B", "C", "D"]
    vols = vols or {c: 0.01 for c in cols}
    g = np.random.default_rng(seed)
    base = {c: g.normal(0.0, vols[c], n) for c in cols}
    if corr_to_first:
        first = base[cols[0]] / (vols[cols[0]] or 1.0)  # unit-vol driver
        for c, rho in corr_to_first.items():
            idio = g.normal(0.0, 1.0, n)
            mixed = rho * first + np.sqrt(max(0.0, 1.0 - rho * rho)) * idio
            base[c] = mixed * vols[c]
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(base, index=idx)


def _equal_weights(cols):
    return {c: 1.0 / len(cols) for c in cols}


# ── EULER summation: the correctness pin ───────────────────────────────────────

def test_euler_contributions_sum_to_portfolio_vol():
    """Σ_i RC_i == σ_p (Euler) and Σ_i RC%_i == 100% — exactly."""
    cols = ["A", "B", "C", "D"]
    R = _returns(250, cols=cols, vols={"A": 0.012, "B": 0.008, "C": 0.02, "D": 0.006})
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))

    assert res["status"] == "ok"
    rc_sum = sum(s["risk_contribution"] for s in res["sleeves"])
    assert rc_sum == pytest.approx(res["portfolio_vol"], rel=1e-9)
    assert res["rc_sum_check"] == pytest.approx(res["portfolio_vol"], rel=1e-9)
    pct_sum = sum(s["risk_pct"] for s in res["sleeves"])
    assert pct_sum == pytest.approx(1.0, abs=1e-9)


def test_mcr_formula_matches_sigma_w_over_sigma_p():
    """MCR_i == (Σw)_i / σ_p on a known covariance (annualized)."""
    cols = ["A", "B", "C"]
    R = _returns(200, cols=cols, vols={"A": 0.01, "B": 0.015, "C": 0.02})
    w = _equal_weights(cols)
    res = run_risk_contribution(sleeve_returns=R, weights=w)

    cov = R.cov().values * 252.0
    wv = np.array([w[c] for c in cols])
    sigma_p = float(np.sqrt(wv @ cov @ wv))
    expected_mcr = (cov @ wv) / sigma_p

    by_name = {s["sleeve"]: s for s in res["sleeves"]}
    for i, c in enumerate(cols):
        assert by_name[c]["mcr"] == pytest.approx(expected_mcr[i], rel=1e-9)
        assert by_name[c]["risk_contribution"] == pytest.approx(w[c] * expected_mcr[i], rel=1e-9)


# ── weight ≠ risk: the analytical pin ──────────────────────────────────────────

def test_high_vol_sleeve_risk_exceeds_weight_diversifier_below():
    """Equal weights, but sleeve HIGHVOL has 4× the vol of the others and a
    diversifier DIVERS is negatively correlated to the driver. Assert HIGHVOL's
    RISK% > its WEIGHT% and DIVERS's RISK% < its WEIGHT% — the decomposition
    captures correlation/vol, not just weight (NOT weight×vol)."""
    cols = ["DRIVER", "HIGHVOL", "MID", "DIVERS"]
    vols = {"DRIVER": 0.01, "HIGHVOL": 0.04, "MID": 0.01, "DIVERS": 0.01}
    # HIGHVOL strongly co-moves with DRIVER; DIVERS strongly offsets it.
    R = _returns(300, cols=cols, vols=vols,
                 corr_to_first={"HIGHVOL": 0.8, "MID": 0.2, "DIVERS": -0.7})
    w = _equal_weights(cols)  # each 25%
    res = run_risk_contribution(sleeve_returns=R, weights=w)
    by = {s["sleeve"]: s for s in res["sleeves"]}

    assert by["HIGHVOL"]["risk_pct"] > by["HIGHVOL"]["weight"]   # 25% weight, >25% risk
    assert by["DIVERS"]["risk_pct"] < by["DIVERS"]["weight"]     # diversifier: <25% risk
    # And it is NOT weight×vol: equal weights would make weight×vol proportional to
    # vol alone, but DIVERS (same vol as DRIVER/MID) has LOWER risk% than MID due
    # to its negative correlation — a pure weight×vol model cannot produce that.
    assert by["DIVERS"]["risk_pct"] < by["MID"]["risk_pct"]


def test_risk_pct_sorted_descending():
    cols = ["A", "B", "C", "D"]
    R = _returns(250, cols=cols, vols={"A": 0.005, "B": 0.02, "C": 0.01, "D": 0.03})
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    pcts = [s["risk_pct"] for s in res["sleeves"]]
    assert pcts == sorted(pcts, reverse=True)


def test_annualization_scales_vol_by_sqrt_252():
    """Annualized portfolio vol == daily portfolio vol × √252."""
    cols = ["A", "B", "C"]
    R = _returns(200, cols=cols)
    w = _equal_weights(cols)
    ann = run_risk_contribution(sleeve_returns=R, weights=w, annualize=True)
    daily = run_risk_contribution(sleeve_returns=R, weights=w, annualize=False)
    assert ann["portfolio_vol"] == pytest.approx(daily["portfolio_vol"] * np.sqrt(252), rel=1e-9)


# ── Insufficient-history & singular-covariance empty states (#38-analog) ───────

def test_insufficient_history_short_sample():
    """n below the covariance floor → empty-state, no contributions, no garbage."""
    cols = ["A", "B", "C"]
    R = _returns(40, cols=cols)  # < 60 floor
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    assert res["status"] == "insufficient_history"
    assert res["n"] < MIN_OBS_FOR_COVARIANCE
    assert "sleeves" not in res


def test_singular_covariance_collinear_sleeves():
    """Perfectly collinear sleeves (rank-deficient Σ) with ample obs → empty-state,
    NOT a NaN/garbage decomposition or a LinAlg error."""
    cols = ["A", "B", "CLONE"]
    R = _returns(120, cols=["A", "B"])  # 120 > 60 floor
    R["CLONE"] = R["A"]                  # exact collinearity → singular covariance
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    assert res["status"] == "insufficient_history"
    assert res.get("reason") == "degenerate_covariance"
    assert "sleeves" not in res


def test_more_sleeves_than_obs_is_empty_state():
    """More sleeves than observations (n ≤ k territory) → empty-state, not error."""
    cols = [f"S{i}" for i in range(10)]
    R = _returns(8, cols=cols)  # 8 obs, 10 sleeves
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    assert res["status"] == "insufficient_history"
    assert "sleeves" not in res


def test_empty_state_copy_pinned():
    """Empty-state copy must lead with the risk-decomposition phrasing (literal)."""
    msg = risk_contribution_insufficient_history_message(12)
    assert msg.startswith("Insufficient history for risk decomposition")


# ── Production wiring sanity (weights default to SAA map, names align) ──────────

# ── Low-confidence band [60, 120): per-band, not all-or-nothing ────────────────
# Mirrors the analogous regression-band tests in tests/test_risk.py.

def test_full_confidence_above_band_threshold():
    """n >= 120 (LOW_CONFIDENCE_OBS_RC) → status ok, low_confidence False."""
    cols = ["A", "B", "C"]
    R = _returns(150, cols=cols)
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    assert res["status"] == "ok"
    assert res["low_confidence"] is False
    assert res["n"] >= LOW_CONFIDENCE_OBS_RC


def test_low_confidence_band_returns_contributions_with_flag():
    """60 <= n < 120 → contributions computed WITH low_confidence=True."""
    cols = ["A", "B", "C"]
    R = _returns(90, cols=cols)
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    assert res["status"] == "ok"
    assert res["low_confidence"] is True
    assert MIN_OBS_FOR_COVARIANCE <= res["n"] < LOW_CONFIDENCE_OBS_RC
    # Euler property still holds in the low-confidence band — the band flags
    # confidence, it does not change the decomposition.
    rc_sum = sum(s["risk_contribution"] for s in res["sleeves"])
    assert rc_sum == pytest.approx(res["portfolio_vol"], rel=1e-9)


def test_band_thresholds_fire_independently_for_risk_contribution():
    """The three bands are distinct: insufficient < ok-low-conf < ok-full-conf."""
    cols = ["A", "B", "C"]

    below_floor = run_risk_contribution(
        sleeve_returns=_returns(40, cols=cols), weights=_equal_weights(cols)
    )
    assert below_floor["status"] == "insufficient_history"

    low = run_risk_contribution(
        sleeve_returns=_returns(90, cols=cols), weights=_equal_weights(cols)
    )
    assert low["status"] == "ok" and low["low_confidence"] is True

    full = run_risk_contribution(
        sleeve_returns=_returns(150, cols=cols), weights=_equal_weights(cols)
    )
    assert full["status"] == "ok" and full["low_confidence"] is False


def test_band_boundary_at_covariance_floor_is_low_confidence():
    """n == 60 exactly (the covariance floor) → ok, NOT insufficient — the floor
    is inclusive on the 'ok' side — and low_confidence True (still below 120)."""
    cols = ["A", "B", "C"]
    R = _returns(MIN_OBS_FOR_COVARIANCE, cols=cols)
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    assert res["status"] == "ok"
    assert res["low_confidence"] is True


def test_band_boundary_at_low_confidence_ceiling_is_full_confidence():
    """n == 120 exactly (LOW_CONFIDENCE_OBS_RC) → full confidence, no caveat."""
    cols = ["A", "B", "C"]
    R = _returns(LOW_CONFIDENCE_OBS_RC, cols=cols)
    res = run_risk_contribution(sleeve_returns=R, weights=_equal_weights(cols))
    assert res["status"] == "ok"
    assert res["low_confidence"] is False


def test_low_confidence_caveat_copy_pinned():
    """Low-confidence caveat copy must name the count and the 120 threshold."""
    msg = risk_contribution_low_confidence_caveat(90)
    assert "90" in msg
    assert str(LOW_CONFIDENCE_OBS_RC) in msg
    assert "Low-confidence estimate" in msg


def test_weights_default_to_saa_map_when_injected_returns_match():
    """When weights are omitted, the SAA weight map is used; with matching sleeve
    columns the decomposition runs and the Euler property still holds."""
    from src.asset_evaluation import SLEEVE_WEIGHTS
    cols = list(SLEEVE_WEIGHTS.keys())
    R = _returns(200, cols=cols, vols={c: 0.01 for c in cols})
    res = run_risk_contribution(sleeve_returns=R)  # weights default to SLEEVE_WEIGHTS
    assert res["status"] == "ok"
    assert res["k"] == len(cols)
    assert sum(s["risk_pct"] for s in res["sleeves"]) == pytest.approx(1.0, abs=1e-9)
