"""#362 — the Tax Lots page covers every taxable account, relieves per account, and
discloses a taxable account whose lots are not in the ledger.

Decided by the owner:
  * relief is per account, always: a sale in one account never closes another's lot;
  * scope is every taxable account: a lot in an IRA carries no capital-gains
    consequence, so tax-advantaged accounts are not shown, and harvest covers every
    account the page shows;
  * a taxable account with no ledger lots (the CSV-fed TOD book) is disclosed, not
    omitted: an empty section reads as "no lots".

All on copies of the frozen book (#342), offline, today pinned.
"""
from __future__ import annotations

import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

SECOND = "Second Taxable"      # a taxable account WITH ledger trades
TOD = "Individual Taxable (TOD)"
IRA = "Traditional IRA"


def _frozen_conftest(config):
    return next(p for p in config.pluginmanager.get_plugins()
                if getattr(p, "FROZEN_TODAY", None) is not None)


def _add_account(con, name, tax_treatment, pseudonym) -> int:
    return con.execute(
        "INSERT INTO accounts (name, type, custodian, is_active, tax_treatment, pseudonym, "
        "display_name, managed_by, included_in_household) "
        "VALUES (?, ?, 'Fidelity', 1, ?, ?, ?, 'external', 1)",
        (name, "taxable" if tax_treatment == "taxable" else "retirement", tax_treatment,
         pseudonym, name)).lastrowid


def _trade(con, account_id, day, action, shares, price):
    assert con.execute(
        "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, fees, "
        "notes, lot_source) VALUES (?, 'VEA', ?, ?, ?, ?, 0, '#362 test', 'Fidelity CSV')",
        (account_id, day, action, shares, price)).rowcount == 1


def _two_account_book(book) -> None:
    """Account 1 (the frozen book) already sold 2.4808 VEA in 2025. A second taxable
    account buys 10 VEA at $100 in March 2026 and sells 2 in June: sales in two
    accounts, and the second account's remaining 8 shares sit at a large loss."""
    con = sqlite3.connect(book)
    with con:
        second = _add_account(con, SECOND, "taxable", "acct_taxable_t2")
        _trade(con, second, "2026-03-02", "Buy", 10.0, 100.0)
        _trade(con, second, "2026-06-01", "Sell", 2.0, 70.0)
    con.close()


def _scoped_book(book) -> None:
    """A CSV-fed taxable account with no ledger trades, and an IRA WITH a trade."""
    con = sqlite3.connect(book)
    with con:
        _add_account(con, TOD, "taxable", "acct_taxable_t3")
        ira = _add_account(con, IRA, "traditional_ira", "acct_trad_ira_t")
        _trade(con, ira, "2026-03-02", "Buy", 4.0, 60.0)
    con.close()


def _with_frozen_book(pytestconfig, tmp_path, setup, body):
    import streamlit as st
    conftest = _frozen_conftest(pytestconfig)
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        with pytest.MonkeyPatch.context() as mp:
            conftest.pin_today(mp)
            book = conftest.point_at_frozen_book(mp, tmp_path)
            if setup:
                setup(book)
            st.cache_data.clear()
            return body(book)
    finally:
        conftest.unpin_leftovers()
        st.cache_data.clear()


def _render_page_12():
    at = AppTest.from_file("pages/12_Tax_Lots.py", default_timeout=180).run()
    assert not at.exception, f"page 12 raised: {at.exception}"
    return at


# ── relief is per account ────────────────────────────────────────────────────

def test_a_sale_relieves_only_its_own_accounts_lots(pytestconfig, tmp_path):
    def body(book):
        from src.tax_lots import get_lot_inventory, taxable_accounts
        accounts = {a["name"]: a["account_id"] for a in taxable_accounts()}
        lots = get_lot_inventory("2026-07-21", account_ids=list(accounts.values()))
        con = sqlite3.connect(f"file:{book}?mode=ro", uri=True)
        a1_bought = con.execute("SELECT SUM(shares) FROM trades WHERE account_id = 1 AND "
                                "ticker = 'VEA' AND LOWER(action) = 'buy'").fetchone()[0]
        a1_sold = con.execute("SELECT SUM(shares) FROM trades WHERE account_id = 1 AND "
                              "ticker = 'VEA' AND LOWER(action) = 'sell'").fetchone()[0]
        con.close()
        return lots, accounts, a1_bought, a1_sold

    lots, accounts, a1_bought, a1_sold = _with_frozen_book(
        pytestconfig, tmp_path, _two_account_book, body)
    vea = lots[lots["ticker"] == "VEA"]
    second = vea[vea["account_id"] == accounts[SECOND]]
    first = vea[vea["account_id"] == 1]
    # Independent of the relief code: each account keeps what it bought less what IT sold.
    assert second["shares"].sum() == pytest.approx(8.0, abs=1e-9), second
    assert second["cost_basis_total"].sum() == pytest.approx(800.0, abs=1e-6), second
    assert first["shares"].sum() == pytest.approx(a1_bought - a1_sold, abs=1e-9), (
        f"account 1 holds {first['shares'].sum():.6f} VEA; it bought {a1_bought:.6f} and "
        f"sold {a1_sold:.6f}. A sale in {SECOND!r} relieved account 1's older lots.")


