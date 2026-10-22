"""What the policy backtest's rebuilt page reads from the engine (decided by the owner on
October 22, 2026), each figure against a hand computation:

  * per series: the ending value of GROWTH_OF, the best and worst month, the share of
    positive months, and the months the deepest drawdown took to its trough and back;
  * the stress episodes: for each year in WINDOWS, the deepest fall whose months from
    peak to trough include a month of it, found in the series, nothing typed;
  * the policy against each comparison: active return, beta, correlation, tracking
    error, and up- and down-capture as Morningstar defines them (Custom Calculation
    Data Points, October 2016: up markets "with returns at or above 0%", down markets
    those "less than 0");
  * the rolling windows summed up, each rebalance's turnover, each sleeve on its own;
  * the six paragraphs the page prints: every figure in them is the result's, every
    comparison states both figures and the window, and each covers what a short or flat
    history leaves missing.

Nothing here reads a figure of the personal book; the demo run is read for its shape.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd
import pytest

from src import policy_backtest as pb


def _months(first: str, n: int) -> "list[str]":
    return [p.strftime("%Y-%m") for p in pd.period_range(first, periods=n, freq="M")]


R = [0.02, 0.01, -0.03, 0.04, -0.10, -0.05, 0.03, 0.06, 0.08, -0.02, 0.01, 0.05, 0.02,
     -0.04, 0.03]
MONTHS = _months("2007-11", 15)                  # 2007-11 to 2009-01


# ── Per series ────────────────────────────────────────────────────────────────

def test_the_new_per_series_figures_against_a_hand_computation():
    """The fifteen months of test_policy_backtest's hand case.
    * ending value: 10,000 x the product of (1 + r);
    * best month July 2008, +8%; worst March 2008, -10%;
    * positive months: 10 of 15;
    * the deepest drawdown peaks in February 2008, troughs in April (2 months) and is
      back above the peak in July (3 months later)."""
    st = pb.statistics(pd.Series(R, index=MONTHS), pd.Series(0.002, index=MONTHS))
    growth = 1.0
    for r in R:
        growth *= 1 + r
    assert st.ending_value == pytest.approx(10_000 * growth, rel=1e-12)
    assert st.best_month == pb.Point("2008-07", 0.08)
    assert st.worst_month == pb.Point("2008-03", -0.10)
    assert st.positive_share == pytest.approx(10 / 15)
    assert (st.months_to_trough, st.months_to_recovery) == (2, 3)


def test_the_ending_value_is_the_growth_charts_last_point():
    r = pd.Series(R, index=MONTHS)
    assert pb.statistics(r, r * 0).ending_value == float((pb.wealth(r) * pb.GROWTH_OF).iloc[-1])


def test_a_month_of_exactly_zero_is_not_positive():
    r = pd.Series([0.01, 0.0, -0.01, 0.02], index=_months("2006-03", 4))
    assert pb.statistics(r, r * 0).positive_share == 0.5


def test_with_no_fall_or_no_recovery_the_months_are_none():
    up = pd.Series([0.01, 0.02], index=_months("2006-03", 2))
    st = pb.statistics(up, up * 0)
    assert (st.months_to_trough, st.months_to_recovery) == (None, None)
    down = pd.Series([0.05, -0.10, 0.02], index=_months("2020-01", 3))
    st = pb.statistics(down, down * 0)
    assert (st.months_to_trough, st.months_to_recovery) == (1, None)


# ── Falls and the stress episodes ─────────────────────────────────────────────

def test_every_fall_against_a_hand_computation():
    """The hand case's wealth: 1.02, 1.0302 (high, December 2007), 0.999294 in January
    (-3%), above the high again in February at 1.0392658 (high); -10% and -5% to April,
    -14.5%, back above it in July at 1.0477581 (high); -2% in August, back in October;
    a high in November, -4% in December, and January 2009 still below it."""
    falls = pb.episodes(pd.Series(R, index=MONTHS))
    assert falls == [
        pb.Episode(pytest.approx(-0.03), "2007-12", "2008-01", 1, "2008-02"),
        pb.Episode(pytest.approx(-0.145), "2008-02", "2008-04", 2, "2008-07"),
        pb.Episode(pytest.approx(-0.02), "2008-07", "2008-08", 1, "2008-10"),
        pb.Episode(pytest.approx(-0.04), "2008-11", "2008-12", 1, None),
    ]


# 2019-12 to 2022-12, 0% except: December 2019 +10%; February and March 2020 -20%, -10%;
# April 2020 +50%; June 2021 -5%, July +10%; January and June 2022 -10% each.
STRESS = {"2019-12": 0.10, "2020-02": -0.20, "2020-03": -0.10, "2020-04": 0.50,
          "2021-06": -0.05, "2021-07": 0.10, "2022-01": -0.10, "2022-06": -0.10}


def _stress() -> pd.Series:
    months = _months("2019-12", 37)
    return pd.Series([STRESS.get(m, 0.0) for m in months], index=months)


def test_the_stress_episodes_against_a_hand_computation():
    """Three falls:
    * A: the high is 1.1, reached in December 2019 and held at 0% through January 2020,
      so the fall's peak is December, the first month at it. 1.1 x 0.8 x 0.9 = 0.792 in
      March, -28%, 3 months after the peak; back above it in April at 1.188.
    * B: the high, 1.188, holds from April 2020 to May 2021; -5% in June 2021, 14 months
      after April 2020; back in July 2021. Its months from peak to trough include 2020.
    * C: the high, 1.24146, holds from July 2021; -10% in January and June 2022 to 0.81 of
      it, -19%, in June 2022, 11 months after the peak, and not back by December 2022.
    2020 holds A and B: A is the deeper. 2022 holds C alone. 2008 is not reached."""
    r = _stress()
    falls = pb.episodes(r)
    a = pb.Episode(pytest.approx(0.792 / 1.1 - 1), "2019-12", "2020-03", 3, "2020-04")
    b = pb.Episode(pytest.approx(-0.05), "2020-04", "2021-06", 14, "2021-07")
    c = pb.Episode(pytest.approx(0.81 - 1), "2021-07", "2022-06", 11, None)
    assert falls == [a, b, c]
    assert pb.stress_episodes(r) == {2020: a, 2022: c}
    assert pb.statistics(r, r * 0).episodes == {2020: a, 2022: c}


def test_a_year_no_fall_reaches_into_has_no_episode():
    months = _months("2021-11", 6)                  # 2021-11 to 2022-04, every month up
    r = pd.Series(0.01, index=months)
    assert pb.stress_episodes(r) == {2022: None}


def test_the_deepest_fall_is_the_maximum_drawdown_on_the_committed_history(use_demo_db):
    bt = pb.run()
    for name in bt.returns.columns:
        deepest = min(pb.episodes(bt.returns[name]), key=lambda e: e.depth)
        dd = bt.statistics[name].drawdown
        assert (deepest.depth, deepest.peak, deepest.trough, deepest.recovered) == (
            dd.depth, dd.peak, dd.trough, dd.recovered), name
        assert deepest.months_to_trough == bt.statistics[name].months_to_trough


# ── Against each comparison ───────────────────────────────────────────────────

P = [0.02, -0.01, 0.03, -0.02]
C = [0.01, -0.02, 0.0, -0.01]


def test_the_comparison_against_a_hand_computation():
    """Policy 2%, -1%, 3%, -2%; comparison 1%, -2%, 0%, -1%.
    * active return: (1.02 x 0.99 x 1.03 x 0.98) ** 3 - (1.01 x 0.98 x 1.0 x 0.99) ** 3;
    * means 0.5% and -0.5%; the products of the deviations sum to 0.0007 and the
      comparison's squares to 0.0005, so beta is 1.4; the policy's squares sum to
      0.0017, so the correlation is 0.0007 / sqrt(0.0017 x 0.0005);
    * the differences 1%, 1%, 3%, -1% have mean 1% and squares about it 0.0008: tracking
      error sqrt(0.0008 / 3) x sqrt(12) = sqrt(0.0032);
    * up months, the comparison at or above 0%: the first and the third, its 0% among
      them, as Morningstar counts it. Policy (1.02 x 1.03) ** 6 - 1 against
      (1.01 x 1.0) ** 6 - 1;
    * down months, the second and fourth: 0.99 x 0.98 and 0.98 x 0.99, the same
      product, so the down-capture is exactly 1."""
    months = _months("2006-03", 4)
    c = pb.compare(pd.Series(P, index=months), pd.Series(C, index=months))
    assert c.months == 4
    assert c.active_return == pytest.approx((1.02 * 0.99 * 1.03 * 0.98) ** 3
                                            - (1.01 * 0.98 * 1.0 * 0.99) ** 3)
    assert c.beta == pytest.approx(1.4)
    assert c.correlation == pytest.approx(0.0007 / math.sqrt(0.0017 * 0.0005))
    assert c.tracking_error == pytest.approx(math.sqrt(0.0032))
    assert (c.up_months, c.down_months) == (2, 2)
    assert c.up_policy == pytest.approx((1.02 * 1.03) ** 6 - 1)
    assert c.up_comparison == pytest.approx(1.01 ** 6 - 1)
    assert c.up_capture == pytest.approx(((1.02 * 1.03) ** 6 - 1) / (1.01 ** 6 - 1))
    assert c.down_policy == c.down_comparison == pytest.approx(0.9702 ** 6 - 1)
    assert c.down_capture == pytest.approx(1.0)


def test_a_comparison_figure_the_months_cannot_give_is_none():
    one = pb.compare(pd.Series([0.01], index=["2006-03"]), pd.Series([0.02], index=["2006-03"]))
    assert (one.beta, one.correlation, one.tracking_error) == (None, None, None)
    assert (one.up_months, one.down_months, one.down_capture) == (1, 0, None)
    months = _months("2006-03", 3)
    flat = pb.compare(pd.Series([0.01, 0.02, -0.01], index=months), pd.Series(0.0, index=months))
    assert (flat.beta, flat.correlation) == (None, None)
    assert flat.tracking_error is not None
    # Every up month at exactly 0%: the comparison's own return over them is 0%.
    assert (flat.up_months, flat.up_comparison, flat.up_capture) == (3, 0.0, None)
    still = pb.compare(pd.Series(0.01, index=months), pd.Series([0.01, 0.02, -0.01], index=months))
    assert still.correlation is None and still.beta == pytest.approx(0.0)


# ── The rolling windows ───────────────────────────────────────────────────────

def test_the_rolling_summary_against_a_hand_computation():
    """Two-month windows over four months. The policy: (1.1 x 0.9) ** 6 - 1,
    (0.9 x 1.05) ** 6 - 1, 1.05 ** 6 - 1, ending April, May and June. The S&P 500 is 1% a
    month: 1.01 ** 12 - 1 in every window, so the policy is below it in the first two.
    The 60/40 is -5% then 0%: 0.95 ** 6 - 1, then 0 and 0; the policy is below it in the
    second window only."""
    months = _months("2006-03", 4)
    returns = pd.DataFrame({pb.POLICY: [0.10, -0.10, 0.05, 0.0],
                            pb.SP500: [0.01] * 4,
                            pb.SIXTY_FORTY: [-0.05, 0.0, 0.0, 0.0]}, index=months)
    r = pb.rolling_summary(returns, months=2)
    policy = [0.99 ** 6 - 1, 0.945 ** 6 - 1, 1.05 ** 6 - 1]
    assert (r.months, r.windows, r.first, r.last) == (2, 3, "2006-04", "2006-06")
    assert r.below == {pb.SP500: 2, pb.SIXTY_FORTY: 1}
    assert r.share_below == pytest.approx({pb.SP500: 2 / 3, pb.SIXTY_FORTY: 1 / 3})
    assert r.best[pb.POLICY] == pb.Point("2006-06", pytest.approx(policy[2]))
    assert r.worst[pb.POLICY] == pb.Point("2006-05", pytest.approx(policy[1]))
    assert r.latest[pb.POLICY] == pb.Point("2006-06", pytest.approx(policy[2]))
    assert r.latest[pb.SP500] == pb.Point("2006-06", pytest.approx(1.01 ** 12 - 1))
    assert r.worst[pb.SIXTY_FORTY] == pb.Point("2006-04", pytest.approx(0.95 ** 6 - 1))


def test_a_window_level_with_the_comparison_is_not_below_it():
    months = _months("2006-03", 3)
    same = pd.DataFrame({pb.POLICY: [0.01, 0.02, 0.03], pb.SP500: [0.01, 0.02, 0.03]},
                        index=months)
    assert pb.rolling_summary(same, months=2).below == {pb.SP500: 0}


def test_with_no_window_every_share_and_point_is_none():
    months = _months("2006-03", 30)
    returns = pd.DataFrame({n: [0.01] * 30 for n in (pb.POLICY, pb.SP500, pb.SIXTY_FORTY)},
                           index=months)
    r = pb.rolling_summary(returns)
    assert (r.windows, r.first, r.last) == (0, None, None)
    assert r.below == {pb.SP500: 0, pb.SIXTY_FORTY: 0}
    assert set(r.share_below.values()) == {None}
    assert set(r.best.values()) == set(r.worst.values()) == set(r.latest.values()) == {None}


# ── Each sleeve on its own ────────────────────────────────────────────────────

def test_a_sleeves_statistics_against_a_hand_computation():
    """Sleeve 10%, 20%, 0%; SPY 0%, 10%, -10%. Annualized (1.1 x 1.2) ** 4 - 1; the
    deviations from the means are 0, 10, -10 points for both, so the correlation is 1;
    wealth never falls, so the drawdown is 0."""
    months = _months("2006-03", 3)
    s = pb.sleeve_statistics("A", pd.Series([0.10, 0.20, 0.0], index=months),
                             pd.Series([0.0, 0.10, -0.10], index=months),
                             pd.Series(0.0, index=months))
    assert s.sleeve == "A"
    assert s.annual_return == pytest.approx(1.32 ** 4 - 1)
    assert s.annual_volatility == pytest.approx(0.1 * math.sqrt(12))
    assert s.drawdown.depth == 0
    assert s.correlation == pytest.approx(1.0)
    flat = pb.sleeve_statistics("A", pd.Series(0.01, index=months),
                                pd.Series([0.0, 0.10, -0.10], index=months),
                                pd.Series(0.0, index=months))
    assert (flat.annual_volatility, flat.correlation, flat.no_volatility) == (
        None, None, pb.NO_VOLATILITY)


def test_the_demo_run_carries_every_new_result(use_demo_db):
    """Shape only: the comparisons, the rolling summary, a turnover at each rebalance,
    and each sleeve's statistics in the policy's order, read from the sleeve's own
    series as the simulation held it."""
    bt = pb.run()
    assert list(bt.comparisons) == [pb.SP500, pb.SIXTY_FORTY]
    for name, c in bt.comparisons.items():
        assert c.months == len(bt.returns)
        assert c.up_months + c.down_months == len(bt.returns)
        assert c.active_return == pytest.approx(
            bt.statistics[pb.POLICY].annual_return - bt.statistics[name].annual_return)
    assert bt.rolling.windows == len(bt.returns) - pb.ROLLING_MONTHS + 1
    assert all(0 < r.turnover < 1 for r in bt.simulation.rebalances)
    assert list(bt.sleeve_statistics) == [s.name for s in bt.sleeves]
    for s in bt.sleeves:
        held = bt.simulation.sleeve_returns[s.name]
        assert bt.sleeve_statistics[s.name].annual_return == pb.annualized(held)
        if len(s.mix) == 1:
            history, spec = pb.load_history(), pb.load_spec()
            assert list(held) == pytest.approx(list(pb.fund_returns(s.mix[0][0], history, spec)),
                                               abs=1e-12)
    # The weights just before a reset, from the start weights and the month's returns,
    # give the turnover the rebalance records.
    history, spec = pb.load_history(), pb.load_spec()
    r = bt.simulation.rebalances[0]
    start = bt.simulation.start_weights.loc[r.month]
    grown = {col: w * (1 + pb.fund_returns(col.split("/")[1], history, spec)[r.month])
             for col, w in start.items()}
    total = sum(grown.values())
    weights = {s.name: sum(v for col, v in grown.items() if col.startswith(f"{s.name}/")) / total
               for s in bt.sleeves}
    assert r.turnover == pytest.approx(sum(abs(weights[s.name] - s.target)
                                           for s in bt.sleeves) / 2)


# ── The paragraphs ────────────────────────────────────────────────────────────

LONG = _months("2006-03", 247)                    # March 2006 to September 2026
NAMES = (pb.POLICY, pb.SP500, pb.SIXTY_FORTY)
TWO = (pb.Sleeve("A", 0.6, 0.05, (("X", 1.0),)), pb.Sleeve("B", 0.4, 0.05, (("Y", 1.0),)))
NO_FALL = pb.Drawdown(0.0, "2006-02", "2006-02", None)


def _st(annual=0.08, vol=0.14, sharpe=0.5, *, drawdown=NO_FALL, ending=20_000.0,
        years=None, partial=(), episodes=None, to_trough=None, to_recovery=None,
        no_volatility=None) -> pb.Statistics:
    return pb.Statistics(
        annual_return=annual, annual_volatility=None if no_volatility else vol,
        sharpe=None if no_volatility else sharpe, drawdown=drawdown,
        calendar_years=years or {}, partial_years=tuple(partial), windows={},
        ending_value=ending, best_month=pb.Point("2009-04", 0.1),
        worst_month=pb.Point("2008-10", -0.1), positive_share=0.6,
        months_to_trough=to_trough, months_to_recovery=to_recovery,
        episodes=episodes or {}, no_volatility=no_volatility)


def _bt(statistics, *, months=LONG, returns=None, comparisons=None, rolling=None,
        rebalances=()) -> pb.Backtest:
    """A result built by hand: only the figures a paragraph reads matter."""
    if returns is None:
        returns = pd.DataFrame({n: np.linspace(-0.02, 0.03, len(months)) * (i + 1)
                                for i, n in enumerate(NAMES)}, index=months)
    sim = pb.Simulation(returns[pb.POLICY], tuple(rebalances), pd.DataFrame(index=months))
    return pb.Backtest(sim, returns, pd.Series(0.0, index=months), statistics, TWO,
                       comparisons=comparisons or {}, rolling=rolling)


def _comparison(**figures) -> pb.Comparison:
    base = dict(months=247, active_return=0.0, beta=1.0, correlation=0.9,
                tracking_error=0.04, up_months=160, up_policy=0.4, up_comparison=0.45,
                up_capture=0.4 / 0.45, down_months=87, down_policy=-0.3,
                down_comparison=-0.35, down_capture=0.3 / 0.35)
    return pb.Comparison(**{**base, **figures})


def test_the_summary_read():
    stats = {pb.POLICY: _st(0.0846, 0.141, 0.4801), pb.SP500: _st(0.1107, 0.151, 0.6192),
             pb.SIXTY_FORTY: _st(0.0812, 0.0965, 0.6634)}
    comparisons = {
        pb.SP500: _comparison(active_return=0.0846 - 0.1107, beta=0.8949, correlation=0.9585,
                              tracking_error=0.0432, up_months=166, up_policy=0.3975,
                              up_comparison=0.4742, up_capture=0.8383, down_months=81,
                              down_policy=-0.3549, down_comparison=-0.3782,
                              down_capture=0.9384),
        pb.SIXTY_FORTY: _comparison(active_return=0.0846 - 0.0812, beta=1.3937,
                                    correlation=0.9537, tracking_error=0.0569,
                                    up_months=165, up_policy=0.4019, up_comparison=0.2953,
                                    up_capture=1.3610, down_months=82, down_policy=-0.3529,
                                    down_comparison=-0.2485, down_capture=1.4206),
    }
    assert pb.summary_read(_bt(stats, comparisons=comparisons)) == (
        "From March 2006 to September 2026 (247 months), the policy returned 8.46% a year, "
        "with 14.10% volatility and a Sharpe ratio of 0.48; the S&P 500 (SPY) 11.07% a "
        "year, with 15.10% volatility and a Sharpe ratio of 0.62; and the 60/40 8.12% a "
        "year, with 9.65% volatility and a Sharpe ratio of 0.66. "
        "Against the S&P 500 (SPY) from March 2006 to September 2026, the policy's active "
        "return was -2.61% a year, from 8.46% for the policy and 11.07% for the S&P 500 "
        "(SPY). Over the same months, against the S&P 500 (SPY): beta 0.89, correlation "
        "0.96, tracking error 4.32%. In the 166 months from March 2006 to September 2026 "
        "that the S&P 500 (SPY) returned at or above 0%, the annualized returns were "
        "39.75% for the policy and 47.42% for the S&P 500 (SPY): an up-capture of 83.83%. "
        "In the 81 months from March 2006 to September 2026 that the S&P 500 (SPY) "
        "returned below 0%, the annualized returns were -35.49% for the policy and -37.82% "
        "for the S&P 500 (SPY): a down-capture of 93.84%. "
        "Against the 60/40 from March 2006 to September 2026, the policy's active return "
        "was +0.34% a year, from 8.46% for the policy and 8.12% for the 60/40. Over the "
        "same months, against the 60/40: beta 1.39, correlation 0.95, tracking error 5.69%. "
        "In the 165 months from March 2006 to September 2026 that the 60/40 returned at or "
        "above 0%, the annualized returns were 40.19% for the policy and 29.53% for the "
        "60/40: an up-capture of 136.10%. In the 82 months from March 2006 to September "
        "2026 that the 60/40 returned below 0%, the annualized returns were -35.29% for "
        "the policy and -24.85% for the 60/40: a down-capture of 142.06%.")


def test_the_summary_read_says_what_is_missing():
    """A comparison that never moves has no beta or correlation; with no month below 0%
    there is no down-capture; a comparison at exactly 0% over its up months gives none
    either; a series with no volatility says so."""
    months = LONG
    returns = pd.DataFrame({pb.POLICY: np.linspace(-0.02, 0.03, 247), pb.SP500: 0.01,
                            pb.SIXTY_FORTY: np.linspace(-0.01, 0.02, 247)}, index=months)
    stats = {pb.POLICY: _st(0.0846), pb.SP500: _st(0.1268, no_volatility=pb.NO_VOLATILITY),
             pb.SIXTY_FORTY: _st(0.07)}
    comparisons = {
        pb.SP500: _comparison(beta=None, correlation=None, up_months=247, up_capture=0.8,
                              down_months=0, down_policy=None, down_comparison=None,
                              down_capture=None),
        pb.SIXTY_FORTY: _comparison(up_comparison=0.0, up_capture=None),
    }
    text = pb.summary_read(_bt(stats, returns=returns, comparisons=comparisons))
    assert ("the S&P 500 (SPY) 12.68% a year, with no volatility or Sharpe ratio (no "
            "volatility: every monthly return is the same);") in text
    assert ("against the S&P 500 (SPY): no beta (every monthly return of the S&P 500 (SPY) "
            "is the same), no correlation (every monthly return of the S&P 500 (SPY) is "
            "the same), tracking error 4.00%.") in text
    assert ("The S&P 500 (SPY) returned below 0% in no month from March 2006 to September "
            "2026, so there is no down-capture against it.") in text
    assert ("for the 60/40: no up-capture, since the 60/40 returned 0% over them.") in text


def test_the_summary_read_with_one_month(use_demo_db):
    """#672's one-month history, through run(): no figure that needs two months."""
    bt = pb.run(history=pb.load_history().loc[:"2006-03"])
    text = pb.summary_read(bt)
    for missing in ("no volatility or Sharpe ratio (fewer than two monthly returns)",
                    "no beta (fewer than two monthly returns)",
                    "no correlation (fewer than two monthly returns)",
                    "no tracking error (fewer than two monthly returns)"):
        assert missing in text, missing
    assert text.startswith("From March 2006 to March 2006 (1 month), the policy returned ")


