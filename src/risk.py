"""
Portfolio factor-risk decomposition — the Risk page's first section.

What this does (Risk page, Phase 1)
-----------------------------------
Regresses the portfolio's daily *excess* return on five systematic factors
SIMULTANEOUSLY (a single multiple regression, not five univariate fits), so
each beta is the marginal exposure to that factor controlling for the others:

    (R_p − RF) ~ α + β₁·Mkt-RF + β₂·SMB + β₃·HML + β₄·RATES + β₅·CREDIT + ε

  Mkt-RF, SMB, HML  — Ken French US daily factors (the same data path the
                      sleeve regressions in src/factors.py already consume).
  RATES             — proxy: IEF (7–10y Treasury) excess return (IEF total
                      return − RF). Disclosed as an ETF-based proxy.
  CREDIT            — proxy: HYG − IEF (high-yield minus duration-matched
                      Treasury), isolating the credit-spread premium. Disclosed
                      as an ETF-based proxy.

Single source of truth
----------------------
The portfolio return series is the SAME settled-frontier-anchored series the
Performance page displays: ``get_portfolio_value_series`` clipped to
``last_settled_price_date`` (so a partial intraday today-bar is never the
right-edge anchor). The risk-free rate is Ken French's US daily RF — the same
RF every other regression in this app uses. No parallel return series, no
re-fetched risk-free.

Insufficient-history discipline (three bands, keyed on aligned-obs count n)
--------------------------------------------------------------------------
A five-factor regression on a handful of observations produces unstable,
misleading coefficients. The guard runs BEFORE the regression:

    n < 30            → status "insufficient_history"; NO betas returned.
    30 <= n < 60      → status "ok" with low_confidence=True (small-sample caveat).
    n >= 60           → status "ok" with low_confidence=False.

On the 2-day personal portfolio this returns the insufficient-history state
(the FF factor publication lag alone leaves zero overlap), NOT garbage betas.
On the ~1-year demo it returns the decomposition. This mirrors the
insufficient-history treatment already applied to period returns and the
benchmark attribution regression.

Standard errors: Newey-West HAC, L = floor(4·(T/100)^(2/9)), matching the rest
of the app's regression machinery (reused from src.factors).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

from src.factors import _nw_lags, load_factors
from src.holdings import (
    get_inception_date,
    get_portfolio_value_series,
    last_settled_price_date,
)
from src.positioning import ETF_DURATION
from src.prices import get_prices

# ── Model configuration ────────────────────────────────────────────────────────

# The five systematic factors, in display order. Mkt-RF/SMB/HML are Ken French
# US factors; RATES/CREDIT are the disclosed ETF proxies built below.
FACTORS = ["Mkt-RF", "SMB", "HML", "RATES", "CREDIT"]

# Obs-count bands (post-alignment n). The 30/60 thresholds match the
# insufficient-history guard used elsewhere in the app.
MIN_OBS_FOR_REGRESSION = 30   # below this: no betas, show empty-state
LOW_CONFIDENCE_OBS     = 60   # [30, 60): betas WITH a small-sample caveat

# Proxy tickers for the RATES and CREDIT factors (standard liquid ETFs already
# fetched by the FI-sleeve model in src/factors.py — same price/cache path).
_RATES_PROXY  = "IEF"   # iShares 7-10 Year Treasury — duration / rates factor
_CREDIT_PROXY = "HYG"   # iShares iBoxx High Yield Corporate — credit leg

# Disclosure strings surfaced on the page (single source of truth for the proxy
# wording so the methodology note and the caption cannot drift apart).
RATES_PROXY_DISCLOSURE  = (
    "Rates factor proxied by IEF (7–10y Treasury) excess return (IEF total "
    "return − risk-free)."
)
CREDIT_PROXY_DISCLOSURE = (
    "Credit factor proxied by HYG − IEF (high-yield over duration-matched "
    "Treasury), isolating the credit-spread premium."
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt_date(iso: str) -> str:
    """'2025-05-01' → 'May 1, 2025' (matches src.factors._fmt_date)."""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _daily_return(prices: pd.Series, date_range: pd.DatetimeIndex) -> pd.Series:
    """Calendar-daily total return of a price series over ``date_range``.

    Reindexes to every calendar day and forward-fills (so a Friday→Monday move
    is a single close-to-close return), then pct_change. The inner join against
    the Ken French trading-day calendar in the regression step drops the
    forward-filled weekend/holiday rows — identical to the sleeve and FI-sleeve
    return construction in src/factors.py.
    """
    s = prices.copy()
    s.index = pd.to_datetime(s.index)
    return s.reindex(date_range).ffill().pct_change()


def _proxy_returns(
    ticker: str,
    inception: str,
    end_date: str,
    date_range: pd.DatetimeIndex,
    injected: Optional[pd.Series],
) -> pd.Series:
    """Daily return series for a proxy ETF, using injected prices when supplied.

    ``injected`` is a price (level) Series for deterministic tests; when None the
    price is fetched via the shared get_prices cache path (adj_close, total-return
    basis) — the same path the FI-sleeve TERM/CREDIT model already uses for
    IEF/HYG.
    """
    if injected is not None:
        return _daily_return(injected, date_range)
    p = get_prices(ticker, inception, end_date)
    p.index = pd.to_datetime(p.index)
    px = p["adj_close"].fillna(p["close"])
    return _daily_return(px, date_range)


# ── Regression engine ──────────────────────────────────────────────────────────

def _ols_five_factor(R_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """Simultaneous five-factor OLS with Newey-West HAC standard errors.

    R_excess : daily portfolio excess returns (R_p − RF), decimal, DatetimeIndex.
    factors  : DataFrame with the five FACTORS columns, decimal, same index.

    All five factors enter ONE regression, so each beta is marginal — the
    exposure to that factor holding the other four fixed. Returns betas, HAC
    t-stats and p-values, R², the residual (unexplained) share = 1 − R², the
    intercept, and the obs count / lag length.
    """
    T = len(R_excess)
    L = _nw_lags(T)

    X = add_constant(factors[FACTORS])
    model   = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    r2 = float(res_hac.rsquared)

    return {
        "alpha_daily":      float(params["const"]),
        "alpha_annual_bps": float(params["const"]) * 252 * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":    {f: float(params[f]) for f in FACTORS},
        "t_stats":  {f: float(tvals[f])  for f in FACTORS},
        "p_values": {f: float(pvals[f])  for f in FACTORS},
        "r_squared":      r2,
        "adj_r_squared":  float(res_hac.rsquared_adj),
        "residual_share": 1.0 - r2,
        "T":       T,
        "nw_lags": L,
    }


def run_portfolio_factor_regression(
    inception: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    pv: Optional[pd.Series] = None,
    ff_df: Optional[pd.DataFrame] = None,
    ief_prices: Optional[pd.Series] = None,
    hyg_prices: Optional[pd.Series] = None,
) -> dict:
    """Five-factor portfolio risk decomposition with insufficient-history guard.

    Returns a status dict (never None) so the page can branch on the three bands:

      {"status": "insufficient_history", "n": <int>, "min_obs": 30}
          when fewer than 30 aligned observations survive — NO betas.

      {"status": "ok", "low_confidence": <bool>, "betas": {...}, "t_stats": {...},
       "p_values": {...}, "alpha_annual_bps", "t_alpha", "p_alpha", "r_squared",
       "adj_r_squared", "residual_share", "n", "nw_lags",
       "sample_start", "sample_end"}
          when >= 30 observations are available; low_confidence is True on the
          [30, 60) small-sample band.

    Inputs are injectable for deterministic tests (no live fetch): ``pv`` a daily
    portfolio value Series, ``ff_df`` the Ken French US factor frame (columns
    Mkt-RF, SMB, HML, RF), ``ief_prices`` / ``hyg_prices`` proxy price levels.
    In production all are None and the function reuses the same settled-frontier
    portfolio series, Ken French factors/RF, and IEF/HYG price path as the rest
    of the app.
    """
    inception = inception or get_inception_date()
    end_date  = end_date or date.today().isoformat()

    # ── Portfolio return: the settled-frontier-anchored Performance series ──────
    if pv is None:
        pv = get_portfolio_value_series(inception, end_date)
        # Clip the partial intraday today-bar exactly as the Performance page
        # does — the displayed series anchors on the last SETTLED trading day.
        c = last_settled_price_date(inception, end_date)
        pv = pv[pv.index <= pd.Timestamp(c)]

    if pv.empty or float(pv.max()) == 0.0:
        return {"status": "insufficient_history", "n": 0, "min_obs": MIN_OBS_FOR_REGRESSION}

    date_range = pd.date_range(start=pv.index.min(), end=pv.index.max(), freq="D")
    R_p = pv.reindex(date_range).ffill().pct_change()

    # ── Factors: Ken French US (Mkt-RF/SMB/HML + RF) and the IEF/HYG proxies ────
    if ff_df is None:
        ff_df = load_factors("us")
    ff = ff_df.copy()
    ff.index = pd.to_datetime(ff.index)

    ief_ret = _proxy_returns(_RATES_PROXY,  inception, end_date, date_range, ief_prices)
    hyg_ret = _proxy_returns(_CREDIT_PROXY, inception, end_date, date_range, hyg_prices)

    # Align everything on the common Ken French trading-day calendar (inner join
    # drops weekends, US holidays, and the factor-publication lag at the right
    # edge). Raw IEF/HYG returns are carried through; RATES/CREDIT are formed
    # AFTER the join so RF and IEF line up date-for-date.
    merged = (
        R_p.to_frame(name="R_p")
        .join(ief_ret.rename("IEF"), how="inner")
        .join(hyg_ret.rename("HYG"), how="inner")
        .join(ff[["Mkt-RF", "SMB", "HML", "RF"]], how="inner")
        .dropna()
    )

    n = len(merged)
    if n < MIN_OBS_FOR_REGRESSION:
        return {"status": "insufficient_history", "n": n, "min_obs": MIN_OBS_FOR_REGRESSION}

    R_excess = merged["R_p"] - merged["RF"]
    factors_df = pd.DataFrame(
        {
            "Mkt-RF": merged["Mkt-RF"].values,
            "SMB":    merged["SMB"].values,
            "HML":    merged["HML"].values,
            "RATES":  (merged["IEF"] - merged["RF"]).values,   # IEF excess return
            "CREDIT": (merged["HYG"] - merged["IEF"]).values,  # HY over duration-matched UST
        },
        index=merged.index,
    )

    result = _ols_five_factor(R_excess, factors_df)
    result["status"]         = "ok"
    result["low_confidence"] = n < LOW_CONFIDENCE_OBS
    result["n"]              = n
    result["sample_start"]   = merged.index[0].date().isoformat()
    result["sample_end"]     = merged.index[-1].date().isoformat()
    return result


# ── Page-facing copy builders (single source for page + any future PDF) ─────────

def insufficient_history_message(n: int, min_obs: int = MIN_OBS_FOR_REGRESSION) -> str:
    """Empty-state copy when history is too short for a stable regression."""
    return (
        f"Insufficient history for factor decomposition — {n} trading "
        f"day{'s' if n != 1 else ''} available, ~{min_obs} minimum. "
        "Betas are suppressed until the sample is large enough to estimate a "
        "stable five-factor regression; this section will populate as the "
        "portfolio accumulates history."
    )


def low_confidence_caveat(n: int) -> str:
    """Small-sample caveat shown on the [30, 60) observation band."""
    return (
        f"Low-confidence estimate: only {n} aligned trading days "
        f"(below the {LOW_CONFIDENCE_OBS}-observation threshold for a stable "
        "five-factor fit). Betas are shown but carry wide confidence intervals "
        "and should be read as provisional until the sample grows."
    )


def methodology_notes() -> list[str]:
    """Methodology disclosure bullets for the factor-decomposition section."""
    return [
        "Model: the portfolio's daily excess return is regressed on five factors "
        "SIMULTANEOUSLY — (R_p − RF) ~ α + β·Mkt-RF + β·SMB + β·HML + β·RATES + "
        "β·CREDIT. Because all five enter one regression, each beta is a MARGINAL "
        "exposure: the loading on that factor controlling for the other four. "
        "This is deliberately not five separate univariate regressions, which "
        "would double-count exposure shared across correlated factors.",

        "Factors: Mkt-RF, SMB, and HML are Ken French US daily factors "
        "(Dartmouth) — the same data path the sleeve regressions use. "
        f"{RATES_PROXY_DISCLOSURE} {CREDIT_PROXY_DISCLOSURE} The rates and credit "
        "legs are ETF-based proxies, disclosed as such — a tradeable stand-in for "
        "the academic term and credit premia, not the academic series themselves.",

        "Returns: the portfolio excess return reuses the settled-frontier-anchored "
        "total-return series shown on the Performance page (portfolio total return "
        "minus the Ken French US daily risk-free rate). A partial intraday "
        "today-bar is never used as the right-edge anchor.",

        "Fit: R² is the share of the portfolio's return variance the five factors "
        "jointly explain; the residual (unexplained) share is 1 − R² — the "
        "portion the systematic factors do NOT account for. The sample size (n) "
        "and date range are shown so the estimates can be judged in context.",

        "Coverage: the regressand is the portfolio's TOTAL return, which includes "
        "the Emerging Markets and Real Assets sleeves — asset classes none of "
        "these five factors is built to span. Their return either loads "
        "imperfectly onto the correlated Mkt-RF/CREDIT betas or lands in the "
        "residual, so the residual is not purely security-specific noise; it is "
        "labeled 'unexplained' rather than 'idiosyncratic' for that reason.",

        "Standard errors: Newey-West HAC, correcting for autocorrelation in daily "
        "residuals, with lag L = floor(4 × (n/100)^(2/9)).",

        "Insufficient-history handling: fewer than "
        f"{MIN_OBS_FOR_REGRESSION} aligned trading days suppresses the regression "
        "entirely (an explicit empty state, not unstable coefficients); between "
        f"{MIN_OBS_FOR_REGRESSION} and {LOW_CONFIDENCE_OBS} the betas are shown "
        "with a small-sample caveat.",
    ]


# ════════════════════════════════════════════════════════════════════════════
#  Phase 2 — Scenario / stress engine
#
#  Computes the portfolio's estimated INSTANTANEOUS P&L under defined factor
#  shocks: P&L = Σ_i (β_i × factor_move_i). It CONSUMES the Phase 1 betas
#  (the `run_portfolio_factor_regression` result dict) — it never re-runs the
#  regression — so the decomposition is the single source of truth. When Phase 1
#  returned the insufficient-history sentinel, the engine inherits that state and
#  produces no scenarios (it never shocks non-existent betas).
#
#  Scenarios are defined in native YIELD / SPREAD / EQUITY terms and TRANSLATED
#  to factor returns so the units are dimensionally correct (a rate move is not
#  "bps × beta" — it is a duration-implied price return × beta):
#    equity  — direct Mkt-RF realization (−20% → Mkt-RF factor = −0.20).
#    rates   — IEF price return ≈ −modified_duration × Δyield; the RATES factor
#              (IEF excess) realizes that return.
#    credit  — HY excess-over-duration-matched-Treasury return ≈ −spread_duration
#              × Δspread; the CREDIT factor (HYG − IEF) realizes that return.
#  The translation chain is carried on each leg so the page can SHOW it.
# ════════════════════════════════════════════════════════════════════════════

# Duration assumptions (years). IEF modified duration is REUSED from the
# maintained ETF duration table in src.positioning (single source of truth;
# 7.5y, refreshed quarterly from the iShares fact sheet). HY spread duration is
# a distinct concept from the rate-duration entries in that table (sensitivity
# to credit-SPREAD moves, not yield moves), so it is a documented assumption
# here — ~3.5y, consistent with the HYG fact sheet (~3–4y). Both are stated on
# the page as assumptions, in the same spirit as the Phase 1 proxy disclosure.
IEF_MODIFIED_DURATION = float(ETF_DURATION["IEF"])  # 7.5y, from src.positioning
HY_SPREAD_DURATION    = 3.5                          # years (assumption)

_DEFAULT_DURATIONS = {"rates": IEF_MODIFIED_DURATION, "credit": HY_SPREAD_DURATION}

# The five stress scenarios. Declarative: each leg names the factor it shocks,
# the shock TYPE (which translation path applies), and the native-unit magnitude
# (equity in decimal return; rates/credit in basis points of yield/spread).
# Magnitudes are illustrative stress sizes, NOT forecasts.
STRESS_SCENARIOS = [
    {
        "name": "Rate shock: +100bps parallel",
        "summary": "A parallel upward shift in the Treasury curve.",
        "legs": [{"factor": "RATES", "type": "rates", "delta_bps": 100}],
    },
    {
        "name": "Equity drawdown: −20%",
        "summary": "A broad equity-market sell-off.",
        "legs": [{"factor": "Mkt-RF", "type": "equity", "pct": -0.20}],
    },
    {
        "name": "Credit widening: +150bps HY spread",
        "summary": "High-yield credit spreads widen.",
        "legs": [{"factor": "CREDIT", "type": "credit", "delta_bps": 150}],
    },
    {
        "name": "Risk-off (flight to quality)",
        "summary": (
            "Equity −15% and HY spreads +200bps, but a −50bps rate RALLY "
            "cushions the FI sleeve — the diversification-works case."
        ),
        "legs": [
            {"factor": "Mkt-RF", "type": "equity", "pct": -0.15},
            {"factor": "CREDIT", "type": "credit", "delta_bps": 200},
            {"factor": "RATES",  "type": "rates",  "delta_bps": -50},
        ],
    },
    {
        "name": "2022-style regime (hedge inverts)",
        "summary": (
            "Rates +200bps AND equity −20% together — the stock-bond hedge "
            "fails and the losses compound; the diversification-fails case."
        ),
        "legs": [
            {"factor": "RATES",  "type": "rates",  "delta_bps": 200},
            {"factor": "Mkt-RF", "type": "equity", "pct": -0.20},
        ],
    },
]

# Disclosures (single source for the page).
INSTANTANEOUS_FRAMING = (
    "These are estimated portfolio impacts under an INSTANTANEOUS factor shock — "
    "not forecasts, not simulated paths, and not probability-weighted. Each "
    "number is a point sensitivity: what the book would lose or gain if the "
    "stated move happened at once, holding the estimated betas fixed."
)
LINEARITY_CAVEAT = (
    "Linear first-order sensitivity (P&L = β × shock): it assumes the betas are "
    "stable and the response is linear across the shock size. Large moves "
    "(e.g. −20% equity) involve real non-linearity — convexity and beta "
    "instability in the tails — that this model does NOT capture. The rates and "
    "credit legs are duration-based translations and likewise do not capture "
    "bond convexity. Read the magnitudes as first-order estimates, not precise "
    "tail outcomes."
)


def _translate_leg(leg: dict, durations: dict) -> tuple[float, str, str]:
    """Translate one native-unit shock to a factor return.

    Returns (factor_return, native_label, chain_middle). The factor_return is the
    realized return of the named factor under the shock; native_label and
    chain_middle build the displayed translation chain. Signs flow naturally:
    a rate RALLY (negative Δyield) yields a POSITIVE rates-factor return.
    """
    t = leg["type"]
    if t == "equity":
        fr = float(leg["pct"])
        return fr, f"Equity {fr:+.0%}", f"Mkt-RF {fr:+.1%}"
    if t == "rates":
        dy  = leg["delta_bps"] / 10_000.0          # bps → decimal yield change
        dur = durations["rates"]
        fr  = -dur * dy                             # price return ≈ −D × Δy
        return fr, f"Rates {leg['delta_bps']:+d}bps", (
            f"IEF duration {dur:g}y → {fr:+.1%} rates factor"
        )
    if t == "credit":
        ds  = leg["delta_bps"] / 10_000.0          # bps → decimal spread change
        dur = durations["credit"]
        fr  = -dur * ds                            # excess return ≈ −SD × Δspread
        return fr, f"Credit {leg['delta_bps']:+d}bps", (
            f"HY spread duration {dur:g}y → {fr:+.1%} credit factor"
        )
    raise ValueError(f"Unknown shock type: {t!r}")


def run_scenarios(
    regression_result: dict,
    current_mv: Optional[float] = None,
    *,
    scenarios: Optional[list] = None,
    durations: Optional[dict] = None,
) -> dict:
    """Estimate instantaneous portfolio P&L under factor-shock scenarios.

    CONSUMES the Phase 1 ``run_portfolio_factor_regression`` result dict — it does
    NOT re-run the regression. Gated on Phase 1: if the input is not an ``ok``
    result (insufficient history), returns the inherited insufficient-history
    sentinel and computes no scenarios. ``low_confidence`` is carried through.

    Returns:
      {"status": "insufficient_history", "n": <int>, "min_obs": <int>}
        when Phase 1 betas are unavailable.

      {"status": "ok", "low_confidence": <bool>, "current_mv": <float|None>,
       "durations": {...}, "scenarios": [
          {"name", "summary", "total_pct", "total_usd"|None, "legs": [
              {"factor", "native", "factor_return", "beta", "contribution",
               "chain"} ...]} ...]}

    All inputs (regression_result, current_mv, scenarios, durations) are
    injectable for deterministic tests — no live fetch.
    """
    if not regression_result or regression_result.get("status") != "ok":
        src = regression_result or {}
        return {
            "status":  "insufficient_history",
            "n":       int(src.get("n", 0)),
            "min_obs": int(src.get("min_obs", MIN_OBS_FOR_REGRESSION)),
        }

    betas     = regression_result["betas"]
    durations = durations or _DEFAULT_DURATIONS
    scenarios = scenarios or STRESS_SCENARIOS
    has_mv    = current_mv is not None and current_mv > 0

    out = []
    for sc in scenarios:
        legs_out = []
        total = 0.0
        for leg in sc["legs"]:
            fr, native, chain_mid = _translate_leg(leg, durations)
            beta    = float(betas[leg["factor"]])
            contrib = beta * fr
            total  += contrib
            legs_out.append({
                "factor":        leg["factor"],
                "native":        native,
                "factor_return": fr,
                "beta":          beta,
                "contribution":  contrib,
                "chain": f"{native} → {chain_mid} → × β {beta:+.2f} → {contrib:+.1%}",
            })
        out.append({
            "name":      sc["name"],
            "summary":   sc["summary"],
            "legs":      legs_out,
            "total_pct": total,
            "total_usd": (total * current_mv) if has_mv else None,
        })

    return {
        "status":         "ok",
        "low_confidence": bool(regression_result.get("low_confidence", False)),
        "current_mv":     current_mv if has_mv else None,
        "durations":      dict(durations),
        "scenarios":      out,
    }


def scenario_insufficient_history_message(
    n: int, min_obs: int = MIN_OBS_FOR_REGRESSION
) -> str:
    """Empty-state copy for the scenario section, inherited from Phase 1."""
    return (
        "Insufficient history — factor betas unavailable for stress testing "
        f"({n} trading day{'s' if n != 1 else ''} available, ~{min_obs} minimum). "
        "Scenarios are suppressed until the factor decomposition above can be "
        "estimated; this section populates together with it."
    )


def scenario_methodology_notes(durations: Optional[dict] = None) -> list[str]:
    """Methodology / disclosure bullets for the scenario section."""
    durations = durations or _DEFAULT_DURATIONS
    return [
        f"Mechanic: estimated P&L = Σ (factor beta × translated factor move), "
        "reusing the SAME betas as the decomposition above — the scenario engine "
        "does not re-estimate anything.",

        "Translation: equity shocks hit Mkt-RF directly; a yield move is "
        "translated to the intermediate-Treasury proxy's duration-implied price "
        "return (≈ −duration × Δyield); a credit-spread move is translated to the "
        "high-yield proxy's spread-duration-implied return (≈ −spread duration × "
        "Δspread). The translation chain is shown on every result so the units "
        "are transparent — not a raw basis-points-times-beta product.",

        f"Duration assumptions: IEF modified duration ≈ {durations['rates']:g}y "
        "(from the maintained ETF duration table) and HY spread duration ≈ "
        f"{durations['credit']:g}y (HYG fact sheet, ~3–4y). Both are stated "
        "assumptions, disclosed like the Phase 1 ETF-proxy disclosure.",

        "Curve shape: the rate shock is modeled as a PARALLEL shift — a single "
        "duration point translating a uniform yield change across the curve. It "
        "cannot represent a non-parallel move (curve steepening, flattening, or "
        "twist), which real rate shocks routinely include.",

        INSTANTANEOUS_FRAMING,

        LINEARITY_CAVEAT,

        "Scenario magnitudes are illustrative stress sizes chosen to probe "
        "distinct regimes — including a risk-off case where a flight-to-quality "
        "rate rally offsets part of the equity loss, and a 2022-style case where "
        "rates and equity fall together and the usual hedge inverts — not "
        "forecasts or probability-weighted outcomes.",
    ]


# ════════════════════════════════════════════════════════════════════════════
#  Phase 3 — Risk contribution (Euler / marginal-contribution-to-risk)
#
#  Decomposes TOTAL portfolio volatility into per-sleeve risk contributions that
#  sum EXACTLY to total vol (the Euler property), so a sleeve's share of risk can
#  be read beside its share of capital. The two diverge because correlation
#  matters: a high-vol or highly-correlated sleeve contributes more risk than its
#  weight; a diversifying sleeve contributes less.
#
#    σ²_p = wᵀ Σ w                          (portfolio variance)
#    MCR_i = (Σw)_i / σ_p                   (marginal contribution to risk)
#    RC_i  = w_i × MCR_i                    (risk contribution)
#    Σ_i RC_i = σ_p  (EXACT — Euler);  RC%_i = RC_i / σ_p  (sums to 100%)
#
#  Single source of truth: sleeve returns come from the SAME sleeve-benchmark
#  return source the Correlations / Asset Evaluation pages use
#  (asset_evaluation.get_sleeve_returns), anchored on the settled frontier; the
#  weight vector is the canonical normalized ex-cash SAA weight map
#  (asset_evaluation.SLEEVE_WEIGHTS), whose keys match the return columns. The
#  covariance is the sample covariance of those returns over the since-inception
#  window — the Correlations page computes a rolling CORRELATION on a long
#  history, a different statistic over a different window, so only the return
#  SOURCE is reused, not that helper.
# ════════════════════════════════════════════════════════════════════════════

# Covariance is more data-hungry than a single regression: a k-sleeve matrix has
# k(k+1)/2 free parameters (≈45 for the 9 SAA sleeves) and is singular whenever
# n ≤ k. A 60-observation floor (vs Phase 1's 30) keeps the estimate away from
# that degenerate edge before it is shown; below it, the section shows the
# insufficient-history empty state.
#
# Above the floor, the same graduated treatment Phase 1 applies to the
# regression applies here: Phase 1's low-confidence ceiling (60) sits at 2x its
# own floor (30). Mirroring that ratio against the covariance's higher floor
# gives 120 (2 x 60) — higher than Phase 1's 60, reflecting that a covariance
# matrix estimates far more parameters than a single regression.
MIN_OBS_FOR_COVARIANCE = 60   # below this: no contributions, show empty-state
LOW_CONFIDENCE_OBS_RC  = 120  # [60, 120): contributions WITH a small-sample caveat
_MAX_COND_NUMBER = 1.0e10  # condition number above which Σ is treated as singular


def _insufficient_rc(n: int, reason: Optional[str] = None) -> dict:
    out = {"status": "insufficient_history", "n": int(n), "min_obs": MIN_OBS_FOR_COVARIANCE}
    if reason:
        out["reason"] = reason
    return out


def run_risk_contribution(
    inception: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    sleeve_returns: Optional[pd.DataFrame] = None,
    weights: Optional[dict] = None,
    annualize: bool = True,
) -> dict:
    """Euler decomposition of portfolio volatility into per-sleeve contributions.

    Returns a status dict (never None):

      {"status": "insufficient_history", "n": <int>, "min_obs": 60[, "reason"]}
        when too few aligned observations OR the covariance is singular/degenerate
        (more sleeves than obs, or perfectly collinear sleeves) — NO contributions.

      {"status": "ok", "low_confidence": <bool>, "portfolio_vol": <σ_p>, "n", "k",
       "annualized", "rc_sum_check": <Σ RC == σ_p>, "sleeves": [
          {"sleeve", "weight", "vol", "mcr", "risk_contribution", "risk_pct"} ...]}
        sorted by risk_pct descending. low_confidence is True on the [60, 120)
        small-sample band, mirroring the regression's [30, 60) band.

    Inputs are injectable for deterministic tests (no live fetch): ``sleeve_returns``
    a DataFrame of daily sleeve returns (columns = sleeve names), ``weights`` a
    {sleeve: weight} map. In production both default to the reused sleeve-return
    source and SAA weight map, over the settled-frontier since-inception window.
    """
    if weights is None:
        from src import asset_evaluation as ae
        weights = dict(ae.SLEEVE_WEIGHTS)

    if sleeve_returns is None:
        from src import asset_evaluation as ae
        inception   = inception or get_inception_date()
        end_settled = last_settled_price_date(inception, end_date or date.today().isoformat())
        sleeve_returns = ae.get_sleeve_returns(inception, end_settled)

    R = sleeve_returns.dropna()
    # Align on sleeves present in BOTH returns and weights (clean in production —
    # SLEEVE_WEIGHTS keys equal the return columns).
    sleeves = [s for s in R.columns if s in weights]
    R = R[sleeves]
    n, k = len(R), len(sleeves)

    if n < MIN_OBS_FOR_COVARIANCE or k < 2:
        return _insufficient_rc(n)

    w_raw = np.array([float(weights[s]) for s in sleeves], dtype=float)
    if w_raw.sum() <= 0:
        return _insufficient_rc(n)
    w = w_raw / w_raw.sum()  # renormalize over the available sleeves

    cov = R.cov().values
    if annualize:
        cov = cov * 252.0

    # Degenerate / singular covariance guard: NaNs, non-finite condition number,
    # near-singular (cond too large), or non-positive variance → empty state, not
    # a NaN/garbage decomposition.
    if not np.all(np.isfinite(cov)):
        return _insufficient_rc(n, reason="degenerate_covariance")
    try:
        cond = np.linalg.cond(cov)
    except np.linalg.LinAlgError:
        cond = np.inf
    var_p = float(w @ cov @ w)
    if (not np.isfinite(cond)) or cond > _MAX_COND_NUMBER or var_p <= 0:
        return _insufficient_rc(n, reason="degenerate_covariance")

    sigma_p = float(np.sqrt(var_p))
    mcr     = (cov @ w) / sigma_p          # marginal contribution to risk
    rc      = w * mcr                       # risk contribution
    rc_pct  = rc / sigma_p                  # share of total risk (sums to 1)

    # Euler property is a mathematical identity here; assert it as the in-code
    # correctness proof (the summation a risk reviewer checks first).
    assert abs(float(rc.sum()) - sigma_p) <= 1.0e-6 * max(1.0, sigma_p), (
        "Euler decomposition failed: risk contributions do not sum to portfolio vol"
    )

    sleeves_out = [
        {
            "sleeve":            s,
            "weight":            float(w[i]),
            "vol":               float(np.sqrt(cov[i, i])),
            "mcr":               float(mcr[i]),
            "risk_contribution": float(rc[i]),
            "risk_pct":          float(rc_pct[i]),
        }
        for i, s in enumerate(sleeves)
    ]
    sleeves_out.sort(key=lambda d: d["risk_pct"], reverse=True)

    return {
        "status":         "ok",
        "low_confidence": n < LOW_CONFIDENCE_OBS_RC,
        "portfolio_vol":  sigma_p,
        "n":              n,
        "k":              k,
        "annualized":     annualize,
        "rc_sum_check":   float(rc.sum()),   # == portfolio_vol (Euler proof)
        "sleeves":        sleeves_out,
    }


def risk_contribution_insufficient_history_message(
    n: int, min_obs: int = MIN_OBS_FOR_COVARIANCE
) -> str:
    """Empty-state copy for the risk-contribution section."""
    return (
        "Insufficient history for risk decomposition — "
        f"{n} trading day{'s' if n != 1 else ''} available, ~{min_obs} minimum "
        "for a stable sleeve covariance matrix. A covariance matrix needs more "
        "history than a single regression; contributions are suppressed until "
        "the sample is adequate rather than shown from a degenerate matrix."
    )


def risk_contribution_low_confidence_caveat(n: int) -> str:
    """Small-sample caveat shown on the [60, 120) observation band."""
    return (
        f"Low-confidence estimate: only {n} trading days of sleeve-return "
        f"history (below the {LOW_CONFIDENCE_OBS_RC}-observation threshold for a "
        "stable covariance estimate). Contributions are shown but the "
        "correlation terms carry wide estimation error and should be read as "
        "provisional until the sample grows."
    )


def risk_contribution_methodology_notes() -> list[str]:
    """Methodology / disclosure bullets for the risk-contribution section."""
    return [
        "Mechanic: the Euler decomposition of volatility. Portfolio variance is "
        "σ²_p = wᵀΣw; each sleeve's marginal contribution to risk is MCR_i = "
        "(Σw)_i / σ_p and its risk contribution is RC_i = w_i × MCR_i. By Euler's "
        "theorem the contributions sum EXACTLY to total policy volatility "
        "(Σ RC_i = σ_p), and the percentage shares sum to 100% — the summation "
        "shown is the correctness check.",

        "Why risk share ≠ weight share: the decomposition accounts for "
        "correlation, not just allocation. A high-volatility or highly-correlated "
        "sleeve contributes MORE risk than its weight; a diversifying "
        "(low/negative-correlation) sleeve contributes LESS. A 10% allocation is "
        "not 10% of the risk.",

        "Inputs: sleeve returns are the SAA sleeve-benchmark series used across "
        "the Correlations and Asset Evaluation pages (single source), anchored on "
        "the settled trading frontier; the weight vector is the SAA target "
        "(policy) weights, ex-cash, normalized to 100%. Actual drift from target "
        "is small in this buy-and-hold book.",

        "Covariance: the realized SAMPLE covariance over the since-inception "
        "window — an estimate, with estimation error concentrated in the "
        "off-diagonal (correlation) terms. The window and observation count are "
        "shown so the contributions can be judged in context; covariance is "
        "annualized with the √252 convention used elsewhere in the app.",

        "Scope: this decomposes TOTAL VOLATILITY — a symmetric, vol-based risk "
        "measure. It is not a VaR or downside-risk contribution (those are "
        "different decompositions of different risk measures).",

        "Insufficient-history / degenerate handling: fewer than "
        f"{MIN_OBS_FOR_COVARIANCE} aligned observations, or a singular / "
        "near-singular covariance matrix (more sleeves than data, or collinear "
        "sleeves), shows an explicit empty state rather than unstable or "
        "ill-defined contributions; between "
        f"{MIN_OBS_FOR_COVARIANCE} and {LOW_CONFIDENCE_OBS_RC} the contributions "
        "are shown with a small-sample caveat.",
    ]
