"""#190 — get_inception_date must stop answering "2025-05-01" when it does not know.

Written BEFORE the implementation. Two states shared one literal:

  A  the account has no trades   -> genuinely has no inception
  B  the query did not answer    -> a locked DB, a renamed table, a permissions
                                    error; the balance of that except clause is a
                                    confident date

State B is unambiguous: raise. State A is the decision, and the reachability
inverts the usual answer. Ten of the eleven callers pass get_portfolio_account_id(),
and get_portfolio_account() requires EXISTS(SELECT 1 FROM trades ...) — so state A
is UNREACHABLE for them by construction. Widening the return type to str | None for
all eleven would make ten of them carry a None branch that can never execute, and a
dead defensive branch is where a wrong assumption gets written down and never
tested.

So the fallback is opt-in per caller: ``default`` omitted means an empty ledger
RAISES (the ten keep a total function — they always get a str), and the one caller
that has a legitimate answer for it (drip's distribution-gap scan: no trades, no
distributions) passes ``default=None`` and says so.

Reachability is asserted here rather than assumed, because it is the whole argument
for the design and it would rot silently if get_portfolio_account's predicate ever
loosened.
"""
import sqlite3

import pytest

from src.holdings import get_inception_date


def _db(tmp_path, trades):
    """A minimal ledger. `trades` is a list of (account_id, trade_date)."""
    p = tmp_path / "ledger.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE trades (account_id INTEGER, ticker TEXT, "
                 "trade_date TEXT, shares REAL, action TEXT)")
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?)",
                     [(a, "VOO", d, 1.0, "Buy") for a, d in trades])
    conn.commit()
    conn.close()
    return p


def _point_at(monkeypatch, db):
    import src.db
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())


# ── the ordinary answer ───────────────────────────────────────────────────────

def test_returns_the_earliest_trade_date(tmp_path, monkeypatch):
    """Non-vacuity for everything below: an implementation that raised on every
    call would satisfy the raise tests and be useless."""
    _point_at(monkeypatch, _db(tmp_path, [(1, "2026-06-09"), (1, "2026-07-01")]))
    assert get_inception_date(account_id=1) == "2026-06-09"


def test_scope_is_the_requested_account(tmp_path, monkeypatch):
    """An earlier trade in ANOTHER account must not move this account's window —
    the reason account_id is required in the first place."""
    _point_at(monkeypatch, _db(tmp_path, [(1, "2026-06-09"), (2, "2025-01-02")]))
    assert get_inception_date(account_id=1) == "2026-06-09"


# ── state B: the query did not answer ─────────────────────────────────────────

def test_a_database_error_raises_instead_of_dating_the_portfolio(tmp_path, monkeypatch):
    """The sharp edge. `except Exception: return "2025-05-01"` swallowed a locked
    DB, a missing table and a permissions error alike, and answered with a date 404
    days before the real inception — a window ~7.5x the portfolio's age, of which
    ~87% predates its existence."""
    empty = tmp_path / "no_trades_table.db"
    sqlite3.connect(empty).close()          # a valid DB with no `trades` table
    _point_at(monkeypatch, empty)
    with pytest.raises(Exception) as exc:
        get_inception_date(account_id=1)
    assert "2025-05-01" not in str(exc.value)


def test_a_database_error_raises_even_when_a_default_was_offered(tmp_path, monkeypatch):
    """`default` speaks for state A only. A caller saying "an empty ledger is fine"
    has not said "answer anyway if the database is broken"."""
    empty = tmp_path / "broken.db"
    sqlite3.connect(empty).close()
    _point_at(monkeypatch, empty)
    with pytest.raises(Exception):
        get_inception_date(account_id=1, default=None)


# ── state A: the account has no trades ────────────────────────────────────────

def test_empty_ledger_raises_when_no_default_is_offered(tmp_path, monkeypatch):
    """The ten portfolio callers keep a TOTAL function: they always receive a str,
    and never a None branch they cannot reach."""
    _point_at(monkeypatch, _db(tmp_path, [(2, "2026-06-09")]))
    with pytest.raises(ValueError, match="no trades"):
        get_inception_date(account_id=1)


def test_empty_ledger_returns_the_default_when_the_caller_names_one(tmp_path, monkeypatch):
    """drip's distribution-gap scan: no trades, no distributions. A legitimate
    question with a legitimate answer, decided at the call site that has it."""
    _point_at(monkeypatch, _db(tmp_path, [(2, "2026-06-09")]))
    assert get_inception_date(account_id=1, default=None) is None


def test_the_default_is_returned_verbatim(tmp_path, monkeypatch):
    _point_at(monkeypatch, _db(tmp_path, [(2, "2026-06-09")]))
    assert get_inception_date(account_id=1, default="2020-01-01") == "2020-01-01"


# ── the value itself is validated ─────────────────────────────────────────────

def test_a_malformed_trade_date_raises_rather_than_propagating(tmp_path, monkeypatch):
    """Eleven callers feed this straight into date.fromisoformat. A non-date string
    is the same class of defect one step later, and cheaper to catch here."""
    _point_at(monkeypatch, _db(tmp_path, [(1, "06/09/2026")]))
    with pytest.raises(ValueError, match="not an ISO date|malformed"):
        get_inception_date(account_id=1)


# ── the reachability argument, asserted ───────────────────────────────────────

def test_the_portfolio_account_can_never_reach_the_empty_ledger_state():
    """The design rests on this: get_portfolio_account() will not resolve an account
    that has no trades, so the ten callers passing it cannot observe state A. If
    that predicate ever loosens, this fails and the total-function claim with it."""
    import inspect

    from src.holdings import get_portfolio_account
    sql = inspect.getsource(get_portfolio_account)
    assert "EXISTS (SELECT 1 FROM trades" in sql, (
        "get_portfolio_account no longer requires the account to carry trades — "
        "state A is now reachable from the ten callers that omit `default`, and "
        "get_inception_date's total-function contract needs revisiting"
    )