def test_the_growth_read():
    episodes = {2008: pb.Episode(-0.4904, "2007-10", "2009-02", 16, "2011-04"),
                2020: pb.Episode(-0.2147, "2019-12", "2020-03", 3, "2020-11"),
                2022: None}
    stats = {pb.POLICY: _st(ending=53_170.83, episodes=episodes),
             pb.SP500: _st(ending=86_826.88), pb.SIXTY_FORTY: _st(ending=49_839.23)}
    assert pb.growth_read(_bt(stats)) == (
        "$10,000 placed at the end of February 2006 was worth $53,171 in the policy, "
        "$86,827 in the S&P 500 (SPY), and $49,839 in the 60/40 at the end of September "
        "2026. Each shaded band runs from the policy's month-end high to its low in its "
        "deepest fall reaching into the year it names: 2008, October 2007 to February "
        "2009; 2020, December 2019 to March 2020. No fall of the policy reaches into 2022, "
        "so no band is shaded for it.")
    stats[pb.POLICY] = _st(ending=12_000.0)
    assert pb.growth_read(_bt(stats)) == (
        "$10,000 placed at the end of February 2006 was worth $12,000 in the policy, "
        "$86,827 in the S&P 500 (SPY), and $49,839 in the 60/40 at the end of September "
        "2026.")


