"""The policy backtest's engine (#667, PR 2 of 3): the book's own SAA policy applied to
history from February 2006. Every figure it returns is hypothetical: no portfolio held
these weights, and the page that shows them (PR 3) says so wherever it shows one.

The rules, decided by the owner on October 18, 2026 and recorded on #667:
  * start at the policy's targets at the end of February 2006, the history's base month;
  * each month, each holding earns that month's total return from
    data/backtest/monthly_returns.csv: its proxy's until the fund's own record is used,
    the fund's after (data/backtest/series.json names each proxy and its last month);
  * at each month-end check, when any sleeve is outside its band, every sleeve goes back
    to its target. "Outside" is rebalance.compute_drift's verdict, the one the SAA page
    shows;
  * Real Assets is 60% VNQ and 40% PDBC, reset to that only when the portfolio
    rebalances: between rebalances the two drift inside the sleeve;
  * no contributions, trading costs or taxes;
  * each book runs its own policy: the sleeves, targets, bands and held funds of the book
    this process serves (policy.sleeves, sleeve_config.sleeve_holdings).

It is set beside the S&P 500 (SPY's total return) and the app's own 60/40, 60% SPY and
40% AGG (benchmarks.NAIVE_60_40) reset to those weights at each calendar quarter's end,
as the app's 60/40 is (#383). Its Sharpe ratio is measured against the 3-month bill
(DGS3MO, the series sample_risk_free reads), carried in the same file.

Nothing here fetches, writes, or enters a quarter lock: the history is a committed file,
read as it stands, and the policy is the book's own.

The page (pages/16_Policy_Backtest.py, PR 3) reads every figure it shows from run(): the
statistics, the rebalances, each proxy's tracking against the fund it stands in for, and
the paths its charts draw (wealth, drawdowns, rolling_annual).

The page's rebuild for readability (decided by the owner on October 22, 2026) reads more
from run(), and nothing it already returned changes: each series' ending value, best and
worst month, share of positive months and the months its deepest drawdown took; its
deepest fall reaching into each year in WINDOWS; the policy against each comparison;
the rolling windows summed up; each rebalance's turnover; each sleeve on its own; and
the six paragraphs the page prints, each read from those results.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import pandas as pd

from src.prose_helpers import _list

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "backtest" / "monthly_returns.csv"
SPEC = ROOT / "data" / "backtest" / "series.json"
# The history's only writer, as the page names it: tools/refresh_backtest_history.py's
# usage line.
REFRESH_COMMAND = "python tools/refresh_backtest_history.py"

# The growth chart's starting amount, and the rolling chart's window in months.
GROWTH_OF = 10_000
ROLLING_MONTHS = 60

# Why a series has no volatility, and so no Sharpe ratio (#672): the page prints these
# beside the dash it shows in their place.
FEWER_THAN_TWO_MONTHS = "fewer than two monthly returns"
NO_VOLATILITY = "no volatility: every monthly return is the same"

REAL_ASSETS = "Real Assets"
# The sleeve's two funds and the shares it is reset to, as the owner decided: its
# benchmark's 60% REIT and 40% commodity legs, held through VNQ and PDBC.
REAL_ASSETS_MIX = (("VNQ", 0.6), ("PDBC", 0.4))
# The calendar years the page shows on their own.
WINDOWS = (2008, 2020, 2022)

POLICY = "Policy"
SP500 = "S&P 500 (SPY)"
SIXTY_FORTY = "60/40"

# Up- and down-capture as Morningstar defines them (decided by the owner on October 22,
# 2026), cited on the page with the source's date. Its definitions: the up capture ratio
# is "a measure of a manager's performance in markets with returns at or above 0%
# relative to the market (benchmark)", the down capture ratio over the periods "in which
# the market return is less than 0" (page 29 and page 7).
CAPTURE_SOURCE = {
    "name": "Morningstar, Custom Calculation Data Points",
    "url": "https://morningstardirect.morningstar.com/clientcomm/customcalculations.pdf",
    "date": "October 2016",
}


class PolicyBacktestError(ValueError):
    """The backtest cannot be run as asked, and nothing was computed."""


@dataclass(frozen=True)
class Sleeve:
    """One sleeve as the backtest holds it: its target and band, and the funds it is
    split into at each reset, with their shares of the sleeve."""

    name: str
    target: float
    band: float
    mix: "tuple[tuple[str, float], ...]"


@dataclass(frozen=True)
class Rebalance:
    """A month-end check that found a sleeve outside its band: the month, every sleeve
    outside, in the policy's order, and the turnover: half the sum of the absolute
    differences between each sleeve's weight just before the reset and its target."""

    month: str
    triggers: "tuple[str, ...]"
    turnover: float


@dataclass(frozen=True)
class Simulation:
    """The policy's monthly returns, its rebalances, each holding's share of the
    portfolio at the start of every month (column "sleeve/fund"), and each sleeve's
    monthly return as the backtest held it (column: the sleeve)."""

    returns: pd.Series
    rebalances: "tuple[Rebalance, ...]"
    start_weights: pd.DataFrame
    sleeve_returns: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(frozen=True)
class Point:
    """A figure and the month it belongs to: a month's return, or a rolling window's
    annualized return with the month the window ends."""

    month: str
    value: float


@dataclass(frozen=True)
class Drawdown:
    """The deepest fall of month-end wealth from a previous high: its depth (negative),
    the month of the high, the month of the low, and the first month wealth stood at
    or above the high again (None while it has not)."""

    depth: float
    peak: str
    trough: str
    recovered: Optional[str]


@dataclass(frozen=True)
class Episode:
    """One fall of month-end wealth below an earlier high: its depth (negative), the
    month of the high, the lowest month before wealth stood at the high again, the
    months from the high to the low, and the first month at or above the high again
    (None while it has not)."""

    depth: float
    peak: str
    trough: str
    months_to_trough: int
    recovered: Optional[str]


