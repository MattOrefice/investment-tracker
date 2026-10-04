"""tools/advance_demo_prices.py: the quarterly close-out's snapshot advance (#397).

Nothing wrote prices into the committed demo book, so a quarter that ended after its
snapshot could be locked only at runtime. The tool advances every ticker through one
settled session, by the #283 data-path shape: fetch into a scratch copy, check the
delta, write it in one transaction, assert the result per table.

Run here offline, on a copy of the frozen book (whose histories end on three different
dates, as the demo's did) with a stand-in for the provider. Pinned:
  * every ticker ends on the target, and nothing but new price and dividend rows changes;
  * a second run writes nothing;
  * a failed fetch, a missing session, or a dividend dated inside the committed history
    refuses the whole advance and leaves the book byte-identical;
  * a date that is not a settled NYSE session is refused.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest

from tests.conftest import FROZEN_BOOK

ROOT = Path(__file__).resolve().parent.parent
THROUGH = date(2026, 7, 24)          # a Friday, four sessions after the book's newest close
TODAY = date(2026, 7, 27)


def _tool():
    spec = importlib.util.spec_from_file_location(
        "advance_demo_prices", ROOT / "tools" / "advance_demo_prices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _provider(daily, dividends=(), drop=(), fail=()):
    """A stand-in for the price provider: the chart JSON for the window asked. An
    exchange-traded ticker gets a bar at 13:30 UTC on each NYSE session; a series that
    trades every day gets one at 00:00 UTC on each day, so, like the real one, it also
    returns the bar on the day after the window's end."""
    from src.demo_refresh import is_session

    def get(url, params=None, timeout=None):
        ticker = unquote(url.rsplit("/", 1)[1])
        if ticker in fail:
            raise OSError("provider down")
        lo, hi = params["period1"], params["period2"]
        day = datetime.fromtimestamp(lo, tz=timezone.utc).date()
        stamps = []
        while True:
            hour = 0 if ticker in daily else 13
            ts = int(datetime(day.year, day.month, day.day, hour, 30 if hour else 0,
                              tzinfo=timezone.utc).timestamp())
            if ts > hi:
                break
            if ts >= lo and (ticker in daily or is_session(day)) and (ticker, day) not in drop:
                stamps.append(ts)
            day += timedelta(days=1)
        closes = [100.0 + i for i in range(len(stamps))]
        events = {str(int(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc)
                          .timestamp())): {"amount": amount}
                  for t, d, amount in dividends if t == ticker}
        # A session that ended long ago, so the price layer reads every bar as settled
        # (prices.unsettled_bar_date). Without it the newest bar is never stored.
        meta = {"currentTradingPeriod": {"regular": {"start": lo, "end": lo + 1}}}
        return _Resp({"chart": {"result": [{
            "meta": meta, "timestamp": stamps,
            "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": closes}]},
            "events": {"dividends": events}}]}})

    return get