def test_the_drawdown_read():
    episodes = {2008: pb.Episode(-0.4904, "2007-10", "2009-02", 16, "2011-04"),
                2020: None,
                2022: pb.Episode(-0.2118, "2021-12", "2022-09", 9, None)}
    stats = {
        pb.POLICY: _st(drawdown=pb.Drawdown(-0.4904, "2007-10", "2009-02", "2011-04"),
                       to_trough=16, to_recovery=26, episodes=episodes),
        pb.SP500: _st(drawdown=pb.Drawdown(-0.5078, "2007-10", "2009-02", None),
                      to_trough=16),
        pb.SIXTY_FORTY: _st(),
    }
    assert pb.drawdown_read(_bt(stats)) == (
        "The deepest drawdown of the policy from March 2006 to September 2026 was -49.04%, "
        "from its October 2007 high to its February 2009 low (16 months), back at that "
        "high in April 2011 (26 months later). The deepest drawdown of the S&P 500 (SPY) "
        "from March 2006 to September 2026 was -50.78%, from its October 2007 high to its "
        "February 2009 low (16 months), not back at that high by September 2026. The 60/40 "
        "closed no month below an earlier month-end high from March 2006 to September "
        "2026. The policy's deepest fall reaching into each of 2008, 2020, and 2022: 2008, "
        "-49.04%, from its October 2007 high to its February 2009 low (16 months), back at "
        "that high in April 2011 (26 months later); 2020, none: no fall of the policy "
        "reaches into it; 2022, -21.18%, from its December 2021 high to its September 2022 "
        "low (9 months), not back at that high by September 2026.")


