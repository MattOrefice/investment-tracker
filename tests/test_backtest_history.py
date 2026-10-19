"""The policy backtest's committed history (#667, PR 1 of 3).

data/backtest/monthly_returns.csv holds a monthly total return for every policy fund,
every proxy, the benchmark legs no fund or proxy supplies (AGG, for the app's 60/40, added
with PR 2) and the 3-month bill series, from February 2006 to the last complete month.
tools/refresh_backtest_history.py is its only writer; data/backtest/series.json, hand-kept,
names each fund's proxy, the last month it stands in, and the issuers' citations.

Expectations are literals: the proxies and splice months the owner decided on October
18, 2026, the file's columns, and one month's figure worked by hand from the closes and
the dividend that produced it. The writer's refusals are exercised on stubbed fetches,
offline.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "backtest" / "monthly_returns.csv"
SPEC = ROOT / "data" / "backtest" / "series.json"

FUNDS = ["VOO", "SPHQ", "VTV", "AVUV", "VEA", "IDHQ", "AVIV", "AVDV", "IEMG", "VGIT",
         "SCHP", "VNQ", "PDBC"]
PROXIES = ["SPY", "DFSVX", "VTMGX", "EFA", "EFV", "DISVX", "EEM", "VFITX", "TIP", "DBC"]
BENCHMARKS = ["AGG"]
BILL = "DGS3MO"
# fund -> (proxy, the last month it stands in, why), as decided on October 18, 2026.
SPLICES = {
    "VOO": ("SPY", "2010-09", "inception"),
    "SPHQ": ("SPY", "2010-06", "index change"),
    "AVUV": ("DFSVX", "2019-09", "inception"),
    "VEA": ("VTMGX", "2007-07", "inception"),
    "IDHQ": ("EFA", "2012-02", "index change"),
    "AVIV": ("EFV", "2021-09", "inception"),
    "AVDV": ("DISVX", "2019-09", "inception"),
    "IEMG": ("EEM", "2012-10", "inception"),
    "VGIT": ("VFITX", "2009-11", "inception"),
    "SCHP": ("TIP", "2010-08", "inception"),
    "PDBC": ("DBC", "2014-11", "inception"),
}


def _tool():
    spec = importlib.util.spec_from_file_location(
        "refresh_backtest_history", ROOT / "tools" / "refresh_backtest_history.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


@pytest.fixture(scope="module")
def history() -> pd.DataFrame:
    return pd.read_csv(HISTORY, index_col="month", dtype={"month": str})


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC.read_text(encoding="utf-8"))


def _months(lo: str, hi: str) -> "list[str]":
    return [p.strftime("%Y-%m") for p in pd.period_range(lo, hi, freq="M")]


# ── The file ──────────────────────────────────────────────────────────────────

def test_the_file_spans_february_2006_to_a_complete_month(history):
    """One row per month, none skipped, from February 2006 to a month that has ended:
    September 2026 when it was written, later after a refresh."""
    months = list(history.index)
    assert months[0] == "2006-02"
    assert months == _months(months[0], months[-1])
    assert "2026-09" <= months[-1] < date.today().strftime("%Y-%m")


def test_the_columns_are_every_fund_every_proxy_the_benchmark_legs_and_the_bill(history):
    assert list(history.columns) == FUNDS + PROXIES + BENCHMARKS + [BILL]


def test_the_benchmark_legs_cover_every_month_of_the_backtest(history):
    """AGG, the 60/40's bond leg, from March 2006 on: SPY, its other leg, is a proxy."""
    for leg in BENCHMARKS:
        assert history.loc["2006-03":, leg].notna().all(), leg


def test_every_cell_is_a_return_and_none_is_missing_inside_a_series(history):
    """Empty only before a series begins: no gap once it has, and no figure a monthly
    return of these funds could not be."""
    for col in history.columns:
        s = history[col]
        first = s.first_valid_index()
        assert s.loc[first:].notna().all(), col
        assert s.loc[first:].between(-0.6, 0.6).all(), col


def test_the_bill_is_a_monthly_rate_from_the_start(history):
    """DGS3MO covers every month, as a month's compounding of a yield between 0 and 6%."""
    bill = history[BILL]
    assert bill.notna().all()
    assert bill.between(0, (1.06 ** (1 / 12)) - 1).all()


# ── Proxies and splices ───────────────────────────────────────────────────────

