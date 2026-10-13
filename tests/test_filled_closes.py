"""The books' 33 missing closes are filled from the provider, and Q2 2026's lock is
restated for the 32 it froze, as an error correction (#491).

data/demo.db and the frozen book held no close on six NYSE sessions of May 2026 for ten
funds, nor for BTC-USD on July 8, 2026: each between two stored closes of its ticker,
where nothing re-reads. The Q2 2026 lock was taken from that book, so its two price
frames held nulls in 32 cells. Pinned here:

  * both books hold all 33 closes, and the same ones;
  * tools/fill_demo_price_gaps.py restores the filled book from a book without them,
    writes nothing on a second run, lists and keeps a stored close within half a cent of
    the provider's, and refuses anything else, leaving both books byte-identical;
  * the four other locks are byte-identical to main's at 1e3e5c9, and Q2 2026's with the
    restatement undone is main's too, so nothing else in it changed;
  * its close frame differs in the 32 cells alone, and its adjusted frame in those and in
    SCHP's and VGIT's levels before June 1; a fresh capture from the filled book takes
    the same frames; no committed lock holds a null inside a ticker's history;
  * tools/restate_demo_filled_closes.py restores the committed lock exactly from the lock
    before it, changes nothing on a second run, and refuses a book, a lock or a change it
    does not name;
  * the cover says so in one sentence, on Q2 2026 alone.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from markupsafe import escape

from tests.conftest import (
    FROZEN_BOOK,
    pin_today,
    point_at_frozen_book,
    q2_2026_before_its_closes_were_filled,
    unpin_leftovers,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo.db"
BOOKS = [DEMO, FROZEN_BOOK]
OTHERS = ["2025Q2", "2025Q3", "2025Q4", "2026Q1"]
RESTATED_ON = date(2026, 10, 13)
SENTENCE = ("Restated October 13, 2026 to correct an error. The lock froze 32 closes "
            "missing from May 2026; they are now filled from the provider.")
# Each lock's stored payload as its SHA-256, read off main's data/demo.db at 1e3e5c9,
# before the restatement. The frozen book's five were the same.
MAIN = {
    "2025Q2": "328256b86e80e21d4f3f5ffdd1d1c68513a9e55a208cbc1e2ebdcea1f4bef88c",
    "2025Q3": "d5bfda797eafba9b23e066c5f38878fc65a6145c78b7643c5345f5fe3f1cf8c9",
    "2025Q4": "75df988319b18ea2840bac6d7557a8eb81e1e883cb5b3796a02c92fbf57605cd",
    "2026Q1": "4e2a6fc7a4197d6dc9e09f37fe5686e40d63af8f4ecae39e3864c8a80c8d69c4",
    "2026Q2": "5a2be5b79e11020df0a5df29c148407ecf1aa856c7d73e53d91494f2646875e6",
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _locks(book) -> "dict[str, tuple]":
    con = _ro(book)
    try:
        return {r[0]: tuple(r[1:]) for r in con.execute(
            "SELECT quarter_id, snapshot_date, captured_at, snapshot_data FROM "
            "quarter_snapshots")}
    finally:
        con.close()


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run(book, sql, *args) -> int:
    con = sqlite3.connect(book)
    try:
        with con:
            return con.execute(sql, args).rowcount
    finally:
        con.close()


# ── the closes ───────────────────────────────────────────────────────────────

def test_the_named_closes_are_33_and_both_books_hold_the_same_ones():
    fill = _load("fill_demo_price_gaps")
    assert len(fill.CELLS) == len(set(fill.CELLS)) == 33
    per_day: dict = {}
    for _ticker, day in fill.CELLS:
        per_day[day] = per_day.get(day, 0) + 1
    assert per_day == {"2026-05-12": 3, "2026-05-13": 3, "2026-05-14": 3, "2026-05-15": 3,
                       "2026-05-28": 10, "2026-05-29": 10, "2026-07-08": 1}
    assert {t for t, d in fill.CELLS if d == "2026-07-08"} == {"BTC-USD"}
    demo, frozen = fill._closes(DEMO), fill._closes(FROZEN_BOOK)
    assert all(demo[c] > 0 for c in fill.CELLS)
    assert [frozen[c] for c in fill.CELLS] == [demo[c] for c in fill.CELLS]


# ── the fill tool, offline, on trimmed copies of the frozen book ─────────────

SINCE, LATER = "2026-01-01", "2026-03-01"


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _provider(closes: dict, dividends: dict, fail=()):
    """A stand-in for the price provider that reports exactly ``closes`` and
    ``dividends``: the chart JSON for the window asked, an exchange-traded ticker's bar at
    13:30 UTC and a daily series' at 00:00 UTC."""
    from tools.advance_demo_prices import DAILY

    def _stamp(ticker, day):
        d = date.fromisoformat(day)
        hour = 0 if ticker in DAILY else 13
        return int(datetime(d.year, d.month, d.day, hour, 30 if hour else 0,
                            tzinfo=timezone.utc).timestamp())

    def get(url, params=None, timeout=None):
        ticker = unquote(url.rsplit("/", 1)[1])
        if ticker in fail:
            raise OSError("provider down")
        lo, hi = params["period1"], params["period2"]
        bars = sorted((_stamp(t, d), c) for (t, d), c in closes.items()
                      if t == ticker and lo <= _stamp(t, d) <= hi)
        events = {str(_stamp("SPY", d)): {"amount": a} for (t, d), a in dividends.items()
                  if t == ticker and lo <= _stamp("SPY", d) <= hi}
        # A session that ended long ago, so the price layer reads every bar as settled.
        meta = {"currentTradingPeriod": {"regular": {"start": lo, "end": lo + 1}}}
        values = [c for _ts, c in bars]
        return _Resp({"chart": {"result": [{
            "meta": meta, "timestamp": [ts for ts, _c in bars],
            "indicators": {"quote": [{"close": values}], "adjclose": [{"adjclose": values}]},
            "events": {"dividends": events}}]}})

    return get


