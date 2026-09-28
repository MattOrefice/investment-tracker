"""A DRIP lot is a real purchase: priced at the raw close on the day it executed, and
never moved by a later distribution (#406 item 12).

compute_drip_lots used to price each lot at adj_close. dividend_adjusted re-anchors
adj_close on every stored dividend, so each new distribution rescaled every earlier
lot, and a book rebuilt on that basis would drift at the next close-out and restate
every locked quarter each time. No return calculation reads a DRIP lot (the value
series and attribution value the non-DRIP shares at adj_close), so nothing needed
the adjusted basis. Pinned here, through the write path (backfill_all_drip_lots over
a real schema, offline):

  * price basis: a lot's price is the stored raw close on its date;
  * stability: adding one later distribution leaves every earlier lot unchanged;
  * the holiday rule: a pay date with no close reinvests at the first stored close
    on or after it, dated there, and no close yet on or after it means no lot.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

import src.db as db
import src.prices as prices
from src.drip import backfill_all_drip_lots, compute_drip_lots

GOOD_FRIDAY = date(2026, 4, 3)          # NYSE closed; a pay date can land on it
D1 = ("2026-02-02", 0.30)               # Monday: pays Wednesday 2026-02-04
D2 = ("2026-03-02", 0.30)               # Monday: pays Wednesday 2026-03-04
D3 = ("2026-04-01", 0.30)               # Wednesday: pays Good Friday, reinvests Apr 6


def _refuse(*_a, **_k):
    raise OSError("offline: these tests read the fixture book only")


@pytest.fixture
def book(tmp_path, monkeypatch):
    """One account holding 100 shares of TST from 2026-01-02, with a raw close on every
    weekday but Good Friday, and the dividends each test adds."""
    path = tmp_path / "book.db"
    con = sqlite3.connect(path)
    con.executescript(db.SCHEMA)
    con.execute("INSERT INTO accounts (name, type, tax_treatment, managed_by, display_name) "
                "VALUES ('Paper', 'taxable', 'taxable', 'self', 'Paper')")
    acct = con.execute("SELECT account_id FROM accounts").fetchone()[0]
    con.execute("INSERT INTO asset_classes (name, target_weight) VALUES ('Test', 1.0)")
    con.execute("INSERT INTO securities (ticker, name, asset_class_id) VALUES ('TST', 'Test "
                "fund', (SELECT asset_class_id FROM asset_classes))")
    con.execute("INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
                "lot_source) VALUES (?, 'TST', '2026-01-02', 'Buy', 100, 50, 'initial')", (acct,))
    d, i = date(2026, 1, 2), 0
    while d <= date(2026, 4, 30):
        if d.weekday() < 5 and d != GOOD_FRIDAY:
            con.execute("INSERT INTO prices (ticker, price_date, close) VALUES ('TST', ?, ?)",
                        (d.isoformat(), 50.0 + 0.05 * i))
            i += 1
        d += timedelta(days=1)
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    monkeypatch.setattr(prices._SESSION, "get", _refuse)
    return path, acct


def _add_dividends(path, *divs):
    con = sqlite3.connect(path)
    con.executemany("INSERT INTO dividends (ticker, ex_date, amount) VALUES ('TST', ?, ?)", divs)
    con.commit()
    con.close()


def _derive(path, acct) -> "dict[str, tuple[float, float]]":
    """Remove the lots, derive them again through the write path, and read them back."""
    con = sqlite3.connect(path)
    con.execute("DELETE FROM trades WHERE lot_source = 'drip'")
    con.commit()
    con.close()
    backfill_all_drip_lots("2026-01-02", "2026-04-30", account_id=acct)
    con = sqlite3.connect(path)
    try:
        return {d: (s, p) for d, s, p in con.execute(
            "SELECT trade_date, shares, price FROM trades WHERE lot_source = 'drip' "
            "ORDER BY trade_date")}
    finally:
        con.close()


def _close(path, day: str) -> float:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT close FROM prices WHERE ticker = 'TST' AND price_date = ?",
                           (day,)).fetchone()[0]
    finally:
        con.close()


def _adjusted(day: str) -> float:
    frame = prices.get_prices("TST", "2026-01-02", "2026-04-30")
    return float(prices.dividend_adjusted("TST", frame)[date.fromisoformat(day)])


def test_a_lot_is_priced_at_the_raw_close_on_its_date(book):
    path, acct = book
    _add_dividends(path, D1, D2, D3)
    lots = _derive(path, acct)
    assert sorted(lots) == ["2026-02-04", "2026-03-04", "2026-04-06"]
    for day, (_shares, price) in lots.items():
        assert price == _close(path, day), day
    # The premise that makes this a test: on the first two lots' dates the adjusted
    # close is NOT the raw close, because later dividends are stored.
    for day in ("2026-02-04", "2026-03-04"):
        assert abs(_adjusted(day) - _close(path, day)) > 0.01, day


def test_a_later_distribution_leaves_every_earlier_lot_unchanged(book):
    path, acct = book
    _add_dividends(path, D1, D2)
    before = _derive(path, acct)
    adjusted_before = _adjusted("2026-02-04")
    _add_dividends(path, D3)
    after = _derive(path, acct)
    assert sorted(after) == sorted(before) + ["2026-04-06"]
    for day in before:
        assert after[day] == before[day], day
    # The premise: the new dividend DID move the adjusted close of the earlier dates,
    # so a lot priced from it would have moved.
    assert abs(_adjusted("2026-02-04") - adjusted_before) > 0.01


def test_a_pay_date_with_no_close_reinvests_at_the_next_close(book):
    path, acct = book
    _add_dividends(path, D1, D2, D3)
    lots = _derive(path, acct)
    assert "2026-04-03" not in lots, "no lot on a day the market was closed"
    shares, price = lots["2026-04-06"]
    assert price == _close(path, "2026-04-06")
    assert price != _close(path, "2026-04-02"), "not the session before the cash arrived"


def test_no_close_yet_on_or_after_the_pay_date_means_no_lot():
    """The cash has not arrived in the data: nothing to reinvest at, so no lot until a
    close exists. Pure, on compute_drip_lots."""
    initial = pd.Series([100.0], index=[date(2026, 1, 2)])
    dists = pd.DataFrame([{"ex_date": date(2026, 4, 1), "dividend_per_share": 0.3}])
    history = pd.Series({date(2026, 4, 1): 50.0, date(2026, 4, 2): 50.5})
    assert compute_drip_lots("TST", initial, dists, history) == []
