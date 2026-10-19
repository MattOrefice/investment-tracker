"""The policy backtest's engine (#667, PR 2 of 3).

The rules the owner decided on October 18, 2026 (recorded on #667), each pinned here on
a case worked by hand:
  * start at the targets; each month every holding earns its fund's return; at each
    month-end check, any sleeve outside its band sends every sleeve back to target
    (compute_drift's verdict, so a drift exactly at the band stays inside);
  * Real Assets is 60/40 VNQ/PDBC, reset only when the portfolio rebalances;
  * each fund reads its proxy through the proxy's last month and its own record after,
    at every splice month series.json names;
  * no month reads a later month's return;
  * the statistics the page will show: annual return and volatility, Sharpe against the
    3-month bill, the maximum drawdown with its dates, calendar-year returns, and the
    years 2008, 2020 and 2022; the 60/40 resets at each calendar quarter's end.

The engine runs on the book the process serves. The last tests run it on the committed
demo book, and assert its shape, never a figure of the personal book.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src import policy_backtest as pb


def _months(first: str, n: int) -> "list[str]":
    return [p.strftime("%Y-%m") for p in pd.period_range(first, periods=n, freq="M")]


TWO = (pb.Sleeve("A", 0.6, 0.05, (("X", 1.0),)), pb.Sleeve("B", 0.4, 0.05, (("Y", 1.0),)))


# ── The rebalancing rule ──────────────────────────────────────────────────────

def test_two_sleeves_through_a_breach_and_a_full_rebalance():
    """60/40 in X and Y, each with a 5-point band.
    March: X +10%, Y 0%. A = 0.66, B = 0.40, total 1.06: A is 62.26%, inside its band.
    April: X +20%, Y -10%. A = 0.792, B = 0.36, total 1.152: A is 68.75%, 8.75 points
    over, B 8.75 under. Both are outside: every sleeve goes back to target, A = 0.6912,
    B = 0.4608.
    May: X 0%, Y +10%. From 60/40 the portfolio returns 0.4 x 10% = 4%."""
    returns = pd.DataFrame({"X": [0.10, 0.20, 0.0], "Y": [0.0, -0.10, 0.10]},
                           index=_months("2006-03", 3))
    sim = pb.simulate(returns, TWO)
    assert list(sim.returns) == pytest.approx([0.06, 1.152 / 1.06 - 1, 0.04], abs=1e-15)
    assert sim.rebalances == (pb.Rebalance("2006-04", ("A", "B")),)
    assert dict(sim.start_weights.loc["2006-04"]) == pytest.approx(
        {"A/X": 0.66 / 1.06, "B/Y": 0.40 / 1.06})
    assert dict(sim.start_weights.loc["2006-05"]) == pytest.approx({"A/X": 0.6, "B/Y": 0.4})


def test_a_drift_exactly_at_the_band_stays_inside():
    """50/50 with a 5-point band. X gains 2/9 and Y nothing: A becomes 0.5 x 11/9 of
    0.5 x 11/9 + 0.5, which is 55%, exactly at the band. compute_drift keeps it inside."""
    sleeves = (pb.Sleeve("A", 0.5, 0.05, (("X", 1.0),)), pb.Sleeve("B", 0.5, 0.05, (("Y", 1.0),)))
    returns = pd.DataFrame({"X": [2 / 9], "Y": [0.0]}, index=["2006-03"])
    assert pb.simulate(returns, sleeves).rebalances == ()


def test_the_real_assets_mix_resets_only_when_the_portfolio_rebalances():
    """Equity 90% (2-point band), Real Assets 10% split 60/40 VNQ/PDBC (2-point band).
    March: VNQ +10%: Real Assets is 10.54%, inside, so no rebalance, and VNQ's share of
    the sleeve drifts to 0.066 / 0.106. April: equity -20%: Real Assets is 12.83% and
    equity 87.17%, both outside, so the portfolio rebalances and the sleeve is split
    60/40 again in May."""
    sleeves = (pb.Sleeve("Equity", 0.9, 0.02, (("E", 1.0),)),
               pb.Sleeve(pb.REAL_ASSETS, 0.1, 0.02, pb.REAL_ASSETS_MIX))
    returns = pd.DataFrame({"E": [0.0, -0.20, 0.0], "VNQ": [0.10, 0.0, 0.0],
                            "PDBC": [0.0, 0.0, 0.0]}, index=_months("2006-03", 3))
    sim = pb.simulate(returns, sleeves)

    def vnq_share(month):
        w = sim.start_weights.loc[month]
        return w["Real Assets/VNQ"] / (w["Real Assets/VNQ"] + w["Real Assets/PDBC"])

    assert vnq_share("2006-03") == pytest.approx(0.6)
    assert vnq_share("2006-04") == pytest.approx(0.066 / 0.106)
    assert sim.rebalances == (pb.Rebalance("2006-04", ("Equity", pb.REAL_ASSETS)),)
    assert vnq_share("2006-05") == pytest.approx(0.6)


def test_no_month_reads_a_later_months_return():
    """Change every return from October 2007 on: nothing before it moves."""
    rng = np.random.default_rng(7)
    months = _months("2006-03", 36)
    returns = pd.DataFrame(rng.normal(0.005, 0.05, size=(36, 2)), index=months,
                           columns=["X", "Y"])
    later = returns.copy()
    later.loc["2007-10":] = rng.normal(-0.2, 0.3, size=later.loc["2007-10":].shape)
    a, b = pb.simulate(returns, TWO), pb.simulate(later, TWO)
    assert a.returns.loc[:"2007-09"].equals(b.returns.loc[:"2007-09"])
    assert a.start_weights.loc[:"2007-10"].equals(b.start_weights.loc[:"2007-10"])
    assert [r for r in a.rebalances if r.month < "2007-10"] == \
        [r for r in b.rebalances if r.month < "2007-10"]
    mix = (("X", 0.6), ("Y", 0.4))
    assert pb.quarterly_mix(returns, mix).loc[:"2007-09"].equals(
        pb.quarterly_mix(later, mix).loc[:"2007-09"])


def test_the_60_40_resets_at_each_quarters_end():
    """Bought 60/40 in February 2006. March closes the quarter: April's return is the
    60/40 average of April's returns. February to March drifted, so March's is not."""
    returns = pd.DataFrame({"S": [0.10, 0.10, 0.05], "B": [0.0, 0.0, 0.01]},
                           index=_months("2006-02", 3))
    out = pb.quarterly_mix(returns, (("S", 0.6), ("B", 0.4)))
    assert out["2006-02"] == pytest.approx(0.06)
    assert out["2006-03"] == pytest.approx(0.066 / 1.06)          # 0.66 x 10% of 1.06
    assert out["2006-04"] == pytest.approx(0.6 * 0.05 + 0.4 * 0.01)


# ── The splices ───────────────────────────────────────────────────────────────

def test_each_fund_reads_its_proxy_through_the_splice_month_and_its_own_after():
    history, spec = pb.load_history(), pb.load_spec()
    for fund, entry in spec["funds"].items():
        series = pb.fund_returns(fund, history, spec)
        if not entry["proxy"]:
            assert series.equals(history[fund].loc[spec["backtest_from"]:].rename(fund))
            continue
        until = entry["proxy_until"]
        after = (pd.Period(until, "M") + 1).strftime("%Y-%m")
        assert series[until] == history.at[until, entry["proxy"]], fund
        assert series[after] == history.at[after, fund], fund
    # The two index changes, by name.
    sphq, idhq = (pb.fund_returns(f, history, spec) for f in ("SPHQ", "IDHQ"))
    assert sphq["2010-06"] == history.at["2010-06", "SPY"]
    assert sphq["2010-07"] == history.at["2010-07", "SPHQ"]
    assert idhq["2012-02"] == history.at["2012-02", "EFA"]
    assert idhq["2012-03"] == history.at["2012-03", "IDHQ"]


def test_a_fund_with_no_series_is_refused():
    with pytest.raises(pb.PolicyBacktestError, match="QQQ has no series"):
        pb.fund_returns("QQQ", pb.load_history(), pb.load_spec())


# ── The statistics ────────────────────────────────────────────────────────────

R = [0.02, 0.01, -0.03, 0.04, -0.10, -0.05, 0.03, 0.06, 0.08, -0.02, 0.01, 0.05, 0.02,
     -0.04, 0.03]
MONTHS = _months("2007-11", 15)                  # 2007-11 to 2009-01


def test_each_statistic_against_a_hand_computation():
    """Fifteen months, November 2007 to January 2009, and a bill of 0.2% a month.
    * annual return: (prod(1 + r)) ** (12 / 15) - 1 = 7.7864%;
    * annual volatility: the sample standard deviation x sqrt(12) = 16.4299%;
    * Sharpe: (7.7864% - (1.002 ** 12 - 1)) / 16.4299% = 0.32622;
    * drawdown: wealth peaks at 1.0393 in February 2008, falls 10% and 5% to its low in
      April, (0.9 x 0.95) - 1 = -14.5%, and stands above the peak again in July;
    * calendar years: 2007's two months, 1.02 x 1.01 - 1 = 3.02%, both partial with
      2009's one month, 3%; 2008's twelve months, 3.50139%, and 2008 is its window."""
    st = pb.statistics(pd.Series(R, index=MONTHS), pd.Series(0.002, index=MONTHS))
    assert st.annual_return == pytest.approx(0.07786399722353554, rel=1e-12)
    assert st.annual_volatility == pytest.approx(0.16429937831375294, rel=1e-12)
    assert st.sharpe == pytest.approx(0.3262229585298788, rel=1e-12)
    assert st.drawdown == pb.Drawdown(pytest.approx(-0.145), "2008-02", "2008-04", "2008-07")
    assert st.calendar_years == pytest.approx({2007: 0.0302, 2008: 0.0350139114131065,
                                               2009: 0.03})
    assert st.partial_years == (2007, 2009)
    assert st.windows == pytest.approx({2008: 0.0350139114131065})
    # The volatility, by hand: mean, then the squares about it over n - 1.
    mean = sum(R) / 15
    assert st.annual_volatility == pytest.approx(
        math.sqrt(sum((r - mean) ** 2 for r in R) / 14) * math.sqrt(12), rel=1e-12)


def test_a_drawdown_not_yet_recovered_has_no_recovery_month():
    st = pb.statistics(pd.Series([0.05, -0.10, 0.02], index=_months("2020-01", 3)),
                       pd.Series(0.0, index=_months("2020-01", 3)))
    assert st.drawdown == pb.Drawdown(pytest.approx(0.9 - 1), "2020-01", "2020-02", None)


# ── The book's own policy ─────────────────────────────────────────────────────

def test_a_sleeve_holding_two_funds_is_refused(monkeypatch):
    import src.sleeve_config as sc
    held = sc.sleeve_holdings()
    sleeve = next(s for s in held if s != pb.REAL_ASSETS)
    monkeypatch.setattr(sc, "sleeve_holdings", lambda: {**held, sleeve: ["AAA", "BBB"]})
    with pytest.raises(pb.PolicyBacktestError, match="holds AAA, BBB"):
        pb.book_sleeves()


def test_each_book_runs_its_own_policy():
    """The sleeves the backtest holds are the targeted sleeves of the book this process
    serves, with their targets and bands. No figure is read."""
    from src import policy
    book = {r["name"]: (float(r["target_weight"]), float(r["tolerance_band"]))
            for r in policy.sleeves() if float(r["target_weight"]) > 0}
    assert {s.name: (s.target, s.band) for s in pb.book_sleeves()} == book


def test_the_demo_backtest_runs_from_march_2006_beside_both_benchmarks(use_demo_db):
    bt = pb.run()
    history = pb.load_history()
    assert list(bt.returns.columns) == [pb.POLICY, pb.SP500, pb.SIXTY_FORTY]
    assert bt.returns.index[0] == "2006-03"
    assert bt.returns.index[-1] == history.index[-1]
    assert not bt.returns.isna().any().any() and not bt.bill.isna().any()
    assert len(bt.sleeves) == 12
    assert next(s for s in bt.sleeves if s.name == pb.REAL_ASSETS).mix == pb.REAL_ASSETS_MIX
    names = {s.name for s in bt.sleeves}
    assert bt.simulation.rebalances and all(set(r.triggers) <= names
                                            for r in bt.simulation.rebalances)
    assert bt.returns[pb.SP500].equals(history["SPY"].loc["2006-03":].rename(pb.SP500))
    for name, st in bt.statistics.items():
        assert set(st.windows) == {2008, 2020, 2022}, name
        assert st.partial_years[0] == 2006, name
