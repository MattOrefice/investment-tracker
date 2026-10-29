"""#369's PR 2: the raw-close value series and its income line, and the rule that picks it
(decided by the owner on October 27 and 28, 2026).

Live reads value the book under the raw-close rule. That is every share held, DRIP lots
included, at that day's close; SPAXX at BIL's total return, each SPAXX trade and each
dividend swept into it entering as units bought at that day's level; and an income line,
each dividend counting from its ex-date. A lock reads its own marker, and a lock with none,
as every existing lock is, reads as adjusted levels. A raw-close lock reads the ledger it
holds.

The independent reference is tests/first_principles.py's raw-close Book. It is built from
the trades table, the stored closes and the stored dividends, and calls neither the price
layer nor src.holdings. Every book is a scratch copy, offline.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import cache, holdings
from tests import first_principles as fp
from tests.conftest import (FROZEN_BOOK, FROZEN_TODAY, block_network,
                            lock_with_the_gap_fetch_off, on_adjusted_levels, pin_today,
                            unpin_leftovers)

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
PERSONAL_BOOK = ROOT / "data" / "tracker.db"
# Each book with the day its reads are taken on: the frozen book's next day, a day after
# demo.db's last close, and the real calendar for the personal book.
BOOKS = {"frozen": (FROZEN_BOOK, FROZEN_TODAY), "demo": (DEMO_DB, date(2026, 10, 1)),
         "personal": (PERSONAL_BOOK, None)}
WINDOWS = ("1M", "3M", "YTD", "1Y", "SI")


def _point(mp, tmp_path: Path, source: Path, today) -> Path:
    """A scratch copy of ``source``, read offline with no gap fetch, today pinned."""
    import src.db as db
    import src.prices as prices
    copy = tmp_path / source.name
    shutil.copyfile(source, copy)
    os.chmod(copy, 0o644)
    block_network(mp)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices, "_GAP_FETCH", False)
    prices._reset_trailing_memo()
    lock_with_the_gap_fetch_off(mp)
    if today is not None:
        pin_today(mp, today)
    return copy


@pytest.fixture(params=sorted(BOOKS))
def book(request, tmp_path):
    source, today = BOOKS[request.param]
    if not source.exists():
        pytest.skip(f"{source.name} is absent (CI, a fresh clone)")
    with pytest.MonkeyPatch.context() as mp:
        _point(mp, tmp_path, source, today)
        try:
            yield request.param
        finally:
            unpin_leftovers()


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _series_and_value(day: str) -> "tuple[pd.Series, float]":
    acct = holdings.get_portfolio_account_id()
    inception = holdings.get_inception_date(account_id=acct)
    pv = holdings.get_portfolio_value_series(inception, day, account_id=acct)
    return pv, holdings.current_market_value_with_coverage(day).value


# ── which rule ───────────────────────────────────────────────────────────────

def test_live_reads_are_on_the_raw_close_rule_and_a_lock_reads_its_own_marker(tmp_path):
    assert cache.valuation_in_force() == (cache.RAW_CLOSE_RULE, None)
    with on_adjusted_levels():
        assert cache.valuation_in_force() == (cache.ADJUSTED_LEVELS_RULE, None)
    assert cache.valuation_in_force() == (cache.RAW_CLOSE_RULE, None)
    with pytest.MonkeyPatch.context() as mp:
        _point(mp, tmp_path, DEMO_DB, BOOKS["demo"][1])
        try:
            snap = cache.get_quarter_snapshot("2026Q3")[0]
            assert snap is not None and snap.valuation_rule is None, (
                "premise: a committed lock carries no marker")
            with cache.snapshot_price_context(snap):
                assert cache.valuation_in_force() == (cache.ADJUSTED_LEVELS_RULE, None)
            with cache.snapshot_price_context(snap.adj_close):
                assert cache.valuation_in_force() == (cache.ADJUSTED_LEVELS_RULE, None)
            assert cache.valuation_in_force() == (cache.RAW_CLOSE_RULE, None)
        finally:
            unpin_leftovers()


# ── one basis ────────────────────────────────────────────────────────────────

def test_the_value_series_ends_at_the_current_value_in_every_book(book):
    """The series' latest value is the Performance page's current value, which holds the
    income line, and both are the independent book's raw-close value."""
    today = _today()
    pv, current = _series_and_value(today)
    assert float(pv.iloc[-1]) == pytest.approx(current, abs=0.005), (book, pv.iloc[-1], current)
    ref = fp.Book(account_id=holdings.get_portfolio_account_id())
    assert ref.rule == cache.RAW_CLOSE_RULE
    parts = ref.current_value_parts(today)
    assert sum(parts.values()) == pytest.approx(current, abs=0.005), (book, parts, current)
    assert holdings.dividend_income(today) == pytest.approx(parts["income"], abs=0.005)
    if book == "personal":
        # The personal book takes its dividends in cash: they sit in its income line.
        assert holdings.dividend_income(today) > 0.0


