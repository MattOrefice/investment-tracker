"""Asset evaluation framework — computations for candidate asset analysis.

Houses pure-Python, unit-testable functions for evaluating candidate assets
for SAA inclusion. Pages consume this module; computations do not import Streamlit.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from src.macro import classify_regime, get_series
from src.prices import get_prices

# ── Constants ────────────────────────────────────────────────────────────────

SAMPLE_START  = "2018-01-01"
# 4.5%, matching src/performance.py's compute_risk_metrics default exactly (same
# rate, same geometric daily-compounding convention: rf_daily = (1+rf)^(1/252)-1
# — see solve_tangency_unconstrained/solve_tangency_constrained/compute_mv_analysis
# below). Redeclared here rather than imported because performance.py exposes the
# rate only as a function-parameter default, not a module constant; the two are
# pinned equal by tests/test_asset_evaluation.py::test_rf_annual_matches_performance_rf,
# a cross-source assertion so the two rates cannot silently drift apart again.
RF_ANNUAL     = 0.045
TRADING_DAYS  = 252

# Sleeve benchmark tickers + ex-cash MV weights, DERIVED from asset_classes at
# CALL time so they describe whatever book this process is pointed at (9 sleeves
# personal, 12 demo) rather than freezing a taxonomy. Never derived at import:
# an import-time derivation opens the database from `import` itself (pytest
# collection, a bare REPL import) and freezes the sleeve set before the phase-46
# international split can rename it. Cash / SPAXX is excluded: near-zero
# variance distorts correlation and optimisation. Names are abbreviated
# ("International …" -> "Intl …") for the correlation-matrix display; the same
# abbreviation is the name bridge test_raw_weights_match_db_targets_and_scale
# uses to compare these against the full-name DB targets.
def _abbrev(name: str) -> str:
    return name.replace("International ", "Intl ")


def _derive_sleeve_maps() -> tuple[dict, dict]:
    from src.sleeve_config import (
        sleeve_benchmarks as _sb,
        strategic_sleeve_weights as _sw,
    )
    bench = {_abbrev(k): v for k, v in _sb(include_cash=False).items()}
    raw = {_abbrev(k): v for k, v in _sw(include_cash=False).items()}
    total = sum(raw.values())
    weights = {k: v / total for k, v in raw.items()}
    return bench, weights


def sleeve_benchmarks() -> dict[str, list[tuple[str, float]]]:
    """Sleeve → benchmark legs for the CURRENT book, resolved at call time."""
    return _derive_sleeve_maps()[0]


def sleeve_weights() -> dict[str, float]:
    """Ex-cash MV weights for the CURRENT book, resolved at call time."""
    return _derive_sleeve_maps()[1]


def sleeve_names() -> list[str]:
    """Sleeve display names (abbreviated) for the CURRENT book, in DB order."""
    return list(_derive_sleeve_maps()[0].keys())

# Shared conclusion — used by both pages/10_Asset_Evaluation.py and src/reports.py.
# Excludes the Streamlit-specific "This page will update automatically" sentence.
CONCLUSION = (
    "Bitcoin's sample-period Sharpe improvement is real but almost entirely "
    "attributable to its exceptional 2020–2021 bull market return. "
    "The 2024 spot ETF launches have already partially satisfied the "
    "operational-risk concern that historically gated institutional adoption — "
    "IBIT, FBTC, and similar wrappers remove the custody and counterparty "
    "frictions that defined the pre-2024 implementation landscape. "
    "The remaining barriers to inclusion are analytical (the post-2020 "
    "correlation regime and the 2022 joint stress) and fundamental (no cash "
    "flow anchor for valuation), not operational. "
    "**Bitcoin does not currently belong in this portfolio's SAA.**\n\n"
    "The framework would re-evaluate inclusion if any of the following "
    "observable conditions are met:\n"
    "- Rolling 5-year correlation with US Large Core falls below 0.20 "
    "(the pre-2020 baseline established in the rolling BTC–SPY correlation), "
    "indicating a return to genuine diversification behavior.\n"
    "- A 5–10% allocation produces a positive Sharpe contribution after "
    "explicit tax-drag adjustment (the analytical gap in the constrained "
    "mean-variance analysis).\n"
    "- A negative-correlation episode is observed during a defined equity "
    "stress period (the 2022 joint drawdown in the drawdown-sensitivity "
    "analysis is the relevant counter-example)."
)


# ── Data loading ─────────────────────────────────────────────────────────────

def _price_series(ticker: str, start: str, end: str) -> pd.Series:
    """adj_close price series indexed by pd.Timestamp, or empty Series on failure."""
    try:
        p = get_prices(ticker, start, end)
        p.index = pd.to_datetime(p.index)
        return p["adj_close"].fillna(p["close"]).ffill()
    except Exception:
        return pd.Series(dtype=float)


def get_candidate_returns(
    ticker: str,
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
) -> pd.Series:
    """Daily pct_change returns for a candidate asset (e.g., BTC-USD)."""
    end    = end_date or date.today().isoformat()
    prices = _price_series(ticker, start_date, end)
    if prices.empty:
        return pd.Series(dtype=float)
    return prices.pct_change().dropna().sort_index()


def get_sleeve_returns(
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
    exclude: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Daily returns for the non-cash SAA sleeves, trading-day intersection.

    Blended sleeves use weighted-average daily returns.
    Rows where any sleeve has a near-zero absolute return sum are dropped
    (Phase 11 zero-return rule: these are non-trading days for equity markets).

    By default all 9 sleeves are included; the common-date intersection is then
    bounded by the latest-starting sleeve (QUAL, ~2013). Pass ``exclude`` (a list
    of sleeve names) to drop sleeves *before* the intersection — e.g.
    ``exclude=["US Large Quality"]`` drops QUAL so the remaining 8-sleeve set
    extends back to ~2007 (DBC inception), making the 2008 crisis visible.
    """
    end  = end_date or date.today().isoformat()
    skip = set(exclude or [])
    ret_dict: dict[str, pd.Series] = {}

    for sleeve_name, components in sleeve_benchmarks().items():
        if sleeve_name in skip:
            continue
        blended: pd.Series | None = None
        for ticker, weight in components:
            p = _price_series(ticker, start_date, end)
            if p.empty:
                blended = None
                break
            r = p.pct_change().dropna()
            blended = r * weight if blended is None else blended.add(r * weight, fill_value=0.0)
        if blended is not None and not blended.empty:
            ret_dict[sleeve_name] = blended

    if not ret_dict:
        return pd.DataFrame()

    df       = pd.DataFrame(ret_dict).dropna()
    non_zero = df.abs().sum(axis=1) > 0
    return df[non_zero]