def test_the_drawdown_read_of_a_single_month():
    stats = {n: _st() for n in NAMES}
    assert pb.drawdown_read(_bt(stats, months=["2006-03"])) == " ".join(
        f"{who} closed no month below an earlier month-end high from March 2006 to March "
        "2006." for who in ("The policy", "The S&P 500 (SPY)", "The 60/40"))


def test_the_rolling_read():
    rolling = pb.RollingSummary(
        months=60, windows=188, first="2011-02", last="2026-09",
        below={pb.SP500: 164, pb.SIXTY_FORTY: 101},
        share_below={pb.SP500: 164 / 188, pb.SIXTY_FORTY: 101 / 188},
        best={pb.POLICY: pb.Point("2014-02", 0.2040)},
        worst={pb.POLICY: pb.Point("2012-05", -0.0039)},
        latest={pb.POLICY: pb.Point("2026-09", 0.1060), pb.SP500: pb.Point("2026-09", 0.1370),
                pb.SIXTY_FORTY: pb.Point("2026-09", 0.0799)})
    assert pb.rolling_read(_bt({n: _st() for n in NAMES}, rolling=rolling)) == (
        "In 164 (87.23%) of the 188 rolling 5-year windows ending February 2011 to "
        "September 2026, the policy's annualized return was below that of the S&P 500 "
        "(SPY), and in the other 24 it was at or above it. In 101 (53.72%) of the 188 "
        "rolling 5-year windows ending February 2011 to September 2026, the policy's "
        "annualized return was below that of the 60/40, and in the other 87 it was at or "
        "above it. Of those windows, the policy's best ended in February 2014, at 20.40% a "
        "year, and its worst in May 2012, at -0.39% a year. In the latest, ending "
        "September 2026, the annualized returns were 10.60% for the policy, 13.70% for the "
        "S&P 500 (SPY), and 7.99% for the 60/40.")


