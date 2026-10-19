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
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "backtest" / "monthly_returns.csv"
SPEC = ROOT / "data" / "backtest" / "series.json"

REAL_ASSETS = "Real Assets"
# The sleeve's two funds and the shares it is reset to, as the owner decided: its
# benchmark's 60% REIT and 40% commodity legs, held through VNQ and PDBC.
REAL_ASSETS_MIX = (("VNQ", 0.6), ("PDBC", 0.4))
# The calendar years the page shows on their own.
WINDOWS = (2008, 2020, 2022)

POLICY = "Policy"
SP500 = "S&P 500 (SPY)"
SIXTY_FORTY = "60/40"


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
    """A month-end check that found a sleeve outside its band: the month, and every
    sleeve outside, in the policy's order."""

    month: str
    triggers: "tuple[str, ...]"


@dataclass(frozen=True)
class Simulation:
    """The policy's monthly returns, its rebalances, and each holding's share of the
    portfolio at the start of every month (column "sleeve/fund")."""

    returns: pd.Series
    rebalances: "tuple[Rebalance, ...]"
    start_weights: pd.DataFrame


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
class Statistics:
    """What the page shows for one series. ``calendar_years`` holds every year the
    months reach, and ``partial_years`` those with fewer than twelve of them (the first
    and the current); ``windows`` the years in WINDOWS."""

    annual_return: float
    annual_volatility: float
    sharpe: float
    drawdown: Drawdown
    calendar_years: "dict[int, float]"
    partial_years: "tuple[int, ...]"
    windows: "dict[int, float]"


@dataclass(frozen=True)
class Backtest:
    """The whole result: the policy's simulation, the three monthly return series side
    by side (POLICY, SP500, SIXTY_FORTY), the bill's monthly returns, each series'
    statistics, and the sleeves the policy was run with."""

    simulation: Simulation
    returns: pd.DataFrame
    bill: pd.Series
    statistics: "dict[str, Statistics]"
    sleeves: "tuple[Sleeve, ...]"


def load_history(path: Path = HISTORY) -> pd.DataFrame:
    """The committed monthly history, indexed by month ('YYYY-MM')."""
    return pd.read_csv(path, index_col="month", dtype={"month": str})


def load_spec(path: Path = SPEC) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _previous(month: str) -> str:
    return (pd.Period(month, "M") - 1).strftime("%Y-%m")


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
    sleeve's target times its share of the sleeve."""
    from src.rebalance import compute_drift
    targets = {s.name: s.target for s in sleeves}
    bands = {s.name: s.band for s in sleeves}

    def reset(total: float) -> "dict[tuple[str, str], float]":
        return {(s.name, f): total * s.target * w for s in sleeves for f, w in s.mix}

    values = reset(1.0)
    monthly, starts, rebalances = [], [], []
    for month in returns.index:
        before = sum(values.values())
        starts.append({f"{s}/{f}": v / before for (s, f), v in values.items()})
        values = {(s, f): v * (1 + float(returns.at[month, f])) for (s, f), v in values.items()}
        after = sum(values.values())
        monthly.append(after / before - 1)
        weights = {s.name: sum(values[(s.name, f)] for f, _w in s.mix) / after for s in sleeves}
        drift = compute_drift(weights, targets, bands)
        outside = tuple(s.name for s in sleeves if not bool(drift.at[s.name, "In Band"]))
        if outside:
            rebalances.append(Rebalance(month, outside))
            values = reset(after)
    return Simulation(pd.Series(monthly, index=returns.index, name=POLICY),
                      tuple(rebalances), pd.DataFrame(starts, index=returns.index))


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


def statistics(returns: pd.Series, bill: pd.Series) -> Statistics:
    """The page's statistics for one monthly series.

    * annual return: the geometric average, (prod(1 + r)) ** (12 / n) - 1;
    * annual volatility: the sample standard deviation of the monthly returns x sqrt(12);
    * Sharpe: (annual return - the bill's annual return over the same months) / annual
      volatility, the form Asset Evaluation uses against the 3-month bill;
    * maximum drawdown: on month-end wealth, which starts at 1.0 at the end of the month
      before the first return;
    * calendar years: each year's months compounded, the years with fewer than twelve
      named as partial."""
    n = len(returns)
    annual = float((1 + returns).prod() ** (12 / n) - 1)
    vol = float(returns.std(ddof=1) * math.sqrt(12))
    bill_annual = float((1 + bill.reindex(returns.index)).prod() ** (12 / n) - 1)
    wealth = pd.concat([pd.Series([1.0], index=[_previous(returns.index[0])]),
                        (1 + returns).cumprod()])
    fall = wealth / wealth.cummax() - 1
    trough = fall.idxmin()
    peak = wealth.loc[:trough].idxmax()
    back = wealth.loc[trough:]
    back = back[back >= wealth[peak]]
    recovered = back.index[0] if len(back) and float(fall.min()) < 0 else None
    years: "dict[int, float]" = {}
    counts: "dict[int, int]" = {}
    for month, r in returns.items():
        y = int(month[:4])
        years[y] = (1 + years.get(y, 0.0)) * (1 + float(r)) - 1
        counts[y] = counts.get(y, 0) + 1
    return Statistics(
        annual_return=annual,
        annual_volatility=vol,
        sharpe=(annual - bill_annual) / vol,
        drawdown=Drawdown(float(fall.min()), peak, trough, recovered),
        calendar_years=years,
        partial_years=tuple(y for y, c in counts.items() if c < 12),
        windows={y: years[y] for y in WINDOWS if y in years},
    )


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
    return Backtest(simulation, returns, bill,
                    {name: statistics(returns[name], bill) for name in returns.columns},
                    sleeves)
