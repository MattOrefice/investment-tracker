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

import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

from src.factors import _nw_lags, load_factors
from src.holdings import (
    get_inception_date,
    get_portfolio_value_series,
    last_settled_price_date,
)
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
    t-stats and p-values, R², the residual (idiosyncratic) share = 1 − R², the
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
        "jointly explain; the residual (idiosyncratic) share is 1 − R² — the "
        "portion the systematic factors do NOT account for. The sample size (n) "
        "and date range are shown so the estimates can be judged in context.",

        "Standard errors: Newey-West HAC, correcting for autocorrelation in daily "
        "residuals, with lag L = floor(4 × (n/100)^(2/9)).",

        "Insufficient-history handling: fewer than "
        f"{MIN_OBS_FOR_REGRESSION} aligned trading days suppresses the regression "
        "entirely (an explicit empty state, not unstable coefficients); between "
        f"{MIN_OBS_FOR_REGRESSION} and {LOW_CONFIDENCE_OBS} the betas are shown "
        "with a small-sample caveat.",
    ]