def test_the_rolling_read_with_no_window_and_with_one():
    for n, expected in (
            (30, "No rolling 5-year return is shown: the history holds 30 months from March "
                 "2006 to August 2008, fewer than 60."),
            (60, "In 0 (0.00%) of the 1 rolling 5-year window ending February 2011, the "
                 "policy's annualized return was below that of the S&P 500 (SPY), and in "
                 "the other 1 it was at or above it.")):
        months = _months("2006-03", n)
        returns = pd.DataFrame({name: [0.01] * n for name in NAMES}, index=months)
        bt = _bt({name: _st() for name in NAMES}, months=months, returns=returns,
                 rolling=pb.rolling_summary(returns))
        assert pb.rolling_read(bt).startswith(expected), pb.rolling_read(bt)


def _calendar(months: "list[str]", januaries: "dict[str, dict[str, float]]") -> pb.Backtest:
    """Every month 0% but each January named: {series: {'2007-01': r}}."""
    returns = pd.DataFrame({n: [januaries[n].get(m, 0.0) for m in months] for n in NAMES},
                           index=months)
    bill = pd.Series(0.0, index=months)
    return _bt({n: pb.statistics(returns[n], bill) for n in NAMES}, months=months,
               returns=returns, comparisons={pb.SP500: _comparison(),
                                             pb.SIXTY_FORTY: _comparison()})


