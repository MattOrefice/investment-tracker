"""Fidelity-transaction import helpers: composite-key dedup + ticker validation.

These keep the import safe for CUMULATIVE Fidelity exports (each month's export
re-includes prior trades) and FK-safe against the trades→securities constraint.
"""
from __future__ import annotations

from collections import Counter


def fingerprint(trade_date: str, ticker: str, action: str, shares, price, account_id) -> str:
    """Deterministic composite key for one trade lot, SCOPED TO ITS ACCOUNT.

    Fixed precision (6dp shares, 4dp price — finer than Fidelity reports) so a
    re-exported lot hashes to the same key as the already-logged one. Amount is
    not stored in the trades table, so it cannot participate in the key.

    ``account_id`` leads the key so that the SAME lot in two different accounts —
    e.g. buy 10 VOO in the taxable book and 10 VOO in the Roth on the same day at
    the same price (the exact shape of a cross-account rebalance) — hashes to two
    DISTINCT keys and both survive dedup. An account-blind key would silently drop
    the second one as a "duplicate", losing a real trade.
    """
    return (
        f"{int(account_id)}|{trade_date}|{str(ticker).upper()}|{str(action).title()}|"
        f"{float(shares):.6f}|{float(price):.4f}"
    )


def _existing_key(t: dict) -> str:
    return fingerprint(
        t["trade_date"], t["ticker"], t.get("action", ""), t["shares"], t["price"],
        t["account_id"],
    )


def dedupe_against_existing(
    parsed_lots: list[dict], existing_trades: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Split parsed lots into (to_import, already_logged) using a MULTISET match.

    A ``collections.Counter`` of existing keys is decremented as matches are
    consumed, so N identical already-logged lots skip exactly N parsed lots —
    and a genuinely-new (N+1)th identical lot is still imported. A plain set
    would wrongly drop real duplicate lots; the multiset preserves them.

    Both ``parsed_lots`` and ``existing_trades`` MUST carry an ``account_id`` —
    the key is account-scoped, so the same lot in different accounts does not
    collide. Each returned lot gains a ``fingerprint`` key (the composite string).
    """
    counts = Counter(_existing_key(t) for t in existing_trades)
    to_import: list[dict] = []
    already: list[dict] = []
    for lot in parsed_lots:
        key = fingerprint(
            lot["trade_date"], lot["ticker"], lot["action"], lot["shares"],
            lot["price"], lot["account_id"],
        )
        tagged = {**lot, "fingerprint": key}
        if counts.get(key, 0) > 0:
            counts[key] -= 1
            already.append(tagged)
        else:
            to_import.append(tagged)
    return to_import, already


def split_known_tickers(
    lots: list[dict], known_tickers
) -> tuple[list[dict], list[dict]]:
    """Split lots into (known, unknown) by membership in ``known_tickers``.

    MANDATORY before writing: trades.ticker is a FK to securities(ticker), and
    write_trades_batch writes the whole batch in one transaction — a single
    unknown ticker would fail and lose every lot. Unknown lots are reported,
    not written.
    """
    known = {str(t).upper() for t in known_tickers}
    ok: list[dict] = []
    unknown: list[dict] = []
    for lot in lots:
        (ok if str(lot["ticker"]).upper() in known else unknown).append(lot)
    return ok, unknown
