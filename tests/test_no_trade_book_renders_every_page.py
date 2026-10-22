"""A book with no trade logged renders every page (#510).

A clean clone's first personal-mode launch has no trade yet, so the portfolio-account
lookup resolves nothing. The as-of banner, which every page shows, raised with it, and
so did seven pages' own first lookup: thirteen of the sixteen loads showed a traceback,
the landing page among them.

Every page is run THROUGH THE ROUTER, as a running app runs it, on a personal book
bootstrapped from nothing, offline, with no positions file. AppTest alone would run a
switched page as a standalone script, without app.py (it resets
PagesManager.uses_pages_directory before each run, and a pages/ directory then sends
the runner down its v1 path); the fixture keeps that flag False after the first load,
which is where app.py's own st.navigation leaves it in a real process, and each load
is checked for the stylesheet app.py injects before it runs a page.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

import src.db as db
from src.asof import NO_REPORT_YET, as_of_report_line
from src.holdings import NO_TRADE_LEDGER, PortfolioAccountUnresolved, get_portfolio_account
from src.ui_helpers import FIRST_TRADE_HINT, NO_PORTFOLIO_YET

ROOT = Path(__file__).resolve().parent.parent
ROUTER_MARK = "@media (max-width: 640px)"       # app.py's stylesheet
LANDING = "(landing)"
REPORT_LINE = f"{NO_REPORT_YET}: {NO_TRADE_LEDGER}."
EMPTY_STATE = NO_PORTFOLIO_YET.format(reason=NO_TRADE_LEDGER) + " " + FIRST_TRADE_HINT
# The pages whose figures are all built from the trade ledger, with the title each shows.
LEDGER_PAGES = {
    "pages/1_SAA.py": "Strategic Asset Allocation",
    "pages/2_Performance.py": "Performance",
    "pages/6_Benchmark_Attribution.py": "Benchmark Attribution",
    "pages/4_Factor_Profile.py": "Factor Profile",
    "pages/7_Risk.py": "Risk",
    "pages/8_Research.py": "Security Research",
    "pages/11_Capital_Deployment.py": "Capital Deployment",
}
# The others, each with a sentence of its own empty state on this book.
OTHER_PAGES = {
    "pages/3_Macro.py": "Treasury Yield Curve unavailable.",
    "pages/9_Correlations.py": None,
    "pages/5_Asset_Evaluation.py": "Price data unavailable.",
    "pages/10_Trade_Log.py": "No trades logged yet.",
    "pages/12_Tax_Lots.py": "No lot data available.",
    "pages/13_Household_View.py": "No Fidelity positions file found",
    "pages/14_Asset_Location.py": "No Fidelity positions file found",
    "pages/15_Liquidity.py": "No Fidelity positions file found",
    # Needs no trade: the bootstrapped book's seeded policy runs, under its disclosure.
    "pages/16_Policy_Backtest.py": "Hypothetical, back-tested results:",
}


def _registered_pages() -> "list[str]":
    """The page scripts app.py registers with st.Page, in its order."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    return [n.args[0].value for n in ast.walk(tree)
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.Page"
            and n.args and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)]


def _empty_personal_book(mp, tmp_dir) -> Path:
    """Personal mode, a 0-byte book, no positions file, nothing reachable."""
    import socket
    import src.bootstrap as bootstrap
    import src.household_data as household_data
    import src.macro as macro
    import src.prices as prices
    from tests.conftest import _pin_mode

    def _offline(*_a, **_k):
        raise OSError("network blocked: an empty book is rendered offline")

    _pin_mode(mp, "personal")
    book = tmp_dir / "data" / "tracker.db"
    book.parent.mkdir()
    book.write_bytes(b"")
    (tmp_dir / "uploads").mkdir()
    mp.setattr(db, "DB_PATH", book)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(bootstrap, "_bootstrapped", set())
    mp.setattr(household_data, "_UPLOADS_DIR", tmp_dir / "uploads")
    mp.setattr(socket, "getaddrinfo", _offline)
    mp.setattr(prices._SESSION, "get", _offline)
    mp.setattr(macro, "fetch_fred_series", _offline)
    prices._reset_trailing_memo()
    return book