def test_the_calendar_read():
    """March 2006 to June 2009: 2007 and 2008 full. The policy +5% and -30%, the S&P 500
    +6% and -35%, the 60/40 +4% and -20%: below the S&P 500 in 2007 alone, below the
    60/40 in 2008 alone."""
    bt = _calendar(_months("2006-03", 40), {
        pb.POLICY: {"2007-01": 0.05, "2008-01": -0.30},
        pb.SP500: {"2007-01": 0.06, "2008-01": -0.35},
        pb.SIXTY_FORTY: {"2007-01": 0.04, "2008-01": -0.20}})
    assert pb.calendar_read(bt) == (
        "In 1 of the 2 full calendar years from 2007 to 2008, the policy's return was "
        "below that of the S&P 500 (SPY), and in the other 1 it was at or above it. In 1 of "
        "the 2 full calendar years from 2007 to 2008, the policy's return was below that "
        "of the 60/40, and in the other 1 it was at or above it. The policy's best of those "
        "years was 2007, at +5.00%, and its worst 2008, at -30.00%. 2006 (March to "
        "December) and 2009 (January to June) are partial years, not counted here.")


def test_the_calendar_read_with_one_full_year_and_with_none():
    bt = _calendar(_months("2006-03", 22), {pb.POLICY: {"2007-01": 0.05},
                                             pb.SP500: {"2007-01": 0.06},
                                             pb.SIXTY_FORTY: {"2007-01": 0.04}})
    assert pb.calendar_read(bt) == (
        "In 2007, the only full calendar year, the returns were +5.00% for the policy, "
        "+6.00% for the S&P 500 (SPY), and +4.00% for the 60/40. 2006 (March to December) "
        "is a partial year, not counted here.")
    bt = _calendar(_months("2006-03", 10), {n: {} for n in NAMES})
    assert pb.calendar_read(bt) == (
        "The backtest holds no full calendar year from March 2006 to December 2006, so "
        "none is counted. 2006 (March to December) is a partial year, not counted here.")


