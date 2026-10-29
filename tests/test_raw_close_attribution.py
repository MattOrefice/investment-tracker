"""#369's PR 3: attribution and the sleeve weights on the raw-close rule, closing #701
(decided by the owner on October 28 and 29, 2026).

One sleeve valuation, read by Brinson-Fachler, the Drift tables and Capital Deployment:
every share at its close; the income line where its rule puts it; SPAXX as the units its
trades bought. A dividend is income in its fund's sleeve on its ex-date. In a DRIP book it
stays in that sleeve until its DRIP lot reinvests it there; in a book that takes its
dividends in cash it moves to Cash / SPAXX at the end of its ex-date and earns there. A move
between sleeves is netted at sleeve level, so the sleeves sum to the value series and, over
a window with no external flow, Brinson-Fachler plus cash drag is the time-weighted return.
Capital Deployment's total and cash are the sleeve weights', so its total is the Performance
page's current value in every book (#701, option (a)).

The independent reference is tests/first_principles.py's raw-close Book, by sleeve. Every
book is a scratch copy, offline.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src import holdings
from tests import first_principles as fp
from tests.conftest import FROZEN_BOOK, FROZEN_TODAY, unpin_leftovers
from tests.test_raw_close_valuation import _point, _today, book  # noqa: F401  (a fixture)

CASH = "Cash / SPAXX"
# The frozen book's 1M window. It holds eight DRIP lots and VGIT's ex-date of July 1, 2026,
# and no external flow.
WINDOW = ("2026-06-20", "2026-07-20")


def _cash_book(copy, ticker: str = "VGIT") -> None:
    """A book that takes ``ticker``'s dividends in cash: its DRIP lots removed."""
    con = sqlite3.connect(copy)
    with con:
        assert con.execute("DELETE FROM trades WHERE ticker = ? AND lot_source = 'drip'",
                           (ticker,)).rowcount > 0
    con.close()


def _spaxx_buy(copy, day: str = "2026-06-01", dollars: float = 100.0) -> None:
    """A SPAXX buy after inception, before the window: units bought at that day's level."""
    con = sqlite3.connect(copy)
    with con:
        assert con.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, fees, "
            "notes, lot_source) VALUES (1, 'SPAXX', ?, 'Buy', ?, 1.0, 0, '#369 staged SPAXX "
            "buy', 'Manual')", (day, dollars)).rowcount == 1
    con.close()


def _sleeves_against_the_reference(day: str) -> "tuple[pd.DataFrame, dict]":
    """The sleeve weights on ``day``, and the independent book's raw-close value by sleeve."""
    frame, _cov = holdings.sleeve_weights_with_coverage(day)
    return frame, fp.Book(account_id=holdings.get_portfolio_account_id()).sleeve_values(day)


def _assert_sleeves(frame: pd.DataFrame, want: dict, day: str) -> None:
    assert set(frame.index) == {s for s, v in want.items() if s != CASH and abs(v) >= 0.005}, (
        day, sorted(frame.index), want)
    for sleeve, row in frame.iterrows():
        assert row["Market Value"] == pytest.approx(want[sleeve], abs=0.005), (day, sleeve)
    assert frame.attrs["cash_mv"] == pytest.approx(want.get(CASH, 0.0), abs=0.005), day
    assert frame.attrs["total_value"] == pytest.approx(sum(want.values()), abs=0.005), day


# ── where a dividend sits ────────────────────────────────────────────────────

def test_in_a_drip_book_a_dividend_sits_in_its_funds_sleeve_until_its_lot(tmp_path):
    """SCHP and VGIT went ex on June 2, 2025, and the DRIP lots that reinvest them are dated
    June 4. On the ex-date and the day after, each dividend is in its fund's sleeve, TIPS and
    Core Fixed Income, held at its amount, and not in Cash / SPAXX. On the lot's date it is the
    lot's shares. Each lot is the first in its fund, so these days also show that the whole
    ledger decides which funds reinvest: a ledger read through the day would not yet hold the
    lot."""
    with pytest.MonkeyPatch.context() as mp:
        _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            seen = {}
            for day in ("2025-06-02", "2025-06-03", "2025-06-04"):
                frame, want = _sleeves_against_the_reference(day)
                _assert_sleeves(frame, want, day)
                assert frame.attrs["total_value"] == holdings.current_market_value_with_coverage(
                    day).value, day
                seen[day] = holdings._raw_close_on(day, account_id=1)
        finally:
            unpin_leftovers()
    for day in ("2025-06-02", "2025-06-03"):
        raw = seen[day]
        assert raw.pending["SCHP"] > 0.1 and raw.pending["VGIT"] > 0.1, raw.pending
        assert raw.cash_income == 0.0, "no dividend of a DRIP book is in SPAXX"
    assert all(abs(v) < 1e-9 for v in seen["2025-06-04"].pending.values()), (
        "each lot is paid from its dividend")


