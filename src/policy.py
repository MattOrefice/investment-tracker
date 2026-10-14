"""The book's policy, as a quarterly report reads it (#525).

The policy is everything a report reads from ``asset_classes`` and from the
ticker-to-sleeve mapping (``securities.asset_class_id``): each asset class's name, its
parent, its target weight, its tolerance band, its benchmark spec and its print order,
and the asset class each ticker belongs to. A rationale is wording and is not part of it:
the pages read rationales live.

A quarter lock holds the policy as an input, like the fund durations
(``input_lock.POLICY``). Inside the lock every reader here serves the held policy, so a
target, a band, a benchmark or a ticker's sleeve changed after the quarter locked does
not move its report. Outside a lock they read the book, so the live pages show the
book's current policy.

A lock holds the policy as it stood when the lock was taken. The eleven locks committed
before locks held it (six in data/demo.db, five in the frozen test book) were given the
policy as it stood on the day it was recorded, marked as recorded after the quarter
locked (tools/hold_policy_in_demo_locks.py), and each cover says so
(cache.policy_recorded_note).
"""
from __future__ import annotations

from typing import Optional

from src.db import get_connection

KIND = "policy"
# The asset_classes columns the policy holds, in the order a lock stores them.
COLUMNS = ("asset_class_id", "name", "parent_id", "target_weight", "tolerance_band",
           "sort_order", "benchmark_ticker")


def read_book(conn=None) -> dict:
    """The book's policy now: {"asset_classes": [row, ...] in asset_class_id order,
    "securities": [(ticker, asset_class_id), ...] in the table's own order}. ``conn``,
    an open connection to a book; the running book's when omitted."""
    if conn is None:
        with get_connection() as own:
            return read_book(own)
    classes = [dict(zip(COLUMNS, row)) for row in conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM asset_classes ORDER BY asset_class_id")]
    securities = [(row[0], row[1]) for row in conn.execute(
        "SELECT ticker, asset_class_id FROM securities ORDER BY rowid")]
    return {"asset_classes": classes, "securities": securities}


def encode(policy: dict, held_on: str, recorded_after_lock: bool) -> dict:
    """A policy as a lock stores it. ``held_on`` is the day it was read;
    ``recorded_after_lock`` is True when it was given to a lock already taken, so it is
    the policy as it stood that day, not when the quarter locked."""
    return {"kind": KIND, "held_on": held_on, "recorded_after_lock": recorded_after_lock,
            "columns": list(COLUMNS),
            "asset_classes": [[row[c] for c in COLUMNS] for row in policy["asset_classes"]],
            "securities": [[ticker, cls] for ticker, cls in policy["securities"]]}


def decode(payload: dict) -> dict:
    """A stored policy as the readers here serve it: read_book's shape, with the day it
    was held and whether it was recorded after the lock."""
    columns = payload["columns"]
    return {"held_on": payload["held_on"],
            "recorded_after_lock": payload["recorded_after_lock"],
            "asset_classes": [dict(zip(columns, row)) for row in payload["asset_classes"]],
            "securities": [(ticker, cls) for ticker, cls in payload["securities"]]}


def current() -> dict:
    """The policy a reader is served: the lock's inside a quarter lock that holds one,
    else the book's."""
    from src.input_lock import POLICY, locked
    held = locked(POLICY)
    return held if held is not None else read_book()


def asset_classes() -> "list[dict]":
    """Every asset class, in asset_class_id order (the table's own)."""
    return current()["asset_classes"]


def sleeves() -> "list[dict]":
    """The asset classes with a parent (the sleeves), in asset_class_id order."""
    return [r for r in asset_classes() if r["parent_id"] is not None]


def ticker_classes() -> "list[tuple[str, int]]":
    """(ticker, asset_class_id) for every security, in the table's own order."""
    return current()["securities"]


def sleeve_of_ticker() -> "dict[str, str]":
    """{ticker: the name of its asset class}, as the securities-to-asset_classes join
    gives it: a ticker whose class is not in the policy is left out."""
    names = {r["asset_class_id"]: r["name"] for r in asset_classes()}
    return {ticker: names[cls] for ticker, cls in ticker_classes() if cls in names}


def nulls_first(value: "Optional[float]") -> tuple:
    """A sort key that orders as SQLite's ORDER BY does: NULL before every value."""
    return (0, 0) if value is None else (1, value)