def _trimmed(dst: Path, since: str, without=()) -> Path:
    """A copy of the frozen book holding prices and dividends from ``since`` on, less the
    closes in ``without``."""
    shutil.copyfile(FROZEN_BOOK, dst)
    os.chmod(dst, 0o644)
    con = sqlite3.connect(dst)
    try:
        with con:
            con.execute("DELETE FROM prices WHERE price_date < ?", (since,))
            con.execute("DELETE FROM dividends WHERE ex_date < ?", (since,))
            for ticker, day in without:
                assert con.execute("DELETE FROM prices WHERE ticker = ? AND price_date = ?",
                                   (ticker, day)).rowcount == 1
    finally:
        con.close()
    return dst


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """The fill tool pointed at two books that lack the 33 closes, the second a later
    slice of the first as the frozen book is of demo.db, and a provider that reports the
    committed frozen book's rows."""
    import src.prices as prices
    tool = _load("fill_demo_price_gaps")
    truth = _trimmed(tmp_path / "truth.db", SINCE)
    demo = _trimmed(tmp_path / "demo.db", SINCE, without=tool.CELLS)
    frozen = _trimmed(tmp_path / "frozen_book.db", LATER, without=tool.CELLS)
    monkeypatch.setattr(tool, "DEMO_DB", demo)
    monkeypatch.setattr(tool, "FROZEN_BOOK", frozen)
    monkeypatch.setattr(tool, "PAUSE_SECONDS", 0)
    s = SimpleNamespace(tool=tool, demo=demo, frozen=frozen,
                        closes=tool._closes(truth), divs=tool._dividends(truth))
    s.serve = lambda closes=None, divs=None, fail=(): monkeypatch.setattr(
        prices._SESSION, "get",
        _provider(s.closes if closes is None else closes,
                  s.divs if divs is None else divs, fail))
    s.serve()
    return s