def test_the_rebalance_read():
    stats = {n: _st() for n in NAMES}

    def read(*rebalances):
        return pb.rebalance_read(_bt(stats, rebalances=rebalances))

    assert read() == ("No month-end check from March 2006 to September 2026 found a sleeve "
                      "outside its band, so the policy never rebalanced.")
    assert read(pb.Rebalance("2007-09", ("A",), 0.0369)) == (
        "One month-end check from March 2006 to September 2026 found a sleeve outside its "
        "band; at it, every sleeve went back to its target. It came in September 2007, "
        "when A was outside its band, with a turnover of 3.69% of the portfolio.")
    assert read(pb.Rebalance("2007-09", ("A", "B"), 0.0369)).endswith(
        "when A and B were outside their bands, with a turnover of 3.69% of the portfolio.")
    # 2007-09 to 2013-06 is 69 months: 34.5 between three checks. A and B twice each.
    assert read(pb.Rebalance("2007-09", ("A",), 0.0369),
                pb.Rebalance("2008-10", ("A", "B"), 0.0554),
                pb.Rebalance("2013-06", ("B",), 0.0653)) == (
        "3 month-end checks from March 2006 to September 2026 found a sleeve outside its "
        "band; at each, every sleeve went back to its target. They came on average 34.5 "
        "months apart, from the first in September 2007 to the last in June 2013. A and B "
        "were each outside their bands at 2 of the 3 checks, the most of any sleeve. The "
        "largest turnover of the 3, half the sum of each sleeve's distance from its "
        "target, was 6.53% of the portfolio, at the June 2013 check.")
    assert ("A was outside its band at 2 of the 2 checks, the most of any sleeve."
            in read(pb.Rebalance("2007-09", ("A",), 0.03), pb.Rebalance("2008-10", ("A",), 0.05)))
    assert ("A and B were each outside their bands at one of the 2 checks, and no sleeve at "
            "two." in read(pb.Rebalance("2007-09", ("A",), 0.03),
                           pb.Rebalance("2008-10", ("B",), 0.05)))


# The page's own check (tests/render/test_policy_backtest_page.py): a sentence naming
# one of the three series holds none of these words.
COMPARISONS = re.compile(
    r"\b(outperform\w*|underperform\w*|beat\w*|better|worse|ahead|behind|lag\w*|"
    r"trail\w*|exceed\w*|higher|lower|more|less|greater|smaller|edge|superior|"
    r"inferior|won|lost|relative|versus|vs)\b", re.IGNORECASE)
SERIES = re.compile(r"\b(polic\w*|S&P 500|SPY|60/40)\b", re.IGNORECASE)
READS = (pb.summary_read, pb.growth_read, pb.drawdown_read, pb.rolling_read,
         pb.calendar_read, pb.rebalance_read)


def test_no_paragraph_calls_the_policy_better_or_worse(use_demo_db):
    bt = pb.run()
    for read in READS:
        sentences = re.split(r"(?<=[.;:])\s+", read(bt))
        assert [s for s in sentences if SERIES.search(s) and COMPARISONS.search(s)] == [], (
            read.__name__)