@pytest.fixture
def book(tmp_path, monkeypatch):
    """(the tool, a writable copy of the frozen book it targets)."""
    import src.prices as prices
    tool = _tool()
    copy = tmp_path / "book.db"
    shutil.copyfile(FROZEN_BOOK, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(tool, "DEMO_DB", copy)
    monkeypatch.setattr(tool, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(prices._SESSION, "get", _provider(tool.DAILY))
    return tool, copy


def _rows(path, sql, *args):
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def test_every_ticker_reaches_the_target_and_nothing_else_changes(book, monkeypatch):
    import src.prices as prices
    from tools.check_price_histories import history_ends
    from tools.fingerprint_db import fingerprint
    tool, copy = book
    assert len(history_ends(copy)) == 3, "premise: the book's histories end on three dates"
    assert "BTC-USD" in tool.DAILY and _rows(copy, "SELECT 1 FROM prices WHERE ticker = "
                                             "'BTC-USD' LIMIT 1"), "premise: a daily series"
    monkeypatch.setattr(prices._SESSION, "get", _provider(
        tool.DAILY, dividends=[("VOO", date(2026, 7, 22), 1.5)]))
    before = fingerprint(copy)
    held = set(_rows(copy, "SELECT ticker, price_date, close, adj_close FROM prices"))
    held_divs = set(_rows(copy, "SELECT ticker, ex_date, amount FROM dividends"))

    report = tool.advance(THROUGH, today=TODAY)

    after = fingerprint(copy)
    assert list(history_ends(copy)) == ["2026-07-24"]
    assert sorted(t for t in before if after[t] != before[t]) == ["dividends", "prices"]
    now = set(_rows(copy, "SELECT ticker, price_date, close, adj_close FROM prices"))
    assert held <= now, "a close the book held was changed or removed"
    new = now - held
    assert report["prices"] == (len(held), len(now)) and len(new) > 0
    assert all(adj is None and close > 0 for _t, _d, close, adj in new), "raw closes only"
    assert max(d for _t, d, _c, _a in new) == "2026-07-24", (
        "the daily series' bar for the day after the target is not written")
    # A ticker that ended 2026-06-08 gains every session since, holidays skipped.
    gained = sorted(d for t, d, _c, _a in new if t == "IWB")
    assert gained[0] == "2026-06-09" and gained[-1] == "2026-07-24"
    assert "2026-06-19" not in gained and "2026-07-03" not in gained, "NYSE holidays"
    assert gained == tool.expected_dates("IWB", "2026-06-08", "2026-07-24")
    # The daily series gains weekends too.
    btc = {d for t, d, _c, _a in new if t == "BTC-USD"}
    assert {"2026-07-18", "2026-07-19"} <= btc
    # The dividend the provider reported in the span, and no other.
    assert set(_rows(copy, "SELECT ticker, ex_date, amount FROM dividends")) - held_divs == {
        ("VOO", "2026-07-22", 1.5)}
    assert report["new_dividends"] == [("VOO", "2026-07-22", 1.5)]
    assert report["unchanged_tables"] == len(before) - 2


def test_a_second_run_writes_nothing(book):
    tool, copy = book
    tool.advance(THROUGH, today=TODAY)
    once = _sha(copy)
    report = tool.advance(THROUGH, today=TODAY)
    assert _sha(copy) == once
    assert report["prices"][0] == report["prices"][1] and report["new_dividends"] == []


@pytest.mark.parametrize("why, kwargs, message", [
    ("one fetch fails", {"fail": {"VTV"}}, "the fetch failed for VTV"),
    ("a session is missing", {"drop": {("VTV", date(2026, 7, 22))}},
     "VTV: after 2026-07-20 it should gain 4 close(s) through 2026-07-24 and gained 3; "
     "missing ['2026-07-22']"),
    ("a dividend is dated inside the committed history",
     {"dividends": [("VTV", date(2026, 7, 14), 0.9)]},
     "VTV: dividend 2026-07-14 (0.9) is not after the ticker's last stored close, 2026-07-20"),
])
def test_a_bad_delta_refuses_the_whole_advance(book, monkeypatch, why, kwargs, message):
    import src.prices as prices
    tool, copy = book
    monkeypatch.setattr(prices._SESSION, "get", _provider(tool.DAILY, **kwargs))
    before = _sha(copy)
    with pytest.raises(tool.AdvanceError) as err:
        tool.advance(THROUGH, today=TODAY)
    assert message in str(err.value), why
    assert _sha(copy) == before, f"{why}: the book must be byte-identical"


def test_a_date_that_is_not_a_settled_session_is_refused(book):
    tool, copy = book
    before = _sha(copy)
    with pytest.raises(tool.AdvanceError, match="not an NYSE session"):
        tool.advance(date(2026, 7, 25), today=TODAY)               # a Saturday
    with pytest.raises(tool.AdvanceError, match="not before today"):
        tool.advance(THROUGH, today=THROUGH)                        # its close may be open
    with pytest.raises(tool.AdvanceError, match="already holds closes after 2026-07-17"):
        tool.advance(date(2026, 7, 17), today=TODAY)               # behind the book
    assert _sha(copy) == before


def test_the_command_reports_the_delta_last_and_aborts_with_exit_1(book, monkeypatch, capsys):
    import src.asof as asof
    import src.prices as prices
    tool, copy = book
    monkeypatch.setattr(asof, "today_et", lambda now=None: TODAY)
    assert tool.main(["advance_demo_prices.py", "2026-07-24"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == ("Advanced book.db through 2026-07-24: every ticker's history ends "
                       "there.")
    assert any(ln.startswith("prices:    ") and "histories ended 2026-06-08 (14), "
               "2026-07-16 (4), 2026-07-20 (29)" in ln for ln in out)
    assert tool.main(["advance_demo_prices.py", "2026-07-24"]) == 0
    assert "Nothing was written." in capsys.readouterr().out
    monkeypatch.setattr(prices._SESSION, "get", _provider(tool.DAILY, fail={"SPY"}))
    assert tool.main(["advance_demo_prices.py", "2026-07-27"]) == 1
    assert capsys.readouterr().out.startswith("ABORT: ")


def test_the_committed_book_ends_on_one_date():
    """What the close-out's check confirms, held in CI: no ticker lags silently."""
    from tools.check_price_histories import history_ends
    ends = history_ends(ROOT / "data" / "demo.db")
    assert len(ends) == 1, {day: len(t) for day, t in ends.items()}