def test_in_a_cash_book_a_dividend_moves_to_cash_at_the_end_of_its_ex_date(tmp_path):
    """With VGIT's DRIP lots removed, as a book that takes its dividends in cash keeps it, the
    dividend of July 1, 2026 is in Cash / SPAXX at the end of that day, as units bought at
    that day's level, and not in Core Fixed Income; the day before, it is in neither."""
    with pytest.MonkeyPatch.context() as mp:
        copy = _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            _cash_book(copy)
            ref = fp.Book(account_id=1)
            swept = ref.swept("2026-06-30", "2026-07-01")
            cash = {}
            for day in ("2026-06-30", "2026-07-01"):
                frame, want = _sleeves_against_the_reference(day)
                _assert_sleeves(frame, want, day)
                assert frame.attrs["total_value"] == holdings.current_market_value_with_coverage(
                    day).value, day
                cash[day] = frame.attrs["cash_mv"]
        finally:
            unpin_leftovers()
    assert set(swept) == {"VGIT"} and swept["VGIT"] > 0.1, swept
    # Cash grew by the dividend and a day of BIL's return on what it held.
    grew = cash["2026-07-01"] - cash["2026-06-30"] - swept["VGIT"]
    assert -0.01 <= grew <= 0.001 * cash["2026-06-30"] + 0.01, (grew, cash, swept)


# ── Capital Deployment ───────────────────────────────────────────────────────

def test_capital_deployments_total_is_the_current_value_in_every_book(book):
    """Capital Deployment's total and cash are the sleeve weights' (#701, option (a)), and
    they hold the income line and SPAXX's units as the current value does, so the total is
    the Performance page's current value in every book. The personal book takes its
    dividends in cash and its ledger holds no SPAXX, so its cash is that income."""
    today = _today()
    frame, _cov = holdings.sleeve_weights_with_coverage(today)
    current = holdings.current_market_value_with_coverage(today).value
    assert frame.attrs["total_value"] == current, (book, frame.attrs, current)
    raw = holdings._raw_close_on(today, account_id=holdings.get_portfolio_account_id())
    if book == "personal":
        assert raw.cash_income > 0 and raw.spaxx_units == 0.0
        assert frame.attrs["cash_mv"] == pytest.approx(raw.cash_income, abs=0.005)
    else:
        _assert_sleeves(frame, fp.Book(account_id=1).sleeve_values(today), today)


def test_income_taken_in_cash_is_in_cash_whatever_sleeve_the_policy_gives_spaxx(tmp_path):
    """The rule puts a cash dividend in Cash / SPAXX. A book's policy can give the SPAXX
    ticker another sleeve, and its SPAXX trades are counted there, as before; the income
    taken in cash is still Cash / SPAXX's. Staged on a frozen-book copy with VGIT's DRIP lots
    removed and SPAXX moved to Real Assets."""
    with pytest.MonkeyPatch.context() as mp:
        copy = _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            _cash_book(copy)
            con = sqlite3.connect(copy)
            with con:
                assert con.execute(
                    "UPDATE securities SET asset_class_id = (SELECT asset_class_id FROM "
                    "asset_classes WHERE name = 'Real Assets') WHERE ticker = 'SPAXX'"
                ).rowcount == 1
            con.close()
            today = _today()
            frame, want = _sleeves_against_the_reference(today)
            income = holdings._raw_close_on(today, account_id=1).cash_income
        finally:
            unpin_leftovers()
    _assert_sleeves(frame, want, today)
    assert income > 1.0 and frame.attrs["cash_mv"] == pytest.approx(income, abs=0.005)


