"""Time-weighted return calculations: daily-linked (GIPS) and Modified Dietz."""
from datetime import date, timedelta
from typing import Literal

import pandas as pd


def twr_daily_linked(values: pd.Series, cashflows: pd.Series) -> float:
    """
    Chain-linked daily TWR (GIPS-compliant).

    For each day t: r_t = (V_t - V_{t-1} - CF_t) / V_{t-1}
    Cash flows are assumed to occur at the beginning of day t (before market moves).
    Returns cumulative product(1 + r_t) - 1.
    """
    if len(values) < 2:
        return 0.0

    cf = cashflows.reindex(values.index).fillna(0.0)
    cumulative = 1.0

    for i in range(1, len(values)):
        v_prev = float(values.iloc[i - 1])
        if v_prev == 0.0:
            continue
        v_curr  = float(values.iloc[i])
        cf_curr = float(cf.iloc[i])
        r = (v_curr - v_prev - cf_curr) / v_prev
        cumulative *= (1.0 + r)

    return cumulative - 1.0


def twr_modified_dietz(values: pd.Series, cashflows: pd.Series) -> float:
    """
    Modified Dietz approximation to TWR.

    Return = (V_end - V_start - sum(CF)) / (V_start + sum(w_i * CF_i))
    where w_i = (end_date - cf_date).days / total_days.

    Cash flows on the first date are excluded (treated as the starting NAV).
    """
    if len(values) < 2:
        return 0.0

    v_start  = float(values.iloc[0])
    v_end    = float(values.iloc[-1])
    start_ts = pd.Timestamp(values.index[0])
    end_ts   = pd.Timestamp(values.index[-1])
    total_days = (end_ts - start_ts).days

    if total_days == 0:
        return (v_end - v_start) / v_start if v_start != 0.0 else 0.0

    # Exclude the first date — that's the starting NAV, not a mid-period flow
    cf = cashflows.reindex(values.index).fillna(0.0).iloc[1:]
    total_cf = float(cf.sum())

    weighted_cf = 0.0
    for dt, flow in cf.items():
        if flow == 0.0:
            continue
        days_remaining = (end_ts - pd.Timestamp(dt)).days
        w = days_remaining / total_days
        weighted_cf += w * float(flow)

    denominator = v_start + weighted_cf
    if denominator == 0.0:
        return 0.0

    return (v_end - v_start - total_cf) / denominator


def annualize(cumulative_return: float, days: int) -> float:
    """Annualize a cumulative return over a number of calendar days."""
    if days <= 0:
        return 0.0
    return (1.0 + cumulative_return) ** (365.0 / days) - 1.0


def period_return(
    method: Literal["daily", "modified_dietz"],
    values: pd.Series,
    cashflows: pd.Series,
    period: Literal["1M", "3M", "YTD", "1Y", "SI"],
) -> float:
    """
    Compute TWR for the given period by slicing the full inception-to-date series.

    period:
      'SI'  — since inception (full series)
      '1Y'  — trailing 365 calendar days
      'YTD' — Jan 1 of the last date's year through last date
      '3M'  — trailing 90 calendar days
      '1M'  — trailing 30 calendar days
    """
    if len(values) < 2:
        return 0.0

    last_ts   = pd.Timestamp(values.index[-1])
    last_date = last_ts.date()

    if period == "SI":
        start_ts = pd.Timestamp(values.index[0])
    elif period == "1Y":
        start_ts = pd.Timestamp(last_date - timedelta(days=365))
    elif period == "YTD":
        start_ts = pd.Timestamp(date(last_date.year, 1, 1))
    elif period == "3M":
        start_ts = pd.Timestamp(last_date - timedelta(days=90))
    elif period == "1M":
        start_ts = pd.Timestamp(last_date - timedelta(days=30))
    else:
        return 0.0

    v_slice = values[values.index >= start_ts]
    if len(v_slice) < 2:
        return 0.0

    cf_slice = cashflows.reindex(v_slice.index).fillna(0.0)

    if method == "daily":
        return twr_daily_linked(v_slice, cf_slice)
    return twr_modified_dietz(v_slice, cf_slice)
