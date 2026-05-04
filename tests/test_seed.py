"""
Integration test: verify demo.db inception trades sum to $1000 after rebase.

Connects directly to demo.db rather than via get_connection() to avoid the
cached TRACKER_MODE state that other test modules may have set at import time.
"""
import sqlite3
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_DEMO_DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "demo.db"


def _conn():
    conn = sqlite3.connect(str(_DEMO_DB))
    conn.row_factory = sqlite3.Row
    return conn


def test_demo_inception_trades_sum_to_1000():
    """
    All inception trades (2025-05-01) in demo.db must sum to $1000 ± $1.
    Fractional share rounding is the only tolerated source of deviation.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(shares * price), 0) AS total "
            "FROM trades "
            "WHERE trade_date = '2025-05-01'"
        ).fetchone()

    total = float(row["total"])
    assert abs(total - 1000.0) < 1.0, (
        f"Inception trade total ${total:.2f} deviates from $1000 by more than $1. "
        "Re-run: python src/seed_paper_trades.py --force"
    )


def test_demo_has_eleven_inception_trades():
    """10 ETFs + SPAXX = 11 trades on 2025-05-01."""
    with _conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_date = '2025-05-01'"
        ).fetchone()[0]

    assert count == 11, f"Expected 11 inception trades, found {count}."


def test_drip_increases_portfolio_value():
    """
    DRIP (dividend reinvestment) must produce a higher portfolio value than
    raw adj_close × inception-shares-only at the same end date.

    The no-DRIP baseline is computed directly from demo.db (inception shares ×
    adj_close from the prices cache) so this comparison is not circular.
    Requires price cache to be populated; skips on cold start.
    """
    import src.db as _db_mod
    # Patch DB_PATH to demo.db so get_connection() routes to the right file.
    # DB_PATH is a module-level variable; get_connection() reads it at call time.
    _original_path = _db_mod.DB_PATH
    _db_mod.DB_PATH = str(_DEMO_DB)

    try:
        from src.holdings import get_portfolio_value_series

        INCEPTION = "2025-05-01"
        END       = "2025-12-31"

        pv = get_portfolio_value_series(INCEPTION, END)
        pv_clean = pv.dropna()
        if pv_clean.empty:
            import pytest
            pytest.skip("No portfolio data — populate price cache first.")

        v_with_drip = float(pv_clean.iloc[-1])

        # Compute no-DRIP baseline: inception shares × adj_close at END from demo.db
        with _conn() as conn:
            trades = conn.execute(
                """SELECT ticker,
                          SUM(CASE WHEN LOWER(action)='buy' THEN shares ELSE -shares END) AS net
                   FROM trades WHERE trade_date = ?
                   GROUP BY ticker""",
                (INCEPTION,),
            ).fetchall()

            no_drip_value = 0.0
            for t in trades:
                ticker = t["ticker"]
                net    = float(t["net"])
                if ticker == "SPAXX" or net <= 0:
                    no_drip_value += net  # SPAXX is valued at $1/share
                    continue
                price_row = conn.execute(
                    """SELECT adj_close FROM prices
                       WHERE ticker = ? AND price_date <= ? AND adj_close IS NOT NULL
                       ORDER BY price_date DESC LIMIT 1""",
                    (ticker, END),
                ).fetchone()
                if price_row:
                    no_drip_value += net * float(price_row["adj_close"])

        if no_drip_value == 0:
            import pytest
            pytest.skip("No price data in DB — populate price cache first.")

        assert v_with_drip > no_drip_value, (
            f"DRIP portfolio value ${v_with_drip:.2f} should exceed "
            f"no-DRIP baseline ${no_drip_value:.2f}. "
            "Check that dividends are being fetched and applied."
        )
    finally:
        _db_mod.DB_PATH = _original_path
