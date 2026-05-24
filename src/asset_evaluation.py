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
RF_ANNUAL     = 0.0432        # 4.32%, consistent with Performance page Sharpe
TRADING_DAYS  = 252

# Sleeve benchmark tickers — same mapping as pages/9_Correlations.py.
# Cash / SPAXX excluded: near-zero variance distorts correlation and optimization.
SLEEVE_BENCHMARKS: dict[str, list[tuple[str, float]]] = {
    "US Large Core":     [("SPY",  1.0)],
    "US Large Quality":  [("QUAL", 1.0)],
    "US Large Value":    [("IWD",  1.0)],
    "US Small Cap":      [("IWM",  1.0)],
    "Intl Developed":    [("EFA",  1.0)],
    "Emerging Markets":  [("EEM",  1.0)],
    "Core Fixed Income": [("IEF",  1.0)],
    "TIPS":              [("TIP",  1.0)],
    "Real Assets":       [("VNQ",  0.6), ("DBC", 0.4)],
}

# SAA target weights for the 9 non-cash sleeves.
# Scaled to sum to 1.0 for MV analysis (cash is excluded and its 3% distributed pro-rata).
_RAW_WEIGHTS: dict[str, float] = {
    "US Large Core":     0.16,
    "US Large Quality":  0.14,
    "US Large Value":    0.08,
    "US Small Cap":      0.07,
    "Intl Developed":    0.19,
    "Emerging Markets":  0.08,
    "Core Fixed Income": 0.09,
    "TIPS":              0.06,
    "Real Assets":       0.10,
}
_raw_total    = sum(_RAW_WEIGHTS.values())
SLEEVE_WEIGHTS: dict[str, float] = {k: v / _raw_total for k, v in _RAW_WEIGHTS.items()}
SLEEVES       = list(SLEEVE_BENCHMARKS.keys())

# Shared conclusion — used by both pages/10_Asset_Evaluation.py and src/reports.py.
# Excludes the Streamlit-specific "This page will update automatically" sentence.
CONCLUSION = (
    "Bitcoin's sample-period Sharpe improvement is real but almost entirely "
    "attributable to its exceptional 2020–2021 bull market return. "
    "The post-2020 correlation structure, the 2022 joint drawdown, and the "
    "fundamental absence of cash flows make a strong case against inclusion "
    "in a tax-aware taxable account with an institutional-style SAA. "
    "The 2024 spot ETF launches have already partially satisfied the "
    "operational-risk concern that historically gated institutional "
    "adoption — IBIT, FBTC, and similar wrappers remove the custody and "
    "counterparty frictions that defined the pre-2024 implementation "
    "landscape. The remaining barriers to inclusion under this framework "
    "are analytical (the correlation regime and the 2022 joint stress) and "
    "fundamental (no cash flow anchor for valuation), not operational. "
    "The framework does not foreclose future re-evaluation if (1) the "
    "correlation regime reverts toward zero or (2) the asset develops a "
    "cleaner valuation framework."
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
) -> pd.DataFrame:
    """
    Daily returns for all 9 non-cash SAA sleeves, trading-day intersection.

    Blended sleeves use weighted-average daily returns.
    Rows where any sleeve has a near-zero absolute return sum are dropped
    (Phase 11 zero-return rule: these are non-trading days for equity markets).
    """
    end = end_date or date.today().isoformat()
    ret_dict: dict[str, pd.Series] = {}

    for sleeve_name, components in SLEEVE_BENCHMARKS.items():
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
    Input mu and cov are daily; rf_annual is converted to daily internally.
    """
    rf_daily = rf_annual / TRADING_DAYS
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

    rf_daily = rf_annual / TRADING_DAYS
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
    rf_daily   = rf_annual / TRADING_DAYS
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
    existing sleeves using sleeve_weights (defaults to SLEEVE_WEIGHTS).
    Returns DataFrame with columns: btc_alloc, sharpe.
    """
    if allocations is None:
        allocations = [0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10]
    if sleeve_weights is None:
        sleeve_weights = SLEEVE_WEIGHTS

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
        sleeve_weights = SLEEVE_WEIGHTS

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
            "BTC Alloc": f"{alpha:.0%}",
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
