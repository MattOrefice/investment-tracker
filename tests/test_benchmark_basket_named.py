"""The benchmark regression's methodology names the basket get_custom_blended_series buys,
read from benchmarks.blended_basket (#739, decided by the owner on November 19, 2026).

Its R_b bullet typed ten legs. Both committed books' baskets have twelve: it lacked EFV,
IQLT and SCZ, the benchmarks of the three sleeves Phase 39 split out of developed
international, and it named BIL, which Cash / SPAXX's zero target buys none of. Pinned:
  * in each committed book the bullet names every leg the basket buys, in the policy's
    order, and nothing else;
  * the series and the bullet read one basket: a basket changed under both moves both,
    and a sleeve with no target buys nothing in the series and is not named;
  * inside a lock the bullet names the lock's policy, not the book's since.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pandas as pd
import pytest

from src import benchmarks, cache
from src.factors import build_benchmark_methodology
from tests.conftest import point_at_frozen_book

R_B = "R_b: daily custom blended SAA benchmark return (target-weight basket: "
# Both committed books' policy: twelve weighted sleeves in sort order, Cash / SPAXX at 0.
COMMITTED_LEGS = "SPY, QUAL, IWD, IWM, EFA, IQLT, EFV, SCZ, EEM, IEF, TIP, 60% VNQ + 40% DBC"


def _named() -> str:
    bullet = build_benchmark_methodology(None)[0]
    assert R_B in bullet
    return bullet.split(R_B, 1)[1].split("). ", 1)[0]


def _weighted_benchmarks(book) -> "list[str]":
    """Each weighted sleeve's benchmark spec, read from the book's own table."""
    with closing(sqlite3.connect(f"file:{book}?mode=ro", uri=True)) as c:
        return [r[0] for r in c.execute(
            "SELECT benchmark_ticker FROM asset_classes WHERE parent_id IS NOT NULL "
            "AND target_weight > 0 ORDER BY sort_order")]


def test_the_demo_book_names_every_leg_its_basket_buys(use_demo_db):
    from src import db
    assert len(_weighted_benchmarks(db.DB_PATH)) == 12
    assert _named() == COMMITTED_LEGS
    assert "BIL" not in _named()


def test_the_frozen_book_names_every_leg_its_basket_buys(tmp_path, monkeypatch):
    book = point_at_frozen_book(monkeypatch, tmp_path)
    specs = _weighted_benchmarks(book)
    assert len(specs) == 12 and "VNQ (60%) + DBC (40%)" in specs
    assert _named() == COMMITTED_LEGS


# A basket no book holds: a blend in shares the books never use, two sleeves on one
# benchmark, and a sleeve at zero.
_BASKET = [("Sleeve A", 0.25, [("SPY", 1.0)]),
           ("Sleeve B", 0.5, [("VNQ", 0.25), ("DBC", 0.75)]),
           ("Sleeve C", 0.0, [("BIL", 1.0)]),
           ("Sleeve D", 0.25, [("SPY", 1.0)])]


def test_the_series_and_the_bullet_read_one_basket(use_demo_db, monkeypatch):
    """Under _BASKET the bullet names each leg it buys once, and the series buys them at
    its weights: SPY, half the basket over two sleeves, up 10% moves the blend 5%, and BIL
    doubling moves it not at all."""
    monkeypatch.setattr(benchmarks, "blended_basket", lambda: _BASKET)
    assert _named() == "SPY, 25% VNQ + 75% DBC"

    start, end = "2026-01-02", "2026-01-09"
    days = pd.date_range(start, end, freq="D")
    moves = {"SPY": 1.10, "BIL": 2.0}
    asked: list = []

    def priced(ticker, start_date, end_date, col="adj_close"):
        asked.append(ticker)
        s = pd.Series(100.0, index=days)
        s.iloc[-1] = 100.0 * moves.get(ticker, 1.0)
        s.attrs["benchmark_gap_bounds"] = []
        return s

    monkeypatch.setattr(benchmarks, "_get_price_series", priced)
    series = benchmarks._fresh_basket_series(start, end)
    assert sorted(asked) == ["BIL", "DBC", "SPY", "VNQ"]
    assert series.iloc[0] == pytest.approx(1.0)
    assert series.iloc[-1] == pytest.approx(1.05)


def test_inside_a_lock_the_bullet_names_the_locks_policy(tmp_path, monkeypatch):
    """The frozen book's Q2 2026 lock, through snapshot_price_context. A benchmark changed
    in the book after the lock moves the live bullet and not the lock's."""
    book = point_at_frozen_book(monkeypatch, tmp_path)
    with closing(sqlite3.connect(book)) as c:
        c.execute("UPDATE asset_classes SET benchmark_ticker = 'IVLU' "
                  "WHERE name = 'International Large Value'")
        c.commit()
    assert _named() == COMMITTED_LEGS.replace("EFV", "IVLU")
    snap = cache.get_quarter_snapshot("2026Q2")[0]
    assert snap is not None
    with cache.snapshot_price_context(snap):
        assert _named() == COMMITTED_LEGS
