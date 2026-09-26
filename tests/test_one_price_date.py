"""One definition of the price date (2026-09-25 demo audit, item 3).

The numbers used every stored close through today; the banner used stored closes
strictly BEFORE today (committed_price_frontier), and Performance's period windows
ended the day before too (last_settled_price_date). After the close, once the refresh
had stored the day's close, pages computed with it while the banner named the day
before. Both rules now read the same thing: stored closes on or before today. Stored
bars are settled closes only (#160), so a stored bar dated today is today's close.

Also here: the two market values (Performance vs Tax Lots) differ by the SPAXX income
the current value models above its $1.00 NAV and by nothing else, and the Tax Lots
tiles are net figures that sum to the total.

All on the frozen book, offline, with today pinned.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from tests.conftest import FROZEN_TODAY, pin_today, point_at_frozen_book, unpin_leftovers

FRONTIER = "2026-07-20"          # the frozen book's last stored close (a Monday)
TODAY = FROZEN_TODAY.isoformat()  # 2026-07-21, a Tuesday


@pytest.fixture
def book(tmp_path, monkeypatch):
    import streamlit as st
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    st.cache_data.clear()
    yield path
    st.cache_data.clear()
    unpin_leftovers()


def _store_todays_close(path, day=TODAY):
    """Every ticker's close for ``day``, as the refresh stores it after the close."""
    con = sqlite3.connect(path)
    with con:
        n = con.execute(
            "INSERT INTO prices (ticker, price_date, close, adj_close) "
            "SELECT ticker, ?, close * 1.01, NULL FROM prices WHERE price_date = ?",
            (day, FRONTIER)).rowcount
    con.close()
    assert n > 10


# ── the rule ──────────────────────────────────────────────────────────────────

def test_before_the_close_both_rules_name_the_last_stored_close(book):
    from src.holdings import committed_price_frontier, last_settled_price_date
    assert committed_price_frontier(TODAY) == FRONTIER
    assert last_settled_price_date("2025-05-01", TODAY) == FRONTIER


def test_once_todays_close_is_stored_both_rules_name_today(book):
    """The case the audit saw: the refresh stored the day's close after the close."""
    from src.holdings import committed_price_frontier, last_settled_price_date
    _store_todays_close(book)
    assert committed_price_frontier(TODAY) == TODAY
    assert last_settled_price_date("2025-05-01", TODAY) == TODAY


def test_a_served_but_unstored_live_bar_never_anchors_a_window(book, monkeypatch):
    """A personal-mode read can serve an open session's bar without storing it; the
    anchor reads storage, so that bar is not a window endpoint."""
    import pandas as pd
    import src.holdings as holdings
    import src.prices as prices
    from src.holdings import last_settled_price_date
    real = prices.get_prices

    def with_live_bar(ticker, start, end=None):
        df = real(ticker, start, end)
        live = df.iloc[[-1]].copy()
        live.index = [date.fromisoformat(TODAY)]
        return pd.concat([df, live])

    # Both bindings: holdings imports get_prices by name.
    monkeypatch.setattr(prices, "get_prices", with_live_bar)
    monkeypatch.setattr(holdings, "get_prices", with_live_bar)
    assert holdings.last_real_price_date("2025-05-01", TODAY) == TODAY, (
        "the served data does reach today: the instrument is live")
    assert last_settled_price_date("2025-05-01", TODAY) == FRONTIER


def test_the_banner_names_the_date_the_numbers_use(book):
    """Before the close it names the last stored close; after it, today."""
    from src.asof import as_of_live_line
    from src.holdings import current_market_value_with_coverage
    assert as_of_live_line() == "Prices through July 20, 2026 (settled closes)."
    before = current_market_value_with_coverage(TODAY)[0]
    _store_todays_close(book)
    after = current_market_value_with_coverage(TODAY)[0]
    assert after != before, "the current value moved to today's close"
    assert as_of_live_line() == "Live data as of July 21, 2026."


# ── every page names the same date ────────────────────────────────────────────

def _render(page):
    from streamlit.testing.v1 import AppTest
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "pages" / page), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return [str(c.value) for c in at.caption]


def test_correlations_names_the_date_its_matrix_ends_on(book):
    caps = _render("9_Correlations.py")
    line = next(c for c in caps if c.startswith("Matrix computed on the trailing"))
    assert f"ending {FRONTIER}." in line and TODAY not in line, line


@pytest.mark.parametrize("page, phrase", [
    # Item 4 of the 2026-09-25 audit made the factor vintage its own sentence, so the
    # price date now opens the next one: "Prices", not "; prices".
    ("6_Benchmark_Attribution.py", "Prices through July 20, 2026 are shown on the Performance page"),
    ("4_Factor_Profile.py", "Prices through July 20, 2026 are shown in the Performance page KPI strip"),
])
def test_the_regression_pages_name_the_price_date_not_today(book, page, phrase):
    caps = _render(page)
    assert any(phrase in c for c in caps), [c for c in caps if "prices" in c]
    assert not any("live prices through" in c for c in caps)


