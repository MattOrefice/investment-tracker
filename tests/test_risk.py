"""
Tests for src/risk.py — portfolio five-factor risk decomposition.

All tests inject fixture factor/price/portfolio data: NO live fetch (consistent
with the live-data-excluded-from-the-default-suite policy in pytest.ini). The
fixtures mirror production data SHAPE — real-ish daily return series with five
non-degenerate factors — so the regression exercises the same code path the
page does, not a synthetic all-zero edge case.

The critical test is the insufficient-history guard (test_insufficient_*): on a
short history the function MUST return the empty-state sentinel with no betas,
never unstable coefficients. This is the #38-analog discipline that keeps the
2-day personal portfolio from showing garbage factor exposures.
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.risk import (
    FACTORS,
    LOW_CONFIDENCE_OBS,
    MIN_OBS_FOR_REGRESSION,
    insufficient_history_message,
    run_portfolio_factor_regression,
)


# ── Fixtures: deterministic, production-shaped factor/price/portfolio data ──────

def _make_factor_data(n_bdays: int, seed: int = 7):
    """Return (ff_df, ief_prices, hyg_prices) on a shared business-day calendar.

    ff_df carries the Ken French column set (Mkt-RF, SMB, HML, RMW, CMA, RF) in
    decimal daily units; IEF/HYG are price LEVELS (the function derives returns).
    Every factor is non-degenerate (distinct random walks), matching the shape of
    real data rather than a collapsed all-zero case.
    """
    rng = pd.bdate_range("2024-01-01", periods=n_bdays)
    g = np.random.default_rng(seed)

    ff = pd.DataFrame(
        {
            "Mkt-RF": g.normal(0.0004, 0.009, n_bdays),
            "SMB":    g.normal(0.0001, 0.004, n_bdays),
            "HML":    g.normal(0.0000, 0.004, n_bdays),
            "RMW":    g.normal(0.0000, 0.003, n_bdays),
            "CMA":    g.normal(0.0000, 0.003, n_bdays),
            "RF":     np.full(n_bdays, 0.00017),  # ~4.5%/yr daily
        },
        index=rng,
    )

    ief_ret = g.normal(0.0001, 0.003, n_bdays)
    hyg_ret = g.normal(0.0002, 0.004, n_bdays)
    ief_prices = pd.Series(100.0 * np.cumprod(1.0 + ief_ret), index=rng)
    hyg_prices = pd.Series(100.0 * np.cumprod(1.0 + hyg_ret), index=rng)
    return ff, ief_prices, hyg_prices


def _make_portfolio(ff, ief_prices, hyg_prices, *, mkt_beta=1.0,
                    rates_beta=0.0, credit_beta=0.0, noise=0.0006, seed=11):
    """Build a portfolio VALUE series that loads on the factors by construction.

    R_p,daily = RF + mkt_beta·Mkt-RF + rates_beta·RATES + credit_beta·CREDIT + ε,
    where RATES = IEF_ret − RF and CREDIT = HYG_ret − IEF_ret on the same
    calendar. Returns a price-level Series (the function derives returns from it).
    """
    g = np.random.default_rng(seed)
    ief_ret = ief_prices.pct_change().fillna(0.0)
    hyg_ret = hyg_prices.pct_change().fillna(0.0)
    rates = ief_ret - ff["RF"]
    credit = hyg_ret - ief_ret

    r_p = (
        ff["RF"]
        + mkt_beta * ff["Mkt-RF"]
        + rates_beta * rates
        + credit_beta * credit
        + g.normal(0.0, noise, len(ff))
    )
    return pd.Series(1000.0 * np.cumprod(1.0 + r_p.values), index=ff.index)


def _run(n_bdays, **kw):
    ff, ief, hyg = _make_factor_data(n_bdays)
    pv = _make_portfolio(ff, ief, hyg, **kw)
    return run_portfolio_factor_regression(
        "2024-01-01", "2024-12-31",
        pv=pv, ff_df=ff, ief_prices=ief, hyg_prices=hyg,
    )


# ── Decomposition: sufficient history (n >= 60) ────────────────────────────────

def test_decomposition_returns_five_betas_and_fit_stats():
    """n >= 60 → status ok, five betas, R² in [0,1], residual share, n + window."""
    res = _run(80)

    assert res["status"] == "ok"
    assert res["low_confidence"] is False
    assert set(res["betas"]) == set(FACTORS)
    assert len(res["betas"]) == 5
    assert 0.0 <= res["r_squared"] <= 1.0
    assert res["residual_share"] == pytest.approx(1.0 - res["r_squared"], abs=1e-12)
    assert 0.0 <= res["residual_share"] <= 1.0
    assert res["n"] >= LOW_CONFIDENCE_OBS
    # Date range present and well-formed.
    assert res["sample_start"] == "2024-01-02"  # first bday dropped by pct_change
    assert res["sample_end"] <= "2024-12-31"
    # Every factor reports a t-stat and p-value.
    assert set(res["t_stats"]) == set(FACTORS)
    assert set(res["p_values"]) == set(FACTORS)


# ── Insufficient-history guard (n < 30): the critical #38-analog ───────────────

def test_insufficient_history_returns_sentinel_no_betas():
    """n < 30 → empty-state sentinel, NO betas, no regression run."""
    res = _run(25)  # ~24 aligned obs

    assert res["status"] == "insufficient_history"
    assert res["n"] < MIN_OBS_FOR_REGRESSION
    assert res["min_obs"] == MIN_OBS_FOR_REGRESSION
    assert "betas" not in res
    assert "r_squared" not in res


def test_insufficient_history_on_empty_portfolio():
    """A zero/empty portfolio value series returns the sentinel, never a crash."""
    empty = pd.Series(dtype=float)
    res = run_portfolio_factor_regression("2024-01-01", "2024-12-31", pv=empty)
    assert res["status"] == "insufficient_history"
    assert res["n"] == 0


def test_insufficient_history_message_pins_copy():
    """Empty-state copy must name the count and the ~30 minimum (literal pin)."""
    msg = insufficient_history_message(12)
    assert msg.startswith(
        "Insufficient history for factor decomposition — 12 trading days "
        "available, ~30 minimum."
    )


# ── Low-confidence boundary (30 <= n < 60): per-band, not all-or-nothing ───────

def test_low_confidence_band_returns_betas_with_flag():
    """30 <= n < 60 → betas returned WITH low_confidence=True."""
    res = _run(50)  # ~49 aligned obs

    assert res["status"] == "ok"
    assert res["low_confidence"] is True
    assert MIN_OBS_FOR_REGRESSION <= res["n"] < LOW_CONFIDENCE_OBS
    assert set(res["betas"]) == set(FACTORS)


def test_band_thresholds_fire_independently():
    """The three bands are distinct: insufficient < ok-low-conf < ok-full-conf."""
    assert _run(25)["status"] == "insufficient_history"

    low = _run(50)
    assert low["status"] == "ok" and low["low_confidence"] is True

    full = _run(80)
    assert full["status"] == "ok" and full["low_confidence"] is False


# ── Regression-correctness pin: actually simultaneous multi-factor OLS ─────────

def test_market_loaded_portfolio_recovers_positive_market_beta():
    """A portfolio built to load 1.0 on the market returns a significant positive
    Mkt-RF beta near 1.0 — proves it is a real multi-factor OLS, not univariate
    or broken. The rates/credit betas (zero by construction) stay near zero."""
    res = _run(120, mkt_beta=1.0, rates_beta=0.0, credit_beta=0.0, noise=0.0004)

    assert res["status"] == "ok"
    assert res["betas"]["Mkt-RF"] == pytest.approx(1.0, abs=0.15)
    assert res["t_stats"]["Mkt-RF"] > 2.0          # significant
    assert abs(res["betas"]["RATES"]) < 0.5        # near-zero, not significant load
    assert abs(res["betas"]["CREDIT"]) < 0.5


def test_rates_and_credit_loadings_are_recovered():
    """A portfolio built to load on RATES and CREDIT recovers those marginal
    betas — confirms all five factors enter simultaneously and the proxy
    construction (IEF excess, HYG−IEF) is wired correctly."""
    res = _run(150, mkt_beta=0.6, rates_beta=1.5, credit_beta=0.8, noise=0.0003)

    assert res["betas"]["Mkt-RF"] == pytest.approx(0.6, abs=0.2)
    assert res["betas"]["RATES"] == pytest.approx(1.5, abs=0.5)
    assert res["betas"]["CREDIT"] == pytest.approx(0.8, abs=0.5)