@dataclass(frozen=True)
class Statistics:
    """What the page shows for one series. ``calendar_years`` holds every year the
    months reach, and ``partial_years`` those with fewer than twelve of them (the first
    and the current); ``windows`` the years in WINDOWS. With fewer than two monthly
    returns, or none that differ, there is no volatility: ``annual_volatility`` and
    ``sharpe`` are None and ``no_volatility`` says why (decided by the owner on October
    19, 2026, #672).

    ``ending_value`` is what GROWTH_OF became; ``best_month`` and ``worst_month`` the
    series' highest and lowest monthly returns (the first, on a tie);
    ``positive_share`` the share of months above 0%. ``months_to_trough`` and
    ``months_to_recovery`` count the deepest drawdown's months from peak to trough and
    from trough to recovery: None with no fall, and the second while it has not
    recovered. ``episodes`` holds, for each year in WINDOWS the months reach, the
    deepest fall whose months from peak to trough include a month of that year, or
    None when no fall's do (stress_episodes)."""

    annual_return: float
    annual_volatility: Optional[float]
    sharpe: Optional[float]
    drawdown: Drawdown
    calendar_years: "dict[int, float]"
    partial_years: "tuple[int, ...]"
    windows: "dict[int, float]"
    ending_value: float
    best_month: Point
    worst_month: Point
    positive_share: float
    months_to_trough: Optional[int]
    months_to_recovery: Optional[int]
    episodes: "dict[int, Optional[Episode]]"
    no_volatility: Optional[str] = None


@dataclass(frozen=True)
class Comparison:
    """The policy beside one comparison over the backtest's months.

    * ``active_return``: the policy's annualized return less the comparison's;
    * ``beta``: the covariance of the two monthly series over the comparison's variance;
    * ``correlation``: of the two monthly series;
    * ``tracking_error``: the sample standard deviation of the monthly difference x
      sqrt(12);
    * up- and down-capture, as Morningstar defines them (CAPTURE_SOURCE): up markets are
      the months the comparison returned at or above 0%, down markets those it returned
      below 0%. Each capture is the policy's annualized
      compound return over those months divided by the comparison's own, and both
      returns are kept beside it (``up_policy``, ``up_comparison``; ``down_...``).

    A figure the months cannot give is None: beta, correlation and tracking error with
    fewer than two months, beta and correlation when the comparison never moves, the
    correlation when the policy never moves, and a capture with no month of its kind
    or a comparison return of 0% over them."""

    months: int
    active_return: float
    beta: Optional[float]
    correlation: Optional[float]
    tracking_error: Optional[float]
    up_months: int
    up_policy: Optional[float]
    up_comparison: Optional[float]
    up_capture: Optional[float]
    down_months: int
    down_policy: Optional[float]
    down_comparison: Optional[float]
    down_capture: Optional[float]


@dataclass(frozen=True)
class RollingSummary:
    """The rolling windows of ``months`` months, summed up: how many there are and the
    months the first and last end; for each comparison, the windows in which the
    policy's annualized return was below the comparison's, as a count and a share; and
    for each series its best and worst window and the latest. With no window every
    share and point is None."""

    months: int
    windows: int
    first: Optional[str]
    last: Optional[str]
    below: "dict[str, int]"
    share_below: "dict[str, Optional[float]]"
    best: "dict[str, Optional[Point]]"
    worst: "dict[str, Optional[Point]]"
    latest: "dict[str, Optional[Point]]"


@dataclass(frozen=True)
class SleeveStatistics:
    """One sleeve's monthly returns as the backtest held it, on their own: annualized
    return, volatility (None with the reason, as statistics() gives it), the deepest
    drawdown, and the correlation with the S&P 500 (SPY) over the backtest's months
    (None when either never moves, or with fewer than two months)."""

    sleeve: str
    annual_return: float
    annual_volatility: Optional[float]
    drawdown: Drawdown
    correlation: Optional[float]
    no_volatility: Optional[str] = None


@dataclass(frozen=True)
class Tracking:
    """A fund beside its proxy over the months the history holds both, from the splice
    month for a fund spliced at an index change (tracking_from): the first and last
    of them and how many, the fund's annualized return minus the proxy's, the tracking
    error (the sample standard deviation of the monthly difference x sqrt(12)), and the
    correlation of the two. A statistic the overlap cannot give is None: every one with
    fewer than two months, the correlation when either series never moves."""

    fund: str
    proxy: str
    first: Optional[str]
    last: Optional[str]
    months: int
    difference: Optional[float]
    error: Optional[float]
    correlation: Optional[float]


@dataclass(frozen=True)
class Backtest:
    """The whole result: the policy's simulation, the three monthly return series side
    by side (POLICY, SP500, SIXTY_FORTY), the bill's monthly returns, each series'
    statistics, the sleeves the policy was run with, and each of their funds' tracking
    against its proxy (funds with no proxy are absent). Then the policy against each
    comparison (keyed SP500 and SIXTY_FORTY), the rolling windows summed up, and each
    sleeve's statistics in the policy's order."""

    simulation: Simulation
    returns: pd.DataFrame
    bill: pd.Series
    statistics: "dict[str, Statistics]"
    sleeves: "tuple[Sleeve, ...]"
    tracking: "dict[str, Tracking]" = field(default_factory=dict)
    comparisons: "dict[str, Comparison]" = field(default_factory=dict)
    rolling: "Optional[RollingSummary]" = None
    sleeve_statistics: "dict[str, SleeveStatistics]" = field(default_factory=dict)


def load_history(path: Path = HISTORY) -> pd.DataFrame:
    """The committed monthly history, indexed by month ('YYYY-MM')."""
    return pd.read_csv(path, index_col="month", dtype={"month": str})


def load_spec(path: Path = SPEC) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _previous(month: str) -> str:
    return (pd.Period(month, "M") - 1).strftime("%Y-%m")


def _next(month: str) -> str:
    return (pd.Period(month, "M") + 1).strftime("%Y-%m")


def backtest_months(history: pd.DataFrame, spec: dict) -> "list[str]":
    """The months the backtest runs: from series.json's ``backtest_from`` to the last
    month the history holds."""
    return [m for m in history.index if m >= spec["backtest_from"]]


