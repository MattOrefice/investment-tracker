"""Splits: the history before an ex-date moves to the new scale, once (decided by the owner
on October 18, 2026, before QUAL's 4-for-1 and EFV's 2-for-1 of November 5, 2026).

The provider's close is split-adjusted, so the first fetch after an ex-date answers on the
new scale while the book's rows before it are on the old one. fetch_prices asked for the
split events and never read them: a 4-for-1 would have read as a 75% fall. Now each event
is stored, and when the stored close for the last session before the ex-date is about
ratio x the provider's close for that day, every stored close and dividend before it is
divided by the ratio, in the store's transaction, and recorded as applied.

On copies of the frozen book (histories to 2026-07-20), with Yahoo stubbed at the HTTP
session, so fetch_prices' own reading of the answer runs. The stub answers split-adjusted:
before the ex-date, a splitting ticker's close is its stored close divided by the ratio;
after the book's last close every ticker holds flat at its last close, so a jump at the
ex-date can only be a scale left unadjusted. Pinned:
  * a 4-for-1 (QUAL) and a 2-for-1 (EFV) leave no jump in the closes, the total-return
    levels or the blended benchmark, and the control without the event shows the jump;
  * a second fetch, and a full re-fetch, change nothing;
  * a history already on the new scale is untouched;
  * a held position whose ticker split after its first trade is unresolved, with its
    reason, in the sleeve weights, the value series and the tax lots; one whose split
    predates every trade is valued; inside a quarter lock nothing changes;
  * the demo's overlay rescales its runtime cache and leaves demo.db byte-identical;
  * tools/advance_demo_prices.py accepts the rows a recorded split explains.
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

import pandas as pd
import pytest

from tests.conftest import FROZEN_BOOK, point_at_frozen_book

ROOT = Path(__file__).resolve().parent.parent
LAST = "2026-07-20"            # the frozen book's last close
EX = "2026-07-22"              # the ex-date every stubbed split goes ex on
END = "2026-07-24"
UTC = timezone.utc


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ts(day: str, hour: int = 13, minute: int = 30) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC).timestamp())


def _closes(path) -> "dict[str, dict[str, float]]":
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        out: "dict[str, dict[str, float]]" = {}
        for ticker, day, close in con.execute("SELECT ticker, price_date, close FROM prices"):
            out.setdefault(ticker, {})[day] = close
        return out
    finally:
        con.close()


def _rows(path, sql: str, *args) -> list:
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _yahoo(monkeypatch, book, splits: "dict[str, tuple[int, int]]", *,
           already_new=(), events=True, daily=frozenset({"BTC-USD"})) -> None:
    """Stub the chart request for every ticker the book holds.

    Each NYSE session in the window asked gets a bar at 13:30 UTC (every day, at 00:00, for
    a series in ``daily``). Its close is the stored close, or, after the book's last
    close, the last stored close held flat. A ticker in ``splits`` (ticker -> (numerator,
    denominator)) answers that divided by the ratio, as the provider adjusts it, unless it
    is in ``already_new``, whose stored history is on the new scale already; its answer
    carries the split event whenever the window holds the ex-date, unless ``events`` is
    False (the provider's prices as they would read with the event ignored)."""
    import src.prices as prices
    from src.demo_refresh import is_session
    stored = _closes(book)

    def get(url, params=None, timeout=None):
        ticker = unquote(url.rsplit("/", 1)[-1])
        lo, hi = params["period1"], params["period2"]
        day = datetime.fromtimestamp(lo, tz=UTC).date()
        series = stored.get(ticker, {})
        last = series[max(series)] if series else 100.0
        stamps, closes = [], []
        while True:
            ts = _ts(day.isoformat(), 0, 0) if ticker in daily else _ts(day.isoformat())
            if ts > hi:
                break
            iso = day.isoformat()
            if ts >= lo and (ticker in daily or is_session(day)):
                close = series.get(iso) if series and iso <= max(series) else last
                if close is not None:
                    if ticker in splits and ticker not in already_new:
                        num, den = splits[ticker]
                        close = close / (num / den)
                    stamps.append(ts)
                    closes.append(close)
            day += timedelta(days=1)
        ev = {}
        if events and ticker in splits and lo <= _ts(EX) <= hi:
            num, den = splits[ticker]
            ev["splits"] = {str(_ts(EX)): {"date": _ts(EX), "numerator": num,
                                           "denominator": den, "splitRatio": f"{num}:{den}"}}
        meta = {"currentTradingPeriod": {"regular": {"start": lo, "end": lo + 1}}}
        return _Resp({"chart": {"result": [{
            "meta": meta, "timestamp": stamps,
            "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": closes}]},
            "events": ev}], "error": None}})

    monkeypatch.setattr(prices._SESSION, "get", get)


