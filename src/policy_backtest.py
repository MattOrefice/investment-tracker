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
    and the current); ``windows`` the years in WINDOWS. With fewer than two monthly
    returns, or none that differ, there is no volatility: ``annual_volatility`` and
    ``sharpe`` are None and ``no_volatility`` says why (decided by the owner on October
    19, 2026, #672)."""

    annual_return: float
    annual_volatility: Optional[float]
    sharpe: Optional[float]
    drawdown: Drawdown
    calendar_years: "dict[int, float]"
    partial_years: "tuple[int, ...]"
    windows: "dict[int, float]"
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
    against its proxy (funds with no proxy are absent)."""

    simulation: Simulation
    returns: pd.DataFrame
    bill: pd.Series
    statistics: "dict[str, Statistics]"
    sleeves: "tuple[Sleeve, ...]"
    tracking: "dict[str, Tracking]" = field(default_factory=dict)


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


def statistics(returns: pd.Series, bill: pd.Series) -> Statistics:
    """The page's statistics for one monthly series.

    * annual return: the geometric average, (prod(1 + r)) ** (12 / n) - 1;
    * annual volatility: the sample standard deviation of the monthly returns x sqrt(12);
    * Sharpe: (annual return - the bill's annual return over the same months) / annual
      volatility, the form Asset Evaluation uses against the 3-month bill;
    * maximum drawdown: on month-end wealth, which starts at 1.0 at the end of the month
      before the first return;
    * calendar years: each year's months compounded, the years with fewer than twelve
      named as partial.

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
        sharpe=None if vol is None else (annual - bill_annual) / vol,
        drawdown=Drawdown(float(fall.min()), peak, trough, recovered),
        calendar_years=years,
        partial_years=tuple(y for y, c in counts.items() if c < 12),
        windows={y: years[y] for y in WINDOWS if y in years},
        no_volatility=no_volatility,
    )


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
                     for f, p in proxied.items()})


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


def _stood_in(pairs: "list[tuple[str, str, str]]") -> str:
    return _list([f"{fund} by {proxy} through {month_name(until)}"
                  for fund, proxy, until in pairs])


def disclosure(bt: Backtest, spec: dict, demo: bool) -> str:
    """The page's first words: what these results are and are not. ``demo`` names the
    record they are not: the paper portfolio's in the demo, this portfolio's in the
    personal book."""
    first, last = month_name(bt.returns.index[0]), month_name(bt.returns.index[-1])
    record = "the paper portfolio's" if demo else "this portfolio's"
    parts = ["**Hypothetical, back-tested results.**",
             f"They apply this book's current SAA targets and tolerance bands to monthly "
             f"returns from {first} through {last}.",
             f"They are not {record} record, and no account earned them."]
    launch, index = [], []
    for fund in held_funds(bt):
        s = stand_in(fund, bt, spec)
        if s is not None:
            (launch if spec["funds"][fund]["reason"] == "inception" else index).append(
                (fund, *s))
    if launch:
        parts.append(f"Funds are represented by named proxies before their launch: "
                     f"{_stood_in(launch)}.")
    if len(index) == 1:
        parts.append(f"{index[0][0]} is represented by its broad market before it followed "
                     f"a quality index: {_stood_in(index)}.")
    elif index:
        parts.append(f"{_list([f for f, _p, _u in index])} are represented by their broad "
                     f"markets before they followed quality indexes: {_stood_in(index)}.")
    parts += ["Returns are after fund expenses and before trading costs and taxes.",
              "The policy was set with knowledge of this period."]
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
    """How many month-end checks found a sleeve outside its band, zero included."""
    first, last = month_name(bt.returns.index[0]), month_name(bt.returns.index[-1])
    k = len(bt.simulation.rebalances)
    if k == 0:
        return (f"No month-end check from {first} to {last} found a sleeve outside its "
                "band, so the policy never rebalanced.")
    checks = "One month-end check" if k == 1 else f"{k} month-end checks"
    return (f"{checks} from {first} to {last} found a sleeve outside its band; at "
            f"{'it' if k == 1 else 'each'}, every sleeve went back to its target.")
