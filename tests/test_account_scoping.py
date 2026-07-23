"""Part B: account-scoped reads + the per-account reconciliation guard.

Covers the resolver (get_portfolio_account), the required-account_id contract on
the base reads, the fix itself (a second account's trades no longer leak into the
portfolio), and the share reconciliation that did not previously exist.
"""
from __future__ import annotations

import sqlite3
import sys
import pathlib

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.holdings import (
    get_external_cashflow_series,
    get_holdings_on_date,
    get_inception_date,
    get_portfolio_account,
    get_portfolio_account_id,
)
from src.reconciliation import account_shares_reconciled, reconcile_account_shares

_ACCOUNTS_DDL = """
CREATE TABLE accounts (
    account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    display_name  TEXT,
    tax_treatment TEXT,
    managed_by    TEXT,
    is_active     INTEGER DEFAULT 1
);
CREATE TABLE trades (
    trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    ticker     TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    action     TEXT NOT NULL,
    shares     REAL NOT NULL,
    price      REAL NOT NULL,
    fees       REAL DEFAULT 0,
    lot_source TEXT DEFAULT 'initial'
);
"""


def _build_db(tmp_path, monkeypatch, accounts, trades, name="scope.db"):
    """accounts: list of (id, name, display_name, tax_treatment, managed_by, is_active).
    trades:   list of (account_id, ticker, date, action, shares, price)."""
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_ACCOUNTS_DDL)
    conn.executemany(
        "INSERT INTO accounts (account_id, name, display_name, tax_treatment, managed_by, is_active)"
        " VALUES (?,?,?,?,?,?)", accounts,
    )
    conn.executemany(
        "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price)"
        " VALUES (?,?,?,?,?,?)", trades,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("src.db.DB_PATH", db_path)
    monkeypatch.setattr("src.db._migrated_paths", set(), raising=False)
    return db_path


# Canonical two-account book: a self-directed taxable book (1) that carries the
# ledger, plus a Roth (5) that also carries a trade.
_TWO_ACCOUNTS = [
    (1, "Personal Fidelity", "Personal Fidelity", "taxable", "self", 1),
    (5, "Roth IRA", "Roth IRA", "roth_ira", "external", 1),
]
_TWO_TRADES = [
    (1, "VOO", "2025-05-01", "buy", 10.0, 500.0),
    (1, "VEA", "2025-05-02", "buy", 5.0, 70.0),
    (5, "VOO", "2025-05-03", "buy", 3.0, 505.0),   # Roth — must NOT leak into the book
]


@pytest.fixture()
def two_account_db(tmp_path, monkeypatch):
    return _build_db(tmp_path, monkeypatch, _TWO_ACCOUNTS, _TWO_TRADES)


# ── Resolver ──────────────────────────────────────────────────────────────────

def test_resolver_picks_the_self_directed_taxable_book(two_account_db):
    acct = get_portfolio_account()
    assert acct["account_id"] == 1
    assert acct["display_name"] == "Personal Fidelity"   # DB-derived, for labels
    assert get_portfolio_account_id() == 1               # Roth (5) excluded


def test_resolver_raises_when_no_taxable_self_ledger(tmp_path, monkeypatch):
    # Only a Roth carries trades — no self-directed taxable book to resolve.
    _build_db(
        tmp_path, monkeypatch,
        [(5, "Roth IRA", "Roth IRA", "roth_ira", "external", 1)],
        [(5, "VOO", "2025-05-01", "buy", 3.0, 505.0)],
        name="noresolve.db",
    )
    with pytest.raises(ValueError, match="cannot resolve the portfolio account"):
        get_portfolio_account()


def test_resolver_raises_when_ambiguous(tmp_path, monkeypatch):
    # The acct_01/acct_taxable_01 duplication, once BOTH carry trades: refuse to guess.
    _build_db(
        tmp_path, monkeypatch,
        [(1, "Personal Fidelity", "Personal Fidelity", "taxable", "self", 1),
         (2, "Individual Taxable (Self-Directed)", "Individual Taxable", "taxable", "self", 1)],
        [(1, "VOO", "2025-05-01", "buy", 10.0, 500.0),
         (2, "VEA", "2025-05-02", "buy", 5.0, 70.0)],
        name="ambiguous.db",
    )
    with pytest.raises(ValueError, match="uniquely"):
        get_portfolio_account()


# ── Required account_id (no silent all-accounts default) ─────────────────────

@pytest.mark.parametrize("call", [
    lambda: get_holdings_on_date("2026-01-01", account_id=None),
    lambda: get_external_cashflow_series("2025-05-01", "2026-01-01", account_id=None),
    lambda: get_inception_date(account_id=None),
])
def test_base_reads_raise_on_none_account(two_account_db, call):
    with pytest.raises(ValueError, match="account_id is required"):
        call()


# ── The fix: a second account's trades do not leak into the portfolio ────────

def test_scoped_holdings_exclude_the_other_account(two_account_db):
    book = get_holdings_on_date("2026-01-01", account_id=1)
    assert float(book.loc["VOO", "net_shares"]) == 10.0   # NOT 13.0 (Roth's 3 excluded)
    assert float(book.loc["VEA", "net_shares"]) == 5.0
    roth = get_holdings_on_date("2026-01-01", account_id=5)
    assert float(roth.loc["VOO", "net_shares"]) == 3.0    # the Roth's own book


def test_scoped_inception_is_per_account(two_account_db):
    assert get_inception_date(account_id=1) == "2025-05-01"
    assert get_inception_date(account_id=5) == "2025-05-03"


# ── The reconciliation guard (item 9) ────────────────────────────────────────

def _positions(pseudonym_qty):
    """pseudonym_qty: {(pseudonym, symbol): quantity} -> positions_df."""
    return pd.DataFrame(
        [{"pseudonym": p, "symbol": s, "quantity": q}
         for (p, s), q in pseudonym_qty.items()]
    )


def test_reconciliation_balances_when_ledger_matches_positions(two_account_db):
    positions = _positions({
        ("acct_taxable_01", "VOO"): 10.0,
        ("acct_taxable_01", "VEA"): 5.0,
    })
    mismatches = reconcile_account_shares(1, positions, "acct_taxable_01", as_of="2026-01-01")
    assert mismatches.empty, f"expected reconciled, got:\n{mismatches}"
    assert account_shares_reconciled(1, positions, "acct_taxable_01", as_of="2026-01-01")


def test_reconciliation_is_per_account_roth_does_not_break_the_taxable_book(two_account_db):
    # The taxable positions show 10 VOO; the ledger's taxable account shows 10 too
    # — the Roth's 3 VOO are in a DIFFERENT account and must not make it diverge.
    # (Pre-Part-B, the account-blind read would have shown 13 and this would FAIL.)
    positions = _positions({
        ("acct_taxable_01", "VOO"): 10.0,
        ("acct_taxable_01", "VEA"): 5.0,
    })
    assert account_shares_reconciled(1, positions, "acct_taxable_01", as_of="2026-01-01")


def test_reconciliation_flags_a_mismatch(two_account_db):
    # Broker reports 12 VOO for the taxable book; the ledger says 10 -> flagged.
    positions = _positions({
        ("acct_taxable_01", "VOO"): 12.0,
        ("acct_taxable_01", "VEA"): 5.0,
    })
    mismatches = reconcile_account_shares(1, positions, "acct_taxable_01", as_of="2026-01-01")
    assert not mismatches.empty
    voo = mismatches.set_index("ticker").loc["VOO"]
    assert voo["trade_shares"] == 10.0 and voo["position_qty"] == 12.0
    assert voo["diff"] == pytest.approx(-2.0)
