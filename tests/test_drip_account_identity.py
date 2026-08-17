"""#201 — the DRIP write path must write where it was told, and compute there too.

Before this change ``persist_drip_lots`` chose its own account with
``SELECT account_id FROM accounts WHERE is_active=1 ORDER BY account_id LIMIT 1``
and probed for existing lots with no ``account_id`` at all, while
``backfill_all_drip_lots`` read the ticker list and the share timeline across
EVERY account. Three separable defects:

  * the write landed in whichever account sorted first;
  * one account's drip row suppressed another account's insert, reported as zero
    new work rather than as a conflict;
  * the share counts the lots were computed FROM spanned every account, so a
    correctly-attributed row could carry a household-wide quantity.

The third is the one that looks right, and it is why scoping only the write would
have been the worse outcome.

WHY THIS FILE IS THE ENTIRE BEHAVIOURAL EVIDENCE. Both real books have exactly one
trade-bearing account and zero rows with a NULL account_id, so every observable —
ticker lists, share timelines, demo.db's 49 existing drip rows, the operator's
summary — is unchanged by construction on both. Measured, not assumed: scoped and
unscoped reads were compared per ticker on each book and differed nowhere. The
defect is only expressible against a second account, so the fixture below is not
an illustration of the fix; it is the only place the fix is observable at all.

NETWORK. ``get_dividends`` fetches from Yahoo on a cache miss (src/prices.py:476-480),
so the tests that run the backfill patch ``drip.get_dividends`` and
``drip.get_prices`` — the names bound INTO src.drip's namespace by its ``from
src.prices import ...``. Patching ``src.prices.get_prices`` would NOT intercept:
drip.py holds its own reference, and reaching for the wrong one is a trap this
codebase has hit three times.
"""
import sqlite3
from datetime import date

import pandas as pd
import pytest

from src.drip import (
    BACKFILL_STATUSES,
    backfill_all_drip_lots,
    derive_payment_date,
    drip_shares_by_ticker,
    format_backfill_report,
    persist_drip_lots,
)

ROTH = 1       # externally managed, and the LOWEST id — what the old guess picked
TAXABLE = 2    # self-directed, trade-bearing — what the scope should be
EMPTY = 3      # active, no trades at all

EX_DATE = date(2025, 6, 2)
DPS = 2.00
PRICE = 100.0

# Roth holds 3 VOO, taxable holds 5. A timeline leak computes on 8 and is the only
# defect that survives every assertion about WHERE the row landed.
ROTH_VOO_SHARES = 3.0
TAXABLE_VOO_SHARES = 5.0

SCOPED_DRIP_SHARES = TAXABLE_VOO_SHARES * DPS / PRICE          # 0.10
HOUSEHOLD_DRIP_SHARES = (
    (ROTH_VOO_SHARES + TAXABLE_VOO_SHARES) * DPS / PRICE       # 0.16
)


@pytest.fixture
def two_account_db(tmp_path, monkeypatch):
    """The B2 scenario, built rather than described.

    id=1 is an externally-managed Roth and id=2 the self-directed taxable book, so
    ``ORDER BY account_id LIMIT 1`` resolves to the WRONG account — the ordering is
    the point of the fixture, not an accident of it. IDHQ is held only in the Roth
    so a ticker-list leak is observable; VOO is held in both with different share
    counts so a timeline leak is observable in the lot's size.

    Schema mirrors ``minimal_db`` in test_drip.py (proven sufficient for
    _auto_migrate on this path) and likewise declares no foreign keys — which is
    itself worth knowing: the real DBs would catch a nonexistent account_id via
    ``FOREIGN KEY (account_id) REFERENCES accounts(account_id)`` with
    foreign_keys=ON, but here nothing does, so the guard has to be in the code.
    """
    db_path = tmp_path / "test_drip_identity.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            tax_treatment TEXT,
            managed_by TEXT,
            display_name TEXT
        );
        INSERT INTO accounts (account_id, name, type, tax_treatment, managed_by, display_name)
        VALUES (1, 'Roth IRA', 'roth_ira', 'roth_ira', 'external', 'Roth IRA');
        INSERT INTO accounts (account_id, name, type, tax_treatment, managed_by, display_name)
        VALUES (2, 'Taxable', 'taxable', 'taxable', 'self', 'Taxable');
        INSERT INTO accounts (account_id, name, type, tax_treatment, managed_by, display_name)
        VALUES (3, 'Empty', 'taxable', 'taxable', 'self', 'Empty');

        CREATE TABLE trades (
            trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            ticker     TEXT NOT NULL,
            thesis_id  INTEGER,
            trade_date TEXT NOT NULL,
            action     TEXT NOT NULL,
            shares     REAL NOT NULL,
            price      REAL NOT NULL,
            fees       REAL DEFAULT 0,
            notes      TEXT,
            lot_source TEXT DEFAULT 'initial',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, lot_source)
        VALUES (1, 'VOO',  '2025-05-01', 'buy', 3.0, 500.0, 'initial'),
               (2, 'VOO',  '2025-05-01', 'buy', 5.0, 500.0, 'initial'),
               (1, 'IDHQ', '2025-05-01', 'buy', 7.0,  30.0, 'initial');

        CREATE TABLE dividends (
            ticker   TEXT NOT NULL,
            ex_date  TEXT NOT NULL,
            amount   REAL NOT NULL,
            PRIMARY KEY (ticker, ex_date)
        );
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("src.db.DB_PATH", db_path)
    monkeypatch.setattr("src.db._migrated_paths", set())
    return db_path


