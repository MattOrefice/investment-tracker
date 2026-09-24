"""#357 — the Performance page's cost basis is the Tax Lots page's: open lots, one account.

The reconciliation caption read its cost basis as SUM(shares * price) over every buy in
every account. It never subtracted a sale, and had no account filter. The Tax Lots page
already relieved sales first-in, first-out, so the two pages disagreed wherever the
ledger held a sale: on the demo book (and the frozen book built from it), whose
Phase 39 restructure sold 2.4808 VEA at $52.65, the caption said $1,157 while the Tax
Lots page said $1,026.32. The caption now takes the Tax Lots page's relief, scoped to
the portfolio account.
"""
from __future__ import annotations

import re
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest


def _frozen_conftest(config):
    return next(p for p in config.pluginmanager.get_plugins()
                if getattr(p, "FROZEN_TODAY", None) is not None)


def _render(pytestconfig, tmp_path, page: str, setup=None):
    """``page`` rendered on a frozen-book copy (offline, today pinned), after
    ``setup(db_path)`` has edited the copy. Returns (AppTest, db_path)."""
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
            at = AppTest.from_file(f"pages/{page}", default_timeout=180).run()
            assert not at.exception, f"{page} raised: {at.exception}"
    finally:
        conftest.unpin_leftovers()
        st.cache_data.clear()
    return at, book


def _performance_cost_basis(at) -> int:
    cap = next(str(c.value) for c in at.caption if "cost basis**" in str(c.value))
    return int(re.search(r"\\\$([\d,]+) cost basis\*\*", cap).group(1).replace(",", ""))


def _tax_lots_cost_basis(at) -> float:
    shown = next(m.value for m in at.metric if m.label == "Total Cost Basis")
    return float(str(shown).replace("$", "").replace(",", ""))


def test_the_performance_cost_basis_is_the_tax_lots_cost_basis(pytestconfig, tmp_path):
    perf, book = _render(pytestconfig, tmp_path / "p2", "2_Performance.py")
    lots, _ = _render(pytestconfig, tmp_path / "p12", "12_Tax_Lots.py")
    tax_lots = _tax_lots_cost_basis(lots)

    # Independent of any relief code: every buy, less the one sale taken from the
    # OLDEST VEA lot, which is large enough to absorb it on its own.
    con = sqlite3.connect(f"file:{book}?mode=ro", uri=True)
    all_buys = con.execute("SELECT SUM(shares * price) FROM trades "
                           "WHERE LOWER(action) = 'buy'").fetchone()[0]
    sells = con.execute("SELECT ticker, shares FROM trades WHERE LOWER(action) = 'sell'").fetchall()
    first_lot = con.execute("SELECT shares, price FROM trades WHERE ticker = 'VEA' "
                            "AND LOWER(action) = 'buy' ORDER BY trade_date, trade_id LIMIT 1").fetchone()
    con.close()
    # PREMISE: exactly the one sale, and the oldest lot covers it.
    assert [t for t, _ in sells] == ["VEA"] and first_lot[0] >= sells[0][1], (sells, first_lot)
    expected = all_buys - sells[0][1] * first_lot[1]
    assert all_buys - expected > 100, "the sale no longer moves the cost basis; nothing is tested"

    assert tax_lots == pytest.approx(expected, abs=0.005), (tax_lots, expected)
    assert _performance_cost_basis(perf) == round(expected), (
        f"the Performance caption reads ${_performance_cost_basis(perf):,} cost basis, the "
        f"Tax Lots page ${tax_lots:,.2f}: the caption is not relieving the sale "
        f"(summing every buy would read ${all_buys:,.0f}; #357)."
    )


def test_another_accounts_buys_do_not_move_it(pytestconfig, tmp_path):
    """A self-managed Roth with a large buy: it is not the portfolio account (not
    taxable), so the page stays on account 1, and its trades must stay out of the
    caption. The old query read every account."""
    def add_roth_buy(book):
        con = sqlite3.connect(book)
        with con:
            roth = con.execute(
                "INSERT INTO accounts (name, type, custodian, is_active, tax_treatment, "
                "pseudonym, display_name, managed_by, included_in_household) "
                "VALUES ('Roth', 'roth', 'Fidelity', 1, 'roth', 'acct_roth_t', 'Roth', 'self', 1)"
            ).lastrowid
            assert con.execute(
                "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
                "fees, notes, lot_source) VALUES (?, 'VOO', '2026-06-01', 'Buy', 10, 697.30, "
                "0, '#357 test: another account', 'Fidelity CSV')", (roth,)).rowcount == 1
        con.close()

    base, _ = _render(pytestconfig, tmp_path / "base", "2_Performance.py")
    with_roth, book = _render(pytestconfig, tmp_path / "roth", "2_Performance.py", add_roth_buy)
    con = sqlite3.connect(f"file:{book}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(DISTINCT account_id) FROM trades").fetchone()[0] == 2
    con.close()
    assert _performance_cost_basis(with_roth) == _performance_cost_basis(base), (
        f"a Roth buy of $6,973 moved the taxable book's cost basis from "
        f"${_performance_cost_basis(base):,} to ${_performance_cost_basis(with_roth):,} (#357)."
    )


def test_the_cost_basis_read_refuses_to_guess_the_account():
    from src.tax_lots import open_lot_cost_basis
    with pytest.raises(ValueError, match="account_id is required"):
        open_lot_cost_basis(account_id=None)
