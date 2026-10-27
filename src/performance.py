"""Risk-adjusted performance metrics."""
import math

import numpy as np
import pandas as pd

from src.returns import flow_netted_daily_returns


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


def window_bounds(window: str, index: "pd.DatetimeIndex") -> "tuple[pd.Timestamp, pd.Timestamp]":
    """The first and last dates a window's metrics cover: its cutoff, or the series'
    first date for Since Inception, through the series' last date. The page averages
    the risk-free rate over exactly these dates (audit item 15a)."""
    end = index[-1]
    cutoff = _window_cutoff(window, end)
    start = index[0] if cutoff is None else max(cutoff, index[0])
    return start, end


def one_year_overlap_note(si_days: int) -> "str | None":
    """What a young portfolio's 1 Year window shares with Since Inception, or None.

    The note used to say Max DD, TE and IR "will diverge from Since Inception once the
    portfolio crosses 18 months", which was already untrue at 17: the two windows
    differ as soon as there is more than a year of history. What is true is how much
    they share, so that is what it says, while the overlap is more than half.
    """
    months = round(si_days / 30.44)
    if si_days <= 365:
        return (f"The portfolio is {months} months old, so the 1 Year window and Since "
                "Inception cover the same days.")
    if si_days < 730:
        return (f"The 1 Year window is the last 12 of the {months} months since inception, "
                "so it shares most of its data with Since Inception.")
    return None


def _downside_deviation(excess: pd.Series) -> float:
    """Root mean square of min(excess, 0) over all observations (target 0)."""
    return math.sqrt((excess.clip(upper=0.0) ** 2).mean())


# THE FLOOR (#502): a window's risk figures need this many TRADING DAYS, the days in
# the window on which the portfolio or the benchmark has a return. One floor, applied
# after the days on which neither moved are dropped (_trading_day_returns).
#
# There were two: fewer than 20 rows, then fewer than 10 such returns. The Performance
# page's series holds a row for every calendar day, so the first let a window through
# on its 20th calendar day, on about 13 sessions, under a sentence that said it needed
# 20 trading days; the second was the only one that counted sessions, and the sentence
# did not describe it.
#
# 15, not 20: the 1 Month window is the 30 days to the last close, and those hold as
# few as 19 sessions (the 30 days to June 22, 2026, with Memorial Day and Juneteenth
# in them), so a floor of 20 would hide it on such days (tests/test_performance.py).
MIN_TRADING_DAYS = 15


def _trading_day_returns(
    pv: pd.Series,
    bl: pd.Series,
    window: str,
    cashflows: "pd.Series | None",
) -> "tuple[pd.Series, pd.Series]":
    """The (portfolio, benchmark) daily returns ``window``'s figures are computed on:
    the window cut from both series, flows taken out of the flow days' returns, and the
    days on which neither series moved dropped. Their length is the window's count of
    trading days. ``pv`` must not be empty."""
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

    # External flows netted out of the flow days' returns, as the TWR nets them (#686).
    # A flow on the window's first date is the starting NAV.
    port_ret  = flow_netted_daily_returns(pv, cashflows).dropna()
    bench_ret = bl.pct_change().dropna()

    idx2      = port_ret.index.intersection(bench_ret.index)
    port_ret  = port_ret.loc[idx2]
    bench_ret = bench_ret.loc[idx2]

    # Exclude weekend/holiday non-trading days: the portfolio value series uses
    # pd.date_range(freq='D') with ffill, producing exact-zero returns on days
    # when markets are closed. Including these dilutes std toward zero (inflating
    # IR) and biases Sharpe/Sortino. Filter to rows where at least one series
    # has a non-zero return — those are actual trading days.
    _trading = (port_ret != 0) | (bench_ret != 0)
    return port_ret[_trading], bench_ret[_trading]


def trading_days_in_window(
    pv: pd.Series,
    bl: pd.Series,
    window: str = "SI",
    cashflows: "pd.Series | None" = None,
) -> int:
    """How many trading days ``window`` holds: the count compute_risk_metrics holds to
    MIN_TRADING_DAYS, from the same returns. The Performance page prints it beside the
    floor when a window has no figures."""
    if pv.empty:
        return 0
    return len(_trading_day_returns(pv, bl, window, cashflows)[0])


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
        rf_annual:  Annual risk-free rate as decimal; also Sortino's target. The
                    Performance page passes each window's average 3-month Treasury bill
                    rate (src.risk_free, audit item 15a). The default, 4.5%, is the
                    fixed rate the page used before, kept for callers that pass none.
        window:     "SI" = full inception history; "1Y" / "3M" / "1M" = trailing
                    calendar-day windows; "YTD" = year-to-date from Jan 1.
        cashflows:  Optional daily net external-flow series (signed, in the value
                    series' units, DatetimeIndex; see
                    src/holdings.get_external_cashflow_series).
                    When given, the daily return on each flow day is computed as
                    r_t = (V_t - V_{t-1} - CF_t) / V_{t-1} — end-of-day flow
                    timing, matching src/returns.twr_daily_linked — so a
                    contribution is not counted as market return
                    (src.returns.flow_netted_daily_returns). All other days
                    (and the None case) keep the plain pct_change computation,
                    so flowless windows are numerically unchanged.

    Returns a dict with keys:
        sharpe, sortino, annualized_vol_pct, max_drawdown_pct, tracking_error_pct,
        information_ratio, beta, active_return_pct, n_days, ann_port_return_pct,
        ann_bench_return_pct, var_95_pct, cvar_95_pct, bench_sharpe, bench_sortino,
        bench_annualized_vol_pct, bench_max_drawdown_pct, bench_var_95_pct,
        bench_cvar_95_pct.
    Returns {} for a window with fewer than MIN_TRADING_DAYS trading days
    (trading_days_in_window gives the count).
    """
    if pv.empty:
        return {}
    port_ret, bench_ret = _trading_day_returns(pv, bl, window, cashflows)

    n = len(port_ret)
    if n < MIN_TRADING_DAYS:
        return {}

    rf_daily   = (1 + rf_annual) ** (1 / 252) - 1
    excess_ret = port_ret - rf_daily

    # Sharpe (annualized)
    sharpe = (excess_ret.mean() / excess_ret.std(ddof=1)) * math.sqrt(252)

    # Sortino: mean excess return over the downside deviation, the root mean square of
    # min(excess, 0) taken over EVERY observation (Sortino and Price, 1994). It used
    # to average the squared shortfalls over the losing days only, which for a
    # roughly symmetric series is about the standard deviation itself, so Sortino
    # read almost exactly Sharpe (1.84 beside 1.86 on the demo's since-inception
    # window). The standard form runs about 1.4x Sharpe on such a series.
    neg = excess_ret[excess_ret < 0]
    if len(neg) > 1:
        sortino = (excess_ret.mean() / _downside_deviation(excess_ret)) * math.sqrt(252)
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
            (bench_excess.mean() / _downside_deviation(bench_excess)) * math.sqrt(252)
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