def test_series_json_names_the_decided_proxies_and_splice_months(spec):
    named = {fund: (f["proxy"], f["proxy_until"], f["reason"])
             for fund, f in spec["funds"].items() if f["proxy"]}
    assert named == SPLICES
    assert [f for f, v in spec["funds"].items() if v["proxy"] is None] == ["VTV", "VNQ"]
    assert list(spec["funds"]) == FUNDS and list(spec["proxies"]) == PROXIES
    assert list(spec["benchmarks"]) == BENCHMARKS
    assert spec["bill"]["series"] == BILL
    assert spec["first_month"] == "2006-02" and spec["backtest_from"] == "2006-03"


@pytest.mark.parametrize("fund", list(SPLICES))
def test_each_splice_month_is_covered_on_both_sides(history, fund):
    """The proxy holds a return for every month from the backtest's first, March 2006,
    through its last month; the fund for every month after. A fund that replaces its
    proxy at inception has no return before; SPHQ and IDHQ have their earlier record,
    under the indexes they followed before a quality one, which the backtest does not use."""
    proxy, until, reason = SPLICES[fund]
    own_from = (pd.Period(until, "M") + 1).strftime("%Y-%m")
    assert history.loc["2006-03":until, proxy].notna().all()
    assert history.loc[own_from:, fund].notna().all()
    if reason == "inception":
        assert history[fund].first_valid_index() == own_from
    else:
        assert history[fund].first_valid_index() < own_from


def test_the_two_index_change_splices_fall_where_each_began_a_quality_index(spec):
    """SPHQ follows its own record from July 2010, IDHQ from March 2012: the first full
    months under the S&P 500 High Quality Rankings Index (from June 30, 2010) and the S&P
    International Developed High Quality Ranking Index (from March 1, 2012)."""
    sphq, idhq = spec["funds"]["SPHQ"], spec["funds"]["IDHQ"]
    assert "until June 30, 2010" in sphq["change"]["cited"]
    assert "until March 1, 2012" in idhq["change"]["cited"]


def test_every_fund_proxy_and_splice_carries_its_citation(spec):
    """Each series names its issuer's date as printed, the source and the source's own
    date (#667); each splice at an index change names the filing that dates it."""
    for name, entry in [*spec["funds"].items(), *spec["proxies"].items(),
                        *spec["benchmarks"].items()]:
        assert entry["inception"], name
        assert entry["source"].startswith("https://"), name
        assert entry["source_date"], name
    for fund in ("SPHQ", "IDHQ"):
        change = spec["funds"][fund]["change"]
        assert change["source"].startswith("https://www.sec.gov/"), fund
        assert change["cited"] and change["source_date"], fund
    assert spec["bill"]["source"] == "https://fred.stlouisfed.org/series/DGS3MO"


def test_every_fund_of_the_books_policy_has_a_series(spec):
    """Each book runs its own policy: every fund held for a sleeve with a target has a
    series, in whichever book this process serves. VNQ is added by name: sleeve_holdings
    leaves it out because it is also a leg of Real Assets' benchmark (its docstring)."""
    from src import policy
    from src.sleeve_config import sleeve_holdings
    targeted = {r["name"] for r in policy.sleeves() if float(r["target_weight"]) > 0}
    held = {t for sleeve, tickers in sleeve_holdings().items() if sleeve in targeted
            for t in tickers}
    if "Real Assets" in targeted:
        held.add("VNQ")
    assert held and held <= set(spec["funds"])


# ── One month by hand ─────────────────────────────────────────────────────────

def test_one_month_matches_a_hand_computation(history):
    """VOO, March 2024. Closes 466.93 on February 29 and 480.70 on March 28, the month's
    last session; a $1.543 dividend went ex on March 22, after a 481.35 close. Reinvested
    at that close, the month returns 480.70 / 466.93 x 481.35 / (481.35 - 1.543) - 1.
    The closes and the dividend are the ones data/demo.db stores."""
    by_hand = 480.70001220703125 / 466.92999267578125 * (
        481.3500061035156 / (481.3500061035156 - 1.543)) - 1
    assert history.at["2024-03", "VOO"] == pytest.approx(by_hand, abs=1e-6)
    assert round(by_hand, 6) == 0.032801


# ── The writer ────────────────────────────────────────────────────────────────

def _sessions(lo="2005-12-01", hi="2006-08-31") -> pd.DatetimeIndex:
    return pd.bdate_range(lo, hi)


def _closes(index: pd.DatetimeIndex, start: float = 100.0, step: float = 0.001) -> pd.Series:
    return pd.Series(start * (1 + step) ** np.arange(len(index)), index=index)


