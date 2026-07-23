"""Per-account share reconciliation: trade-derived holdings vs broker positions.

This is the guard that did not exist. The account-identity duplication
(acct_01 carries the trade ledger; acct_taxable_01 carries the positions CSV)
reconciled today ONLY because the trade-derived reads were account-blind — they
summed every account, so the taxable book's trade-derived shares happened to
equal the taxable positions. The moment a second account's trades landed, the
blind sum diverged from the per-account positions with nothing to catch it.

Now that the reads are account-scoped (src/holdings.py), the reconciliation is
meaningful per account: for a given account, the net shares implied by its trade
ledger must equal the quantities the broker reports for that same account
(matched by pseudonym). A divergence means a trade was booked to the wrong
account, a fill was missed, or a corporate action is unrecorded — all things a
silent account-blind sum used to hide.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.holdings import get_holdings_on_date


def reconcile_account_shares(
    account_id: int,
    positions_df: pd.DataFrame,
    pseudonym: str,
    as_of: str | None = None,
    tol: float = 1e-4,
) -> pd.DataFrame:
    """Return per-ticker MISMATCHES between the trade-derived net shares for
    ``account_id`` and the broker position quantities for ``pseudonym``.

    ``positions_df`` is the parsed Fidelity positions frame (columns include
    ``pseudonym``, ``symbol``, ``quantity``). SPAXX/cash rows, whose "quantity"
    is a dollar balance rather than a share count, are excluded from the compare.

    Returns a DataFrame with columns [ticker, trade_shares, position_qty, diff],
    EMPTY when every ticker balances within ``tol``. Empty == reconciled.
    """
    as_of = as_of or date.today().isoformat()

    holdings = get_holdings_on_date(as_of, account_id=account_id)
    trade_sh = (
        holdings["net_shares"] if not holdings.empty
        else pd.Series(dtype=float, name="net_shares")
    )

    pos = positions_df[positions_df["pseudonym"] == pseudonym]
    pos_qty = (
        pos[pos["symbol"] != "SPAXX"]
        .groupby("symbol")["quantity"].sum()
        if not pos.empty else pd.Series(dtype=float)
    )

    tickers = sorted(set(trade_sh.index) | set(pos_qty.index) - {"SPAXX"})
    rows = []
    for t in tickers:
        if t == "SPAXX":
            continue
        ts = float(trade_sh.get(t, 0.0))
        pq = float(pos_qty.get(t, 0.0))
        if abs(ts - pq) > tol:
            rows.append({
                "ticker":       t,
                "trade_shares": ts,
                "position_qty": pq,
                "diff":         ts - pq,
            })
    return pd.DataFrame(rows, columns=["ticker", "trade_shares", "position_qty", "diff"])


def account_shares_reconciled(
    account_id: int,
    positions_df: pd.DataFrame,
    pseudonym: str,
    as_of: str | None = None,
    tol: float = 1e-4,
) -> bool:
    """True when the trade ledger and the broker positions agree for the account."""
    return reconcile_account_shares(
        account_id, positions_df, pseudonym, as_of=as_of, tol=tol
    ).empty