def fund_returns(fund: str, history: pd.DataFrame, spec: dict) -> pd.Series:
    """``fund``'s monthly total returns over the backtest's months: its proxy's through
    the proxy's last month, its own after. Refuses a fund with no series, or a month
    neither covers."""
    entry = spec["funds"].get(fund)
    if entry is None:
        raise PolicyBacktestError(f"{fund} has no series in {SPEC.name}: the backtest "
                                  "has no history for it")
    months = backtest_months(history, spec)
    own = history[fund].reindex(months)
    if entry.get("proxy"):
        proxy = history[entry["proxy"]].reindex(months)
        out = own.where(own.index > entry["proxy_until"], proxy)
    else:
        out = own
    missing = list(out.index[out.isna()])
    if missing:
        raise PolicyBacktestError(f"{fund} has no return for {', '.join(missing[:6])}")
    return out.rename(fund)


def book_sleeves() -> "tuple[Sleeve, ...]":
    """The sleeves of the book this process serves that carry a target, in the policy's
    order, each with its band and the fund it holds; Real Assets with REAL_ASSETS_MIX.

    Refuses a sleeve holding no fund, or more than one outside Real Assets, and targets
    that do not sum to 1: each would need a rule nobody has decided."""
    from src import policy
    from src.sleeve_config import sleeve_holdings
    held = sleeve_holdings()
    of_ticker = policy.sleeve_of_ticker()
    rows = sorted(policy.sleeves(), key=lambda r: policy.nulls_first(r["sort_order"]))
    out = []
    for r in rows:
        target = float(r["target_weight"])
        if target <= 0:
            continue
        name = r["name"]
        if name == REAL_ASSETS:
            lacking = [f for f, _w in REAL_ASSETS_MIX if of_ticker.get(f) != REAL_ASSETS]
            if lacking:
                raise PolicyBacktestError(f"{REAL_ASSETS} does not hold {', '.join(lacking)}, "
                                          "the funds its 60/40 mix is split into")
            mix = REAL_ASSETS_MIX
        else:
            funds = held.get(name, [])
            if len(funds) != 1:
                raise PolicyBacktestError(f"{name} holds {', '.join(funds) or 'no fund'}: the "
                                          "backtest holds one fund per sleeve")
            mix = ((funds[0], 1.0),)
        out.append(Sleeve(name, target, float(r["tolerance_band"]), mix))
    total = sum(s.target for s in out)
    if abs(total - 1.0) > 1e-9:
        raise PolicyBacktestError(f"the targets sum to {total:.6f}, not 1")
    return tuple(out)


def simulate(returns: pd.DataFrame, sleeves: "tuple[Sleeve, ...] | list[Sleeve]") -> Simulation:
    """Run the policy over ``returns`` (months x funds), starting at the targets.

    Each month every holding earns its fund's return for that month and nothing later.
    Then the month-end check: compute_drift's verdict on each sleeve's share of the
    portfolio. When any sleeve is outside its band, every holding is reset to its
    sleeve's target times its share of the sleeve, and the rebalance records its
    turnover from the weights just before the reset. Each sleeve's monthly return is
    its holdings' value at the month's end over their value at its start."""
    from src.rebalance import compute_drift
    targets = {s.name: s.target for s in sleeves}
    bands = {s.name: s.band for s in sleeves}

    def reset(total: float) -> "dict[tuple[str, str], float]":
        return {(s.name, f): total * s.target * w for s in sleeves for f, w in s.mix}

    def by_sleeve(held: "dict[tuple[str, str], float]") -> "dict[str, float]":
        return {s.name: sum(held[(s.name, f)] for f, _w in s.mix) for s in sleeves}

    values = reset(1.0)
    monthly, starts, rebalances, sleeve_monthly = [], [], [], []
    for month in returns.index:
        before = sum(values.values())
        sleeves_before = by_sleeve(values)
        starts.append({f"{s}/{f}": v / before for (s, f), v in values.items()})
        values = {(s, f): v * (1 + float(returns.at[month, f])) for (s, f), v in values.items()}
        after = sum(values.values())
        monthly.append(after / before - 1)
        sleeves_after = by_sleeve(values)
        sleeve_monthly.append({n: sleeves_after[n] / sleeves_before[n] - 1
                               for n in sleeves_after})
        weights = {n: v / after for n, v in sleeves_after.items()}
        drift = compute_drift(weights, targets, bands)
        outside = tuple(s.name for s in sleeves if not bool(drift.at[s.name, "In Band"]))
        if outside:
            turnover = sum(abs(weights[s.name] - s.target) for s in sleeves) / 2
            rebalances.append(Rebalance(month, outside, turnover))
            values = reset(after)
    return Simulation(pd.Series(monthly, index=returns.index, name=POLICY),
                      tuple(rebalances), pd.DataFrame(starts, index=returns.index),
                      pd.DataFrame(sleeve_monthly, index=returns.index,
                                   columns=[s.name for s in sleeves]))


def quarterly_mix(returns: pd.DataFrame, mix: "tuple[tuple[str, float], ...]") -> pd.Series:
    """A basket bought at ``mix`` and reset to it at each calendar quarter's end, its
    monthly returns: the app's rule for its 60/40 (benchmarks.get_naive_60_40_series,
    #383), applied to month-end values. The basket is bought at the first month's start
    rather than at the quarter-end before it, because the history starts there."""
    values = {t: w for t, w in mix}
    out = []
    for month in returns.index:
        before = sum(values.values())
        values = {t: v * (1 + float(returns.at[month, t])) for t, v in values.items()}
        after = sum(values.values())
        out.append(after / before - 1)
        if month[5:] in ("03", "06", "09", "12"):
            values = {t: after * w for t, w in mix}
    return pd.Series(out, index=returns.index)


def wealth(returns: pd.Series) -> pd.Series:
    """Month-end wealth: 1.0 at the end of the month before the first return, then each
    month's return compounded. The growth chart draws it times GROWTH_OF."""
    return pd.concat([pd.Series([1.0], index=[_previous(returns.index[0])]),
                      (1 + returns).cumprod()])


def drawdowns(returns: pd.Series) -> pd.Series:
    """Each month-end's fall from the highest month-end wealth up to it (0 at a new
    high). Its minimum is the maximum drawdown statistics() reports."""
    w = wealth(returns)
    return w / w.cummax() - 1


