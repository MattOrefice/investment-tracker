"""Risk-adjusted performance metrics."""
import math

import numpy as np
import pandas as pd


def _window_cutoff(window: str, anchor_end: "pd.Timestamp") -> "pd.Timestamp | None":
    """Return the start-of-window cutoff for the given window, or None for SI.

    Anchored on ``anchor_end`` — the value series' last date (the settled frontier
    after the page's display-anchor clip), NOT date.today(). This keeps the
    risk-metric windows aligned with the period-RETURN windows of the same label
    (both offset back from the series' last date) and makes the cutoff
    wall-clock-independent, so a short (1M) risk stat no longer drifts day-to-day
    with the calendar.
    """
    if window == "1Y":
        return anchor_end - pd.Timedelta(days=365)
    if window == "3M":
        return anchor_end - pd.Timedelta(days=90)
    if window == "1M":
        return anchor_end - pd.Timedelta(days=30)
    if window == "YTD":
        return pd.Timestamp(anchor_end.year, 1, 1)
    return None  # "SI" = full inception series


def compute_risk_metrics(
    pv: pd.Series,
    bl: pd.Series,
    rf_annual: float = 0.045,
    window: str = "SI",
    cashflows: "pd.Series | None" = None,
) -> dict:
    """
    Compute annualized risk-adjusted metrics from daily portfolio and benchmark series.

    Args:
        pv:         Daily portfolio value series (absolute dollars, DatetimeIndex).
        bl:         Daily benchmark series (any scale, DatetimeIndex).
        rf_annual:  Annual risk-free rate as decimal. Default 4.5% (current cash yield).
        window:     "SI" = full inception history; "1Y" / "3M" / "1M" = trailing
                    calendar-day windows; "YTD" = year-to-date from Jan 1.
        cashflows:  Optional daily net external-flow series (signed dollars,
                    DatetimeIndex; see src/holdings.get_external_cashflow_series).
                    When given, the daily return on each flow day is computed as
                    r_t = (V_t - V_{t-1} - CF_t) / V_{t-1} — end-of-day flow
                    timing, matching src/returns.twr_daily_linked — so a
                    contribution is not counted as market return. All other days
                    (and the None case) keep the plain pct_change computation,
                    so flowless windows are numerically unchanged.

    Returns a dict with keys:
        sharpe, sortino, annualized_vol_pct, max_drawdown_pct, tracking_error_pct,
        information_ratio, beta, active_return_pct, n_days, ann_port_return_pct,
        ann_bench_return_pct, var_95_pct, cvar_95_pct, bench_sharpe, bench_sortino,
        bench_annualized_vol_pct, bench_max_drawdown_pct, bench_var_95_pct,
        bench_cvar_95_pct.
    Returns {} if fewer than 20 observations.
    """
    if pv.empty:
        return {}
    # Anchor trailing windows on the series' last date (the settled frontier), not
    # the wall clock — see _window_cutoff. pv.index[-1] is C when the page passes a
    # display-clipped series; for synthetic test series it is the series' own end.
    cutoff = _window_cutoff(window, pv.index[-1])
    if cutoff is not None:
        # .copy() breaks any view relationship with the @st.cache_data-frozen
        # Series; without it, pandas may reference the original full-length
        # underlying array through the view's base, collapsing the window filter.
        pv = pv.loc[pv.index >= cutoff].copy()
        bl = bl.loc[bl.index >= cutoff].copy()

    idx = pv.index.intersection(bl.index)
    pv  = pv.loc[idx].copy()
    bl  = bl.loc[idx].copy()

    if len(pv) < 20:
        return {}

    port_ret  = pv.pct_change().dropna()
    bench_ret = bl.pct_change().dropna()

    # Net external flows out of the flow days' returns (end-of-day timing, same
    # convention as twr_daily_linked). Only days with a nonzero flow are touched:
    # every other day keeps its exact pct_change value, so a window with no
    # flows produces bit-identical metrics to the cashflows=None path. A flow on
    # the window's first date is excluded (it is the starting NAV — pct_change's
    # first row is NaN and already dropped). Days with V_{t-1} == 0 are left to
    # the pre-existing pct_change behavior rather than divided by zero here.
    if cashflows is not None:
        cf = cashflows.reindex(port_ret.index).fillna(0.0)
        v_prev = pv.shift(1)
        flow_days = cf.index[(cf != 0.0) & (v_prev.reindex(cf.index) != 0.0)]
        if len(flow_days):
            port_ret.loc[flow_days] = (
                pv.loc[flow_days] - v_prev.loc[flow_days] - cf.loc[flow_days]
            ) / v_prev.loc[flow_days]

    idx2      = port_ret.index.intersection(bench_ret.index)
    port_ret  = port_ret.loc[idx2]
    bench_ret = bench_ret.loc[idx2]

    # Exclude weekend/holiday non-trading days: the portfolio value series uses
    # pd.date_range(freq='D') with ffill, producing exact-zero returns on days
    # when markets are closed. Including these dilutes std toward zero (inflating
    # IR) and biases Sharpe/Sortino. Filter to rows where at least one series
    # has a non-zero return — those are actual trading days.
    _trading = (port_ret != 0) | (bench_ret != 0)
    port_ret  = port_ret[_trading]
    bench_ret = bench_ret[_trading]

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

    # Annualized portfolio volatility (std dev of daily returns * sqrt(252))
    ann_vol = float(port_ret.std(ddof=1)) * math.sqrt(252)

    # Tracking error, information ratio, beta
    active_ret     = port_ret - bench_ret
    tracking_error = float(active_ret.std(ddof=1)) * math.sqrt(252)
    ann_port       = float((1 + port_ret).prod() ** (252 / n) - 1)
    ann_bench      = float((1 + bench_ret).prod() ** (252 / n) - 1)
    ir = (ann_port - ann_bench) / tracking_error if tracking_error > 1e-10 else float("nan")

    cov_mat   = np.cov(port_ret.values, bench_ret.values, ddof=1)
    bench_var = cov_mat[1, 1]
    beta      = cov_mat[0, 1] / bench_var if bench_var > 1e-10 else float("nan")

    # VaR(95%) and CVaR(95%) — daily positive loss magnitudes
    var_threshold = float(np.percentile(port_ret, 5))   # 5th percentile (negative)
    var_95        = -var_threshold                        # positive loss magnitude
    tail_mask     = port_ret <= var_threshold
    cvar_95       = -float(port_ret[tail_mask].mean()) if tail_mask.any() else var_95

    # Benchmark standalone metrics — same trading-day filter and RF as portfolio row
    bench_excess      = bench_ret - rf_daily
    _bench_excess_std = bench_excess.std(ddof=1)
    bench_sharpe = (
        (bench_excess.mean() / _bench_excess_std) * math.sqrt(252)
        if _bench_excess_std > 1e-10 else float("nan")
    )
    _bench_neg      = bench_excess[bench_excess < 0]
    if len(_bench_neg) > 1:
        bench_sortino = (
            (bench_excess.mean() / math.sqrt((_bench_neg ** 2).mean())) * math.sqrt(252)
        )
    else:
        bench_sortino = float("nan")
    bench_cum      = (1 + bench_ret).cumprod()
    bench_peak     = bench_cum.cummax()
    bench_max_dd   = float(((bench_cum - bench_peak) / bench_peak).min())
    bench_ann_vol  = float(bench_ret.std(ddof=1)) * math.sqrt(252)
    bench_var_thr  = float(np.percentile(bench_ret, 5))
    bench_var_95   = -bench_var_thr
    bench_tail     = bench_ret <= bench_var_thr
    bench_cvar_95  = -float(bench_ret[bench_tail].mean()) if bench_tail.any() else bench_var_95

    return {
        "sharpe":                    sharpe,
        "sortino":                   sortino,
        "annualized_vol_pct":        ann_vol * 100,
        "max_drawdown_pct":          max_dd * 100,
        "tracking_error_pct":        tracking_error * 100,
        "information_ratio":         ir,
        "beta":                      beta,
        "active_return_pct":         (ann_port - ann_bench) * 100,
        "n_days":                    n,
        "ann_port_return_pct":       ann_port * 100,
        "ann_bench_return_pct":      ann_bench * 100,
        "var_95_pct":                var_95 * 100,
        "cvar_95_pct":               cvar_95 * 100,
        "bench_sharpe":              bench_sharpe,
        "bench_sortino":             bench_sortino,
        "bench_annualized_vol_pct":  bench_ann_vol * 100,
        "bench_max_drawdown_pct":    bench_max_dd * 100,
        "bench_var_95_pct":          bench_var_95 * 100,
        "bench_cvar_95_pct":         bench_cvar_95 * 100,
    }