def _stub_spec() -> dict:
    return {"first_month": "2006-02", "backtest_from": "2006-03", "reference": "REF",
            "funds": {"F": {"proxy": "P", "proxy_until": "2006-04", "reason": "inception"},
                      "G": {"proxy": None}},
            "proxies": {"P": {}}, "bill": {"series": "BILL"}}


def _stub_fetches(drop: "dict[str, list[str]] | None" = None, calls: "list | None" = None):
    days = _sessions()
    histories = {"REF": _closes(days), "P": _closes(days, 50.0),
                 "F": _closes(days[days >= "2006-04-10"], 20.0),
                 "G": _closes(days, 10.0, 0.002)}
    for ticker, gone in (drop or {}).items():
        histories[ticker] = histories[ticker].drop(pd.to_datetime(gone))

    def fetch(ticker):
        if calls is not None:
            calls.append(ticker)
        return histories[ticker]

    def fetch_bills(start):
        return pd.Series(0.04, index=days)
    return fetch, fetch_bills


def test_the_writer_builds_the_stub_history():
    tool = _tool()
    calls = []
    fetch, bills = _stub_fetches(calls=calls)
    frame = tool.build(_stub_spec(), "2006-07", fetch=fetch, fetch_bills=bills)
    assert list(frame.index) == _months("2006-02", "2006-07")
    assert list(frame.columns) == ["F", "G", "P", "BILL"]
    assert frame["F"].first_valid_index() == "2006-05"
    # One fetch per series: each history is read from one response, never two.
    assert sorted(calls) == ["F", "G", "P", "REF"]
    # G's close compounds 0.2% a session: May 2006 has 23 sessions after April 28.
    assert frame.at["2006-05", "G"] == pytest.approx(1.002 ** 23 - 1)
    assert frame.at["2006-05", "BILL"] == pytest.approx(1.04 ** (1 / 12) - 1)


def test_the_writer_refuses_a_series_with_a_gap():
    """G holds no close on June 30, 2006, June's last session: a gap, so nothing is built."""
    tool = _tool()
    fetch, bills = _stub_fetches(drop={"G": ["2006-06-30"]})
    with pytest.raises(tool.Refused, match=r"G: no close on the last session of 2006-06"):
        tool.build(_stub_spec(), "2006-07", fetch=fetch, fetch_bills=bills)


def test_the_writer_refuses_a_bill_month_with_no_observation():
    tool = _tool()
    fetch, _ = _stub_fetches()
    days = _sessions()

    def bills(start):
        return pd.Series(0.04, index=days[(days < "2006-06-01") | (days > "2006-06-30")])
    with pytest.raises(tool.Refused, match=r"DGS3MO: no observation in 2006-06"):
        tool.build(_stub_spec(), "2006-07", fetch=fetch, fetch_bills=bills)


def test_the_writer_refuses_a_fund_that_does_not_begin_where_its_proxy_ends():
    """F replaces P at inception from May 2006, so F must begin in May, not June."""
    tool = _tool()
    fetch, bills = _stub_fetches(drop={"F": [str(d.date()) for d in _sessions()
                                             if "2006-04-10" <= str(d.date()) <= "2006-05-31"]})
    with pytest.raises(tool.Refused, match=r"F: replaces P at inception from 2006-05"):
        tool.build(_stub_spec(), "2006-07", fetch=fetch, fetch_bills=bills)


def test_a_refusal_writes_nothing_and_a_clean_run_writes_the_file(tmp_path, monkeypatch):
    tool = _tool()
    out = tmp_path / "monthly_returns.csv"
    monkeypatch.setattr(tool, "OUT", out)
    monkeypatch.setattr(tool, "load_spec", _stub_spec)
    monkeypatch.setattr(tool, "last_complete_month", lambda today: "2006-07")
    fetch, bills = _stub_fetches(drop={"G": ["2006-06-30"]})
    monkeypatch.setattr(tool, "fetch_history", fetch)
    monkeypatch.setattr(tool, "fetch_bills", bills)
    assert tool.main() == 1
    assert not out.exists()
    fetch, bills = _stub_fetches()
    monkeypatch.setattr(tool, "fetch_history", fetch)
    monkeypatch.setattr(tool, "fetch_bills", bills)
    assert tool.main() == 0
    written = pd.read_csv(out, index_col="month", dtype={"month": str})
    assert list(written.index) == _months("2006-02", "2006-07")
    assert list(tmp_path.iterdir()) == [out]