@pytest.mark.parametrize("name", ["frozen", "demo"])
def test_the_current_value_holds_a_dividend_until_its_drip_lot_is_paid_from_it(name, tmp_path):
    """In a DRIP book a dividend is income from its ex-date, held at its amount, and its
    DRIP lot is paid from it: the line holds it on the ex-date and not after the lot."""
    source, today = BOOKS[name]
    with pytest.MonkeyPatch.context() as mp:
        copy = _point(mp, tmp_path, source, today)
        try:
            con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            lot_day, ticker = con.execute(
                "SELECT trade_date, ticker FROM trades WHERE lot_source = 'drip' "
                "ORDER BY trade_date, trade_id LIMIT 1").fetchone()
            ex, amount = con.execute(
                "SELECT ex_date, amount FROM dividends WHERE ticker = ? AND ex_date < ? "
                "ORDER BY ex_date DESC LIMIT 1", (ticker, lot_day)).fetchone()
            shares, = con.execute(
                "SELECT SUM(CASE WHEN LOWER(action) = 'buy' THEN shares ELSE -shares END) "
                "FROM trades WHERE ticker = ? AND trade_date < ?", (ticker, ex)).fetchone()
            lot_dollars, = con.execute(
                "SELECT SUM(shares * price + COALESCE(fees, 0)) FROM trades WHERE ticker = ? "
                "AND trade_date = ? AND lot_source = 'drip'", (ticker, lot_day)).fetchone()
            all_lots, = con.execute(
                "SELECT SUM(shares * price + COALESCE(fees, 0)) FROM trades WHERE trade_date = ? "
                "AND lot_source = 'drip'", (lot_day,)).fetchone()
            others, = con.execute(
                "SELECT COUNT(*) FROM dividends WHERE ex_date > ? AND ex_date <= ? AND ticker IN "
                "(SELECT ticker FROM trades WHERE trade_date < ?)", (ex, lot_day, lot_day)).fetchone()
            con.close()
            income = shares * amount
            assert income > 0 and lot_dollars == pytest.approx(income, abs=0.01), (
                "premise: the lot costs its dividend's income")
            ref = fp.Book(account_id=1)
            for day in (ex, lot_day):
                assert holdings.dividend_income(day) == pytest.approx(
                    ref.current_value_parts(day)["income"], abs=1e-9), day
                pv, current = _series_and_value(day)
                assert float(pv.iloc[-1]) == pytest.approx(current, abs=0.005), day
                assert ref.value(day) == pytest.approx(float(pv.iloc[-1]), abs=1e-6), day
            # On its ex-date the line holds the dividend at its amount. Its lot is paid from
            # it: with no other dividend going ex in between, the line falls by that day's
            # lots, this one among them.
            assert holdings.dividend_income(ex) >= income - 1e-9
            assert others == 0, "premise: no other held fund goes ex before the lot"
            drop = holdings.dividend_income(ex) - holdings.dividend_income(lot_day)
            assert all_lots >= lot_dollars and drop == pytest.approx(all_lots, abs=1e-9), drop
        finally:
            unpin_leftovers()


