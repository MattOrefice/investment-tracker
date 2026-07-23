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


def write_trades_batch(
    trades_list: list[dict], expected_total: float, account_id: int
) -> bool:
    """Write a batch of trades to the trades table in a single transaction.

    Each dict in trades_list must contain:
        ticker, trade_date, shares, price, total_value
    Optional keys: thesis_id, action (default "Buy"), lot_source (default "Manual"),
                   notes.

    ``account_id`` is REQUIRED and is the account every lot in this batch belongs
    to. It is NEVER guessed: a wrong-account write is unrecoverable — trades carry
    no other account marker, and once a Roth lot is written under the taxable
    account there is nothing left to distinguish it from a taxable trade. So a
    missing/None account_id RAISES (ValueError) rather than resolving to a
    plausible default, and an account_id that is not an active account RAISES too.
    (The old behaviour — SELECT the lowest active account_id — is exactly the
    silent-misattribution defect this signature removes.)

    Guard:
        Returns False immediately if is_write_enabled() is False.

    Integrity assertion (fires before any write):
        abs(sum(total_value) - expected_total) < 0.10

    Returns True on success, False on guard/failure.
    Empty list returns True without writing anything.
    Raises ValueError if account_id is None or not an active account.
    """
    if account_id is None:
        raise ValueError(
            "write_trades_batch requires an explicit account_id — it will not guess. "
            "A wrong-account write is unrecoverable (a lot carries no other account "
            "marker), so the caller must name the account this batch belongs to."
        )

    if not is_write_enabled():
        write_guard_toast()
        return False

    if not trades_list:
        return True

    # Fail loud on an account_id that is not a real, active account — writing to a
    # non-existent/inactive account is as unrecoverable as writing to the wrong one.
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM accounts WHERE account_id = ? AND is_active = 1",
            (int(account_id),),
        ).fetchone()
    if exists is None:
        raise ValueError(
            f"write_trades_batch: account_id {account_id!r} is not an active account; "
            "refusing to write rather than silently mis-file the batch."
        )
    account_id = int(account_id)

    total = sum(float(t.get("total_value", 0.0)) for t in trades_list)
    assert abs(total - expected_total) < 0.10, (
        f"write_trades_batch: sum(total_value) {total:.4f} ≠ expected {expected_total:.4f}"
    )

    try:
        with get_connection() as conn:
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