def _through_the_router(mp):
    """An AppTest on app.py whose later loads run app.py too, as a real process's do."""
    from streamlit.runtime.pages_manager import PagesManager
    from streamlit.testing.v1 import AppTest
    after_first_load = {"yes": False}
    init = PagesManager.__init__

    def _init(self, *a, **k):
        init(self, *a, **k)
        if after_first_load["yes"]:
            PagesManager.uses_pages_directory = False

    mp.setattr(PagesManager, "__init__", _init)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    assert PagesManager.uses_pages_directory is False, (
        "premise: app.py's st.navigation leaves the flag False, as in a running app")
    after_first_load["yes"] = True
    return at


def _shown(at) -> dict:
    return {
        "exceptions": [str(e.value) for e in at.exception],
        "through_router": any(ROUTER_MARK in str(m.value) for m in at.markdown),
        "titles": [str(t.value) for t in at.title],
        "captions": [str(c.value) for c in at.caption],
        "info": [str(i.value) for i in at.info],
        "said": " ".join(str(e.value) for kind in (at.info, at.warning, at.error, at.markdown,
                                                   at.caption) for e in kind),
        "metrics": [m.label for m in at.metric],
    }


@pytest.fixture(scope="module")
def loads(tmp_path_factory):
    """{page: what it showed} for the landing page and every registered page."""
    import streamlit as st
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        book = _empty_personal_book(mp, tmp_path_factory.mktemp("no_trade"))
        st.cache_data.clear()
        at = _through_the_router(mp)
        out[LANDING] = _shown(at)
        for page in _registered_pages():
            at.switch_page(page)
            at.run()
            out[page] = _shown(at)
        con = sqlite3.connect(book)
        out["trades"] = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        con.close()
        st.cache_data.clear()
    return out


def test_the_premise_the_book_holds_no_trade_and_every_page_is_covered(loads):
    assert loads["trades"] == 0
    pages = _registered_pages()
    assert len(pages) == 16
    assert set(pages) == set(LEDGER_PAGES) | set(OTHER_PAGES), (
        "a page app.py registers is in neither list, so nothing below reads it")


def test_every_load_ran_through_the_router(loads):
    """Without the fixture's flag a switched page runs standalone, and "no traceback"
    would say nothing about the banner app.py renders or the bootstrap it runs."""
    pages = [LANDING] + _registered_pages()
    assert [p for p in pages if not loads[p]["through_router"]] == []


def test_no_page_shows_a_traceback(loads):
    pages = [LANDING] + _registered_pages()
    assert {p: loads[p]["exceptions"] for p in pages if loads[p]["exceptions"]} == {}


def test_the_banner_says_why_in_the_lookups_own_words(loads):
    """The live line already said "No committed price data." The report line raised."""
    banner = loads[LANDING]["said"]
    assert f"No committed price data. {REPORT_LINE}" in banner
    assert REPORT_LINE == ("No quarterly report yet: no active self-directed taxable "
                           "account carries a trade ledger.")
    assert as_of_report_line.__module__ == "src.asof"


@pytest.mark.parametrize("page", list(LEDGER_PAGES))
def test_a_ledger_page_shows_its_title_the_banner_and_the_reason(loads, page):
    """And nothing below them: no figure is computed on a book with no scope."""
    shown = loads[page]
    assert shown["titles"] == [LEDGER_PAGES[page]]
    assert shown["captions"] == [f"No committed price data. {REPORT_LINE}"]
    assert shown["info"] == [EMPTY_STATE]
    assert shown["metrics"] == []


@pytest.mark.parametrize("page", list(OTHER_PAGES))
def test_the_other_pages_render_their_own_empty_states(loads, page):
    """These do not need the portfolio account, and none shows the ledger pages' note."""
    shown = loads[page]
    assert EMPTY_STATE not in shown["info"]
    if OTHER_PAGES[page] is not None:
        assert OTHER_PAGES[page] in shown["said"], shown["said"][:300]


# ── the lookup, and the two things built on it ────────────────────────────────

@pytest.fixture
def empty_book(tmp_path, monkeypatch):
    import src.bootstrap as bootstrap
    book = _empty_personal_book(monkeypatch, tmp_path)
    bootstrap.bootstrap_personal_db()
    return book