# ── SPAXX ────────────────────────────────────────────────────────────────────

SPAXX_DAY = "2026-06-01"


def test_a_spaxx_buy_after_inception_books_no_gain_on_its_day(tmp_path):
    """A SPAXX buy enters at its dollars, as units bought at SPAXX's level that day, so it
    earns BIL's total return from its own date (decided by the owner on October 28, 2026).
    No book has such a trade today; this one is staged on a copy of the frozen book. Valued
    at BIL's level from inception, as every SPAXX share was before, it would add its
    dollars × that level, and book the difference as that day's gain."""
    from src.returns import twr_index
    day = pd.Timestamp(SPAXX_DAY)
    reads = {}
    for staged in (False, True):
        with pytest.MonkeyPatch.context() as mp:
            (tmp_path / str(staged)).mkdir()
            copy = _point(mp, tmp_path / str(staged), FROZEN_BOOK, FROZEN_TODAY)
            try:
                if staged:
                    con = sqlite3.connect(copy)
                    with con:
                        assert con.execute(
                            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, "
                            "price, fees, notes, lot_source) VALUES (1, 'SPAXX', ?, 'Buy', 100, "
                            "1.0, 0, '#369 staged SPAXX buy', 'Manual')", (SPAXX_DAY,)).rowcount == 1
                    con.close()
                acct = holdings.get_portfolio_account_id()
                inception = holdings.get_inception_date(account_id=acct)
                today = _today()
                pv = holdings.get_portfolio_value_series(inception, today, account_id=acct)
                cf = holdings.get_external_cashflow_series(inception, today, account_id=acct)
                level, _st = holdings._spaxx_levels(acct, today, pv.index)
                reads[staged] = {"pv": pv, "cf": cf.reindex(pv.index).fillna(0.0),
                                 "level": level, "current": holdings.get_current_market_value(today),
                                 "spaxx_income": holdings.spaxx_modeled_income(today),
                                 "ref": fp.Book(account_id=acct).value(today)}
            finally:
                unpin_leftovers()
    before, after = reads[False], reads[True]
    at = float(after["level"][day])
    assert at > 1.001, f"premise: SPAXX's level has grown since inception ({at})"
    assert float(after["pv"][day] - before["pv"][day]) == pytest.approx(100.0, abs=1e-9)
    assert float(after["cf"][day] - before["cf"][day]) == pytest.approx(100.0, abs=1e-12)
    step = {}
    for k, r in reads.items():
        index = twr_index(r["pv"], r["cf"])
        step[k] = float(index[day] / index.shift(1)[day])
    assert step[True] == pytest.approx(step[False], abs=1e-12), step
    last = after["pv"].index[-1]
    grown = 100.0 * float(after["level"][last]) / at
    assert float(after["pv"][last] - before["pv"][last]) == pytest.approx(grown, abs=1e-9)
    assert after["current"] - before["current"] == pytest.approx(grown, abs=0.011)
    assert after["ref"] - before["ref"] == pytest.approx(grown, abs=1e-9)
    # SPAXX's modeled income above its $1.00 NAV is what the units earned from that day.
    assert after["spaxx_income"] - before["spaxx_income"] == pytest.approx(grown - 100.0, abs=1e-9)


# ── a raw-close lock ─────────────────────────────────────────────────────────