def test_the_fill_restores_the_filled_book_and_a_second_run_writes_nothing(staged):
    from tools.fingerprint_db import fingerprint
    tool = staged.tool
    assert not [c for c in tool.CELLS if c in tool._closes(staged.demo)], "premise: lacking"
    before = {b: fingerprint(b) for b in (staged.demo, staged.frozen)}

    report = tool.fill()

    assert tool._closes(staged.demo) == staged.closes
    assert tool._closes(staged.frozen) == {k: c for k, c in staged.closes.items()
                                           if k[1] >= LATER}
    for book in (staged.demo, staged.frozen):
        after = fingerprint(book)
        assert sorted(t for t in before[book] if after[t] != before[book][t]) == ["prices"]
        assert after["prices"][0] == before[book]["prices"][0] + 33
    assert report["written"] == {"demo.db": 33, "frozen_book.db": 33} and report["kept"] == []

    once = (_sha(staged.demo), _sha(staged.frozen))
    again = tool.fill()
    assert (_sha(staged.demo), _sha(staged.frozen)) == once
    assert again["written"] == {"demo.db": 0, "frozen_book.db": 0}


def test_a_stored_close_within_half_a_cent_stays_and_is_listed(staged):
    tool, key = staged.tool, ("VOO", "2026-04-15")
    stored = staged.closes[key]
    staged.serve(closes={**staged.closes, key: stored + 0.004})
    report = tool.fill()
    assert report["kept"] == [("VOO", "2026-04-15", stored, stored + 0.004)]
    assert tool._closes(staged.demo)[key] == stored, "the stored close was not replaced"
    assert all(c in tool._closes(staged.demo) for c in tool.CELLS)


def _without(rows: dict, key) -> dict:
    assert key in rows, key
    return {k: v for k, v in rows.items() if k != key}


@pytest.mark.parametrize("why, serve, message", [
    ("a named close is not reported",
     lambda s: {"closes": _without(s.closes, ("VOO", "2026-05-28"))},
     "not reported [('VOO', '2026-05-28')]"),
    ("the provider reports a close that is not named",
     lambda s: {"closes": {**s.closes, ("VOO", "2026-06-06"): 700.0}},
     "not named [('VOO', '2026-06-06')]"),
    ("a stored close differs by more than half a cent",
     lambda s: {"closes": {**s.closes, ("VOO", "2026-04-15"): s.closes[("VOO", "2026-04-15")] + 0.006}},
     "1 stored close(s) differ from the provider's by more than 0.005"),
    ("the provider no longer reports a stored close",
     lambda s: {"closes": _without(s.closes, ("VOO", "2026-04-15"))},
     "the provider no longer reports 1 close(s) the book holds"),
    ("a dividend differs",
     lambda s: {"divs": {**s.divs, ("SCHP", "2026-06-01"): s.divs[("SCHP", "2026-06-01")] + 0.01}},
     "the provider's dividends are not the book's: 0 added [], 0 gone [], 1 changed"),
    ("one fetch fails", lambda s: {"fail": {"VTV"}}, "the fetch failed for VTV"),
])
def test_anything_else_refuses_the_fill_and_leaves_both_books(staged, why, serve, message):
    staged.serve(**serve(staged))
    before = (_sha(staged.demo), _sha(staged.frozen))
    with pytest.raises(staged.tool.FillError) as err:
        staged.tool.fill()
    assert message in str(err.value), why
    assert (_sha(staged.demo), _sha(staged.frozen)) == before, why


