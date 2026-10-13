"""Early in a year the Performance page says why its year-to-date risk figures are
missing, and shows them from the year's 15th session.

performance.compute_risk_metrics returns nothing for a window with fewer than 15 trading
days (#502). So each January the year-to-date window is empty for three weeks, and the
page must say so in words, with the floor and the window's own count: no blank tiles, no
error.

Rendered on copies of the frozen book, with every ticker's close moved a little on each
NYSE session through the day rendered, today pinned to that day, and the window selector
set to YTD:
  * Monday, January 4, 2027, the year's first session;
  * Friday, January 22, its 14th: still no figures;
  * Monday, January 25, its 15th: figures.

(The floor was 20 ROWS of a series that holds a row for every calendar day, under a
sentence that named 20 trading days: the figures came back on January 20, on 12 sessions.)
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from src.demo_refresh import is_session
from src.performance import MIN_TRADING_DAYS
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

JANUARY_4 = date(2027, 1, 4)
SESSION_14 = date(2027, 1, 22)
SESSION_15 = date(2027, 1, 25)
# day rendered -> the sessions the year holds through it
SESSIONS = {JANUARY_4: 1, SESSION_14: 14, SESSION_15: 15}
# Every tile the Risk-Adjusted Metrics section draws for a window that has figures.
RISK_TILES = {"Std Dev (ann.)", "Sharpe", "Sortino", "Max DD", "VaR (95%)", "CVaR (95%)",
              "Track. Err", "Info Ratio", "Beta", "Active Ret (ann.)"}
# A session's move, by its place in a five-session cycle: never zero, so every session
# is a day with a return, and not constant, so the figures are finite.
_CYCLE = (0.004, -0.003, 0.002, -0.001, 0.003)


def says(have: int) -> str:
    return (f"Insufficient data for the YTD window: it needs at least {MIN_TRADING_DAYS} "
            f"trading days; it has {have}.")


def _stage_sessions(book, through: date) -> None:
    """Give every ticker a close on each NYSE session after its last stored one, through
    ``through``, each moved from the one before by the cycle above (scaled by ticker, so
    the portfolio and its benchmark do not move as one)."""
    con = sqlite3.connect(book)
    last = {t: (d, c) for t, d, c in con.execute(
        "SELECT p.ticker, p.price_date, p.close FROM prices p JOIN (SELECT ticker, "
        "MAX(price_date) mx FROM prices GROUP BY ticker) m ON p.ticker = m.ticker "
        "AND p.price_date = m.mx")}
    rows = []
    for k, (ticker, (day, close)) in enumerate(sorted(last.items())):
        cur, n = date.fromisoformat(day) + timedelta(days=1), 0
        while cur <= through:
            if is_session(cur):
                close *= 1 + _CYCLE[n % len(_CYCLE)] * (1 + (k % 3) / 4)
                rows.append((ticker, cur.isoformat(), close))
                n += 1
            cur += timedelta(days=1)
    with con:
        con.executemany("INSERT INTO prices (ticker, price_date, close, adj_close) "
                        "VALUES (?, ?, ?, NULL)", rows)
    con.close()


def _window(at: AppTest):
    return next(r for r in at.radio if r.key == "risk_metrics_window")


@pytest.fixture(scope="module")
def january(tmp_path_factory):
    """{day: (the page as it first renders there, the page with YTD selected, the
    sessions SPY holds in 2027)} for the three days."""
    import streamlit as st
    rendered = {}
    for day in SESSIONS:
        with pytest.MonkeyPatch.context() as mp:
            book = point_at_frozen_book(mp, tmp_path_factory.mktemp(f"ytd-{day.isoformat()}"))
            _stage_sessions(book, day)
            con = sqlite3.connect(book)
            newest, in_year = con.execute(
                "SELECT MAX(price_date), SUM(price_date >= '2027-01-01') FROM prices "
                "WHERE ticker = 'SPY'").fetchone()
            con.close()
            pin_today(mp, day)
            st.cache_data.clear()
            at = AppTest.from_file("pages/2_Performance.py", default_timeout=300)
            at.run()
            first = {"exception": list(at.exception), "window": _window(at).value,
                     "tiles": {m.label for m in at.metric} & RISK_TILES}
            _window(at).set_value("YTD")
            at.run()
            rendered[day] = (first, at, (newest, in_year))
            st.cache_data.clear()
        unpin_leftovers()
    return rendered


@pytest.mark.parametrize("day", list(SESSIONS))
def test_the_premise_the_year_holds_that_many_sessions(january, day):
    """The book's newest close is the day rendered, the year holds the sessions the test
    names, and Since Inception, the window the page opens on, has every tile."""
    first, at, (newest, in_year) = january[day]
    assert (newest, in_year) == (day.isoformat(), SESSIONS[day])
    assert not first["exception"], first["exception"]
    assert first["window"] == "Since Inception" and first["tiles"] == RISK_TILES
    assert not at.exception, at.exception
    assert _window(at).value == "YTD"


@pytest.mark.parametrize("day", [JANUARY_4, SESSION_14])
def test_before_the_fifteenth_session_the_page_says_why_the_ytd_figures_are_missing(january, day):
    """The floor and the window's count, and nothing drawn for the window: no blank
    tile, no error."""
    _first, at, _ = january[day]
    assert [c.value for c in at.caption if "Insufficient data" in c.value] == [says(SESSIONS[day])]
    assert {m.label for m in at.metric} & RISK_TILES == set(), "no tile, so none blank"
    assert [e.value for e in at.error] == []
    assert [w.value for w in at.warning if "YTD" in w.value] == []


def test_on_the_fifteenth_session_the_ytd_figures_show(january):
    _first, at, _ = january[SESSION_15]
    assert [c.value for c in at.caption if "Insufficient data" in c.value] == []
    assert {m.label for m in at.metric} & RISK_TILES == RISK_TILES
    shown = {m.label: m.value for m in at.metric if m.label in RISK_TILES}
    assert shown["Std Dev (ann.)"].endswith("%") and shown["Max DD"].endswith("%"), shown
    assert [e.value for e in at.error] == []


def test_the_sentence_states_the_floor_the_function_applies():
    assert MIN_TRADING_DAYS == 15
    assert says(14) == ("Insufficient data for the YTD window: it needs at least 15 trading "
                        "days; it has 14.")


def test_the_page_counts_from_the_series_and_flows_it_measures():
    """The count in the sentence and the figures it stands in for come from the same
    inputs: the page hands trading_days_in_window the series, the benchmark and the
    flows it hands compute_risk_metrics. These renders hold no flow in 2027, so they
    could not tell a count taken without the flows from one taken with them."""
    import ast
    from pathlib import Path
    page = Path(__file__).resolve().parent.parent.parent / "pages" / "2_Performance.py"
    calls = {}
    for node in ast.walk(ast.parse(page.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in (
                "compute_risk_metrics", "trading_days_in_window"):
            calls.setdefault(node.func.id, []).append(node)
    assert {k: len(v) for k, v in calls.items()} == {"compute_risk_metrics": 1,
                                                     "trading_days_in_window": 1}

    def inputs(call):
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
        return [ast.unparse(a) for a in call.args], kw.get("cashflows")

    assert inputs(calls["trading_days_in_window"][0]) == inputs(calls["compute_risk_metrics"][0])
    assert inputs(calls["compute_risk_metrics"][0]) == (["pv", "_bl_for_metrics"], "cf")