def test_a_raw_close_lock_reads_the_ledger_it_holds(tmp_path):
    """A Q4 2026 lock, on a copy of demo.db carried through the quarter with today January 4,
    2027, is on the raw-close rule and holds the ledger. Inside it the value series, the
    flows, the holdings and the current value read that ledger: a DRIP lot removed and a
    buy added in the trades table afterwards move live reads and nothing the lock reads.
    A Q3 2026 lock taken the same day is on adjusted levels and holds no ledger."""
    from tests.test_valuation_rule_marker import DEMO_DB as SOURCE
    from tests.test_valuation_rule_marker import JAN_4_2027, _extend_through_q4, _point_at
    copy = tmp_path / "demo.db"
    shutil.copyfile(SOURCE, copy)
    os.chmod(copy, 0o644)
    _extend_through_q4(copy)
    end = "2026-12-31"
    with pytest.MonkeyPatch.context() as mp:
        _point_at(mp, copy)
        pin_today(mp, JAN_4_2027)
        try:
            snap, _ = cache.capture_quarter_snapshot("2026Q4")
            q3, _ = cache.capture_quarter_snapshot("2026Q3")
            assert snap.valuation_rule == cache.RAW_CLOSE_RULE and snap.ledger is not None
            assert q3.valuation_rule == cache.ADJUSTED_LEVELS_RULE and q3.ledger is None

            def reads():
                acct = holdings.get_portfolio_account_id()
                inception = holdings.get_inception_date(account_id=acct)
                return (holdings.get_portfolio_value_series(inception, end, account_id=acct),
                        holdings.get_external_cashflow_series(inception, end, account_id=acct),
                        holdings.get_holdings_on_date(end, account_id=acct),
                        holdings.get_current_market_value(end))

            with cache.snapshot_price_context(snap):
                assert cache.valuation_in_force()[0] == cache.RAW_CLOSE_RULE
                locked = reads()
            live = reads()
            con = sqlite3.connect(copy)
            with con:
                assert con.execute(
                    "DELETE FROM trades WHERE trade_id = (SELECT MAX(trade_id) FROM trades "
                    "WHERE lot_source = 'drip')").rowcount == 1
                assert con.execute(
                    "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
                    "fees, notes, lot_source) VALUES (1, 'VOO', '2026-11-20', 'Buy', 0.2, 600, "
                    "0, 'test', 'Manual')").rowcount == 1
            con.close()
            with cache.snapshot_price_context(snap):
                after = reads()
            moved = reads()
            with cache.snapshot_price_context(q3):
                assert cache.valuation_in_force() == (cache.ADJUSTED_LEVELS_RULE, None)
        finally:
            unpin_leftovers()
    pd.testing.assert_series_equal(after[0], locked[0])
    pd.testing.assert_series_equal(after[1], locked[1])
    pd.testing.assert_frame_equal(after[2], locked[2])
    assert after[3] == locked[3]
    assert not moved[0].equals(live[0]) and not moved[1].equals(live[1])
    assert moved[3] != live[3]


# ── the attribution check, on one basis since PR 3 ───────────────────────────

@pytest.mark.parametrize("name", ["frozen", "demo"])
def test_the_attribution_check_reads_zero_against_the_raw_close_twr(name, tmp_path):
    """On every window of a committed book (none holds a flow after inception), ex-cash
    Brinson-Fachler plus cash drag equals the book's raw-close TWR, the Stage 2 tile's, to
    0.00 bps: both are on the raw-close rule since #369's PR 3 (decided by the owner on
    October 28 and 29, 2026). On adjusted levels, the rule of every lock taken before the
    marker, the two still agree with each other, and at least one window is over the check's
    0.5 bps between the two rules, so the comparison can see a basis it does not share."""
    from src.attribution import brinson_fachler_period
    from src.returns import clamped_period_bounds, period_return
    source, today = BOOKS[name]
    gaps, adj_gaps, apart = {}, {}, {}
    with pytest.MonkeyPatch.context() as mp:
        _point(mp, tmp_path, source, today)
        try:
            acct = holdings.get_portfolio_account_id()
            inception = holdings.get_inception_date(account_id=acct)
            c = holdings.last_settled_price_date(inception, _today())

            def series():
                pv = holdings.get_portfolio_value_series(inception, c, account_id=acct)
                cf = holdings.get_external_cashflow_series(inception, c, account_id=acct)
                return pv, cf.reindex(pv.index).fillna(0.0)

            def bridged(start, end):
                bf = brinson_fachler_period(start, end, account_id=acct)
                return float((bf["w_p"] * bf["r_p"]).sum()) + float(bf.attrs.get("cash_drag", 0.0))

            with on_adjusted_levels():
                pv_adj, cf_adj = series()
                bounds = {w: clamped_period_bounds(w, c, inception)[:2] for w in WINDOWS}
                bf_adj = {w: bridged(*bounds[w]) for w in WINDOWS}
            pv_raw, cf_raw = series()
            assert (cf_raw.iloc[1:] == 0.0).all(), "premise: no flow after inception"
            for w in WINDOWS:
                bf_raw = bridged(*bounds[w])
                gaps[w] = (bf_raw - period_return("daily", pv_raw, cf_raw, w)) * 10_000
                adj_gaps[w] = (bf_adj[w] - period_return("daily", pv_adj, cf_adj, w)) * 10_000
                apart[w] = (bf_raw - bf_adj[w]) * 10_000
        finally:
            unpin_leftovers()
    assert all(abs(g) < 0.005 for g in gaps.values()), gaps
    assert all(abs(g) < 0.005 for g in adj_gaps.values()), adj_gaps
    assert any(abs(g) >= 0.5 for g in apart.values()), apart