def test_books_the_fill_does_not_describe_are_refused(staged):
    tool = staged.tool
    # The frozen book holds a close that is not demo.db's.
    assert _run(staged.frozen, "UPDATE prices SET close = close + 1 WHERE ticker = 'VOO' "
                               "AND price_date = '2026-04-15'") == 1
    before = (_sha(staged.demo), _sha(staged.frozen))
    with pytest.raises(tool.FillError, match="holds 1 price row"):
        tool.fill()
    assert (_sha(staged.demo), _sha(staged.frozen)) == before
    # One book filled and the other not.
    _trimmed(staged.frozen, LATER)
    before = (_sha(staged.demo), _sha(staged.frozen))
    with pytest.raises(tool.FillError, match="already hold some of the 33 closes"):
        tool.fill()
    assert (_sha(staged.demo), _sha(staged.frozen)) == before


def test_the_command_reports_last_and_aborts_with_exit_1(staged, capsys):
    tool = staged.tool
    staged.serve(fail={"SPY"})
    assert tool.main() == 1
    assert capsys.readouterr().out.startswith("ABORT: the fetch failed for SPY")
    staged.serve()
    assert tool.main() == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "Filled 33 closes in demo.db and frozen_book.db."
    assert "demo.db: prices " in out[-3] and "(+33); every other table unchanged" in out[-3]
    assert tool.main() == 0
    assert capsys.readouterr().out.strip() == (
        "demo.db and frozen_book.db already hold all 33 closes. Nothing was written.")


# ── the committed locks ─────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_nothing_but_q2_2026s_frames_and_record_changed_in_any_lock(book):
    """The four other locks are main's bytes. Q2 2026's, with its frames recomputed
    without the 32 closes and its record removed, is main's bytes too."""
    locks = _locks(book)
    assert set(OTHERS + ["2026Q2"]) <= set(locks)
    for q in OTHERS:
        assert _digest(locks[q][2]) == MAIN[q], (book.name, q)
    text = locks["2026Q2"][2]
    assert _digest(text) != MAIN["2026Q2"], "premise: the lock was restated"
    assert _digest(q2_2026_before_its_closes_were_filled(text)) == MAIN["2026Q2"], book.name
    assert locks["2026Q2"][:2] == ("2026-06-30", "2026-09-26T02:07:28+00:00"), (
        "its date and capture time are kept")


def test_q2_2026s_frames_differ_only_where_named():
    restate, fill = _load("restate_demo_filled_closes"), _load("fill_demo_price_gaps")
    text = _locks(DEMO)["2026Q2"][2]
    assert _locks(FROZEN_BOOK)["2026Q2"][2] == text, "the two books' locks are one text"
    new, old = json.loads(text), json.loads(q2_2026_before_its_closes_were_filled(text))
    record = new["price_corrections"]
    cells = {tuple(c) for c in record["filled"]}
    assert record["restated_on"] == RESTATED_ON.isoformat() and len(cells) == 32
    assert cells == {c for c in fill.CELLS if c[0] != "BTC-USD"}

    diff = restate._differences(old, new)
    assert set(diff["close"]) == cells
    assert all(a is None and b > 0 for a, b in diff["close"].values())
    closes = fill._closes(DEMO)
    assert all(abs(diff["close"][c][1] - closes[c]) < 1e-9 for c in cells), "the book's closes"
    assert cells <= set(diff["adj_close"])
    assert all(diff["adj_close"][c][0] is None and diff["adj_close"][c][1] > 0 for c in cells)

    moved = {k: v for k, v in diff["adj_close"].items() if k not in cells}
    assert {t for t, _d in moved} == {"SCHP", "VGIT"}
    assert max(d for _t, d in moved) == "2026-05-27", "nothing on or after the June 1 ex-date"
    assert len(diff["adj_close"]) == 32 + len(moved) == 3248
    # Each moved by the one ratio its June 1 dividend's two bases give, in basis points.
    shift = {t: {round((b / a - 1) * 1e4, 4) for (tk, _d), (a, b) in moved.items() if tk == t}
             for t in ("SCHP", "VGIT")}
    assert shift == {"SCHP": {0.228}, "VGIT": {0.0782}}


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_no_committed_lock_holds_a_null_inside_a_tickers_history(book):
    for q, (_end, _at, text) in _locks(book).items():
        blob = json.loads(text)
        for basis in ("adj_close", "close"):
            frame = blob[basis]
            for col, ticker in enumerate(frame["columns"]):
                values = [row[col] for row in frame["data"]]
                first = next(i for i, v in enumerate(values) if v is not None)
                assert None not in values[first:], (book.name, q, basis, ticker)


