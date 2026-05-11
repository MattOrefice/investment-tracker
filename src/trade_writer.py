"""Batch trade writer — used by Execute and Log on the Capital Deployment page.

Every write path must call is_write_enabled() first and return False if disabled.
"""
from __future__ import annotations

from src.config import is_write_enabled
from src.db import get_connection
from src.ui_helpers import write_guard_toast


def build_thesis_lookup(pos_theses: list[dict]) -> dict[str, int | None]:
    """Return ticker → thesis_id from active position theses.

    Title convention: "Sleeve Name — TICKER". The last segment after " — "
    is treated as the ticker symbol. Inactive theses are excluded.
    """
    lookup: dict[str, int | None] = {}
    for pt in pos_theses:
        if pt.get("status") != "active":
            continue
        parts = (pt.get("title") or "").rsplit(" — ", 1)
        if len(parts) == 2:
            ticker = parts[-1].strip()
            lookup[ticker] = pt.get("thesis_id")
    return lookup


def write_trades_batch(trades_list: list[dict], expected_total: float) -> bool:
    """Write a batch of trades to the trades table in a single transaction.

    Each dict in trades_list must contain:
        ticker, trade_date, shares, price, total_value
    Optional keys: thesis_id, action (default "Buy"), lot_source (default "Manual"),
                   notes.

    Guard:
        Returns False immediately if is_write_enabled() is False.

    Integrity assertion (fires before any write):
        abs(sum(total_value) - expected_total) < 0.10

    Returns True on success, False on guard/failure.
    Empty list returns True without writing anything.
    """
    if not is_write_enabled():
        write_guard_toast()
        return False

    if not trades_list:
        return True

    total = sum(float(t.get("total_value", 0.0)) for t in trades_list)
    assert abs(total - expected_total) < 0.10, (
        f"write_trades_batch: sum(total_value) {total:.4f} ≠ expected {expected_total:.4f}"
    )

    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT account_id FROM accounts WHERE is_active=1 ORDER BY account_id LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            account_id = int(row["account_id"])

            for t in trades_list:
                if not t.get("ticker") or float(t.get("shares", 0)) <= 0:
                    return False
                conn.execute(
                    """INSERT INTO trades
                       (account_id, ticker, thesis_id, trade_date, action,
                        shares, price, fees, notes, lot_source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                    (
                        account_id,
                        t["ticker"],
                        t.get("thesis_id"),
                        t["trade_date"],
                        str(t.get("action", "Buy")).title(),
                        float(t["shares"]),
                        float(t["price"]),
                        t.get("notes"),
                        t.get("lot_source", "Manual"),
                    ),
                )
        return True
    except Exception:
        return False