# ── a dividend taken in cash, and who is entitled to one ─────────────────────

def test_a_dividend_no_drip_lot_reinvests_goes_into_spaxx_at_that_days_level(tmp_path):
    """A fund the ledger holds no DRIP lots for takes its dividends in cash, as the personal
    book does: each goes into SPAXX on its ex-date as units bought at that day's level, and
    earns BIL's total return from then. The committed books reinvest every dividend, so a
    frozen-book copy has VGIT's DRIP lots removed; the independent book values the same."""
    with pytest.MonkeyPatch.context() as mp:
        copy = _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            con = sqlite3.connect(copy)
            with con:
                assert con.execute("DELETE FROM trades WHERE ticker = 'VGIT' "
                                   "AND lot_source = 'drip'").rowcount == 14
            con.close()
            today = _today()
            pv, current = _series_and_value(today)
            ref = fp.Book(account_id=1)
            parts = ref.current_value_parts(today)
            income = holdings.dividend_income(today)
        finally:
            unpin_leftovers()
    assert float(pv.iloc[-1]) == pytest.approx(current, abs=0.005)
    assert ref.value(today) == pytest.approx(float(pv.iloc[-1]), abs=1e-6)
    assert income == pytest.approx(parts["income"], abs=1e-9)
    assert income > 1.0, "VGIT's monthly dividends since inception sit in SPAXX"


@pytest.mark.parametrize("day, staged", [("2026-06-26", "VOO"), ("2026-06-25", "VOO")],
                         ids=["on-the-ex-date", "the-day-before"])
def test_a_dividend_is_income_on_the_shares_held_the_day_before_its_ex_date(
        tmp_path, day, staged):
    """VOO went ex on June 26, 2026. A buy on the ex-date is not entitled to that dividend,
    so the income line does not move; a buy the day before is, by its shares × the amount."""
    reads = {}
    for planted in (False, True):
        with pytest.MonkeyPatch.context() as mp:
            (tmp_path / str(planted)).mkdir()
            copy = _point(mp, tmp_path / str(planted), FROZEN_BOOK, FROZEN_TODAY)
            try:
                if planted:
                    con = sqlite3.connect(copy)
                    with con:
                        close, = con.execute("SELECT close FROM prices WHERE ticker = ? AND "
                                             "price_date = ?", (staged, day)).fetchone()
                        assert con.execute(
                            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, "
                            "price, fees, notes, lot_source) VALUES (1, ?, ?, 'Buy', 1.0, ?, 0, "
                            "'#369 staged buy', 'Manual')", (staged, day, close)).rowcount == 1
                    con.close()
                reads[planted] = holdings.dividend_income("2026-06-26")
            finally:
                unpin_leftovers()
    moved = reads[True] - reads[False]
    assert moved == pytest.approx(0.0 if day == "2026-06-26" else 1.962, abs=1e-9), moved
