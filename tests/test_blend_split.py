"""Real Assets against its blended benchmark, stated as fact (audit item 15c).

The sleeve holds VNQ and PDBC, bought in equal dollars at inception, against a VNQ 60% /
DBC 40% benchmark: an overweight in the commodity fund that attribution reports as
selection. Aligning the benchmark would restate every locked quarter, so the SAA page
states the difference, derived from the trades and prices, and gives no reason for it.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import src.db as db
import src.prices as prices
from src.holdings import BlendLeg, blend_split, pair_legs
from src.prose_helpers import blend_split_sentence

ROOT = Path(__file__).resolve().parent.parent
DAY = "2026-07-20"          # the demo book's committed price frontier


@pytest.fixture
def demo(tmp_path, monkeypatch):
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    return copy


def test_real_assets_pairs_each_fund_with_its_leg(demo):
    legs = blend_split("Real Assets", DAY)
    assert [(l.holding, l.leg, l.leg_weight) for l in legs] == [
        ("VNQ", "VNQ", 0.6), ("PDBC", "DBC", 0.4)]
    assert sum(l.inception_weight for l in legs) == pytest.approx(1.0)
    assert sum(l.current_weight for l in legs) == pytest.approx(1.0)


def test_the_weights_are_the_trades_and_the_closes(demo):
    """Recomputed here from the tables, not from the function's own path."""
    import sqlite3
    con = sqlite3.connect(demo)
    first = con.execute("SELECT MIN(trade_date) FROM trades").fetchone()[0]
    bought = dict(con.execute(
        "SELECT ticker, SUM(shares * price) FROM trades WHERE trade_date = ? AND "
        "ticker IN ('VNQ', 'PDBC') AND lot_source != 'drip' GROUP BY ticker", (first,)))
    shares = dict(con.execute(
        "SELECT ticker, SUM(shares) FROM trades WHERE trade_date <= ? AND "
        "ticker IN ('VNQ', 'PDBC') GROUP BY ticker", (DAY,)))
    close = {t: con.execute("SELECT close FROM prices WHERE ticker = ? AND price_date <= ? "
                            "ORDER BY price_date DESC LIMIT 1", (t, DAY)).fetchone()[0]
             for t in ("VNQ", "PDBC")}
    con.close()
    value = {t: shares[t] * close[t] for t in close}
    legs = {l.holding: l for l in blend_split("Real Assets", DAY)}
    for t in ("VNQ", "PDBC"):
        assert legs[t].inception_weight == pytest.approx(bought[t] / sum(bought.values()))
        assert legs[t].current_weight == pytest.approx(value[t] / sum(value.values()))


@pytest.mark.parametrize("legs, held, pairs", [
    ([("A", 0.6), ("X", 0.4)], ["A", "Z"], {"A": "A", "X": "Z"}),
    ([("A", 0.6), ("X", 0.4)], ["B", "A"], {"A": "A", "X": "B"}),
    ([("A", 0.5), ("B", 0.5)], ["B", "A"], {"A": "A", "B": "B"}),
    ([("A", 1.0)], ["A"], None),
    ([("A", 0.5), ("X", 0.3), ("Y", 0.2)], ["A", "P", "Q"], None),
    ([("A", 0.6), ("X", 0.4)], ["A"], None),
])
def test_legs_pair_with_holdings_one_to_one(legs, held, pairs):
    assert pair_legs(legs, held) == pairs


def test_a_later_buy_does_not_move_the_inception_split(demo):
    """Inception is the portfolio's first day. A later discretionary buy moves today's
    split, never the one bought at inception."""
    import sqlite3
    before = {l.holding: l for l in blend_split("Real Assets", DAY)}
    con = sqlite3.connect(demo)
    acct = con.execute("SELECT account_id FROM trades WHERE ticker = 'PDBC' LIMIT 1").fetchone()[0]
    con.execute("INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
                "fees, lot_source) VALUES (?, 'PDBC', '2026-01-05', 'Buy', 4, 13.0, 0, 'Manual')",
                (acct,))
    con.commit()
    con.close()
    after = {l.holding: l for l in blend_split("Real Assets", DAY)}
    assert after["PDBC"].inception_weight == pytest.approx(before["PDBC"].inception_weight)
    assert after["PDBC"].current_weight > before["PDBC"].current_weight


def test_a_single_fund_benchmark_has_no_split(demo):
    import sqlite3
    con = sqlite3.connect(demo)
    sleeves = [r[0] for r in con.execute(
        "SELECT name FROM asset_classes WHERE parent_id IS NOT NULL AND name != 'Real Assets'")]
    con.close()
    assert len(sleeves) >= 11
    assert all(blend_split(s, DAY) is None for s in sleeves)


def test_the_demo_sentence(demo):
    s = blend_split_sentence(blend_split("Real Assets", DAY))
    assert s == ("The sleeve was bought VNQ 50% and PDBC 50% at inception and holds VNQ 45% "
                 "and PDBC 55% today, against a benchmark of VNQ 60% and DBC 40%. That is a "
                 "10-point overweight in PDBC against its DBC leg (15 today), which "
                 "attribution reports as selection.")


def _leg(h, l, w, i, c):
    return BlendLeg(h, l, w, i, c)


@pytest.mark.parametrize("legs, tail", [
    ([_leg("A", "A", 0.6, 0.6, 0.62), _leg("B", "X", 0.4, 0.4, 0.38)],
     " The inception split matches the benchmark's."),
    ([_leg("A", "A", 0.6, 0.5, 0.6), _leg("B", "X", 0.4, 0.5, 0.4)],
     " That is a 10-point overweight in B against its X leg (none today), which "
     "attribution reports as selection."),
    ([_leg("A", "A", 0.6, 0.5, 0.63), _leg("B", "X", 0.4, 0.5, 0.37)],
     " That is a 10-point overweight in B against its X leg (an underweight of 3 today), "
     "which attribution reports as selection."),
    ([_leg("A", "A", 0.5, 0.6, 0.6), _leg("B", "X", 0.5, 0.4, 0.4)],
     " That is a 10-point overweight in A against the benchmark's A weight (10 today), "
     "which attribution reports as selection."),
])
def test_the_sentence_at_its_edges(legs, tail):
    assert blend_split_sentence(legs).endswith(tail)


def test_three_legs_take_the_serial_comma():
    legs = [_leg("A", "A", 0.5, 0.4, 0.4), _leg("B", "B", 0.3, 0.3, 0.3),
            _leg("C", "C", 0.2, 0.3, 0.3)]
    assert "bought A 40%, B 30%, and C 30% at inception" in blend_split_sentence(legs)
