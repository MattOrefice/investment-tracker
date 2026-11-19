"""A split inside a quarter, and that quarter's lock taken across it: what January's
close-out meets (splits decided by the owner on October 18, 2026, #669; the raw-close rule
on October 27 and 28, 2026, #369, whose first lock is Q4 2026's).

QUAL went 4-for-1 and EFV 2-for-1 on November 5, 2026 (iShares Trust, Form 497 supplement
dated October 5, 2026: record date November 3, payable after the close of November 4,
split-adjusted trading from November 5). Both are benchmark legs in demo.db, held in no
account. demo.db's histories end September 30, so the first fetch that meets the split is
the close-out's advance through the quarter, and the report then locks Q4 on the
raw-close rule.

Two scratch copies of demo.db are carried through Q4 2026 on the same closes, a small wave
for every ticker, with one QUAL dividend after the ex-date:
  * the CONTROL has no split: QUAL and EFV stay on the scale they were stored on;
  * the SPLIT copy holds QUAL and EFV only through November 4. The rest comes through the
    price layer's own fetch, from a provider stubbed at the HTTP session to answer as
    Yahoo does after a split: every close and dividend divided by the ratio, with the
    split event.
Every return and every figure the split copy prints must then be the control's, or a scale
was left unadjusted. Pinned:
  * the split copy holds every QUAL and EFV close and dividend at the control's divided by
    the ratio, both splits recorded as applied, and nothing else differs;
  * the blended benchmark, the sleeve benchmarks and the raw-close value series are the
    control's, read live and inside the Q4 lock, with no split-sized move at the ex-date;
  * every figure the Q4 lock's report prints is the control's, and the Q3 lock is
    byte-identical and prints the same figures;
  * without the event, the blended benchmark jumps at the ex-date (the control half).

A held position whose ticker split after its first trade is not valued at all (#669,
tests/test_splits.py). Neither QUAL nor EFV is held, which a premise asserts.
"""
from __future__ import annotations

import math
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import pytest

from src import benchmarks, cache, holdings
from tests import locked_figures as lf
from tests.conftest import pin_today, unpin_leftovers
from tests.test_valuation_rule_marker import DEMO_DB, JAN_4_2027, _point_at

EX = "2026-11-05"
DAY_BEFORE = "2026-11-04"
SPLITS = {"QUAL": (4, 1), "EFV": (2, 1)}
Q3_END, Q4_END = "2026-09-30", "2026-12-31"
DIVIDEND = ("QUAL", "2026-12-16")      # after the ex-date, so the provider pays it new-scale
SLEEVES = ("US Large Quality", "International Large Value")   # QUAL's and EFV's sleeves


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ts(day: str) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, 14, 30, tzinfo=timezone.utc).timestamp())


def _rows(path: Path, sql: str, *args) -> list:
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _q4_sessions() -> "list[str]":
    from src.demo_refresh import is_session
    return [d.isoformat() for d in (date(2026, 10, 1) + timedelta(n) for n in range(92))
            if is_session(d)]


def _carry_through_q4(copy: Path, *, hold_back=()) -> None:
    """Every ticker's Q4 closes, its September 30 close on a small wave, and the QUAL
    dividend. A ticker in ``hold_back`` is stored only through the day before the ex-date."""
    con = sqlite3.connect(copy)
    try:
        with con:
            last = dict(con.execute(
                "SELECT ticker, close FROM prices p WHERE price_date = "
                "(SELECT MAX(price_date) FROM prices q WHERE q.ticker = p.ticker)").fetchall())
            ends = {r[0] for r in con.execute(
                "SELECT MAX(price_date) FROM prices GROUP BY ticker")}
            assert ends == {Q3_END}, f"premise: every history ends September 30: {ends}"
            con.executemany(
                "INSERT INTO prices (ticker, price_date, close, adj_close) VALUES (?, ?, ?, NULL)",
                [(t, d, last[t] * (1 + 0.004 * math.sin(0.9 * n + i)))
                 for i, t in enumerate(sorted(last)) for n, d in enumerate(_q4_sessions())
                 if not (t in hold_back and d >= EX)])
            ticker, day = DIVIDEND
            if ticker not in hold_back:
                con.execute("INSERT INTO dividends (ticker, ex_date, amount) VALUES (?, ?, ?)",
                            (ticker, day, round(last[ticker] * 0.004, 4)))
    finally:
        con.close()


