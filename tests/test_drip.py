"""Unit and integration tests for src/drip.py."""
import sys
import pathlib
import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.drip import (
    PAYMENT_DATE_OFFSET_TRADING_DAYS,
    compute_drip_lots,
    derive_payment_date,
    fetch_distributions,
    persist_drip_lots,
    backfill_all_drip_lots,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def minimal_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with minimal schema for persistence tests."""
    db_path = tmp_path / "test_drip.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        );
        INSERT INTO accounts (name, type) VALUES ('Test Account', 'taxable');

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
        VALUES (1, 'VOO', '2025-05-01', 'buy', 10.0, 500.0, 'initial');

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_initial(shares: float, start: date = date(2025, 5, 1)) -> pd.Series:
    return pd.Series([shares], index=[start])


def _make_distributions(*rows) -> pd.DataFrame:
    """rows: (ex_date, dps) tuples."""
    return pd.DataFrame(
        [{"ex_date": r[0], "dividend_per_share": r[1]} for r in rows]
    )


def _make_prices(dates_prices: dict) -> pd.Series:
    """dict of {datetime.date: adj_close}."""
    return pd.Series(dates_prices)


# ── compute_drip_lots — basic cases ──────────────────────────────────────────

def test_single_distribution_correct_shares():
    """10 shares × $1.00 dps / $100 price = 0.1 DRIP shares."""
    initial   = _make_initial(10.0)
    dists     = _make_distributions((date(2025, 6, 30), 1.0))
    prices    = _make_prices({date(2025, 6, 30): 100.0})
    result    = compute_drip_lots("VOO", initial, dists, prices)
    assert len(result) == 1
    assert result[0]["shares"] == pytest.approx(0.1)


def test_single_distribution_correct_cost_basis():
    """Cost basis per share equals the adj_close on ex_date."""
    initial = _make_initial(10.0)
    dists   = _make_distributions((date(2025, 6, 30), 1.0))
    prices  = _make_prices({date(2025, 6, 30): 123.45})
    result  = compute_drip_lots("VOO", initial, dists, prices)
    assert result[0]["cost_basis_per_share"] == pytest.approx(123.45)


def test_single_distribution_correct_purchase_date():
    """purchase_date on the returned lot equals payment_date (ex_date + 2 trading days)."""
    ex = date(2025, 9, 29)   # Monday → payment_date = Wednesday 2025-10-01
    expected_pay = date(2025, 10, 1)
    initial = _make_initial(10.0)
    dists   = _make_distributions((ex, 1.5))
    prices  = _make_prices({ex: 100.0, expected_pay: 100.0})
    result  = compute_drip_lots("VOO", initial, dists, prices)
    assert result[0]["purchase_date"] == expected_pay


def test_single_distribution_lot_source_is_drip():
    initial = _make_initial(10.0)
    dists   = _make_distributions((date(2025, 6, 30), 1.0))
    prices  = _make_prices({date(2025, 6, 30): 100.0})
    result  = compute_drip_lots("VOO", initial, dists, prices)
    assert result[0]["lot_source"] == "drip"


def test_zero_distributions_returns_empty():
    initial = _make_initial(10.0)
    dists   = pd.DataFrame(columns=["ex_date", "dividend_per_share"])
    prices  = _make_prices({date(2025, 6, 30): 100.0})
    result  = compute_drip_lots("VOO", initial, dists, prices)
    assert result == []


# ── compute_drip_lots — compounding ──────────────────────────────────────────

def test_compounding_later_drip_uses_accumulated_shares():
    """
    Two distributions: first adds 0.1 shares, second accrues on 10.1 shares.
    Validates that DRIP compounding is applied correctly.
    """
    initial = _make_initial(10.0)
    dists   = _make_distributions(
        (date(2025, 6, 30), 1.0),   # first: 10 sh × $1 / $100 = 0.1 new shares
        (date(2025, 9, 30), 1.0),   # second: 10.1 sh × $1 / $110
    )
    prices  = _make_prices({
        date(2025, 6, 30): 100.0,
        date(2025, 9, 30): 110.0,
    })
    result = compute_drip_lots("VOO", initial, dists, prices)
    assert len(result) == 2

    first_shares  = result[0]["shares"]
    second_shares = result[1]["shares"]
    assert first_shares == pytest.approx(0.1)
    # second: (10.0 + 0.1) × 1.0 / 110.0
    expected_second = (10.0 + first_shares) * 1.0 / 110.0
    assert second_shares == pytest.approx(expected_second)


# ── compute_drip_lots — inception-day skip ────────────────────────────────────

def test_inception_day_distribution_is_skipped():
    """
    Distribution dated on the inception day (2025-05-01) is skipped because
    shares_before = 0 — no portfolio existed before inception.
    Matches the in-memory algorithm's prev_dates.empty guard.
    """
    initial = _make_initial(shares=10.0, start=date(2025, 5, 1))
    dists   = _make_distributions((date(2025, 5, 1), 0.187))
    prices  = _make_prices({date(2025, 5, 1): 57.32})
    result  = compute_drip_lots("VGIT", initial, dists, prices)
    assert result == []


def test_distribution_one_day_after_inception_qualifies():
    """Distribution on 2025-05-02 does qualify because shares exist on 2025-05-01."""
    initial = _make_initial(shares=10.0, start=date(2025, 5, 1))
    dists   = _make_distributions((date(2025, 5, 2), 1.0))
    prices  = _make_prices({date(2025, 5, 2): 100.0})
    result  = compute_drip_lots("VGIT", initial, dists, prices)
    assert len(result) == 1
    assert result[0]["shares"] == pytest.approx(10.0 * 1.0 / 100.0)


# ── compute_drip_lots — price lookup ─────────────────────────────────────────

def test_weekend_ex_date_uses_previous_trading_day_price():
    """
    If ex_date falls on a weekend and only the prior Friday has a price,
    the Friday price is used (index <= ex_date, last available).
    """
    friday  = date(2025, 6, 27)  # Friday
    saturday = date(2025, 6, 28)  # Saturday ex_date
    initial = _make_initial(10.0)
    dists   = _make_distributions((saturday, 1.0))
    prices  = _make_prices({friday: 95.0})  # no Saturday price
    result  = compute_drip_lots("VOO", initial, dists, prices)
    assert len(result) == 1
    assert result[0]["cost_basis_per_share"] == pytest.approx(95.0)


# ── persist_drip_lots — idempotency ──────────────────────────────────────────

def test_persist_drip_lots_inserts_new_rows(minimal_db):
    """First call inserts all lots; returns correct count."""
    lots = [
        {
            "ticker":               "VOO",
            "purchase_date":        date(2025, 6, 30),
            "shares":               0.1,
            "cost_basis_per_share": 100.0,
            "lot_source":           "drip",
        }
    ]
    n = persist_drip_lots("VOO", lots)
    assert n == 1


def test_persist_drip_lots_idempotent(minimal_db):
    """Second call with identical lots inserts zero new rows."""
    lots = [
        {
            "ticker":               "VOO",
            "purchase_date":        date(2025, 6, 30),
            "shares":               0.1,
            "cost_basis_per_share": 100.0,
            "lot_source":           "drip",
        }
    ]
    persist_drip_lots("VOO", lots)
    n2 = persist_drip_lots("VOO", lots)
    assert n2 == 0


def test_persist_drip_lots_empty_list_returns_zero(minimal_db):
    assert persist_drip_lots("VOO", []) == 0


# ── backfill_all_drip_lots — SPAXX exclusion ─────────────────────────────────

def test_backfill_skips_spaxx(minimal_db):
    """
    backfill_all_drip_lots must not include SPAXX in the returned results.
    Uses a minimal DB that includes a SPAXX buy trade.
    """
    import sqlite3 as _sq
    conn = _sq.connect(str(minimal_db))
    conn.execute(
        "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, lot_source)"
        " VALUES (1, 'SPAXX', '2025-05-01', 'buy', 30.0, 1.0, 'initial')"
    )
    conn.commit()
    conn.close()

    results = backfill_all_drip_lots(start_date="2025-05-01", end_date="2025-05-10")
    assert "SPAXX" not in results


# ── TWR reconciliation — integration ─────────────────────────────────────────

@pytest.mark.slow
def test_twr_reconciliation_within_5bps():
    """
    After backfill, TWR for all periods must match pre-backfill values
    within 5 basis points (0.0005). Uses demo DB.

    Pre-backfill baseline (captured before Phase 17):
      1M  +5.2050%    3M  +5.7566%    YTD +12.4148%
      1Y  +31.7340%   SI  +33.5249%
    """
    import os
    os.environ.setdefault("TRACKER_MODE", "demo")

    from src.holdings import get_portfolio_value_series
    from src.returns import period_return
    from src.db import get_connection

    INCEPTION = "2025-05-01"
    TODAY     = date.today().isoformat()

    BASELINE = {
        "1M":  0.052050,
        "3M":  0.057566,
        "YTD": 0.124148,
        "1Y":  0.317340,
        "SI":  0.335249,
    }

    with get_connection() as conn:
        row = conn.execute(
            "SELECT SUM(shares * price) FROM trades WHERE trade_date=? AND LOWER(action)='buy' AND lot_source='initial'",
            (INCEPTION,),
        ).fetchone()
    seed = float(row[0]) if row and row[0] else 0.0

    values = get_portfolio_value_series(INCEPTION, TODAY)
    cf     = pd.Series(0.0, index=values.index)
    cf.iloc[0] = seed

    TOLERANCE = 0.0005  # 5 basis points

    for period, baseline in BASELINE.items():
        actual = period_return("daily", values, cf, period)
        diff   = abs(actual - baseline)
        assert diff <= TOLERANCE, (
            f"{period} TWR diverged: baseline={baseline * 100:.4f}% "
            f"actual={actual * 100:.4f}% diff={diff * 100:.4f}% > 5bps"
        )


# ── derive_payment_date ───────────────────────────────────────────────────────

def test_derive_payment_date_constant_is_two():
    assert PAYMENT_DATE_OFFSET_TRADING_DAYS == 2


def test_derive_payment_date_mid_week():
    """Monday ex-date + 2 trading days = Wednesday."""
    ex = date(2025, 9, 29)  # Monday
    assert derive_payment_date(ex) == date(2025, 10, 1)  # Wednesday


def test_derive_payment_date_thursday_skips_weekend():
    """Thursday ex-date + 2 trading days skips weekend → Monday."""
    ex = date(2025, 6, 26)  # Thursday
    pay = derive_payment_date(ex)
    # Fri 2025-06-27 (+1), Mon 2025-06-30 (+2)
    assert pay == date(2025, 6, 30)


def test_derive_payment_date_friday_skips_weekend():
    """Friday ex-date + 2 trading days skips weekend → Tuesday."""
    ex = date(2026, 3, 27)  # Friday (VOO Q1 2026 ex-date)
    pay = derive_payment_date(ex)
    # Mon 2026-03-30 (+1), Tue 2026-03-31 (+2)
    assert pay == date(2026, 3, 31)


def test_derive_payment_date_ticker_unused_by_default():
    """Passing a ticker with no override produces the same result as no ticker."""
    ex = date(2025, 7, 1)  # Tuesday
    assert derive_payment_date(ex, "VOO") == derive_payment_date(ex)


# ── compute_drip_lots uses payment_date ──────────────────────────────────────

def test_compute_drip_lots_purchase_date_is_payment_date():
    """purchase_date on returned lot equals ex_date + 2 trading days."""
    ex = date(2025, 9, 29)   # Monday
    expected_pay = date(2025, 10, 1)  # Wednesday

    initial  = _make_initial(10.0)
    dists    = _make_distributions((ex, 1.0))
    # Provide prices through payment_date
    prices   = _make_prices({ex: 100.0, expected_pay: 101.0})
    result   = compute_drip_lots("VOO", initial, dists, prices)

    assert len(result) == 1
    assert result[0]["purchase_date"] == expected_pay


def test_compute_drip_lots_basis_uses_payment_date_price():
    """cost_basis_per_share uses payment_date adj_close, not ex_date adj_close."""
    ex = date(2025, 9, 29)   # Monday
    pay = date(2025, 10, 1)  # Wednesday

    initial = _make_initial(10.0)
    dists   = _make_distributions((ex, 1.0))
    prices  = _make_prices({ex: 100.0, pay: 105.0})
    result  = compute_drip_lots("VOO", initial, dists, prices)

    assert result[0]["cost_basis_per_share"] == pytest.approx(105.0)


def test_compute_drip_lots_shares_use_payment_date_price():
    """new_shares = cash_div / payment_date_price."""
    ex = date(2025, 9, 29)
    pay = date(2025, 10, 1)

    initial = _make_initial(10.0)
    dists   = _make_distributions((ex, 2.0))   # cash_div = 10 × 2 = $20
    prices  = _make_prices({ex: 100.0, pay: 200.0})  # $20 / $200 = 0.1 shares
    result  = compute_drip_lots("VOO", initial, dists, prices)

    assert result[0]["shares"] == pytest.approx(0.1)


def test_compute_drip_lots_payment_date_price_falls_back_to_prior_trading_day():
    """If payment_date has no price, falls back to the last available price before it."""
    ex = date(2025, 6, 26)   # Thursday → pay date = Monday 2025-06-30
    pay = date(2025, 6, 30)
    friday = date(2025, 6, 27)

    initial = _make_initial(10.0)
    dists   = _make_distributions((ex, 1.0))
    # Only Friday price available; Monday (pay_date) has no price
    prices  = _make_prices({ex: 90.0, friday: 92.0})
    result  = compute_drip_lots("VOO", initial, dists, prices)

    assert len(result) == 1
    # price_history[index <= pay_date].iloc[-1] = Friday's price (last before Monday)
    assert result[0]["cost_basis_per_share"] == pytest.approx(92.0)
    assert result[0]["purchase_date"] == pay