def annualized(returns: pd.Series) -> float:
    """The geometric average of monthly returns as a year: (prod(1 + r)) ** (12 / n) - 1."""
    return float((1 + returns).prod() ** (12 / len(returns)) - 1)


def rolling_annual(returns: pd.Series, months: int = ROLLING_MONTHS) -> pd.Series:
    """Each month's annualized return over the ``months`` months ending with it, from the
    first month with that many behind it."""
    growth = (1 + returns).rolling(months).apply(lambda r: r.prod(), raw=True)
    return (growth ** (12 / months) - 1).dropna()


def months_between(first: str, last: str) -> int:
    """How many months ``last`` falls after ``first``: '2008-02' to '2008-04' is 2."""
    return (pd.Period(last, "M") - pd.Period(first, "M")).n


def episodes(returns: pd.Series) -> "list[Episode]":
    """Every fall of month-end wealth below an earlier high, in order. A fall runs from
    the high (the first month-end of an unbroken run at it) through the months below it;
    its trough is the lowest of them (the first, on a tie) and its recovery the first
    month back at or above the high, or None when the months end below it. On wealth
    that never touches an earlier high exactly, the deepest is statistics()'s drawdown."""
    w = wealth(returns)
    fall = (w / w.cummax() - 1).to_numpy()
    months = list(w.index)
    out, i = [], 0
    while i < len(months):
        if fall[i] >= 0:
            i += 1
            continue
        start = i
        while i < len(months) and fall[i] < 0:
            i += 1
        peak = start - 1
        while peak > 0 and w.iloc[peak - 1] == w.iloc[peak]:
            peak -= 1
        low = start + int(fall[start:i].argmin())
        out.append(Episode(float(fall[low]), months[peak], months[low],
                           months_between(months[peak], months[low]),
                           months[i] if i < len(months) else None))
    return out


def stress_episodes(returns: pd.Series) -> "dict[int, Optional[Episode]]":
    """For each year in WINDOWS the months reach, the deepest fall (episodes()) whose
    months from peak to trough, both included, include a month of that year; the
    earliest on a tie, and None when no fall's do. Nothing is typed: each year's fall is
    found in the series."""
    reached = {int(m[:4]) for m in returns.index}
    falls = episodes(returns)
    out: "dict[int, Optional[Episode]]" = {}
    for year in WINDOWS:
        if year not in reached:
            continue
        within = [e for e in falls if int(e.peak[:4]) <= year <= int(e.trough[:4])]
        out[year] = min(within, key=lambda e: e.depth) if within else None
    return out


def statistics(returns: pd.Series, bill: pd.Series) -> Statistics:
    """The page's statistics for one monthly series.

    * annual return: the geometric average, (prod(1 + r)) ** (12 / n) - 1;
    * annual volatility: the sample standard deviation of the monthly returns x sqrt(12);
    * Sharpe: (annual return - the bill's annual return over the same months) / annual
      volatility, the form Asset Evaluation uses against the 3-month bill;
    * maximum drawdown: on month-end wealth, which starts at 1.0 at the end of the month
      before the first return;
    * calendar years: each year's months compounded, the years with fewer than twelve
      named as partial;
    * ending value: GROWTH_OF times the last month-end's wealth, the growth chart's last
      point; best and worst month; the share of months above 0%; the months the deepest
      drawdown took from peak to trough and from trough to recovery;
    * the stress episodes: stress_episodes().

    Volatility and the Sharpe ratio need at least two monthly returns that differ. With
    fewer than two months the sample standard deviation is undefined, and with every
    return the same it is zero: both are None then, with the reason (#672)."""
    annual = annualized(returns)
    if len(returns) < 2:
        no_volatility: Optional[str] = FEWER_THAN_TWO_MONTHS
    elif returns.nunique() < 2:
        no_volatility = NO_VOLATILITY
    else:
        no_volatility = None
    vol = None if no_volatility else float(returns.std(ddof=1) * math.sqrt(12))
    bill_annual = annualized(bill.reindex(returns.index))
    path = wealth(returns)
    fall = drawdowns(returns)
    trough = fall.idxmin()
    peak = path.loc[:trough].idxmax()
    back = path.loc[trough:]
    back = back[back >= path[peak]]
    fell = float(fall.min()) < 0
    recovered = back.index[0] if len(back) and fell else None
    years: "dict[int, float]" = {}
    counts: "dict[int, int]" = {}
    for month, r in returns.items():
        y = int(month[:4])
        years[y] = (1 + years.get(y, 0.0)) * (1 + float(r)) - 1
        counts[y] = counts.get(y, 0) + 1
    best, worst = returns.idxmax(), returns.idxmin()
    return Statistics(
        annual_return=annual,
        annual_volatility=vol,
        sharpe=None if vol is None else (annual - bill_annual) / vol,
        drawdown=Drawdown(float(fall.min()), peak, trough, recovered),
        calendar_years=years,
        partial_years=tuple(y for y, c in counts.items() if c < 12),
        windows={y: years[y] for y in WINDOWS if y in years},
        ending_value=float(path.iloc[-1] * GROWTH_OF),
        best_month=Point(best, float(returns[best])),
        worst_month=Point(worst, float(returns[worst])),
        positive_share=float((returns > 0).mean()),
        months_to_trough=months_between(peak, trough) if fell else None,
        months_to_recovery=months_between(trough, recovered) if recovered else None,
        episodes=stress_episodes(returns),
        no_volatility=no_volatility,
    )


def compare(policy: pd.Series, other: pd.Series) -> Comparison:
    """The policy beside one comparison, month for month (Comparison says how each
    figure is computed)."""
    n = len(policy)

    def capture(months: pd.Series) -> "tuple[Optional[float], Optional[float], Optional[float]]":
        if not months.any():
            return None, None, None
        mine, theirs = annualized(policy[months]), annualized(other[months])
        return mine, theirs, (mine / theirs if theirs != 0 else None)

    up, down = other >= 0, other < 0
    up_policy, up_comparison, up_capture = capture(up)
    down_policy, down_comparison, down_capture = capture(down)
    other_moves = n >= 2 and other.nunique() > 1
    return Comparison(
        months=n,
        active_return=annualized(policy) - annualized(other),
        beta=float(policy.cov(other) / other.var()) if other_moves else None,
        correlation=(float(policy.corr(other)) if other_moves and policy.nunique() > 1
                     else None),
        tracking_error=float((policy - other).std(ddof=1) * math.sqrt(12)) if n >= 2 else None,
        up_months=int(up.sum()), up_policy=up_policy, up_comparison=up_comparison,
        up_capture=up_capture,
        down_months=int(down.sum()), down_policy=down_policy,
        down_comparison=down_comparison, down_capture=down_capture,
    )


