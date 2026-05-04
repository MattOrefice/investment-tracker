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