def align_to_equity_days(
    candidate: pd.Series,
    sleeves: pd.DataFrame,
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Return (candidate, sleeves) restricted to dates present in both index sets.
    BTC trades 24/7; restricting to equity trading days makes correlations comparable.
    """
    common = candidate.index.intersection(sleeves.index)
    return candidate.reindex(common), sleeves.reindex(common)


# ── Univariate statistics ─────────────────────────────────────────────────────

def compute_univariate_stats(
    returns: pd.Series,
    rf_annual: float = RF_ANNUAL,
) -> dict[str, float]:
    """
    Annualized return, vol, Sharpe, max drawdown, skewness, and excess kurtosis.
    Annualization: geometric mean scaled to 252 trading days.
    """
    n = len(returns)
    if n < 2:
        return {}
    ann_ret  = float((1 + returns).prod() ** (TRADING_DAYS / n) - 1)
    ann_vol  = float(returns.std() * np.sqrt(TRADING_DAYS))
    sharpe   = (ann_ret - rf_annual) / ann_vol if ann_vol > 0 else 0.0
    cum      = (1 + returns).cumprod()
    roll_max = cum.cummax()
    max_dd   = float((cum / roll_max - 1).min())
    return {
        "ann_return":   ann_ret,
        "ann_vol":      ann_vol,
        "sharpe":       sharpe,
        "max_drawdown": max_dd,
        "skewness":     float(returns.skew()),
        "kurtosis":     float(returns.kurtosis()),   # excess kurtosis (normal = 0)
    }


def build_univariate_table(
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
    rf_annual: float = RF_ANNUAL,
) -> pd.DataFrame:
    """
    Univariate stats table for BTC plus comparison assets.
    Rows: BTC, SPY, AGG, GLD, VOO, Real Assets (60% VNQ + 40% PDBC).
    Returns a DataFrame indexed by asset name.
    """
    end = end_date or date.today().isoformat()
    assets: dict[str, pd.Series] = {}

    for ticker in ("BTC-USD", "SPY", "AGG", "GLD", "VOO"):
        p = _price_series(ticker, start_date, end)
        if not p.empty:
            assets[ticker.replace("-USD", "")] = p.pct_change().dropna()

    # Real Assets blend
    vnq  = _price_series("VNQ",  start_date, end)
    pdbc = _price_series("PDBC", start_date, end)
    if not vnq.empty and not pdbc.empty:
        common = vnq.index.intersection(pdbc.index)
        ra = (vnq.reindex(common).pct_change() * 0.5 +
              pdbc.reindex(common).pct_change() * 0.5).dropna()
        assets["Real Assets"] = ra

    rows = {}
    for name, rets in assets.items():
        rows[name] = compute_univariate_stats(rets, rf_annual)
    return pd.DataFrame(rows).T


# ── Correlation analysis ──────────────────────────────────────────────────────

def compute_full_sample_correlations(
    candidate: pd.Series,
    sleeves: pd.DataFrame,
) -> pd.Series:
    """Pearson correlation of candidate vs each sleeve, full sample on common days."""
    cand_a, slv_a = align_to_equity_days(candidate, sleeves)
    if cand_a.empty:
        return pd.Series(dtype=float)
    return pd.Series({col: float(cand_a.corr(slv_a[col])) for col in slv_a.columns})


def compute_rolling_correlation(
    candidate: pd.Series,
    reference: pd.Series,
    window: int = 60,
) -> pd.Series:
    """Rolling window Pearson correlation on the common-date intersection."""
    common = candidate.index.intersection(reference.index)
    if len(common) < window:
        return pd.Series(dtype=float)
    return candidate.reindex(common).rolling(window).corr(reference.reindex(common)).dropna()


def compute_weekly_correlations(
    candidate: pd.Series,
    sleeves: pd.DataFrame,
) -> pd.Series:
    """
    Pearson correlation using weekly Friday-to-Friday returns.
    Resamples daily returns to weekly cumulative returns, then correlates.
    """
    cand_a, slv_a = align_to_equity_days(candidate, sleeves)
    if cand_a.empty:
        return pd.Series(dtype=float)

    def _weekly(r: pd.Series) -> pd.Series:
        return (1 + r).resample("W-FRI").prod() - 1

    cand_w  = _weekly(cand_a)
    result  = {}
    for col in slv_a.columns:
        slv_w  = _weekly(slv_a[col])
        common = cand_w.index.intersection(slv_w.index)
        result[col] = (float(cand_w.reindex(common).corr(slv_w.reindex(common)))
                       if len(common) >= 4 else float("nan"))
    return pd.Series(result)


# ── Mean-variance optimization ────────────────────────────────────────────────

def portfolio_sharpe_annual(
    weights: np.ndarray,
    mu: np.ndarray,
    cov: np.ndarray,
    rf_annual: float = RF_ANNUAL,
) -> float:
    """Annualized Sharpe ratio from daily sample mean / covariance."""
    port_ret = float(np.dot(weights, mu)) * TRADING_DAYS - rf_annual
    port_vol = float(np.sqrt(weights @ cov @ weights)) * np.sqrt(TRADING_DAYS)
    return port_ret / port_vol if port_vol > 1e-12 else 0.0


def solve_tangency_unconstrained(
    mu: np.ndarray,
    cov: np.ndarray,
    rf_annual: float = RF_ANNUAL,
) -> np.ndarray:
    """
    Closed-form unconstrained tangency portfolio.
    w* = Σ^{-1}(μ − rf*1) / [1'Σ^{-1}(μ − rf*1)].
    May produce negative weights (short positions allowed).
    Input mu and cov are daily; rf_annual is converted to daily internally via
    geometric compounding, (1+rf_annual)^(1/252) - 1 — matching
    src/performance.py's compute_risk_metrics convention.
    """
    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    excess   = mu - rf_daily
    cov_inv  = np.linalg.pinv(cov)
    raw      = cov_inv @ excess
    total    = raw.sum()
    if abs(total) < 1e-10:
        return np.ones(len(mu)) / len(mu)
    return raw / total


def solve_tangency_constrained(
    mu: np.ndarray,
    cov: np.ndarray,
    rf_annual: float = RF_ANNUAL,
    max_wt: float = 0.10,
) -> np.ndarray:
    """
    Constrained tangency: 0 ≤ w_i ≤ max_wt, Σw = 1. Maximizes Sharpe.
    Uses scipy.optimize.minimize with SLSQP.
    """
    from scipy.optimize import minimize

    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    n        = len(mu)

    def neg_sharpe(w: np.ndarray) -> float:
        port_ret = float(np.dot(w, mu)) - rf_daily
        port_vol = float(np.sqrt(w @ cov @ w))
        return -(port_ret / port_vol) if port_vol > 1e-12 else 0.0

    result = minimize(
        neg_sharpe,
        x0=np.ones(n) / n,
        method="SLSQP",
        bounds=[(0.0, max_wt)] * n,
        constraints=[{"type": "eq", "fun": lambda w: float(w.sum()) - 1.0}],
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    w = np.clip(result.x, 0.0, max_wt)
    return w / w.sum()


def compute_mv_analysis(
    candidate_ret: pd.Series,
    sleeve_rets: pd.DataFrame,
    rf_annual: float = RF_ANNUAL,
    max_wt: float = 0.25,
) -> dict:
    """
    Full MV analysis: unconstrained and constrained tangency with/without candidate.

    Returns a dict with:
      sleeves               — ordered list of sleeve names (no BTC)
      w_unc_no, w_unc_with  — unconstrained tangency weights (9 sleeves / 10 incl. BTC)
      w_con_no, w_con_with  — constrained tangency weights
      sharpe_unc_no/with    — theoretical max-Sharpe: √((μ-rf)'Σ⁻¹(μ-rf)) × √252
      sharpe_con_no/with    — Sharpe of the constrained tangency portfolio
    """
    cand_a, slv_a  = align_to_equity_days(candidate_ret, sleeve_rets)
    sleeves_list   = list(slv_a.columns)

    mu_nb  = slv_a.mean().values
    cov_nb = slv_a.cov().values

    combined        = slv_a.copy()
    combined["BTC"] = cand_a
    mu_wb  = combined.mean().values
    cov_wb = combined.cov().values

    w_unc_nb = solve_tangency_unconstrained(mu_nb, cov_nb, rf_annual)
    w_con_nb = solve_tangency_constrained(mu_nb, cov_nb, rf_annual, max_wt)
    w_unc_wb = solve_tangency_unconstrained(mu_wb, cov_wb, rf_annual)
    w_con_wb = solve_tangency_constrained(mu_wb, cov_wb, rf_annual, max_wt)

    # Unconstrained Sharpe: √((μ-rf)'Σ⁻¹(μ-rf)) × √252 — non-negative by construction.
    # Using portfolio_sharpe_annual on normalized weights can produce negative Sharpe when
    # the normalization denominator (1'Σ⁻¹excess) is negative, flipping all weight signs.
    rf_daily   = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    excess_nb  = mu_nb - rf_daily
    cov_inv_nb = np.linalg.pinv(cov_nb)
    sharpe_unc_no = float(
        np.sqrt(max(float(excess_nb @ cov_inv_nb @ excess_nb), 0.0)) * np.sqrt(TRADING_DAYS)
    )

    excess_wb  = mu_wb - rf_daily
    cov_inv_wb = np.linalg.pinv(cov_wb)
    sharpe_unc_with = float(
        np.sqrt(max(float(excess_wb @ cov_inv_wb @ excess_wb), 0.0)) * np.sqrt(TRADING_DAYS)
    )

    return {
        "sleeves":         sleeves_list,
        "w_unc_no":        w_unc_nb,
        "w_unc_with":      w_unc_wb,
        "w_con_no":        w_con_nb,
        "w_con_with":      w_con_wb,
        "sharpe_unc_no":   sharpe_unc_no,
        "sharpe_unc_with": sharpe_unc_with,
        "sharpe_con_no":   portfolio_sharpe_annual(w_con_nb, mu_nb, cov_nb, rf_annual),
        "sharpe_con_with": portfolio_sharpe_annual(w_con_wb, mu_wb, cov_wb, rf_annual),
        "mu_nb": mu_nb, "cov_nb": cov_nb,
        "mu_wb": mu_wb, "cov_wb": cov_wb,
    }


# ── Marginal Sharpe curve ─────────────────────────────────────────────────────

def compute_marginal_sharpe_curve(
    candidate_ret: pd.Series,
    sleeve_rets: pd.DataFrame,
    sleeve_weights: dict[str, float] | None = None,
    rf_annual: float = RF_ANNUAL,
    allocations: list[float] | None = None,
) -> pd.DataFrame:
    """
    Portfolio Sharpe at each candidate allocation.

    For each α, the remaining (1−α) is distributed proportionally across
    existing sleeves using sleeve_weights (defaults to the book's derived
    ex-cash MV weights).
    Returns DataFrame with columns: btc_alloc, sharpe.
    """
    if allocations is None:
        allocations = [0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10]
    if sleeve_weights is None:
        # The module function sleeve_weights() is shadowed by this parameter.
        sleeve_weights = _derive_sleeve_maps()[1]

    cand_a, slv_a = align_to_equity_days(candidate_ret, sleeve_rets)
    sleeves_list  = [s for s in slv_a.columns if s in sleeve_weights]
    raw_total     = sum(sleeve_weights[s] for s in sleeves_list)
    norm_sw       = {s: sleeve_weights[s] / raw_total for s in sleeves_list}

    combined       = slv_a[sleeves_list].copy()
    combined["BTC"] = cand_a
    mu  = combined.mean().values
    cov = combined.cov().values
    n   = len(sleeves_list)

    rows = []
    for alpha in allocations:
        w = np.array([norm_sw[s] * (1 - alpha) for s in sleeves_list] + [alpha])
        w = np.clip(w, 0, 1)
        w = w / w.sum()
        rows.append({"btc_alloc": alpha, "sharpe": portfolio_sharpe_annual(w, mu, cov, rf_annual)})

    return pd.DataFrame(rows)


# ── Drawdown sensitivity ──────────────────────────────────────────────────────

def _port_series(weights: np.ndarray, rets_df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Daily portfolio return series given weight vector and return DataFrame."""
    return (rets_df[cols].values @ weights)


def compute_drawdown_sensitivity(
    candidate_ret: pd.Series,
    sleeve_rets: pd.DataFrame,
    sleeve_weights: dict[str, float] | None = None,
    allocations: list[float] | None = None,
    rf_annual: float = RF_ANNUAL,
) -> pd.DataFrame:
    """
    Portfolio CAGR, max drawdown, and Sharpe at each candidate allocation.
    Also computes portfolio MDD during the 2022 stress period.
    """
    if allocations is None:
        allocations = [0.00, 0.02, 0.05, 0.10]
    if sleeve_weights is None:
        # The module function sleeve_weights() is shadowed by this parameter.
        sleeve_weights = _derive_sleeve_maps()[1]

    cand_a, slv_a = align_to_equity_days(candidate_ret, sleeve_rets)
    sleeves_list  = [s for s in slv_a.columns if s in sleeve_weights]
    raw_total     = sum(sleeve_weights[s] for s in sleeves_list)
    norm_sw       = {s: sleeve_weights[s] / raw_total for s in sleeves_list}

    combined = slv_a[sleeves_list].copy()
    combined["BTC"] = cand_a
    cols = sleeves_list + ["BTC"]

    stress = combined.loc["2022-01-01":"2022-12-31"]

    rows = []
    for alpha in allocations:
        w = np.array([norm_sw[s] * (1 - alpha) for s in sleeves_list] + [alpha])
        w = w / w.sum()

        dr   = _port_series(w, combined, cols)
        n    = len(dr)
        cagr = float((1 + dr).prod() ** (TRADING_DAYS / n) - 1) if n > 0 else 0.0
        vol  = float(dr.std() * np.sqrt(TRADING_DAYS))
        shr  = (cagr - rf_annual) / vol if vol > 0 else 0.0
        cum  = (1 + dr).cumprod()
        mdd  = float((cum / np.maximum.accumulate(cum) - 1).min()) if n > 0 else 0.0

        mdd22 = float("nan")
        if len(stress) > 1:
            dr22  = _port_series(w, stress, cols)
            cum22 = (1 + dr22).cumprod()
            mdd22 = float((cum22 / np.maximum.accumulate(cum22) - 1).min())

        rows.append({
            "BTC Alloc": f"{alpha:.1%}",
            "CAGR":       cagr,
            "Max DD":     mdd,
            "Sharpe":     shr,
            "2022 MDD":   mdd22,
        })

    return pd.DataFrame(rows)


# ── Regime-conditional correlation ────────────────────────────────────────────

def _build_regime_series(start_date: str, end_date: str) -> pd.Series:
    """
    Vectorized daily regime label series from FRED monthly/daily data.
    Returns a Series indexed by pd.Timestamp with string regime labels.
    """
    try:
        usrec  = get_series("USREC",  start_date="1945-01-01").dropna()
        t10y2y = get_series("T10Y2Y", start_date="1976-06-01").dropna()
        unrate = get_series("UNRATE", start_date="1948-01-01").dropna()
    except Exception:
        return pd.Series(dtype=str)

    dr = pd.date_range(start=start_date, end=end_date, freq="D")
    df = pd.DataFrame({
        "usrec":  usrec.reindex(dr).ffill().bfill(),
        "t10y2y": t10y2y.reindex(dr).ffill().bfill(),
        "unrate": unrate.reindex(dr).ffill().bfill(),
    }, index=dr)

    # Vectorized classify_regime — same priority order as macro.classify_regime
    labels = pd.Series("Mid-cycle", index=dr, dtype=str)

    curve_inv   = df["t10y2y"].notna() & (df["t10y2y"] < -0.25)
    labor_tight = df["unrate"].notna() & (df["unrate"] < 4.2)
    labels[curve_inv | labor_tight] = "Late-cycle"

    unrate_high = df["unrate"].notna() & (df["unrate"] > 5.5)
    curve_ok    = df["t10y2y"].isna()  | (df["t10y2y"] > -0.25)
    labels[unrate_high & curve_ok] = "Early-cycle"

    labels[df["usrec"].notna() & (df["usrec"] >= 0.5)] = "Recession"

    return labels


def compute_regime_conditional_correlation(
    candidate_ret: pd.Series,
    reference_ret: pd.Series,
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    BTC-vs-reference correlation conditional on macro regime.
    Returns DataFrame with columns: Regime, Correlation, N Obs.
    """
    end    = end_date or date.today().isoformat()
    common = candidate_ret.index.intersection(reference_ret.index)
    cand   = candidate_ret.reindex(common)
    ref    = reference_ret.reindex(common)

    regimes = _build_regime_series(start_date, end)
    if regimes.empty:
        return pd.DataFrame(columns=["Regime", "Correlation", "N Obs"])

    reg_aligned = regimes.reindex(common).dropna()
    cand_r = cand.reindex(reg_aligned.index)
    ref_r  = ref.reindex(reg_aligned.index)

    rows = []
    for label in ("Recession", "Early-cycle", "Mid-cycle", "Late-cycle"):
        mask = reg_aligned == label
        n    = int(mask.sum())
        corr = float(cand_r[mask].corr(ref_r[mask])) if n >= 10 else float("nan")
        rows.append({"Regime": label, "Correlation": corr, "N Obs": n})

    return pd.DataFrame(rows)


# ── Aggregate rolling correlation over time (Phase 31) ─────────────────────────
#
# These operate on any returns DataFrame, so Phase 32 can reuse rolling_pairwise_*
# by passing a frame that includes a candidate column alongside the sleeves.

# Sleeve-name groups for the equity-vs-bond-equity decomposition. Real Assets is
# intentionally in neither group (a diversifier that is neither equity nor bond).
BOND_SLEEVES = ["Core Fixed Income", "TIPS"]


def equity_sleeve_names() -> list[str]:
    """Equity sleeves of the CURRENT book, resolved at call time.

    Derived from the book's own sleeve set rather than hardcoded: the old
    frozen list carried the personal book's names, so on the demo book the
    four international sleeves silently vanished from the intra-equity line
    (the `c in cols` filter dropped them without error). Call-time (not
    import-time) so importing this module never opens the database and the
    set tracks the phase-46 split within a warm process. Distinct on purpose
    from location_config.EQUITY_SLEEVES, which is a frozenset of sleeve
    *category keys* — this returns display names for the correlation views."""
    return [s for s in sleeve_names() if s not in BOND_SLEEVES and s != "Real Assets"]


def _ordinal(n: float) -> str:
    """Format a percentile as an ordinal, e.g. 58 -> '58th'."""
    n = int(round(n))
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def _pairwise_rolling_frame(returns_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    DataFrame of every off-diagonal pairwise rolling correlation, one column per
    unordered pair ("A × B"), indexed like ``returns_df``. Generic over columns.
    """
    cols = list(returns_df.columns)
    data: dict[str, pd.Series] = {}
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            data[f"{a} × {b}"] = returns_df[a].rolling(window).corr(returns_df[b])
    return pd.DataFrame(data, index=returns_df.index)


def rolling_pairwise_mean(returns_df: pd.DataFrame, window: int) -> pd.Series:
    """
    Per date, the mean of all off-diagonal pairwise rolling correlations across
    the columns of ``returns_df`` over the trailing ``window``.

    Generic — works for any number of columns (N >= 2). Phase 32 reuses this by
    passing a returns frame that includes a candidate column to obtain the
    candidate's average rolling correlation with the rest.
    """
    pf = _pairwise_rolling_frame(returns_df, window)
    if pf.empty:
        return pd.Series(dtype=float)
    return pf.mean(axis=1, skipna=True).dropna().rename("avg_pairwise_corr")


def rolling_pairwise_band(returns_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Per date, the min and max of the off-diagonal pairwise rolling correlations —
    the dispersion the mean hides. Columns: ``min``, ``max``.
    """
    pf = _pairwise_rolling_frame(returns_df, window)
    if pf.empty:
        return pd.DataFrame(columns=["min", "max"])
    out = pd.DataFrame({
        "min": pf.min(axis=1, skipna=True),
        "max": pf.max(axis=1, skipna=True),
    })
    return out.dropna(how="all")


def average_pairwise_rolling_correlation(
    window: int = 60,
    start_date: str = "2007-01-01",
    end_date: Optional[str] = None,
    exclude: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Sleeve-level aggregate: the mean (and min/max band) of all pairwise rolling
    correlations across the SAA sleeves over time.

    ``exclude`` is forwarded to ``get_sleeve_returns`` — e.g. dropping
    "US Large Quality" extends the common history back to ~2007. Returns a
    DataFrame with columns ``mean``, ``min``, ``max`` (empty if data is
    unavailable).
    """
    returns = get_sleeve_returns(start_date=start_date, end_date=end_date, exclude=exclude)
    if returns.empty or returns.shape[1] < 2:
        return pd.DataFrame(columns=["mean", "min", "max"])
    mean = rolling_pairwise_mean(returns, window).rename("mean")
    band = rolling_pairwise_band(returns, window)
    out = pd.concat([mean, band], axis=1).dropna(subset=["mean"])
    return out


def rolling_equity_vs_bondequity(returns_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Two decomposed rolling-correlation lines over time:
      - ``intra_equity``: mean pairwise correlation AMONG the equity sleeves.
      - ``bond_equity``:  mean correlation BETWEEN the bond sleeves
        (Core Fixed Income, TIPS) and the equity sleeves.

    Sleeves are classified by name via equity_sleeve_names() / BOND_SLEEVES;
    Real Assets is excluded from both groups. Columns absent from ``returns_df``
    are skipped.
    """
    cols = list(returns_df.columns)
    eq = [c for c in equity_sleeve_names() if c in cols]
    bd = [c for c in BOND_SLEEVES if c in cols]

    intra_eq = (
        rolling_pairwise_mean(returns_df[eq], window)
        if len(eq) >= 2 else pd.Series(dtype=float)
    )

    be_pairs: dict[str, pd.Series] = {}
    for b in bd:
        for e in eq:
            be_pairs[f"{b} × {e}"] = returns_df[b].rolling(window).corr(returns_df[e])
    bond_eq = (
        pd.DataFrame(be_pairs, index=returns_df.index).mean(axis=1, skipna=True)
        if be_pairs else pd.Series(dtype=float)
    )

    out = pd.DataFrame({"intra_equity": intra_eq, "bond_equity": bond_eq})
    return out.dropna(how="all")


def interpret_rolling_correlation(current_value: float, history_series: pd.Series) -> str:
    """
    Banded interpretation of the average pairwise sleeve correlation, in the
    macro/Factor-Regime voice.

    Reports the current average correlation, its percentile within its own
    history (reusing ``macro.percentile``), and frames diversification as
    strong / moderate / compressed by the absolute current level. Distinguishes
    the equity sleeves' convergence toward +1 in stress from the bond-damped
    blended average, and notes the non-stationary caveat.
    """
    from src.macro import percentile as _macro_percentile

    hist = history_series.dropna()
    pct  = _macro_percentile(hist, current_value) if not hist.empty else 50.0
    start_year = hist.index.min().year if not hist.empty else "the sample start"

    if current_value >= 0.65:
        level = "compressed: the sleeves are moving largely together and offer little offset right now"
    elif current_value >= 0.40:
        level = "moderate: the sleeves share meaningful common market beta but retain some offset"
    else:
        level = "strong: average co-movement is low and the sleeves are genuinely diversifying"

    return (
        f"Average pairwise sleeve correlation is currently ρ ≈ {current_value:+.2f}, the "
        f"{_ordinal(pct)} percentile of its history since {start_year} — cross-sleeve "
        f"diversification is {level}. In stress episodes the equity sleeves converge toward "
        "+1 (diversification among equity styles evaporates in a crash); the blended average "
        "stays lower only because the bond sleeves decouple from equities, which is the "
        "diversification the fixed-income allocation is there to provide. Correlations are "
        "non-stationary and tend to rise precisely when diversification is most needed."
    )


# ── Candidate-to-sleeves correlation (Phase 32) ────────────────────────────────
#
# The right metric for "how correlated is a candidate to my sleeves" is the mean
# of the candidate's correlation ROW — NOT rolling_pairwise_mean on an augmented
# frame (which averages all pairs, in which the candidate is a small minority and
# barely moves the result). Full-sample average is
# compute_full_sample_correlations(candidate, sleeves).mean(); the rolling version
# is rolling_correlation_to_set below.

def rolling_correlation_to_set(
    candidate_returns: pd.Series,
    references_df: pd.DataFrame,
    window: int = 60,
) -> pd.Series:
    """
    Per date, the mean over the reference columns of the rolling correlation
    between ``candidate_returns`` and each reference, over the trailing ``window``.

    This is the candidate's average rolling correlation to the reference set — the
    mean of its correlation row, computed on the candidate/reference common-date
    intersection. Generic over the number of reference columns. NOT the same as
    ``rolling_pairwise_mean`` (which averages all pairs, including reference-vs-
    reference, and so does not isolate the candidate).
    """
    common = candidate_returns.index.intersection(references_df.index)
    if len(common) < window or references_df.shape[1] == 0:
        return pd.Series(dtype=float)
    cand = candidate_returns.reindex(common)
    refs = references_df.reindex(common)
    per_ref = [cand.rolling(window).corr(refs[c]) for c in refs.columns]
    mat = pd.concat(per_ref, axis=1)
    return mat.mean(axis=1, skipna=True).dropna().rename("avg_corr_to_set")


def interpret_candidate_diversification(
    avg_corr: float,
    per_sleeve: pd.Series,
    ticker: str = "The candidate",
) -> str:
    """
    "Diversifies vs doubles down" verdict for a candidate, in the page's 5j voice.

    Classifies by BOTH the average correlation to the sleeves and the bond-sleeve
    correlations (Core Fixed Income, TIPS): a high average with near-zero bond
    correlation reads as an equity double-down with some duration offset; a high
    average including the bonds reads as broadly redundant; a low average reads as
    a genuine diversifier. Names the highest-correlation sleeves (what it doubles
    down on) and any genuinely low/negative ones (the real offsets).

    Thresholds: avg ≥ 0.60 → doubles down; avg < 0.30 → genuine diversifier;
    in between → mixed. Bond split at 0.20.
    """
    ps = per_sleeve.dropna().sort_values(ascending=False)
    top = ps.head(2)
    top_str = " and ".join(f"ρ ≈ {v:+.2f} with {s}" for s, v in top.items())

    offsets = ps[ps < 0.15].sort_values().head(3)
    if not offsets.empty:
        off_str = ", ".join(f"{s} ({v:+.2f})" for s, v in offsets.items())
        if avg_corr < 0.30:
            offset_clause = f"Its lowest-correlation sleeves are {off_str}. "
        else:
            offset_clause = f"Only {off_str} offer genuine offset. "
    else:
        offset_clause = "No sleeve offers a genuinely low or negative correlation. "

    bond_vals = [
        float(per_sleeve[b]) for b in BOND_SLEEVES
        if b in per_sleeve.index and pd.notna(per_sleeve[b])
    ]
    avg_bond = float(np.mean(bond_vals)) if bond_vals else float("nan")

    if avg_corr >= 0.60:
        if not np.isnan(avg_bond) and avg_bond < 0.20:
            verdict = (
                "largely doubles down on the existing equity beta, with some duration "
                "offset from the bond sleeves"
            )
            tail = (
                "As a diversifier it adds little; as an expression of that exposure it is "
                "largely redundant given the existing SAA equity sleeves."
            )
        else:
            verdict = (
                "is redundant across the board — it co-moves even with the bond sleeves and "
                "adds little diversification"
            )
            tail = (
                "It overlaps the existing SAA broadly rather than filling a correlation gap."
            )
    elif avg_corr < 0.30:
        verdict = "is a genuine diversifier — its co-movement with the existing sleeves is low"
        tail = (
            "It is additive: it fills a correlation gap the current SAA sleeves do not cover."
        )
    else:
        verdict = (
            "is a mixed case — partial overlap with the equity sleeves but meaningful offset "
            "elsewhere"
        )
        tail = (
            "It is neither a pure double-down nor a clean diversifier; weigh the specific "
            "sleeve overlaps against the offsets."
        )

    return (
        f"{ticker}'s average correlation to the SAA sleeves is ρ ≈ {avg_corr:+.2f}, driven by "
        f"{top_str} — it {verdict}. {offset_clause}{tail}"
    )
