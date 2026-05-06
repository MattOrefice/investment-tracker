"""Risk-adjusted performance metrics."""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd


def compute_risk_metrics(
    pv: pd.Series,
    bl: pd.Series,
    rf_annual: float = 0.045,
    window: str = "SI",
) -> dict:
    """
    Compute annualized risk-adjusted metrics from daily portfolio and benchmark series.

    Args:
        pv:         Daily portfolio value series (absolute dollars, DatetimeIndex).
        bl:         Daily benchmark series (any scale, DatetimeIndex).
        rf_annual:  Annual risk-free rate as decimal. Default 4.5% (current cash yield).
        window:     "SI" = full inception history; "1Y" / "3M" / "1M" = trailing
                    calendar-day windows; "YTD" = year-to-date from Jan 1.

    Returns a dict with keys:
        sharpe, sortino, max_drawdown_pct, tracking_error_pct, information_ratio,
        n_days, ann_port_return_pct, ann_bench_return_pct.
    Returns {} if fewer than 20 observations.
    """
    if window != "SI":
        today = date.today()
        if window == "1Y":
            cutoff = pd.Timestamp(today - timedelta(days=365))
        elif window == "3M":
            cutoff = pd.Timestamp(today - timedelta(days=90))
        elif window == "1M":
            cutoff = pd.Timestamp(today - timedelta(days=30))
        elif window == "YTD":
            cutoff = pd.Timestamp(date(today.year, 1, 1))
        else:
            cutoff = None
        if cutoff is not None:
            pv = pv[pv.index >= cutoff]
            bl = bl[bl.index >= cutoff]

    idx = pv.index.intersection(bl.index)
    pv  = pv.loc[idx]
    bl  = bl.loc[idx]

    if len(pv) < 20:
        return {}

    port_ret  = pv.pct_change().dropna()
    bench_ret = bl.pct_change().dropna()
    idx2      = port_ret.index.intersection(bench_ret.index)
    port_ret  = port_ret.loc[idx2]
    bench_ret = bench_ret.loc[idx2]

    n = len(port_ret)
    if n < 10:
        return {}

    rf_daily   = (1 + rf_annual) ** (1 / 252) - 1
    excess_ret = port_ret - rf_daily

    # Sharpe (annualized)
    sharpe = (excess_ret.mean() / excess_ret.std(ddof=1)) * math.sqrt(252)

    # Sortino — semi-deviation of excess returns below zero
    neg = excess_ret[excess_ret < 0]
    if len(neg) > 1:
        sortino = (excess_ret.mean() / math.sqrt((neg ** 2).mean())) * math.sqrt(252)
    else:
        sortino = float("nan")

    # Max drawdown (most negative peak-to-trough)
    cum   = (1 + port_ret).cumprod()
    peak  = cum.cummax()
    max_dd = float(((cum - peak) / peak).min())

    # Tracking error and information ratio
    active_ret     = port_ret - bench_ret
    tracking_error = float(active_ret.std(ddof=1)) * math.sqrt(252)
    ann_port       = float((1 + port_ret).prod() ** (252 / n) - 1)
    ann_bench      = float((1 + bench_ret).prod() ** (252 / n) - 1)
    ir = (ann_port - ann_bench) / tracking_error if tracking_error > 1e-10 else float("nan")

    # VaR(95%) and CVaR(95%) — daily positive loss magnitudes
    var_threshold = float(np.percentile(port_ret, 5))   # 5th percentile (negative)
    var_95        = -var_threshold                        # positive loss magnitude
    tail_mask     = port_ret <= var_threshold
    cvar_95       = -float(port_ret[tail_mask].mean()) if tail_mask.any() else var_95

    return {
        "sharpe":               sharpe,
        "sortino":              sortino,
        "max_drawdown_pct":     max_dd * 100,
        "tracking_error_pct":   tracking_error * 100,
        "information_ratio":    ir,
        "n_days":               n,
        "ann_port_return_pct":  ann_port * 100,
        "ann_bench_return_pct": ann_bench * 100,
        "var_95_pct":           var_95 * 100,
        "cvar_95_pct":          cvar_95 * 100,
    }