def rolling_summary(returns: pd.DataFrame, months: int = ROLLING_MONTHS) -> RollingSummary:
    """``returns``' rolling windows of ``months`` months (rolling_annual), summed up.
    The policy is the column POLICY; every other column is a comparison. A window
    counts as below a comparison only when the policy's return is strictly below it."""
    paths = {name: rolling_annual(returns[name], months) for name in returns.columns}
    policy = paths[POLICY]
    k = len(policy)
    others = [name for name in returns.columns if name != POLICY]
    below = {name: int((policy < paths[name]).sum()) for name in others}

    def point(path: pd.Series, month: Optional[str]) -> Optional[Point]:
        return None if month is None else Point(month, float(path[month]))

    return RollingSummary(
        months=months, windows=k,
        first=policy.index[0] if k else None, last=policy.index[-1] if k else None,
        below=below,
        share_below={name: (below[name] / k if k else None) for name in others},
        best={n: point(p, p.idxmax() if k else None) for n, p in paths.items()},
        worst={n: point(p, p.idxmin() if k else None) for n, p in paths.items()},
        latest={n: point(p, p.index[-1] if k else None) for n, p in paths.items()},
    )


def sleeve_statistics(sleeve: str, returns: pd.Series, spy: pd.Series,
                      bill: pd.Series) -> SleeveStatistics:
    """One sleeve's monthly returns on their own, by statistics()' definitions, and
    their correlation with ``spy`` over the same months."""
    st = statistics(returns, bill)
    moves = len(returns) >= 2 and returns.nunique() > 1 and spy.nunique() > 1
    return SleeveStatistics(sleeve, st.annual_return, st.annual_volatility, st.drawdown,
                            float(returns.corr(spy)) if moves else None, st.no_volatility)


def tracking_from(fund: str, spec: dict) -> Optional[str]:
    """The first month ``fund``'s tracking is measured from. For a fund spliced at an
    index change, the splice month: the first it followed the quality index the sleeve
    holds, so the stand-in is measured against that strategy (decided by the owner on
    October 19, 2026, refining #667's measure). For a proxy that stands in before the
    fund's launch, None: every month both series exist."""
    entry = spec["funds"][fund]
    return _next(entry["proxy_until"]) if entry.get("reason") == "index change" else None


def tracking(fund: str, proxy: str, history: pd.DataFrame,
             since: Optional[str] = None) -> Tracking:
    """``fund`` beside ``proxy`` over every month the history holds a return for both,
    from ``since`` when given (tracking_from says which)."""
    months = history if since is None else history.loc[history.index >= since]
    both = months[[fund, proxy]].dropna()
    n = len(both)
    first, last = (both.index[0], both.index[-1]) if n else (None, None)
    if n < 2:
        return Tracking(fund, proxy, first, last, n, None, None, None)
    f, p = both[fund], both[proxy]
    moves = f.nunique() > 1 and p.nunique() > 1
    return Tracking(fund, proxy, first, last, n,
                    difference=annualized(f) - annualized(p),
                    error=float((f - p).std(ddof=1) * math.sqrt(12)),
                    correlation=float(f.corr(p)) if moves else None)


def run(history: Optional[pd.DataFrame] = None, spec: Optional[dict] = None,
        sleeves: "Optional[tuple[Sleeve, ...]]" = None) -> Backtest:
    """The backtest of the book this process serves, beside the S&P 500 and the 60/40,
    from the committed history. ``history``, ``spec`` and ``sleeves`` default to the
    committed file, series.json and the book's policy."""
    from src.benchmarks import NAIVE_60_40
    history = load_history() if history is None else history
    spec = load_spec() if spec is None else spec
    sleeves = book_sleeves() if sleeves is None else tuple(sleeves)
    months = backtest_months(history, spec)
    funds = list(dict.fromkeys(f for s in sleeves for f, _w in s.mix))
    simulation = simulate(pd.DataFrame({f: fund_returns(f, history, spec) for f in funds}),
                          sleeves)

    def column(name: str) -> pd.Series:
        s = history[name].reindex(months)
        if s.isna().any():
            raise PolicyBacktestError(f"{name} has no return for "
                                      f"{', '.join(list(s.index[s.isna()])[:6])}")
        return s

    legs = pd.DataFrame({t: column(t) for t, _w in NAIVE_60_40})
    returns = pd.DataFrame({POLICY: simulation.returns, SP500: column("SPY"),
                            SIXTY_FORTY: quarterly_mix(legs, NAIVE_60_40)})
    bill = column(spec["bill"]["series"])
    proxied = {f: spec["funds"][f]["proxy"] for f in funds if spec["funds"][f].get("proxy")}
    return Backtest(simulation, returns, bill,
                    {name: statistics(returns[name], bill) for name in returns.columns},
                    sleeves,
                    {f: tracking(f, p, history, tracking_from(f, spec))
                     for f, p in proxied.items()},
                    comparisons={name: compare(returns[POLICY], returns[name])
                                 for name in (SP500, SIXTY_FORTY)},
                    rolling=rolling_summary(returns),
                    sleeve_statistics={
                        s.name: sleeve_statistics(s.name, simulation.sleeve_returns[s.name],
                                                  returns[SP500], bill)
                        for s in sleeves})


# ── What the page says, read from run() and series.json ──────────────────────
# Every figure and date in these sentences comes from a Backtest or the spec; none is
# typed. Nothing here compares the policy with either benchmark: the page states each
# series' figures side by side.

def month_name(month: str) -> str:
    """'2006-03' as 'March 2006'."""
    return pd.Period(month, "M").strftime("%B %Y")