def test_before_the_restatement_q2_2026_held_the_32_nulls():
    """The contrast for the test above: the same scan finds the 32 in the lock as it was."""
    old = json.loads(q2_2026_before_its_closes_were_filled(_locks(DEMO)["2026Q2"][2]))
    for basis in ("adj_close", "close"):
        frame, nulls = old[basis], 0
        for col in range(len(frame["columns"])):
            values = [row[col] for row in frame["data"]]
            first = next(i for i, v in enumerate(values) if v is not None)
            nulls += values[first:].count(None)
        assert nulls == 32, basis


def test_a_fresh_capture_from_the_filled_book_takes_the_same_frames(tmp_path, monkeypatch):
    """The restated frames are what the lock rule takes from the book as it now stands."""
    import src.db as db
    import src.prices as prices
    from src.cache import capture_quarter_snapshot
    committed = json.loads(_locks(DEMO)["2026Q2"][2])
    copy = tmp_path / "demo.db"
    shutil.copyfile(DEMO, copy)
    os.chmod(copy, 0o644)
    assert _run(copy, "DELETE FROM quarter_snapshots WHERE quarter_id = '2026Q2'") == 1
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(
        OSError("offline")))
    capture_quarter_snapshot("2026Q2")
    fresh = json.loads(_locks(copy)["2026Q2"][2])
    assert fresh["close"] == committed["close"]
    assert fresh["adj_close"] == committed["adj_close"]


# ── the restatement tool ─────────────────────────────────────────────────────