def _provider(mp, control: Path, *, events: bool = True) -> None:
    """Yahoo after the split, stubbed at the HTTP session for QUAL and EFV: the control's
    closes and dividends divided by the ratio, and the split event whenever the window
    asked holds the ex-date, unless ``events`` is False."""
    import src.prices as prices
    closes = {t: dict(_rows(control, "SELECT price_date, close FROM prices WHERE ticker = ?", t))
              for t in SPLITS}
    divs = {t: dict(_rows(control, "SELECT ex_date, amount FROM dividends WHERE ticker = ?", t))
            for t in SPLITS}

    def get(url, params=None, timeout=None):
        ticker = unquote(url.rsplit("/", 1)[-1])
        num, den = SPLITS[ticker]
        lo, hi = params["period1"], params["period2"]
        days = [d for d in sorted(closes[ticker]) if lo <= _ts(d) <= hi]
        ev = {"dividends": {str(_ts(d)): {"amount": a / (num / den), "date": _ts(d)}
                            for d, a in divs[ticker].items() if lo <= _ts(d) <= hi}}
        if events and lo <= _ts(EX) <= hi:
            ev["splits"] = {str(_ts(EX)): {"date": _ts(EX), "numerator": num,
                                           "denominator": den, "splitRatio": f"{num}:{den}"}}
        quoted = [closes[ticker][d] / (num / den) for d in days]
        return _Resp({"chart": {"result": [{
            "meta": {"currentTradingPeriod": {"regular": {"start": lo, "end": lo + 1}}},
            "timestamp": [_ts(d) for d in days],
            "indicators": {"quote": [{"close": quoted}], "adjclose": [{"adjclose": quoted}]},
            "events": ev}], "error": None}})

    mp.setattr(prices._SESSION, "get", get)


def _copy(tmp: Path, name: str) -> Path:
    copy = tmp / f"{name}.db"
    shutil.copyfile(DEMO_DB, copy)
    os.chmod(copy, 0o644)
    return copy


def _fetch_the_split(copy: Path, control: Path, *, events: bool = True) -> None:
    """The close-out's fetch for QUAL and EFV, from the ex-date through the quarter."""
    import src.prices as prices
    with pytest.MonkeyPatch.context() as mp:
        _point_at(mp, copy)
        _provider(mp, control, events=events)
        pin_today(mp, JAN_4_2027)
        try:
            for ticker in SPLITS:
                prices.fetch_prices(ticker, EX, Q4_END)
        finally:
            unpin_leftovers()


def _reads() -> dict:
    acct = holdings.get_portfolio_account_id()
    inception = holdings.get_inception_date(account_id=acct)
    return {"blended": benchmarks.get_custom_blended_series(Q3_END, Q4_END),
            "sleeves": benchmarks.get_sleeve_benchmark_returns(Q3_END, Q4_END)[list(SLEEVES)],
            "value": holdings.get_portfolio_value_series(inception, Q4_END, account_id=acct),
            "current": holdings.get_current_market_value(Q4_END)}


def _lock_and_read(copy: Path) -> dict:
    """Lock Q4 on the copy, as the report does on January 4, 2027, and read live and
    inside the lock."""
    with pytest.MonkeyPatch.context() as mp:
        _point_at(mp, copy)
        pin_today(mp, JAN_4_2027)
        try:
            snap, _ = cache.capture_quarter_snapshot("2026Q4")
            live = _reads()
            with cache.snapshot_price_context(snap):
                locked = _reads()
        finally:
            unpin_leftovers()
    return {"rule": snap.valuation_rule, "live": live, "locked": locked}