def held_funds(bt: Backtest) -> "list[str]":
    """Every fund the policy holds, in the policy's order."""
    return list(dict.fromkeys(f for s in bt.sleeves for f, _w in s.mix))


def stand_in(fund: str, bt: Backtest, spec: dict) -> "Optional[tuple[str, str]]":
    """(proxy, the last month it stands in) when a proxy stands in for ``fund`` in any of
    the backtest's months, else None."""
    entry = spec["funds"][fund]
    if entry.get("proxy") and entry["proxy_until"] >= bt.returns.index[0]:
        return entry["proxy"], entry["proxy_until"]
    return None


def disclosure(bt: Backtest, spec: dict, demo: bool) -> str:
    """The page's first words, in four short sentences (decided by the owner on October
    22, 2026): what these results are, over which months; whose record they are not
    (the paper portfolio's in the demo, this portfolio's in the personal book); the
    stand-ins, named in the sleeve table rather than here; and what the returns
    include, with the policy set knowing the period. The stand-in sentence names only
    the kinds the policy uses, and is left out when it uses none."""
    first, last = month_name(bt.returns.index[0]), month_name(bt.returns.index[-1])
    record = "the paper portfolio's" if demo else "this portfolio's"
    parts = [f"**Hypothetical, back-tested results:** this book's current SAA targets and "
             f"tolerance bands applied to monthly returns from {first} to {last}.",
             f"They are not {record} record, and no account earned them."]
    launch, index = False, []
    for fund in held_funds(bt):
        if stand_in(fund, bt, spec) is not None:
            if spec["funds"][fund]["reason"] == "inception":
                launch = True
            else:
                index.append(fund)
    one = len(index) == 1
    broad = (f"{'its broad market' if one else 'their broad markets'} before "
             f"{'it followed a quality index' if one else 'they followed quality indexes'}")
    named = "each named in the sleeve table below"
    if launch and index:
        parts.append("Funds are represented by named proxies before their launch, and "
                     f"{_list(index)} by {broad}, {named}.")
    elif launch:
        parts.append(f"Funds are represented by named proxies before their launch, {named}.")
    elif index:
        parts.append(f"{_list(index)} {'is' if one else 'are'} represented by {broad}, "
                     f"{named}.")
    parts.append("Returns are after fund expenses and before trading costs and taxes, and "
                 "the policy was set with knowledge of this period.")
    return " ".join(parts)


def series_used(fund: str, bt: Backtest, spec: dict) -> str:
    """'SPY March 2006 to September 2010, then VOO', or 'VTV throughout'."""
    s = stand_in(fund, bt, spec)
    if s is None:
        return f"{fund} throughout"
    proxy, until = s
    return f"{proxy} {month_name(bt.returns.index[0])} to {month_name(until)}, then {fund}"


def _cite(entry: dict) -> str:
    """'([host](url), the source's own date)'."""
    return f"([{urlparse(entry['source']).netloc}]({entry['source']}), {entry['source_date']})"


def source_lines(bt: Backtest, spec: dict) -> "list[str]":
    """One line per fund the policy holds, in its order: the issuer's inception date as
    printed, with the source and the source's date; the proxy standing in, the same way;
    and for an index change, the indexes the filing names. Then each benchmark leg no
    fund supplies, and the bill."""
    from src.benchmarks import naive_60_40_label
    out = []
    for fund in held_funds(bt):
        entry = spec["funds"][fund]
        line = f"**{fund}**: inception {entry['inception']} {_cite(entry)}."
        s = stand_in(fund, bt, spec)
        if s is None:
            line += " Its own record is used throughout."
        else:
            proxy, until = s
            p = spec["proxies"][proxy]
            line += (f" **{proxy}** stands in through {month_name(until)}: inception "
                     f"{p['inception']} {_cite(p)}.")
            if entry.get("change"):
                line += f" The fund followed {entry['change']['cited']} {_cite(entry['change'])}."
        out.append(line)
    for leg, entry in spec.get("benchmarks", {}).items():
        out.append(f"**{leg}**, in the {naive_60_40_label()}: inception {entry['inception']} "
                   f"{_cite(entry)}.")
    bill = spec["bill"]
    out.append(f"**{bill['series']}**, the bill the Sharpe ratio is measured against: "
               f"{bill['name']} ([{urlparse(bill['source']).netloc}]({bill['source']})).")
    return out


def year_span(year: int, bt: Backtest) -> str:
    """The months the backtest holds in ``year``: 'March to December', or 'March'."""
    months = [pd.Period(m, "M").strftime("%B") for m in bt.returns.index
              if m.startswith(f"{year}-")]
    return months[0] if len(months) == 1 else f"{months[0]} to {months[-1]}"


def year_label(year: int, bt: Backtest) -> str:
    """'2008', or for a year the backtest holds fewer than twelve months of,
    '2006, partial (March to December)'."""
    if year not in bt.statistics[POLICY].partial_years:
        return str(year)
    return f"{year}, partial ({year_span(year, bt)})"


def rebalance_sentence(bt: Backtest) -> str:
    """How many month-end checks found a sleeve outside its band, zero included, after
    the backtest's months."""
    window = _cap(_window(bt))
    k = len(bt.simulation.rebalances)
    if k == 0:
        return (f"{window}, no month-end check found a sleeve outside its band, so the "
                "policy never rebalanced.")
    checks = "one month-end check" if k == 1 else f"{k} month-end checks"
    return (f"{window}, {checks} found a sleeve outside its band; at "
            f"{'it' if k == 1 else 'each'}, every sleeve went back to its target.")


# ── The page's paragraphs ─────────────────────────────────────────────────────
# Each returns text the page prints, read from a Backtest alone (decided by the owner on
# October 22, 2026). Each names its window once, at its start; later sentences say "over
# the same months" or nothing. Prose prints returns to one decimal, and ratios and
# shares of windows as whole percents; the tables keep two decimals. Every comparison of
# the policy with the S&P 500 or the 60/40 states both figures, and no sentence calls
# either better or worse. Each covers what a short or flat history leaves missing: no
# volatility, no rolling window, no full year, no rebalance, no fall.