# ── scope, disclosure, harvest, on the rendered page ────────────────────────

def test_the_page_shows_taxable_accounts_and_discloses_the_one_without_lots(pytestconfig, tmp_path):
    def setup(book):
        _two_account_book(book)
        _scoped_book(book)

    at = _with_frozen_book(pytestconfig, tmp_path, setup, lambda book: _render_page_12())
    warnings = [str(w.value) for w in at.warning]
    notes = [w for w in warnings if "whose lots are not shown" in w]
    assert len(notes) == 1 and f"**{TOD}**" in notes[0], warnings
    assert "does not mean it holds no lots" in notes[0], notes[0]
    assert IRA not in notes[0], "a tax-advantaged account is out of scope, not undisclosed"

    table = next(d.value for d in at.dataframe if "Purchase Date" in d.value.columns)
    shown = set(table["Account"])
    assert shown == {"Personal Fidelity", SECOND}, shown          # no IRA, no TOD rows
    scope = next(str(c.value) for c in at.caption if str(c.value).startswith("Scope:"))
    assert TOD in scope and IRA not in scope, scope

    # Harvest covers every account shown: the second account's 8 VEA at $100 are a loss.
    cands = next((d.value for d in at.dataframe if "Est. Tax Benefit" in d.value.columns), None)
    assert cands is not None, "no harvest candidate table rendered"
    assert SECOND in set(cands["Account"]), cands


def _account_1_sells_everything(book) -> None:
    """Account 1 sells, per ticker, exactly what it holds (buys less sales, as SQL sums
    them), so no lot is open, and it stays the portfolio account (it still has a
    ledger)."""
    con = sqlite3.connect(book)
    with con:
        held = con.execute(
            "SELECT ticker, SUM(CASE WHEN LOWER(action) = 'buy' THEN shares ELSE -shares END) "
            "FROM trades WHERE account_id = 1 GROUP BY ticker").fetchall()
        for ticker, shares in held:
            if shares > 0:
                assert con.execute(
                    "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
                    "fees, notes, lot_source) VALUES (1, ?, '2026-07-01', 'Sell', ?, 1.0, 0, "
                    "'#364 test: sell everything', 'Manual')", (ticker, shares)).rowcount == 1
    con.close()


def test_the_disclosure_survives_an_empty_lot_table(pytestconfig, tmp_path):
    """Account 1 has sold everything, so no ledger lot is open, and the other taxable
    account is CSV-fed. The page's empty state ("No lot data available") must not
    stand alone, or the CSV-fed account reads as holding nothing.

    On the REAL inventory. This used to stub it empty, because FIFO left 1e-16-share
    slivers open after an exact full sale and the page never reached its empty state
    (#364). With that fixed, the stub came out."""
    def setup(book):
        _scoped_book(book)
        _account_1_sells_everything(book)

    at = _with_frozen_book(pytestconfig, tmp_path, setup, lambda book: _render_page_12())
    lot_tables = [d.value for d in at.dataframe if "Purchase Date" in d.value.columns]
    assert not lot_tables, (
        f"a full-position sale rendered {len(lot_tables[0])} lot rows (largest "
        f"{lot_tables[0]['Shares'].max():.3g} shares) instead of the empty state (#364)")
    assert any("No lot data available" in str(i.value) for i in at.info), "not the empty state"
    notes = [str(w.value) for w in at.warning if "whose lots are not shown" in str(w.value)]
    assert len(notes) == 1 and f"**{TOD}**" in notes[0], notes


def test_the_frozen_book_needs_no_disclosure(pytestconfig, tmp_path):
    """CONTROL: one taxable account, with lots. Nothing is withheld, so nothing is
    disclosed, and every lot is that account's."""
    at = _with_frozen_book(pytestconfig, tmp_path, None, lambda book: _render_page_12())
    assert not [w for w in at.warning if "whose lots are not shown" in str(w.value)]
    table = next(d.value for d in at.dataframe if "Purchase Date" in d.value.columns)
    assert set(table["Account"]) == {"Personal Fidelity"}


# ── the pieces, directly ─────────────────────────────────────────────────────

def test_the_inventory_refuses_to_guess_its_accounts():
    from src.tax_lots import get_lot_inventory
    with pytest.raises(ValueError, match="account_ids is required"):
        get_lot_inventory("2026-07-21", account_ids=None)


def test_relief_without_accounts_is_refused():
    import pandas as pd
    from src.tax_lots import _open_lots
    with pytest.raises(ValueError, match="relief is per account"):
        _open_lots(pd.DataFrame([{"trade_id": 1, "ticker": "X", "trade_date": "2026-01-02",
                                  "action": "buy", "shares": 1.0, "price": 1.0,
                                  "lot_source": "initial"}]))


def test_the_notice_names_every_account_it_withholds():
    from src.tax_lots import unledgered_taxable_notice
    assert unledgered_taxable_notice([]) is None
    two = unledgered_taxable_notice(["A", "B"])
    assert two.startswith("**A** and **B** are taxable accounts") and "they hold" in two