@pytest.fixture(scope="module")
def across(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("split_inside_a_lock")
    control, split = _copy(tmp, "control"), _copy(tmp, "split")
    committed_locks = lf.locks_in(DEMO_DB)
    _carry_through_q4(control)
    _carry_through_q4(split, hold_back=tuple(SPLITS))
    _fetch_the_split(split, control)
    rows = {name: {t: _rows(path, f"SELECT * FROM {t} ORDER BY 1, 2")
                   for t in ("prices", "dividends", "trades")}
            for name, path in (("control", control), ("split", split))}
    reads = {name: _lock_and_read(path) for name, path in (("control", control), ("split", split))}
    renders = {name: lf.render_book(name, tmp, book=path, quarters=("2026Q3", "2026Q4"))
               for name, path in (("control", control), ("split", split))}
    return {"control": control, "split": split, "rows": rows, "reads": reads,
            "renders": renders, "committed_locks": committed_locks}


def test_premise_qual_and_efv_are_benchmark_legs_held_in_no_account(tmp_path):
    copy = _copy(tmp_path, "premise")
    with pytest.MonkeyPatch.context() as mp:
        _point_at(mp, copy)
        legs = {s: [t for t, _ in c] for s, c in benchmarks._sleeve_benchmarks().items()}
    assert "QUAL" in legs[SLEEVES[0]] and "EFV" in legs[SLEEVES[1]]
    assert _rows(copy, "SELECT DISTINCT ticker FROM trades WHERE ticker IN ('QUAL', 'EFV')") == []


def test_the_split_copy_holds_the_controls_rows_on_the_new_scale(across):
    control, split = across["rows"]["control"], across["rows"]["split"]
    for ticker, (num, den) in SPLITS.items():
        ratio = num / den
        c = {r[1]: r[2] for r in control["prices"] if r[0] == ticker}
        s = {r[1]: r[2] for r in split["prices"] if r[0] == ticker}
        assert set(s) == set(c) and DAY_BEFORE in s and EX in s
        assert all(s[d] == pytest.approx(c[d] / ratio, rel=1e-12) for d in c), ticker
        cd = {r[1]: r[2] for r in control["dividends"] if r[0] == ticker}
        sd = {r[1]: r[2] for r in split["dividends"] if r[0] == ticker}
        assert cd and set(sd) == set(cd)
        assert all(sd[d] == pytest.approx(cd[d] / ratio, rel=1e-12) for d in cd), ticker
    for table in ("prices", "dividends"):
        assert ([r for r in split[table] if r[0] not in SPLITS]
                == [r for r in control[table] if r[0] not in SPLITS]), table
    assert split["trades"] == control["trades"]
    assert sorted(_rows(across["split"], "SELECT ticker, ex_date, ratio, state FROM splits")) == [
        ("EFV", EX, 2.0, "rescaled"), ("QUAL", EX, 4.0, "rescaled")]


def _move(series: pd.Series) -> float:
    s = series.copy()
    s.index = pd.to_datetime(s.index)
    return float(s.loc[EX] / s.loc[DAY_BEFORE] - 1)


@pytest.mark.parametrize("where", ["live", "locked"])
def test_benchmark_returns_and_the_raw_close_value_series_are_the_controls(across, where):
    control = across["reads"]["control"][where]
    split = across["reads"]["split"][where]
    pd.testing.assert_series_equal(split["blended"], control["blended"], rtol=1e-12)
    pd.testing.assert_frame_equal(split["sleeves"], control["sleeves"], rtol=1e-12)
    pd.testing.assert_series_equal(split["value"], control["value"], rtol=1e-12)
    assert split["current"] == pytest.approx(control["current"], rel=1e-12)
    # At the ex-date each series moves as the control does: a wave step, nothing split-sized.
    assert abs(_move(split["blended"])) < 0.01
    for sleeve in SLEEVES:
        assert _move(1 + split["sleeves"][sleeve]) == pytest.approx(
            _move(1 + control["sleeves"][sleeve]), rel=1e-9)
        assert abs(_move(1 + split["sleeves"][sleeve])) < 0.01


def test_the_q4_lock_is_on_the_raw_close_rule_and_prints_the_controls_figures(across):
    for name in ("control", "split"):
        assert across["reads"][name]["rule"] == cache.RAW_CLOSE_RULE
    control, split = across["renders"]["control"], across["renders"]["split"]
    assert set(split) == set(control) == {"2026Q3", "2026Q4"}
    for quarter in control:
        assert split[quarter]["tiles"] == control[quarter]["tiles"], quarter
        assert split[quarter]["sections"] == control[quarter]["sections"], quarter
        assert split[quarter]["state"] == control[quarter]["state"], quarter
    assert lf.count_figures(lf.golden_entry(split["2026Q4"])) > 100


def test_the_q3_lock_and_every_earlier_one_stay_byte_identical(across):
    committed = across["committed_locks"]
    for name in ("control", "split"):
        locks = lf.locks_in(across[name])
        assert {q: locks[q] for q in committed} == committed
        assert set(locks) - set(committed) == {"2026Q4"}


def test_control_without_the_event_the_benchmark_jumps_at_the_ex_date(across, tmp_path):
    """The same answers with the split event left out, as the price layer read them before
    #669: new-scale closes stored beside old-scale ones."""
    unread = _copy(tmp_path, "unread")
    _carry_through_q4(unread, hold_back=tuple(SPLITS))
    _fetch_the_split(unread, across["control"], events=False)
    with pytest.MonkeyPatch.context() as mp:
        _point_at(mp, unread)
        pin_today(mp, JAN_4_2027)
        try:
            jumped = benchmarks.get_custom_blended_series(Q3_END, Q4_END)
            sleeves = benchmarks.get_sleeve_benchmark_returns(Q3_END, Q4_END)
        finally:
            unpin_leftovers()
    control = across["reads"]["control"]["live"]
    assert _move(jumped) - _move(control["blended"]) < -0.05
    assert _move(1 + sleeves["US Large Quality"]) == pytest.approx(
        (1 + _move(1 + control["sleeves"]["US Large Quality"])) / 4 - 1, rel=1e-9)
    assert _move(1 + sleeves["International Large Value"]) == pytest.approx(
        (1 + _move(1 + control["sleeves"]["International Large Value"])) / 2 - 1, rel=1e-9)