def _log_a_trade(account_name: str = "Personal Fidelity") -> None:
    with db.get_connection() as conn:
        account = conn.execute("SELECT account_id FROM accounts WHERE name = ?",
                               (account_name,)).fetchone()[0]
        conn.execute("INSERT INTO trades (account_id, ticker, trade_date, action, shares, price) "
                     "VALUES (?, 'VOO', '2026-10-05', 'buy', 1, 100.0)", (account,))


class _Stopped(Exception):
    """Stands in for Streamlit's stop."""


def _empty_state_drawn(mp) -> "list[tuple[str, str | None]]":
    """What require_portfolio_account draws, in order, when the lookup resolves nothing:
    each call it makes on ``st``, the stop included."""
    import src.ui_helpers as ui
    drawn = []

    class _St:
        def __getattr__(self, name):
            def call(text=None):
                drawn.append((name, text))
                if name == "stop":
                    raise _Stopped
            return call

    mp.setattr(ui, "st", _St())
    with pytest.raises(_Stopped):
        ui.require_portfolio_account("Risk")
    return drawn


def test_the_empty_state_is_a_title_the_banner_the_reason_and_a_stop(empty_book, monkeypatch):
    assert _empty_state_drawn(monkeypatch) == [
        ("title", "Risk"), ("caption", f"No committed price data. {REPORT_LINE}"),
        ("info", EMPTY_STATE), ("stop", None)]
    assert EMPTY_STATE.endswith(FIRST_TRADE_HINT)


def test_with_a_trade_logged_the_page_gets_its_account_and_draws_nothing(empty_book, monkeypatch):
    import src.ui_helpers as ui
    _log_a_trade()

    class _St:
        def __getattr__(self, name):
            raise AssertionError(f"the helper drew st.{name} on a book with a portfolio")

    monkeypatch.setattr(ui, "st", _St())
    assert ui.require_portfolio_account("Risk")["name"] == "Personal Fidelity"


def test_the_lookup_still_raises_and_now_carries_its_reason(empty_book):
    """A figure is never computed on a guessed scope: the raise stays, a ValueError as
    before, with the message its callers and tests already match."""
    with pytest.raises(ValueError, match="cannot resolve the portfolio account: no active "
                                         "self-directed taxable account carries a trade "
                                         "ledger. Refusing to guess a scope") as raised:
        get_portfolio_account()
    assert isinstance(raised.value, PortfolioAccountUnresolved)
    assert raised.value.reason == NO_TRADE_LEDGER


def test_the_report_line_names_a_quarter_once_a_trade_is_logged(empty_book):
    """The contrast: the same book with one purchase. The lookup resolves, and the line
    is the ordinary one for a portfolio younger than a quarter."""
    assert as_of_report_line() == REPORT_LINE
    _log_a_trade()
    assert get_portfolio_account()["name"] == "Personal Fidelity"
    assert as_of_report_line() == "No completed quarter yet."


def test_two_accounts_with_trades_are_named_as_the_reason_too(empty_book, monkeypatch):
    """The lookup's other failure. The banner says that one in its words as well, and
    the page's note carries no first-trade hint, which would be wrong advice here."""
    _log_a_trade()
    with db.get_connection() as conn:
        conn.execute("INSERT INTO accounts (name, type, custodian, tax_treatment, managed_by, "
                     "included_in_household, is_active) VALUES ('Second', 'taxable', 'X', "
                     "'taxable', 'self', 1, 1)")
    _log_a_trade("Second")
    with pytest.raises(PortfolioAccountUnresolved) as raised:
        get_portfolio_account()
    assert raised.value.reason == "2 self-directed taxable accounts carry trades"
    assert as_of_report_line() == ("No quarterly report yet: 2 self-directed taxable "
                                   "accounts carry trades.")
    assert _empty_state_drawn(monkeypatch) == [
        ("title", "Risk"),
        ("caption", "No committed price data. No quarterly report yet: 2 self-directed "
                    "taxable accounts carry trades."),
        ("info", "This page is built from the portfolio's trade ledger, and there is none "
                 "to read: 2 self-directed taxable accounts carry trades."),
        ("stop", None),
    ]


def test_an_explicit_inception_never_reaches_the_lookup(empty_book):
    """The pages that pass their own inception keep the path they had."""
    assert as_of_report_line(inception="2026-10-05") == "No completed quarter yet."
