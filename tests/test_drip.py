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
    distribution_gaps_for_holdings,
    drip_distribution_gap_notice,
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


# ── Income double-count: DRIP excluded from value series, kept in holdings ───

def test_drip_excluded_from_value_series_but_kept_in_holdings(minimal_db, monkeypatch):
    """Worked-proof: a flat adj_close (price drop exactly offset by the dividend →
    0% total return) plus a DRIP lot. With the income double-count fix, the value
    series stays flat (0% TWR = adj_close on the actual shares); the pre-fix code
    would jump when the DRIP shares were added on top of adj_close. The DRIP shares
    must still be present in the holdings/position query for cost-basis/tax displays.
    """
    import src.holdings as H
    from src.holdings import get_portfolio_value_series, get_holdings_on_date
    from src.db import get_connection

    # Fixture seeds 10 VOO @ 500 (initial). Add a DRIP lot on top.
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, lot_source)"
            " VALUES (1, 'VOO', '2025-06-15', 'buy', 0.5, 98.0, 'drip')"
        )
        conn.commit()

    # Flat adj_close = 98 → total return 0% (the dividend is already embedded in
    # adj_close; price-only decline is exactly offset). Deterministic, offline.
    def _fake_prices(ticker, start, end=None):
        rng = pd.date_range(start, end or date.today().isoformat(), freq="D")
        return pd.DataFrame(
            {"close": 98.0, "adj_close": 98.0}, index=[d.date() for d in rng]
        )
    monkeypatch.setattr(H, "get_prices", _fake_prices)

    pv = get_portfolio_value_series("2025-05-01", "2025-07-01")

    # Actual (non-DRIP) shares = 10 → flat 10 × 98 = 980 throughout. If the 0.5 DRIP
    # shares leaked into the value series it would jump to 10.5 × 98 = 1029.
    assert pv.nunique() == 1, (
        f"value series not flat — DRIP shares double-counted in: {sorted(pv.unique())}"
    )
    assert pv.iloc[-1] == pytest.approx(980.0)            # 10 × 98, NOT 1029
    assert (pv.iloc[-1] / pv.iloc[0] - 1) == pytest.approx(0.0, abs=1e-9)  # correct TR

    # The DRIP shares ARE retained for position / cost-basis / tax-lot displays.
    holdings = get_holdings_on_date("2025-07-01")
    assert holdings.loc["VOO", "net_shares"] == pytest.approx(10.5)   # 10 + 0.5 DRIP


def test_current_market_value_is_all_shares_raw_close_not_return_series(minimal_db, monkeypatch):
    """The DISPLAYED dollar value (get_current_market_value) = ALL shares incl DRIP
    × RAW close (true account worth); the RETURN series (get_portfolio_value_series)
    = NON-DRIP shares × adj_close. With raw close ≠ adj_close the two diverge: the
    dollar figure counts the real DRIP shares' market worth, the return series does
    not (their income is already embedded in adj_close). Confirms the dollar-value
    fix does NOT alter the return series.
    """
    import src.holdings as H
    from src.holdings import get_current_market_value, get_portfolio_value_series
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, lot_source)"
            " VALUES (1, 'VOO', '2025-06-15', 'buy', 0.5, 98.0, 'drip')"
        )
        conn.commit()

    # Raw close 100 ≠ adj_close 98 → the two bases are distinguishable.
    def _fake_prices(ticker, start, end=None):
        rng = pd.date_range(start, end or date.today().isoformat(), freq="D")
        return pd.DataFrame(
            {"close": 100.0, "adj_close": 98.0}, index=[d.date() for d in rng]
        )
    monkeypatch.setattr(H, "get_prices", _fake_prices)

    mv     = get_current_market_value("2025-07-01")
    pv_end = float(get_portfolio_value_series("2025-05-01", "2025-07-01").iloc[-1])

    assert mv == pytest.approx(1050.0)      # 10.5 shares (incl DRIP) × 100 RAW close
    assert pv_end == pytest.approx(980.0)   # 10 non-DRIP shares × 98 adj_close (return series)
    assert mv > pv_end                      # dollar value includes the DRIP shares' worth


# ── TWR reconciliation — integration ─────────────────────────────────────────