@pytest.fixture
def unrestated(tmp_path, monkeypatch):
    """(the tool, a copy of demo.db, a copy of the frozen book), Q2 2026's lock put back
    in each as it stood before the restatement: the before-image, rebuilt from the
    committed after-image."""
    tool = _load("restate_demo_filled_closes")
    copies = []
    for book in BOOKS:
        copy = tmp_path / book.name
        shutil.copyfile(book, copy)
        os.chmod(copy, 0o644)
        before = q2_2026_before_its_closes_were_filled(_locks(book)["2026Q2"][2])
        assert _run(copy, "UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                          "quarter_id = '2026Q2'", before) == 1
        copies.append(copy)
    monkeypatch.setattr(tool, "DEMO_DB", copies[0])
    monkeypatch.setattr(tool, "FROZEN_BOOK", copies[1])
    return tool, copies[0], copies[1]


def test_the_tool_restores_the_committed_locks_exactly_and_twice_changes_nothing(
        unrestated, capsys):
    tool, demo, frozen = unrestated
    assert _digest(_locks(demo)["2026Q2"][2]) == MAIN["2026Q2"], "premise: main's lock"
    assert tool.main(RESTATED_ON) == 0
    assert capsys.readouterr().out.strip() == (
        "2026Q2 restated on 2026-10-13 in demo.db and frozen_book.db: 32 closes filled.")
    assert _locks(demo) == _locks(DEMO) and _locks(frozen) == _locks(FROZEN_BOOK)
    assert tool.main(date(2026, 10, 14)) == 0
    assert "already restated" in capsys.readouterr().out
    assert _locks(demo) == _locks(DEMO) and _locks(frozen) == _locks(FROZEN_BOOK)


def _lock_differs(book):
    text = _locks(book)["2026Q2"][2]
    assert text.count('"gaps": []') == 1
    assert _run(book, "UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = "
                      "'2026Q2'", text.replace('"gaps": []', '"gaps": [["XYZ", "a gap"]]')) == 1


_DROP = "DELETE FROM prices WHERE ticker = 'VOO' AND price_date = '2026-05-28'"
_MOVE = "UPDATE prices SET close = close + 1 WHERE ticker = 'VOO' AND price_date = '2026-04-15'"


@pytest.mark.parametrize("why, stage, message", [
    ("the book lacks a close to restate with",
     lambda tool, demo, frozen, mp: (_run(demo, _DROP), _run(frozen, _DROP)),
     "lacks 1 of the 32 closes"),
    ("the frozen book's closes are not demo.db's",
     lambda tool, demo, frozen, mp: _run(frozen, _DROP),
     "closes are not demo.db's for 1 of the named cells"),
    ("a close outside the 32 changed since the lock was taken",
     lambda tool, demo, frozen, mp: _run(demo, _MOVE),
     "the frames are not the lock's own (2 cell(s) differ)"),
    ("the frozen book's lock is not demo.db's",
     lambda tool, demo, frozen, mp: _lock_differs(frozen),
     "frozen_book.db's 2026Q2 payload update changed 0 rows, not 1"),
    ("a level moves that the tool does not name",
     lambda tool, demo, frozen, mp: mp.setattr(tool, "REANCHORED", {"SCHP": "2026-06-01"}),
     "moved outside the filled cells and ['SCHP']'s earlier levels: 1608 cell(s)"),
    ("a named ticker has no filled close before its ex-date",
     lambda tool, demo, frozen, mp: mp.setattr(
         tool, "REANCHORED", {**tool.REANCHORED, "TIP": "2026-06-01"}),
     "TIP's 2026-06-01 dividend has no filled close before it"),
])
def test_the_tool_refuses_what_it_does_not_name_and_changes_neither_book(
        unrestated, monkeypatch, why, stage, message):
    tool, demo, frozen = unrestated
    stage(tool, demo, frozen, monkeypatch)
    before = (_locks(demo), _locks(frozen))
    with pytest.raises(SystemExit) as err:
        tool.main(RESTATED_ON)
    assert message in str(err.value), why
    assert (_locks(demo), _locks(frozen)) == before, why


# ── the cover ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_q2_2026_says_so_on_its_cover_and_no_other_quarter_does(book, tmp_path, monkeypatch):
    import pandas as pd
    import src.db as db
    from src.cache import SnapshotFrames, filled_closes_note, get_quarter_snapshot
    copy = tmp_path / book.name
    shutil.copyfile(book, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    assert filled_closes_note(get_quarter_snapshot("2026Q2")[0]) == SENTENCE
    for q in OTHERS:
        assert filled_closes_note(get_quarter_snapshot(q)[0]) is None, (book.name, q)
    assert filled_closes_note(SnapshotFrames(adj_close=pd.DataFrame())) is None
    assert filled_closes_note(None) is None


def test_the_sentence_counts_and_dates_from_the_locks_own_record():
    import pandas as pd
    from src.cache import SnapshotFrames, filled_closes_note
    snap = SnapshotFrames(adj_close=pd.DataFrame(), price_corrections={
        "restated_on": "2027-01-05",
        "filled": [["VOO", "2026-11-30"], ["VOO", "2026-12-01"], ["VTV", "2026-12-01"]]})
    assert filled_closes_note(snap) == (
        "Restated January 5, 2027 to correct an error. The lock froze 3 closes missing "
        "from November 2026 and December 2026; they are now filled from the provider.")


def test_the_q2_report_carries_the_sentence_once(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch)
    point_at_frozen_book(monkeypatch, tmp_path)
    try:
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
    finally:
        unpin_leftovers()
    assert html.count(str(escape(SENTENCE))) == 1
    template = (ROOT / "templates" / "quarterly_report.html").read_text(encoding="utf-8")
    assert template.count("{{ filled_closes_note }}") == 1