def test_capital_deployment_names_its_price_date(book):
    caps = _render("11_Capital_Deployment.py")
    assert any(c.startswith("Prices through July 20, 2026") for c in caps), caps[:6]


# ── the two market values ─────────────────────────────────────────────────────

def test_the_two_market_values_differ_by_the_spaxx_income_alone(book):
    from src.holdings import (current_market_value_with_coverage, get_portfolio_account_id,
                              spaxx_modeled_income)
    from src.tax_lots import get_lot_inventory, open_lot_cost_basis, summary_metrics, taxable_accounts
    perf_mv = current_market_value_with_coverage(TODAY)[0]
    lots = get_lot_inventory(TODAY, account_ids=[a["account_id"] for a in taxable_accounts()])
    m = summary_metrics(lots)
    income = spaxx_modeled_income(TODAY)
    assert income > 0, "the book holds SPAXX and BIL has earned since inception"
    assert perf_mv == pytest.approx(m["market_value_total"] + income, abs=0.01)
    cost = open_lot_cost_basis(account_id=get_portfolio_account_id(), as_of=TODAY)
    assert cost == pytest.approx(m["cost_basis_total"], abs=0.005)
    assert perf_mv - cost - income == pytest.approx(m["unrealized_gl_total"], abs=0.01), (
        "the same unrealized G/L on both pages")


def test_the_pages_state_the_reconciliation(book):
    import re
    from src.tax_lots import get_lot_inventory, summary_metrics, taxable_accounts
    perf = _render("2_Performance.py")
    rec = next(c for c in perf if c.startswith("Reconciliation: **\\$"))
    assert "unrealized G/L** on those lots (as on the Tax Lots page)" in rec
    assert "money-market income** on SPAXX, modeled at BIL's total return" in rec
    shown = re.search(r"\*\*([+−])\\\$([\d,]+\.\d\d) unrealized G/L\*\*", rec)
    assert shown, rec
    unrealized = float(shown.group(2).replace(",", "")) * (1 if shown.group(1) == "+" else -1)
    lots = get_lot_inventory(TODAY, account_ids=[a["account_id"] for a in taxable_accounts()])
    assert unrealized == pytest.approx(summary_metrics(lots)["unrealized_gl_total"], abs=0.01), (
        "Performance's unrealized G/L is the Tax Lots page's")
    lots = _render("12_Tax_Lots.py")
    assert any("SPAXX is valued at its \\$1.00 NAV" in c for c in lots)


def test_the_st_and_lt_tiles_are_net_and_sum_to_the_total(book):
    from streamlit.testing.v1 import AppTest
    import pathlib
    from src.tax_lots import get_lot_inventory, summary_metrics, taxable_accounts
    # A short-term lot at a loss, so the gross and net ST figures differ.
    con = sqlite3.connect(book)
    with con:
        con.execute("INSERT INTO trades (account_id, ticker, trade_date, action, shares, "
                    "price, fees, notes, lot_source) VALUES (1, 'VOO', '2026-07-01', 'Buy', "
                    "0.05, 900.0, 0, 'ST loss for the tile test', 'Manual')")
    con.close()
    m = summary_metrics(get_lot_inventory(
        TODAY, account_ids=[a["account_id"] for a in taxable_accounts()]))
    assert round(m["unrealized_st_gain"], 2) != round(m["unrealized_st_net"], 2)
    root = pathlib.Path(__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "pages" / "12_Tax_Lots.py"), default_timeout=300).run()
    tiles = {m.label: m.value for m in at.metric}
    cents = {k: round(float(v.replace("$", "").replace(",", "").replace("+", "")
                            .replace("-", "-")) * 100) for k, v in tiles.items()
             if k in ("Total Unrealized G/L", "ST Unrealized G/L", "LT Unrealized G/L")}
    assert set(cents) == {"Total Unrealized G/L", "ST Unrealized G/L", "LT Unrealized G/L"}, tiles
    assert cents["ST Unrealized G/L"] + cents["LT Unrealized G/L"] == cents["Total Unrealized G/L"]
    assert cents["ST Unrealized G/L"] == round(m["unrealized_st_net"] * 100), "the net, not the gross"
    assert "ST Unrealized Gain" not in tiles and "LT Unrealized Gain" not in tiles


def test_the_net_figures_include_the_losses():
    import pandas as pd
    from src.tax_lots import summary_metrics
    lots = pd.DataFrame({
        "cost_basis_total": [100.0, 100.0, 100.0], "market_value": [110.0, 95.0, 130.0],
        "unrealized_gl": [10.0, -5.0, 30.0], "tax_status": ["ST", "ST", "LT"]})
    m = summary_metrics(lots)
    assert (m["unrealized_st_gain"], m["unrealized_st_net"]) == (10.0, 5.0)
    assert m["unrealized_st_net"] + m["unrealized_lt_net"] == m["unrealized_gl_total"] == 35.0