@pytest.mark.slow
def test_drip_lots_do_not_inflate_value_series_twr():
    """Income double-count fix (replaces the old date-stale TWR-baseline pin).

    Persisted DRIP lots must NOT change the value-series TWR: adj_close already
    embeds dividend reinvestment, so valuing the actual (non-DRIP) shares at
    adj_close is the correct total return. This reconstructs the pre-fix
    DRIP-INCLUDED series (the bug: holdings × adj_close WITH the DRIP shares) and
    asserts the production (DRIP-excluded) SI TWR is materially LOWER — the removed
    double-count — by a margin consistent with the demo's cumulative distribution
    yield. Date-independent: no frozen baselines (the old pin's 1M/3M/etc.
    constants had drifted with the calendar regardless of this fix). Uses demo DB.
    """
    import os
    os.environ.setdefault("TRACKER_MODE", "demo")

    from src.holdings import get_portfolio_value_series
    from src.returns import period_return
    from src.prices import get_prices
    from src.db import get_connection

    INCEPTION = "2025-05-01"
    TODAY     = date.today().isoformat()

    prod = get_portfolio_value_series(INCEPTION, TODAY)   # DRIP-excluded (fixed)
    if prod.dropna().empty:
        pytest.skip("Portfolio data empty — skipped in local/empty-DB mode")

    with get_connection() as conn:
        drip_rows = conn.execute(
            "SELECT trade_date, ticker, shares FROM trades "
            "WHERE lot_source='drip' AND trade_date <= ? ORDER BY trade_date",
            (TODAY,),
        ).fetchall()
    if not drip_rows:
        pytest.skip("No DRIP lots in demo DB")

    # Reconstruct the pre-fix DRIP-share contribution, valued at adj_close — the
    # exact double-count the fix removes (adj_close + DRIP shares on top).
    drip_df = pd.DataFrame([dict(r) for r in drip_rows])
    drip_df["trade_date"] = pd.to_datetime(drip_df["trade_date"])
    delta = pd.Series(0.0, index=prod.index)
    for ticker, grp in drip_df.groupby("ticker"):
        cum = (grp.groupby("trade_date")["shares"].sum().cumsum()
               .reindex(prod.index).ffill().fillna(0.0))
        p = get_prices(ticker, INCEPTION, TODAY)
        p.index = pd.to_datetime(p.index)
        px = p["adj_close"].fillna(p["close"]).reindex(prod.index).ffill()
        delta = delta + cum * px

    incl = prod + delta                       # pre-fix DRIP-included series
    cf   = pd.Series(0.0, index=prod.index)
    si_prod = period_return("daily", prod, cf, "SI")
    si_incl = period_return("daily", incl, cf, "SI")

    assert si_incl - si_prod > 0.005, (
        f"DRIP-included SI TWR ({si_incl*100:.3f}%) should exceed the production "
        f"DRIP-excluded SI TWR ({si_prod*100:.3f}%) by >50 bps (the removed income "
        f"double-count). Got {(si_incl - si_prod)*100:.3f}%. If this is ~0, the DRIP "
        f"exclusion in get_portfolio_value_series may have regressed."
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


# ── distribution_gaps_for_holdings — surfacing the warn-and-skip in-app ────────
# CODE-GAP 4: backfill_all_drip_lots' warn-and-skip (this file, above) only ever
# fired inside the CLI-only backfill script — invisible in the app. These tests
# pin the same detection made live/render-reachable, naming the affected ticker
# both in the returned list AND in a logged warning.

def test_distribution_gaps_for_holdings_flags_ticker_with_no_distributions(minimal_db, caplog):
    """VOO has a trade but zero dividend rows in the fixture DB — the same
    condition backfill_all_drip_lots warns and skips on. Must be named in the
    returned gap list AND in a logged warning (debuggable, not silent).

    PROVES the pre-fix gap: distribution_gaps_for_holdings did not exist before
    this change, so a missing distribution had no in-app-reachable trace at
    all — nothing a page or report could import and call.
    """
    import logging
    with caplog.at_level(logging.WARNING):
        gaps = distribution_gaps_for_holdings("2025-05-01", "2025-05-10")

    assert gaps == ["VOO"]
    assert any("VOO" in rec.message for rec in caplog.records), (
        "expected a logged warning naming the gapped ticker; none found — "
        f"captured records: {[r.message for r in caplog.records]}"
    )


def test_distribution_gaps_for_holdings_empty_when_distribution_present(minimal_db):
    """A ticker WITH a distribution on record must not appear in the gap list —
    the common case (and the demo's actual case) is an empty list."""
    import sqlite3 as _sq
    conn = _sq.connect(str(minimal_db))
    conn.execute(
        "INSERT INTO dividends (ticker, ex_date, amount) VALUES ('VOO', '2025-05-05', 1.50)"
    )
    conn.commit()
    conn.close()

    gaps = distribution_gaps_for_holdings("2025-05-01", "2025-05-10")
    assert gaps == []


def test_distribution_gaps_for_holdings_excludes_spaxx(minimal_db):
    """SPAXX is a money-market sweep with no DRIP reinvestment (see module
    docstring) — it must never appear in the gap list even though it never has
    distribution rows, mirroring backfill_all_drip_lots' SPAXX exclusion."""
    import sqlite3 as _sq
    conn = _sq.connect(str(minimal_db))
    conn.execute(
        "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, lot_source)"
        " VALUES (1, 'SPAXX', '2025-05-01', 'buy', 30.0, 1.0, 'initial')"
    )
    conn.commit()
    conn.close()

    gaps = distribution_gaps_for_holdings("2025-05-01", "2025-05-10")
    assert "SPAXX" not in gaps
    assert gaps == ["VOO"]  # VOO still flagged; SPAXX correctly excluded


def test_drip_distribution_gap_notice_names_ticker_and_impact():
    """Notice copy must name the ticker and the current-market-value impact —
    not a generic 'something is wrong' message."""
    msg = drip_distribution_gap_notice(["VOO"])
    assert "VOO" in msg
    assert "current market value" in msg
    assert "understate" in msg


def test_drip_distribution_gap_notice_multiple_tickers():
    msg = drip_distribution_gap_notice(["VOO", "AVUV"])
    assert "VOO" in msg and "AVUV" in msg


def test_drip_distribution_gap_notice_empty_list_returns_empty_string():
    """No gaps → no notice (the caller's `if gaps:` guard is the real suppression,
    but the builder itself must not fabricate a warning from nothing)."""
    assert drip_distribution_gap_notice([]) == ""