@pytest.fixture
def book(tmp_path, monkeypatch):
    return point_at_frozen_book(monkeypatch, tmp_path)


# ── The history moves to the new scale ─────────────────────────────────────────

@pytest.mark.parametrize("ticker, num", [("QUAL", 4), ("EFV", 2)])
def test_a_split_leaves_no_jump_in_the_closes_or_the_dividends(book, monkeypatch, ticker, num):
    import src.prices as prices
    before = _closes(book)[ticker]
    divs = dict(_rows(book, "SELECT ex_date, amount FROM dividends WHERE ticker = ?", ticker))
    _yahoo(monkeypatch, book, {ticker: (num, 1)})

    prices.fetch_prices(ticker, "2026-07-21", END)

    after = _closes(book)[ticker]
    assert all(after[d] == pytest.approx(c / num, rel=1e-12) for d, c in before.items())
    # The ex-date's close is the last one before it: no jump.
    assert after[EX] / after[LAST] == pytest.approx(1.0, rel=1e-12)
    new_divs = dict(_rows(book, "SELECT ex_date, amount FROM dividends WHERE ticker = ?", ticker))
    assert divs and all(new_divs[d] == pytest.approx(a / num, rel=1e-12) for d, a in divs.items())
    assert _rows(book, "SELECT ticker, ex_date, ratio, state FROM splits") == [
        (ticker, EX, float(num), prices.RESCALED)]


def test_total_return_levels_do_not_jump(book, monkeypatch):
    import src.prices as prices
    _yahoo(monkeypatch, book, {"QUAL": (4, 1)})
    tr = prices.total_return_series(prices.get_prices("QUAL", "2026-07-01", END))
    tr.index = pd.to_datetime(tr.index)
    assert tr.pct_change().loc["2026-07-21":].abs().max() == pytest.approx(0.0, abs=1e-12)


def _blended_moves(book, monkeypatch, **kw) -> float:
    from src import benchmarks
    _yahoo(monkeypatch, book, {"QUAL": (4, 1), "EFV": (2, 1)}, **kw)
    s = benchmarks.get_custom_blended_series("2026-07-01", END)
    return float(s.pct_change().loc["2026-07-21":].abs().max())


def test_the_blended_benchmark_does_not_jump(book, monkeypatch):
    """QUAL (US Large Quality) and EFV (International Large Value) are legs of the
    blended benchmark. With every leg flat after July 20, the series is flat too."""
    assert _blended_moves(book, monkeypatch) == pytest.approx(0.0, abs=1e-12)


def test_control_without_the_event_the_benchmark_would_jump(book, monkeypatch):
    """The same answers with the split event left out, as the price layer read them
    before: new-scale closes stored beside old-scale ones."""
    assert _blended_moves(book, monkeypatch, events=False) > 0.05


def test_a_second_fetch_and_a_full_refetch_change_nothing(book, monkeypatch):
    import src.prices as prices
    _yahoo(monkeypatch, book, {"QUAL": (4, 1)})
    prices.fetch_prices("QUAL", "2026-07-21", END)
    sql = ("SELECT * FROM prices WHERE ticker = 'QUAL' UNION ALL "
           "SELECT ticker, ex_date, amount, NULL FROM dividends WHERE ticker = 'QUAL' "
           "ORDER BY 1, 2")
    once, record = _rows(book, sql), _rows(book, "SELECT * FROM splits")

    prices.fetch_prices("QUAL", "2026-07-21", END)
    prices.fetch_prices("QUAL", "2024-01-02", END)

    assert _rows(book, sql) == once
    assert _rows(book, "SELECT * FROM splits") == record