def test_the_sleeve_weights_count_spaxx_as_the_units_its_trades_bought(tmp_path):
    """A SPAXX buy after inception counts in Cash / SPAXX as the units it bought at that
    day's level, so it earns BIL's total return from its own date, as in the current value.
    The committed books bought SPAXX only at inception, where its level is $1.00."""
    with pytest.MonkeyPatch.context() as mp:
        copy = _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            _spaxx_buy(copy)
            today = _today()
            frame, want = _sleeves_against_the_reference(today)
            at_nav = holdings.get_holdings_on_date(today, account_id=1).loc["SPAXX", "net_shares"]
            price, _st = holdings._spaxx_price(today)
        finally:
            unpin_leftovers()
    _assert_sleeves(frame, want, today)
    assert abs(frame.attrs["cash_mv"] - float(at_nav) * price) > 0.05, (
        "premise: the buy's units differ from its dollars at today's level")


def test_capital_deployment_counts_a_cash_dividend_in_its_spaxx_balance(tmp_path):
    """The page's SPAXX balance, the funding float, holds the dividends taken in cash."""
    import pathlib
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    root = pathlib.Path(__file__).resolve().parent.parent
    with pytest.MonkeyPatch.context() as mp:
        copy = _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            _cash_book(copy)
            today = _today()
            want = fp.Book(account_id=1).sleeve_values(today)[CASH]
            st.cache_data.clear()
            at = AppTest.from_file(str(root / "pages" / "11_Capital_Deployment.py"),
                                   default_timeout=300).run()
            assert not at.exception, [str(e.value) for e in at.exception]
            shown = at.number_input(key="rebal_cash").proto.help
            income = holdings.dividend_income(today)
        finally:
            st.cache_data.clear()
            unpin_leftovers()
    assert income > 1.0, "premise: VGIT's dividends since inception are in SPAXX"
    assert f"Current SPAXX balance: ${want:,.2f}" in shown, (want, shown)


# ── Brinson-Fachler ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", ["drip", "cash", "spaxx"])
def test_the_identity_holds_across_a_drip_lot_and_a_sweep(tmp_path, case):
    """Over the frozen book's 1M window: as built, its eight DRIP lots each a move inside its
    fund's sleeve; with VGIT's lots removed, its July 1 dividend a sweep into SPAXX; and with
    a SPAXX buy before the window, its units bought at that day's level. Brinson-Fachler's
    sleeves sum to the value series on both days, so ex-cash Brinson-Fachler plus cash drag is
    the time-weighted return, and each sleeve's return is the independent book's, a sweep
    credited to its fund's sleeve and not to Cash / SPAXX's."""
    from src.attribution import brinson_fachler_period
    start, end = WINDOW
    with pytest.MonkeyPatch.context() as mp:
        copy = _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            if case == "cash":
                _cash_book(copy)
            if case == "spaxx":
                _spaxx_buy(copy)
            bf = brinson_fachler_period(start, end, account_id=1)
            pv = holdings.get_portfolio_value_series("2025-05-01", end, account_id=1)
            cf = holdings.get_external_cashflow_series(start, end, account_id=1)
            ref = fp.Book(account_id=1)
            want = ref.sleeve_returns(start, end)
            swept = ref.swept(start, end)
            con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            lots, = con.execute("SELECT COUNT(*) FROM trades WHERE lot_source = 'drip' AND "
                                "trade_date > ? AND trade_date <= ?", (start, end)).fetchone()
            con.close()
        finally:
            unpin_leftovers()
    assert (cf.iloc[1:] == 0.0).all(), "premise: no external flow in the window"
    assert lots >= 7, "premise: the window holds DRIP lots"
    assert bool(swept) == (case == "cash"), swept
    twr = float(pv[pd.Timestamp(end)] / pv[pd.Timestamp(start)]) - 1.0
    assert bf.attrs["r_p_total_incl"] == pytest.approx(twr, abs=1e-12), case
    bridged = float((bf["w_p"] * bf["r_p"]).sum()) + float(bf.attrs["cash_drag"])
    assert bridged == pytest.approx(twr, abs=1e-12), case
    for row in bf.itertuples():
        assert row.r_p == pytest.approx(want[row.sleeve], abs=1e-10), (case, row.sleeve)
    excash = [s for s in want if s != CASH]
    assert sorted(bf["sleeve"]) == sorted(excash)