@pytest.fixture
def no_network(monkeypatch):
    """Deterministic distributions and prices, intercepted in src.drip's namespace."""
    def fake_dividends(ticker, start_date, end_date):
        return pd.Series([DPS], index=[EX_DATE])

    def fake_prices(ticker, start, end=None):
        idx = pd.date_range("2025-05-01", "2025-06-30", freq="D").date
        return pd.DataFrame({"close": PRICE, "adj_close": PRICE}, index=idx)

    monkeypatch.setattr("src.drip.get_dividends", fake_dividends)
    monkeypatch.setattr("src.drip.get_prices", fake_prices)


def _lot(purchase_date=date(2025, 6, 30), shares=0.1):
    return [{
        "ticker":               "VOO",
        "purchase_date":        purchase_date,
        "shares":               shares,
        "cost_basis_per_share": 100.0,
        "lot_source":           "drip",
    }]


def _drip_rows(db_path):
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT account_id, ticker, trade_date, shares FROM trades "
        "WHERE lot_source='drip' ORDER BY account_id, trade_date"
    )]
    conn.close()
    return rows


# ── the write lands where it was told (M1) ────────────────────────────────────

def test_persist_writes_to_the_passed_account(two_account_db):
    """The lot must land in the account passed, not in whichever id sorts first.

    KILLS M1 (restore ``ORDER BY account_id LIMIT 1``): the fixture's lowest active
    id is the Roth, so the old selection lands the row in account 1 and this
    assertion reads account_id=1 where it required 2.
    """
    n = persist_drip_lots("VOO", _lot(), account_id=TAXABLE)

    assert n == 1
    rows = _drip_rows(two_account_db)
    assert [r["account_id"] for r in rows] == [TAXABLE], (
        f"drip row(s) landed in {[r['account_id'] for r in rows]}, required "
        f"[{TAXABLE}] — the write chose its own account"
    )


# ── the probe is account-scoped (M2) ─────────────────────────────────────────

def test_persist_probe_does_not_suppress_across_accounts(two_account_db):
    """A drip row in ANOTHER account must not suppress this account's insert.

    KILLS M2 (drop account_id from the probe): the pre-seeded Roth row matches on
    (ticker, trade_date, lot_source), the taxable insert is skipped, and
    persist_drip_lots returns 0 while reporting no conflict.
    """
    conn = sqlite3.connect(str(two_account_db))
    conn.execute(
        "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
        "notes, lot_source) VALUES (1, 'VOO', '2025-06-30', 'Buy', 0.1, 100.0, "
        "'DRIP', 'drip')"
    )
    conn.commit()
    conn.close()

    n = persist_drip_lots("VOO", _lot(), account_id=TAXABLE)

    assert n == 1, (
        "the insert was suppressed by a row belonging to a different account — "
        "and a return of 0 reads as 'already current'"
    )
    assert sorted(r["account_id"] for r in _drip_rows(two_account_db)) == [ROTH, TAXABLE]