def test_a_history_already_on_the_new_scale_is_untouched(book, monkeypatch):
    import src.prices as prices
    stored = "SELECT * FROM prices WHERE ticker = 'QUAL' AND price_date <= ?"
    before = _rows(book, stored, LAST)
    divs = _rows(book, "SELECT * FROM dividends WHERE ticker = 'QUAL'")
    _yahoo(monkeypatch, book, {"QUAL": (4, 1)}, already_new={"QUAL"})

    prices.fetch_prices("QUAL", "2026-07-21", END)

    assert _rows(book, stored, LAST) == before
    assert _rows(book, "SELECT * FROM dividends WHERE ticker = 'QUAL'") == divs
    assert _rows(book, "SELECT state FROM splits") == [(prices.ALREADY_NEW,)]


def test_a_split_that_cannot_be_read_stores_nothing(book, monkeypatch):
    import src.prices as prices
    _yahoo(monkeypatch, book, {"QUAL": (4, 1)})
    get = prices._SESSION.get

    def broken(url, params=None, timeout=None):
        resp = get(url, params, timeout)
        for event in resp.json()["chart"]["result"][0]["events"].get("splits", {}).values():
            event["numerator"] = "four"
        return resp
    monkeypatch.setattr(prices._SESSION, "get", broken)
    before = _rows(book, "SELECT * FROM prices WHERE ticker = 'QUAL'")

    with pytest.raises(prices.YahooResponseError, match="a split that could not be read"):
        prices.fetch_prices("QUAL", "2026-07-21", END)
    assert _rows(book, "SELECT * FROM prices WHERE ticker = 'QUAL'") == before


# ── A held position the split crosses ─────────────────────────────────────────

VTV_WHY = "its share counts don't reflect its 2-for-1 split of 2026-07-22"


def test_a_held_position_crossing_a_split_is_unresolved_with_its_reason(book, monkeypatch):
    """VTV, held since 2025-05-01, splits 2-for-1 on July 22: its share counts are on
    the old scale, so it is not valued anywhere, and each place says why."""
    import src.prices as prices
    from src import holdings, tax_lots
    from src.coverage import unresolved_marker
    from src.harvest import compute_harvest_candidates
    _yahoo(monkeypatch, book, {"VTV": (2, 1)})
    prices.fetch_prices("VTV", "2026-07-21", END)
    acct = holdings.get_portfolio_account_id()

    _frame, cov = holdings.sleeve_weights_with_coverage(END)
    vtv = [u for u in cov.unresolved if u.ticker == "VTV"]
    assert [(u.reason, u.detail) for u in vtv] == [("split_unadjusted", VTV_WHY)]
    marker = unresolved_marker(cov)
    assert f"VTV is not valued, because {VTV_WHY}, so its market value counted as zero." in marker
    assert "no committed price" not in marker

    with pytest.raises(holdings.UnpricedHoldingError) as err:
        holdings.get_portfolio_value_series("2026-07-01", END, account_id=acct)
    assert "VTV is not valued on" in err.value.reason
    assert err.value.reason.endswith(f"because {VTV_WHY}")

    lots = tax_lots.get_lot_inventory(END, account_ids=[acct])
    held = lots[lots["ticker"] == "VTV"]
    assert len(held) and (held["not_valued"] == VTV_WHY).all()
    assert held["market_value"].isna().all() and held["unrealized_gl"].isna().all()
    valued = lots[lots["ticker"] != "VTV"]
    assert tax_lots.summary_metrics(lots)["cost_basis_total"] == pytest.approx(
        valued["cost_basis_total"].sum())
    assert tax_lots.not_valued_note(lots).startswith(f"VTV's {len(held)} lot")
    assert "VTV" not in {c["ticker"] for c in compute_harvest_candidates(lots, action_threshold=0,
                                                                          pct_threshold=0)}


def test_inside_a_quarter_lock_the_position_is_valued_as_the_lock_holds_it(book, monkeypatch):
    """A lock serves its own closes, on the scale its share counts were valued at."""
    import src.prices as prices
    from src import holdings
    _yahoo(monkeypatch, book, {"VTV": (2, 1)})
    prices.fetch_prices("VTV", "2026-07-21", END)
    acct = holdings.get_portfolio_account_id()
    token = prices._PRICE_LOCK.set(lambda ticker, start, end: None)
    try:
        _p, st = holdings._price_and_status("VTV", "2026-07-10", LAST, account_id=acct)
    finally:
        prices._PRICE_LOCK.reset(token)
    assert st.resolved