def test_attribution_puts_a_dividend_where_the_sleeve_weights_do(tmp_path):
    """A window from inception to June 3, 2025 ends after SCHP and VGIT went ex and before
    the first DRIP lots that reinvest them. Each dividend is in its fund's sleeve at the
    window's end, as the sleeve weights hold it that day: the whole ledger decides which
    funds reinvest, not the window's. Swept into SPAXX instead, each fund's sleeve would
    return the same, the sweep netted, and the book would differ by what SPAXX earned on
    it, so the portfolio's return is checked against the value series to 1e-12."""
    from src.attribution import brinson_fachler_period
    start, end = "2025-05-01", "2025-06-03"
    with pytest.MonkeyPatch.context() as mp:
        _point(mp, tmp_path, FROZEN_BOOK, FROZEN_TODAY)
        try:
            bf = brinson_fachler_period(start, end, account_id=1)
            ref = fp.Book(account_id=1)
            want = ref.sleeve_returns(start, end)
            pending = holdings._raw_close_on(end, account_id=1).pending
            pv = holdings.get_portfolio_value_series(start, end, account_id=1)
        finally:
            unpin_leftovers()
    assert pending["SCHP"] > 0.1 and pending["VGIT"] > 0.1, "premise: both dividends wait"
    twr = float(pv.iloc[-1] / pv.iloc[0]) - 1.0
    assert bf.attrs["r_p_total_incl"] == pytest.approx(twr, abs=1e-12)
    for row in bf.itertuples():
        assert row.r_p == pytest.approx(want[row.sleeve], abs=1e-10), row.sleeve


def test_attribution_holds_the_start_holdings_fixed_through_a_buy_and_its_drip_lot(tmp_path):
    """A VOO buy on June 23, 2026, after the 1M window's start and before VOO went ex on
    June 26, is a deposit Brinson-Fachler cannot see: it holds the start's holdings fixed.
    The DRIP lot of June 30 reinvests the dividend on every share held, the bought one among
    them, so attribution keeps the part of the lot the start's holdings earned. Staged on a
    frozen-book copy, the buy, with the lot grown by the bought share's dividend, moves
    nothing in the window's attribution, though it moves the book."""
    from src.attribution import brinson_fachler_period
    start, end = WINDOW
    reads = {}
    for staged in (False, True):
        with pytest.MonkeyPatch.context() as mp:
            (tmp_path / str(staged)).mkdir()
            copy = _point(mp, tmp_path / str(staged), FROZEN_BOOK, FROZEN_TODAY)
            try:
                if staged:
                    con = sqlite3.connect(copy)
                    with con:
                        close, = con.execute("SELECT close FROM prices WHERE ticker = 'VOO' "
                                             "AND price_date = '2026-06-23'").fetchone()
                        amount, = con.execute("SELECT amount FROM dividends WHERE ticker = "
                                              "'VOO' AND ex_date = '2026-06-26'").fetchone()
                        assert con.execute(
                            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, "
                            "price, fees, notes, lot_source) VALUES (1, 'VOO', '2026-06-23', "
                            "'Buy', 1.0, ?, 0, '#369 staged buy', 'Manual')", (close,)
                        ).rowcount == 1
                        assert con.execute(
                            "UPDATE trades SET shares = shares + ? / price WHERE ticker = 'VOO' "
                            "AND lot_source = 'drip' AND trade_date = '2026-06-30'", (amount,)
                        ).rowcount == 1
                    con.close()
                reads[staged] = (brinson_fachler_period(start, end, account_id=1),
                                 holdings.get_current_market_value(end))
            finally:
                unpin_leftovers()
    (bf, value), (bf_staged, value_staged) = reads[False], reads[True]
    assert value_staged - value > 500.0, "premise: the buy and its lot move the book"
    pd.testing.assert_frame_equal(bf_staged, bf, check_exact=False, rtol=0, atol=1e-12)
    for key in ("cash_drag", "cash_weight", "r_p_total_excash", "r_p_total_incl"):
        assert bf_staged.attrs[key] == pytest.approx(bf.attrs[key], abs=1e-12), key