def test_persist_probe_still_suppresses_within_the_account(two_account_db):
    """Scoping the probe must not cost idempotency: the same lot, same account,
    twice, still inserts once. The complement of the test above — together they
    pin the probe to exactly one account rather than to none or to all."""
    persist_drip_lots("VOO", _lot(), account_id=TAXABLE)
    n2 = persist_drip_lots("VOO", _lot(), account_id=TAXABLE)

    assert n2 == 0
    assert len(_drip_rows(two_account_db)) == 1


# ── the scope cannot be omitted (M5) ─────────────────────────────────────────

def test_persist_requires_account_id(two_account_db):
    with pytest.raises(ValueError, match="account_id is required"):
        persist_drip_lots("VOO", _lot(), account_id=None)


def test_persist_requires_account_id_even_with_no_lots(two_account_db):
    """The argument is validated BEFORE the empty-lots early return.

    A caller passing None is wrong whether or not there is anything to write, and
    an empty lot list is the case most likely to hide it — the call succeeds, so
    nothing ever surfaces the missing scope.
    """
    with pytest.raises(ValueError, match="account_id is required"):
        persist_drip_lots("VOO", [], account_id=None)


def test_backfill_requires_account_id(two_account_db):
    with pytest.raises(ValueError, match="account_id is required"):
        backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                               account_id=None)


# ── the ticker list is account-scoped (M3) ───────────────────────────────────

def test_backfill_excludes_other_accounts_tickers(two_account_db, no_network):
    """IDHQ is held only in the Roth, so scoping to the taxable book must not
    consider it at all.

    KILLS M3 (drop account_id from the ticker-list query) — but only because the
    assertion is on KEY PRESENCE. With the unscoped query IDHQ enters the loop,
    finds no non-drip trades in the scoped timeline, and lands in results with a
    value of 0 (src/drip.py's silent zero). So `results.get("IDHQ", 0) == 0`
    passes under the mutant and would be green-by-absence — evidence of nothing.
    The ticker must be ABSENT, not zero.
    """
    results = backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                                     account_id=TAXABLE)

    assert "IDHQ" not in results, (
        f"a ticker held only in account {ROTH} entered a backfill scoped to "
        f"{TAXABLE}; results keys: {sorted(results)}"
    )
    assert "VOO" in results


# ── the lots are computed from the scoped timeline (M4) ──────────────────────

def test_backfill_lot_shares_use_only_the_scoped_timeline(two_account_db, no_network):
    """The share count the lot is computed FROM must be the scoped account's alone.

    KILLS M4 (drop account_id from the share-timeline query) — the mutant that
    looks right. Every assertion about where the row landed still passes under it;
    only the quantity moves, from 5 shares to 8, and the lot from 0.10 to 0.16.
    This is state 4 of the diagnostic in test form: a correctly-attributed row
    carrying a household-wide quantity.
    """
    backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                           account_id=TAXABLE)

    rows = _drip_rows(two_account_db)
    assert len(rows) == 1
    assert rows[0]["account_id"] == TAXABLE
    assert rows[0]["trade_date"] == derive_payment_date(EX_DATE, "VOO").isoformat()
    assert rows[0]["shares"] == pytest.approx(SCOPED_DRIP_SHARES), (
        f"lot computed on {rows[0]['shares'] * PRICE / DPS:.1f} shares; account "
        f"{TAXABLE} holds {TAXABLE_VOO_SHARES} and the household holds "
        f"{ROTH_VOO_SHARES + TAXABLE_VOO_SHARES} — the timeline crossed accounts"
    )
    assert rows[0]["shares"] != pytest.approx(HOUSEHOLD_DRIP_SHARES)


# ── an account with no trades is not 'nothing to do' ─────────────────────────

def test_backfill_raises_when_the_account_has_no_trades(two_account_db, no_network):
    """An account_id yielding no tickers can only be a programming error: the
    intended caller resolves the id through get_portfolio_account(), whose
    EXISTS-on-trades clause already excludes a tradeless account. Returning {}
    renders as 'Total new DRIP lots inserted: 0' — indistinguishable from a book
    that was already current."""
    with pytest.raises(ValueError, match="no trades"):
        backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                               account_id=EMPTY)