def test_a_split_before_every_trade_changes_nothing(book, monkeypatch):
    """VOO's first trade is 2025-05-01. A split recorded for 2024-06-03 predates it, so
    no share count crosses it (SCHP's 2024 split in both books is this case)."""
    import src.prices as prices
    from src import holdings
    con = sqlite3.connect(book)
    with con:
        con.execute(prices.SPLITS_DDL)
        con.execute("INSERT INTO splits VALUES ('VOO', '2024-06-03', 2.0, ?, '2026-10-19')",
                    (prices.ALREADY_NEW,))
    con.close()
    acct = holdings.get_portfolio_account_id()
    assert holdings.split_crossing("VOO", acct) is None
    _p, st = holdings._price_and_status("VOO", "2026-07-10", LAST, account_id=acct)
    assert st.resolved


# ── The demo's overlay ────────────────────────────────────────────────────────

def test_the_overlay_rescales_the_runtime_cache_and_not_demo_db(tmp_path, monkeypatch):
    """In the demo, prices, dividends and splits are views of the runtime cache. The
    rescale lands there; the committed book is byte-identical."""
    import src.db as db
    import src.prices as prices
    committed = point_at_frozen_book(monkeypatch, tmp_path)
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    db.use_runtime_cache(tmp_path / "cache.db", committed=committed)
    sha = hashlib.sha256(committed.read_bytes()).hexdigest()
    old = _closes(committed)["QUAL"][LAST]
    _yahoo(monkeypatch, committed, {"QUAL": (4, 1)})

    prices.fetch_prices("QUAL", "2026-07-21", END)

    cache = sqlite3.connect(tmp_path / "cache.db")
    try:
        assert cache.execute("SELECT close FROM runtime_prices WHERE ticker = 'QUAL' AND "
                             "price_date = ?", (LAST,)).fetchone()[0] == pytest.approx(old / 4)
        assert cache.execute("SELECT state FROM runtime_splits").fetchall() == [
            (prices.RESCALED,)]
    finally:
        cache.close()
    assert hashlib.sha256(committed.read_bytes()).hexdigest() == sha
    assert prices.recorded_splits("QUAL") == [(EX, 4.0)]


# ── The quarterly advance ─────────────────────────────────────────────────────

def _advance_tool():
    spec = importlib.util.spec_from_file_location(
        "advance_demo_prices", ROOT / "tools" / "advance_demo_prices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_advance_accepts_the_rows_a_recorded_split_explains(tmp_path, monkeypatch):
    """QUAL goes 4-for-1 inside the advance's window. The fetch into the scratch copy
    rescales QUAL's history before the ex-date; the advance writes those rows and the
    split's record with the new closes, and a second run writes nothing."""
    tool = _advance_tool()
    copy = tmp_path / "book.db"
    shutil.copyfile(FROZEN_BOOK, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(tool, "DEMO_DB", copy)
    monkeypatch.setattr(tool, "PAUSE_SECONDS", 0)
    before = _closes(copy)["QUAL"]
    divs = dict(_rows(copy, "SELECT ex_date, amount FROM dividends WHERE ticker = 'QUAL'"))
    _yahoo(monkeypatch, copy, {"QUAL": (4, 1)}, daily=tool.DAILY)

    report = tool.advance(date(2026, 7, 24), today=date(2026, 7, 27))

    after = _closes(copy)["QUAL"]
    assert all(after[d] == pytest.approx(c / 4, rel=1e-12) for d, c in before.items())
    assert after[EX] == pytest.approx(before[LAST] / 4, rel=1e-12)
    assert dict(_rows(copy, "SELECT ex_date, amount FROM dividends WHERE ticker = 'QUAL'")) \
        == pytest.approx({d: a / 4 for d, a in divs.items()})
    assert _rows(copy, "SELECT ticker, ex_date, ratio, state FROM splits") == [
        ("QUAL", EX, 4.0, "rescaled")]
    assert report["prices"][1] > report["prices"][0]

    sha = hashlib.sha256(copy.read_bytes()).hexdigest()
    tool.advance(date(2026, 7, 24), today=date(2026, 7, 27))
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == sha