def pct(x: float, signed: bool = False, places: int = 2) -> str:
    """0.0846 as '8.46%', or signed '+8.46%', as the page's tables print it; the
    paragraphs ask for one place (a return) or none (a ratio or a share of windows)."""
    return f"{x * 100:{'+' if signed else ''}.{places}f}%"


def _ret(x: float, signed: bool = False) -> str:
    """A return as the paragraphs print it: '8.5%'."""
    return pct(x, signed, places=1)


def _whole(x: float) -> str:
    """A ratio or a share of windows as the paragraphs print it: '84%'."""
    return pct(x, places=0)


def money(x: float) -> str:
    """53170.83 as '$53,171'."""
    return f"${x:,.0f}"


def count(n: int, noun: str) -> str:
    """'1 month', '247 months'."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def rolling_label(months: int = ROLLING_MONTHS) -> str:
    """'5-year' for 60 months, '18-month' for a span that is not whole years."""
    return f"{months // 12}-year" if months % 12 == 0 else f"{months}-month"


def series_name(name: str) -> str:
    """How a sentence names a series: 'the policy', 'the S&P 500 (SPY)', 'the 60/40'."""
    return "the policy" if name == POLICY else f"the {name}"


def _window(bt: Backtest) -> str:
    """'from March 2006 to September 2026'."""
    return f"from {month_name(bt.returns.index[0])} to {month_name(bt.returns.index[-1])}"


def _same(name: str) -> str:
    """Why a series has no dispersion, for a figure that needs one."""
    return f"every monthly return of {series_name(name)} is the same"


def _cap(text: str) -> str:
    """'from March 2006' as 'From March 2006'."""
    return text[0].upper() + text[1:]


def _capture(side: str, months: int, ratio: Optional[float], them: str, first: bool) -> str:
    """'an up-capture of 84% over the 166 months the S&P 500 (SPY) returned at or above
    0%', or why there is none."""
    when = "at or above 0%" if side == "up" else "below 0%"
    if months == 0:
        return f"no {side}-capture, since {them} returned {when} in no month"
    if ratio is None:
        return (f"no {side}-capture, since {them} returned 0% a year over the "
                f"{count(months, 'month')} it returned {when}")
    return (f"{'an' if side == 'up' else 'a'} {side}-capture of {_whole(ratio)} over the "
            f"{count(months, 'month')} {them if first else 'it'} returned {when}")


def summary_read(bt: Backtest) -> str:
    """Each series' annualized return, volatility and Sharpe ratio over the backtest's
    months; then, against each comparison, the policy's active return with both
    annualized returns, its beta, correlation and tracking error, and its up- and
    down-capture with the months each covers."""
    n = len(bt.returns)

    def series(name: str, verb: str) -> str:
        st = bt.statistics[name]
        out = f"{series_name(name)}{verb} {_ret(st.annual_return)} a year"
        if st.no_volatility:
            return out + f", with no volatility or Sharpe ratio ({st.no_volatility})"
        return (out + f", with {_ret(st.annual_volatility)} volatility and a Sharpe ratio "
                f"of {st.sharpe:.2f}")

    names = list(bt.returns.columns)
    rest = [series(x, "") for x in names[1:]]
    out = [f"{_cap(_window(bt))} ({count(n, 'month')}), {series(names[0], ' returned')}"
           + "".join(f"; {'and ' if i == len(rest) - 1 else ''}{x}"
                     for i, x in enumerate(rest)) + "."]
    policy = bt.statistics[POLICY].annual_return
    for name, c in bt.comparisons.items():
        them = series_name(name)
        if n < 2:
            why = {"beta": FEWER_THAN_TWO_MONTHS, "correlation": FEWER_THAN_TWO_MONTHS}
        elif bt.returns[name].nunique() < 2:
            why = {"beta": _same(name), "correlation": _same(name)}
        else:
            why = {"beta": "", "correlation": _same(POLICY)}
        joint = [f"beta {c.beta:.2f}" if c.beta is not None else f"no beta ({why['beta']})",
                 f"correlation {c.correlation:.2f}" if c.correlation is not None
                 else f"no correlation ({why['correlation']})",
                 f"tracking error {_ret(c.tracking_error)}" if c.tracking_error is not None
                 else f"no tracking error ({FEWER_THAN_TWO_MONTHS})"]
        out.append(f"Against {them}, the policy's active return was "
                   f"{_ret(c.active_return, signed=True)} a year ({_ret(policy)} against "
                   f"{_ret(bt.statistics[name].annual_return)}), with {_list(joint)}.")
        out.append(f"The policy had {_capture('up', c.up_months, c.up_capture, them, True)}, "
                   f"and {_capture('down', c.down_months, c.down_capture, them, False)}.")
    return " ".join(out)


def growth_read(bt: Backtest) -> str:
    """What GROWTH_OF became in each series, and the months the growth chart's bands
    shade: the policy's stress episodes, peak to trough."""
    start = month_name(wealth(bt.returns[POLICY]).index[0])
    values = [f"{money(bt.statistics[x].ending_value)} in {series_name(x)}"
              for x in bt.returns.columns]
    out = [f"From the end of {start} to the end of {month_name(bt.returns.index[-1])}, "
           f"{money(GROWTH_OF)} became {_list(values)}."]
    years = bt.statistics[POLICY].episodes
    shaded = [f"{y}, {month_name(e.peak)} to {month_name(e.trough)}"
              for y, e in years.items() if e is not None]
    if shaded:
        out.append("Each shaded band runs from the policy's month-end high to its low in "
                   f"its deepest fall reaching into the year it names: {'; '.join(shaded)}.")
    unshaded = [str(y) for y, e in years.items() if e is None]
    if unshaded:
        out.append(f"No fall of the policy reaches into {_list(unshaded)}, so no band is "
                   f"shaded for {'it' if len(unshaded) == 1 else 'them'}.")
    return " ".join(out)