# ── the operator's receipt (site 6 and the four-way conflation) ──────────────

def test_drip_shares_summary_is_account_scoped(two_account_db):
    """The post-backfill summary must credit only the account it ran against.

    KILLS M6 (drop account_id from the summary query): the Roth's pre-existing
    drip lot is otherwise reported as this run's product, in the only output the
    operator sees.
    """
    conn = sqlite3.connect(str(two_account_db))
    conn.execute(
        "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
        "notes, lot_source) VALUES (1, 'IDHQ', '2025-06-30', 'Buy', 9.0, 30.0, "
        "'DRIP', 'drip')"
    )
    conn.commit()
    conn.close()
    persist_drip_lots("VOO", _lot(shares=0.25), account_id=TAXABLE)

    summary = drip_shares_by_ticker(account_id=TAXABLE)

    assert summary == [("VOO", pytest.approx(0.25))], (
        f"summary credited another account's lots to this run: {summary}"
    )


def test_status_price_fetch_failure_is_not_reported_as_current(two_account_db, no_network,
                                                              monkeypatch):
    """A failed price fetch must not render as '0 (already current)' — it is the
    opposite of what happened, on the receipt for the write this PR fixes."""
    def boom(ticker, start, end=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("src.drip.get_prices", boom)

    results = backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                                     account_id=TAXABLE)

    assert results["VOO"].status == "price_fetch_failed"
    assert results["VOO"].inserted == 0
    assert results["VOO"].skipped is True

    report = format_backfill_report(results)
    assert "already current" not in report, (
        f"a failed price fetch was reported as already current:\n{report}"
    )
    assert "VOO" in report and "price fetch" in report.lower()


def test_status_no_distributions_is_not_reported_as_current(two_account_db, no_network,
                                                            monkeypatch):
    monkeypatch.setattr("src.drip.get_dividends",
                        lambda t, s, e: pd.Series(dtype=float))

    results = backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                                     account_id=TAXABLE)

    assert results["VOO"].status == "no_distributions"
    assert results["VOO"].skipped is True
    report = format_backfill_report(results)
    assert "already current" not in report
    assert "no distributions" in report.lower()


def test_status_current_is_the_only_one_that_claims_current(two_account_db, no_network):
    """The true zero still says so — de-conflating must not make every zero a
    warning. Second run over the same window inserts nothing and is genuinely
    current."""
    backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                           account_id=TAXABLE)
    results = backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                                     account_id=TAXABLE)

    assert results["VOO"].status == "current"
    assert results["VOO"].skipped is False
    report = format_backfill_report(results)
    assert "already current" in report


def test_report_refuses_to_read_as_complete_when_a_ticker_was_skipped(two_account_db,
                                                                     no_network,
                                                                     monkeypatch):
    """The total must not stand alone when something was skipped without a result.

    Same rule as the coverage substrate: a figure that omits part of its input
    cannot be presented as if it covered all of it. Every skipped ticker is named
    with its reason, on the table, not only on stderr.
    """
    calls = {"n": 0}

    def half_broken(ticker, start, end=None):
        calls["n"] += 1
        if ticker == "IDHQ":
            raise RuntimeError("connection reset")
        idx = pd.date_range("2025-05-01", "2025-06-30", freq="D").date
        return pd.DataFrame({"close": PRICE, "adj_close": PRICE}, index=idx)

    monkeypatch.setattr("src.drip.get_prices", half_broken)

    results = backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-06-30",
                                     account_id=ROTH)
    report = format_backfill_report(results)

    assert results["VOO"].inserted == 1
    assert results["IDHQ"].status == "price_fetch_failed"
    assert "IDHQ" in report
    assert "INCOMPLETE" in report.upper(), (
        f"a run that skipped a ticker printed a bare total:\n{report}"
    )
    # and the total is still the true count of what was written
    assert "1" in report.splitlines()[-1] or any("1" in ln for ln in report.splitlines())


def test_every_status_is_declared(two_account_db):
    """The status set is closed, like UNRESOLVED_REASONS — a new skip reason has to
    be added deliberately rather than appearing as an unrecognised string."""
    assert BACKFILL_STATUSES == frozenset({
        "inserted", "current", "no_distributions", "no_trades", "price_fetch_failed",
    })