def drawdown_read(bt: Backtest) -> str:
    """For each year in WINDOWS the months reach, the three series side by side in their
    deepest fall reaching into it: each one's drawdown, signed as the page's tables print
    it, and how many months after its low it was back at its high, or that it has not
    been. The deepest drawdown is the table's above, and is not repeated. No figure
    follows the verb "fell" (#536, #604: tests/test_a_fall_prints_as_a_magnitude.py)."""
    years = bt.statistics[POLICY].episodes
    window = _cap(_window(bt))
    if not years:
        return (f"{window}, the backtest's months reach none of "
                f"{_list([str(y) for y in WINDOWS])}.")
    out = []
    for i, y in enumerate(years):
        parts = []
        for name in bt.returns.columns:
            e = bt.statistics[name].episodes[y]
            who = series_name(name)
            if e is None:
                parts.append(f"{who} had none")
            elif e.recovered is None:
                parts.append(f"{who} had a drawdown of {_ret(e.depth)} and had not recovered")
            else:
                back = count(months_between(e.trough, e.recovered), "month")
                parts.append(f"{who} had a drawdown of {_ret(e.depth)} and recovered {back} "
                             "after its low")
        lead = f"{window}, in" if i == 0 else "In"
        out.append(f"{lead} the deepest fall of each series reaching into {y}, "
                   f"{_list(parts)}.")
    return " ".join(out)


def rolling_read(bt: Backtest) -> str:
    """The rolling windows summed up: in how many the policy's annualized return was
    below each comparison's and in how many it was at or above it, the policy's best and
    worst window, and the latest window's return for each series. With no window, why."""
    r = bt.rolling
    label = rolling_label(r.months)
    if r.windows == 0:
        return (f"{_cap(_window(bt))}, the history holds {count(len(bt.returns), 'month')}, "
                f"fewer than the {r.months} a rolling {label} return needs, so none is "
                "shown.")
    span = (f"ending {month_name(r.first)} to {month_name(r.last)}" if r.windows > 1
            else f"ending {month_name(r.first)}")
    out = []
    for i, (name, below) in enumerate(r.below.items()):
        figures = (f"below that of {series_name(name)} in {below} "
                   f"({_whole(r.share_below[name])}) and at or above it in "
                   f"{r.windows - below}")
        out.append(f"Of the {count(r.windows, f'rolling {label} window')} {span}, the "
                   f"policy's annualized return was {figures}." if i == 0 else
                   f"It was {figures}.")
    best, worst = r.best[POLICY], r.worst[POLICY]
    out.append(f"Of those windows, the policy's best ended in {month_name(best.month)}, at "
               f"{_ret(best.value)} a year, and its worst in {month_name(worst.month)}, at "
               f"{_ret(worst.value)} a year.")
    latest = [f"{_ret(p.value)} for {series_name(name)}" for name, p in r.latest.items()]
    out.append(f"In the latest window, the annualized returns were {_list(latest)}.")
    return " ".join(out)


def calendar_read(bt: Backtest) -> str:
    """Over the full calendar years: in how many the policy's return was below each
    comparison's and in how many it was at or above it, and the policy's best and worst
    full year; with one full year, the three returns. Partial years are named and not
    counted."""
    st = bt.statistics[POLICY]
    full = [y for y in st.calendar_years if y not in st.partial_years]
    out = []
    if not full:
        out.append(f"{_cap(_window(bt))}, the backtest holds no full calendar year, so "
                   "none is counted.")
    elif len(full) == 1:
        y = full[0]
        returns = [f"{_ret(bt.statistics[x].calendar_years[y], signed=True)} for "
                   f"{series_name(x)}" for x in bt.returns.columns]
        out.append(f"In {y}, the only full calendar year, the returns were {_list(returns)}.")
    else:
        for i, name in enumerate(bt.comparisons):
            theirs = bt.statistics[name].calendar_years
            below = sum(st.calendar_years[y] < theirs[y] for y in full)
            figures = (f"below that of {series_name(name)} in {below} and at or above it "
                       f"in {len(full) - below}")
            out.append(f"Of the {len(full)} full calendar years from {full[0]} to "
                       f"{full[-1]}, the policy's return was {figures}." if i == 0 else
                       f"It was {figures}.")
        best = max(full, key=lambda y: st.calendar_years[y])
        worst = min(full, key=lambda y: st.calendar_years[y])
        out.append(f"The policy's best of those years was {best}, at "
                   f"{_ret(st.calendar_years[best], signed=True)}, and its worst {worst}, at "
                   f"{_ret(st.calendar_years[worst], signed=True)}.")
    partial = [f"{y} ({year_span(y, bt)})" for y in st.partial_years]
    if partial:
        out.append(f"{_list(partial)} "
                   f"{'is a partial year' if len(partial) == 1 else 'are partial years'}, "
                   "not counted here.")
    return " ".join(out)


def rebalance_read(bt: Backtest) -> str:
    """How many checks rebalanced (rebalance_sentence), the average months between
    them, the sleeves outside their bands most often, and the largest turnover."""
    done = bt.simulation.rebalances
    k = len(done)
    out = [rebalance_sentence(bt)]
    if k == 1:
        r = done[0]
        one = len(r.triggers) == 1
        out.append(f"It came in {month_name(r.month)}, when {_list(list(r.triggers))} "
                   f"{'was' if one else 'were'} outside {'its band' if one else 'their bands'}"
                   f", with a turnover of {_ret(r.turnover)} of the portfolio.")
    elif k > 1:
        apart = months_between(done[0].month, done[-1].month) / (k - 1)
        out.append(f"They came on average {apart:.1f} months apart, from the first in "
                   f"{month_name(done[0].month)} to the last in {month_name(done[-1].month)}.")
        times = {s.name: sum(s.name in r.triggers for r in done) for s in bt.sleeves}
        most = max(times.values())
        often = [name for name, t in times.items() if t == most]
        if len(often) == 1:
            out.append(f"{often[0]} was outside its band at {most} of the {k} checks, the "
                       "most of any sleeve.")
        elif most == 1:
            out.append(f"{_list(often)} were each outside their bands at one of the {k} "
                       "checks, and no sleeve at two.")
        else:
            out.append(f"{_list(often)} were each outside their bands at {most} of the {k} "
                       "checks, the most of any sleeve.")
        top = max(done, key=lambda r: r.turnover)
        out.append(f"The largest turnover of the {k}, half the sum of each sleeve's distance "
                   f"from its target, was {_ret(top.turnover)} of the portfolio, at the "
                   f"{month_name(top.month)} check.")
    return " ".join(out)
