"""Every CAPE reading is dated, and a quarter lock takes its last month only from a
reading made after that month ended (#478 I06, with I07's credit).

multpl's top row is the current month's reading so far, and the file stores it under
that month. With no read date beside it, nothing told it from the month's own figure:
the 2026-09-25 refresh filed 41.48 under September, a Q3 lock would have held it as
"the quarter's last monthly reading", and multpl's September row read 40.90 once the
month had ended. The PDF also credited CAPE to Yale, which is only the fallback.

Pinned here:
  * the committed file gives every reading a read date and a source, and every month
    but the latest was read after it ended;
  * the refresh writes them, and re-dates only what is new, revised, or read for the
    first time after its month ended;
  * the lock's gate, from both sides: a reading made on the month's last session or
    before it waits, one made the day after locks;
  * a new lock holds each reading's read date and source; the five committed locks
    hold none, stay as they were, and their text claims none;
  * the PDF credits multpl.com and prints the read date with every CAPE figure; a
    figure read from Yale says so.

The pages are rendered in tests/render/test_cape_read_dates_render.py.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from markupsafe import escape

from tests.conftest import FROZEN_BOOK, pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", FROZEN_BOOK]
COMMITTED = {"2025Q2": "June 2025", "2025Q3": "September 2025", "2025Q4": "December 2025",
             "2026Q1": "March 2026", "2026Q2": "June 2026"}
NOT_RECORDED = "its read date and source were not recorded"


# ── the committed file ───────────────────────────────────────────────────────

def test_every_committed_reading_records_its_read_date_and_source():
    from src import shiller
    raw = pd.read_csv(shiller._CACHE_CSV, dtype=str)
    assert list(raw.columns) == ["date", "cape", "read_on", "source"]
    assert raw["read_on"].notna().all() and raw["source"].notna().all()
    assert set(raw["source"]) <= {shiller.MULTPL, shiller.YALE}
    r = shiller.get_cape_readings()
    assert len(r) == len(raw) > 1800, "premise: the scan reaches the whole record"
    early = [m.date().isoformat() for m, d in zip(r.index[:-1], r["read_on"][:-1])
             if not shiller.read_after_month_end(m, d)]
    assert early == [], ("only the latest month may be a reading made before its month "
                         f"ended: {early}")


def test_the_series_is_the_readings_values():
    from src import shiller
    r, s = shiller.get_cape_readings(), shiller.get_cape_series()
    assert s.name == "CAPE" and s.index.equals(r.index)
    assert s.tolist() == r["cape"].tolist()
    latest = shiller.latest_reading()
    assert latest.value == shiller.current_cape() and latest.month == r.index[-1].date()
    assert latest.read_on == r["read_on"].iloc[-1] and latest.source == r["source"].iloc[-1]


def test_a_file_with_no_read_dates_reads_as_undated(tmp_path, monkeypatch):
    """A file from before readings were dated: the values load, and no date is invented."""
    from src import shiller
    (tmp_path / "old.csv").write_text("date,cape\n2026-05-01,40.11\n2026-06-01,40.16\n")
    monkeypatch.setattr(shiller, "_CACHE_CSV", tmp_path / "old.csv")
    r = shiller.get_cape_readings()
    assert r["cape"].tolist() == [40.11, 40.16]
    assert r["read_on"].tolist() == [None, None] and r["source"].tolist() == [None, None]
    assert shiller.latest_reading() == shiller.CapeReading(date(2026, 6, 1), 40.16, None, None)


# ── a reading made after its month ended ────────────────────────────────────

@pytest.mark.parametrize("month, read_on, after", [
    ("2026-09-01", date(2026, 9, 25), False),   # the reading the file held: mid-month
    ("2026-09-01", date(2026, 9, 30), False),   # the last session's own date
    ("2026-09-01", date(2026, 10, 1), True),    # the day after it
    ("2026-05-01", date(2026, 5, 29), False),   # May 2026 ends on a Sunday: Friday the 29th
    ("2026-05-01", date(2026, 5, 30), True),    # ... so Saturday the 30th is after it
    ("2027-05-01", date(2027, 5, 28), False),   # May 31, 2027 is Memorial Day: Friday the 28th
    ("2027-05-01", date(2027, 5, 29), True),    # a weekday-only calendar would say False
    ("2026-09-01", None, False),                # no read date: it cannot be shown to be one
])
def test_read_after_month_end_is_two_sided(month, read_on, after):
    from src.demo_refresh import is_session
    from src.shiller import read_after_month_end
    assert not is_session(date(2027, 5, 31)) and date(2027, 5, 31).weekday() == 0, (
        "premise: the month's last weekday is an NYSE holiday")
    assert is_session(date(2026, 9, 30)) and date(2026, 5, 31).weekday() == 6
    assert read_after_month_end(pd.Timestamp(month), read_on) is after


# ── the refresh writes the read date ────────────────────────────────────────

def _stored(rows) -> pd.DataFrame:
    idx = pd.DatetimeIndex([r[0] for r in rows], name="date")
    out = pd.DataFrame({"cape": [r[1] for r in rows]}, index=idx)
    out["read_on"] = pd.Series([r[2] for r in rows], index=idx, dtype=object)
    out["source"] = pd.Series([r[3] for r in rows], index=idx, dtype=object)
    return out


def _fetched(rows, source="multpl") -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime([r[0] for r in rows]),
                         "cape": [r[1] for r in rows], "source": source})


def test_a_refresh_dates_what_is_new_revised_or_first_read_after_its_month_ended():
    from src.shiller import stamp_readings
    stored = _stored([
        ("2026-06-01", 40.16, date(2026, 8, 12), "multpl"),   # settled and unchanged: kept
        ("2026-07-01", 40.50, date(2026, 8, 12), "multpl"),   # settled, revised since
        ("2026-08-01", 41.13, date(2026, 8, 12), "multpl"),   # same value, read mid-month
        ("2026-09-01", 41.48, date(2026, 9, 25), "multpl"),   # read mid-month, since moved
        ("2026-11-01", 39.00, date(2026, 12, 2), "multpl"),   # the source no longer returns it
    ])
    fetched = _fetched([("2026-06-01", 40.16), ("2026-07-01", 40.02), ("2026-08-01", 41.13),
                        ("2026-09-01", 40.90), ("2026-10-01", 41.38)])
    out = stamp_readings(fetched, stored, date(2026, 10, 4))
    assert list(out.columns) == ["date", "cape", "read_on", "source"]
    assert out.values.tolist() == [
        ["2026-06-01", 40.16, "2026-08-12", "multpl"],
        ["2026-07-01", 40.02, "2026-10-04", "multpl"],
        ["2026-08-01", 41.13, "2026-10-04", "multpl"],
        ["2026-09-01", 40.90, "2026-10-04", "multpl"],
        ["2026-10-01", 41.38, "2026-10-04", "multpl"],
    ]


def test_a_refresh_that_fell_back_to_yale_says_so_on_every_reading_it_took():
    from src.shiller import stamp_readings
    stored = _stored([("2023-08-01", 30.5, date(2026, 8, 12), "multpl"),
                      ("2023-09-01", 30.1, date(2026, 8, 12), "multpl")])
    out = stamp_readings(_fetched([("2023-08-01", 30.5), ("2023-09-01", 30.1)], "yale"),
                         stored, date(2026, 10, 4))
    assert out["source"].tolist() == ["yale", "yale"], "the same value from another source"
    assert out["read_on"].tolist() == ["2026-10-04", "2026-10-04"]


def test_a_first_refresh_dates_every_reading(tmp_path):
    from src.shiller import stamp_readings
    for stored in (None, _stored([("2026-06-01", 40.16, None, None)])):
        out = stamp_readings(_fetched([("2026-06-01", 40.16)]), stored, date(2026, 10, 4))
        assert out.values.tolist() == [["2026-06-01", 40.16, "2026-10-04", "multpl"]]


def test_the_fetch_names_the_source_that_answered(monkeypatch):
    from src import shiller

    class _Resp:
        def __init__(self, text=None, content=None):
            self.text, self.content = text, content

        def raise_for_status(self):
            pass

    html = ("<table><thead><tr><th>Date</th><th>Value</th></tr></thead><tbody>"
            "<tr><td>Sep 1, 2026</td><td>40.90</td></tr></tbody></table>")
    monkeypatch.setattr(shiller.requests, "get", lambda url, **k: _Resp(text=html))
    assert shiller.fetch_cape_dataframe()["source"].tolist() == ["multpl"]

    def _multpl_down(url, **k):
        if url == shiller._MULTPL_URL:
            raise OSError("multpl down")
        return _Resp(content=b"xls")

    monkeypatch.setattr(shiller.requests, "get", _multpl_down)
    monkeypatch.setattr(shiller, "_parse_excel_bytes", lambda b: pd.DataFrame(
        {"date": pd.to_datetime(["2023-09-01"]), "cape": [30.1], "sp500_real": [1.0],
         "earnings_real": [1.0]}))
    df = shiller.fetch_cape_dataframe()
    assert list(df.columns) == ["date", "cape", "source"] and df["source"].tolist() == ["yale"]


def test_the_refresh_tool_writes_the_read_date_and_the_source(tmp_path, monkeypatch, capsys):
    import src.asof as asof
    import tools.refresh_market_data as tool
    from src import shiller
    csv = tmp_path / "cape.csv"
    csv.write_text("date,cape\n2026-08-01,41.13\n2026-09-01,41.48\n")
    monkeypatch.setattr(shiller, "_CACHE_CSV", csv)
    monkeypatch.setattr(asof, "today_et", lambda now=None: date(2026, 10, 4))
    fetched = _fetched([("2026-08-01", 41.13), ("2026-09-01", 40.90), ("2026-10-01", 41.38)])
    monkeypatch.setitem(tool._TARGETS, "cape", (csv, lambda: fetched, tool._write_cape,
                                               shiller.cape_frontier))
    assert tool.refresh(["cape"]) == 0
    assert csv.read_text().split() == [
        "date,cape,read_on,source", "2026-08-01,41.13,2026-10-04,multpl",
        "2026-09-01,40.9,2026-10-04,multpl", "2026-10-01,41.38,2026-10-04,multpl"]
    out = capsys.readouterr().out
    assert "source: multpl" in out and "REFRESHED  2026-09-01 -> 2026-10-01" in out

    # A second refresh a day later re-dates only October, the month still open.
    monkeypatch.setattr(asof, "today_et", lambda now=None: date(2026, 10, 5))
    assert tool.refresh(["cape"]) == 0
    assert [ln.split(",")[2] for ln in csv.read_text().split()[1:]] == [
        "2026-10-04", "2026-10-04", "2026-10-05"]


# ── the wording ──────────────────────────────────────────────────────────────

def test_a_figure_states_when_and_where_it_was_read():
    from src.shiller import CapeReading, read_clause, read_short, reading_label
    multpl = CapeReading(date(2026, 9, 1), 40.9, date(2026, 10, 4), "multpl")
    yale = CapeReading(date(2023, 9, 1), 30.1, date(2026, 10, 4), "yale")
    undated = CapeReading(date(2026, 6, 1), 40.16, None, None)
    assert read_clause(multpl) == "read October 4, 2026 from multpl.com"
    assert read_clause(yale) == ("read October 4, 2026 from Robert Shiller's Yale data file, "
                                 "the fallback source")
    assert read_clause(undated) == NOT_RECORDED
    assert read_short(multpl) == "read Oct 4, 2026"
    assert read_short(yale) == "read Oct 4, 2026 from Yale, the fallback"
    assert read_short(undated) == "read date not recorded"
    assert reading_label(multpl) == "September 2026, read October 4, 2026 from multpl.com"
    assert reading_label(undated, "the quarter's last monthly reading") == (
        f"June 2026, the quarter's last monthly reading; {NOT_RECORDED}")


# ── the lock's gate ──────────────────────────────────────────────────────────

def _extend_prices_through(book, day: str):
    """Carry every ticker's last close forward, weekdays, through ``day``."""
    con = sqlite3.connect(book)
    last = {t: (d, c) for t, d, c in con.execute(
        "SELECT p.ticker, p.price_date, p.close FROM prices p JOIN (SELECT ticker, "
        "MAX(price_date) mx FROM prices GROUP BY ticker) m ON p.ticker = m.ticker "
        "AND p.price_date = m.mx")}
    rows = []
    for t, (d, c) in last.items():
        cur = date.fromisoformat(d) + timedelta(days=1)
        while cur <= date.fromisoformat(day):
            if cur.weekday() < 5:
                rows.append((t, cur.isoformat(), c))
            cur += timedelta(days=1)
    with con:
        con.executemany("INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close) "
                        "VALUES (?, ?, ?, NULL)", rows)
    con.close()


def _september(tmp_path, monkeypatch, value, read_on, source="multpl", name="cape.csv"):
    """The committed file through August, with September as given."""
    from src import shiller
    df = pd.read_csv(ROOT / "data" / "shiller_cape.csv", dtype=str)
    df = df[df["date"] < "2026-09-01"]
    if value is not None:
        df = pd.concat([df, pd.DataFrame([{"date": "2026-09-01", "cape": str(value),
                                           "read_on": read_on, "source": source}])])
    df.to_csv(tmp_path / name, index=False)
    monkeypatch.setattr(shiller, "_CACHE_CSV", tmp_path / name)


@pytest.fixture
def q3_book(tmp_path, monkeypatch):
    """The frozen book on October 5, 2026, with prices carried through September 30."""
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch, date(2026, 10, 5))
    book = point_at_frozen_book(monkeypatch, tmp_path)
    _extend_prices_through(book, "2026-09-30")
    yield book
    unpin_leftovers()


def _exec_cape(snap) -> str:
    from src import reports
    from src.cache import snapshot_price_context
    with snapshot_price_context(snap):
        narrative = reports._build_executive_summary("2026-06-30", "2026-09-30")["narrative"]
    (sentence,) = [s for s in narrative if "CAPE" in s]
    return sentence


@pytest.mark.parametrize("read_on, why", [
    ("2026-09-25", "the reading the file held, made mid-month"),
    ("2026-09-30", "made on the month's last session"),
    (None, "no read date"),
])
def test_q3_waits_for_a_september_reading_made_after_september_ended(
        q3_book, tmp_path, monkeypatch, read_on, why):
    from src.cache import capture_quarter_snapshot
    from src.input_lock import CAPE
    _september(tmp_path, monkeypatch, 41.48, read_on)
    snap, _ = capture_quarter_snapshot("2026Q3")
    assert CAPE not in snap.inputs, why
    assert snap.inputs_pending[CAPE] == "2026-08-01", (
        "pending names the latest month with a reading made after it ended")
    assert _exec_cape(snap) == (
        "CAPE for the quarter's last month is not yet on file as a reading made after the "
        "month ended (the latest such reading on file is for August 2026); the reading "
        "locks when it is.")


def test_q3_waits_when_september_is_not_on_file_at_all(q3_book, tmp_path, monkeypatch):
    from src.cache import capture_quarter_snapshot
    from src.input_lock import CAPE
    _september(tmp_path, monkeypatch, None, None)
    snap, _ = capture_quarter_snapshot("2026Q3")
    assert snap.inputs_pending[CAPE] == "2026-08-01" and CAPE not in snap.inputs


def test_a_file_with_no_dated_reading_says_none_is_on_file(q3_book, tmp_path, monkeypatch):
    from src import shiller
    from src.cache import capture_quarter_snapshot
    from src.input_lock import CAPE
    (tmp_path / "undated.csv").write_text("date,cape\n2026-08-01,41.13\n2026-09-01,41.48\n")
    monkeypatch.setattr(shiller, "_CACHE_CSV", tmp_path / "undated.csv")
    snap, _ = capture_quarter_snapshot("2026Q3")
    assert snap.inputs_pending[CAPE] is None
    assert "(no such reading is on file)" in _exec_cape(snap)


def test_q3_locks_the_reading_made_the_day_after_and_holds_its_read_date(
        q3_book, tmp_path, monkeypatch):
    """The waiting lock takes September once it is read after the month, as a dated
    reading; a later revision of the file then reaches neither the value nor the date."""
    from src.cache import (capture_quarter_snapshot, complete_quarter_inputs,
                           get_quarter_snapshot)
    from src.input_lock import CAPE
    _september(tmp_path, monkeypatch, 41.48, "2026-09-25")
    capture_quarter_snapshot("2026Q3")
    _september(tmp_path, monkeypatch, 40.9, "2026-10-01")
    snap = complete_quarter_inputs("2026Q3")
    assert CAPE not in snap.inputs_pending
    held = snap.inputs[CAPE]
    assert list(held.columns) == ["cape", "read_on", "source"]
    assert held.index.max() == pd.Timestamp("2026-09-01")
    assert held.iloc[-1].tolist() == [40.9, date(2026, 10, 1), "multpl"]
    assert _exec_cape(snap).endswith(
        "(September 2026, the quarter's last monthly reading, read October 1, 2026 from "
        "multpl.com).")
    assert "CAPE stands at 40.9x" in _exec_cape(snap)

    _september(tmp_path, monkeypatch, 99.0, "2026-11-02", name="revised.csv")
    again = complete_quarter_inputs("2026Q3")
    pd.testing.assert_frame_equal(again.inputs[CAPE], held)
    pd.testing.assert_frame_equal(get_quarter_snapshot("2026Q3")[0].inputs[CAPE], held)
    assert _exec_cape(again) == _exec_cape(snap)


def test_a_locked_reading_from_yale_says_so(q3_book, tmp_path, monkeypatch):
    from src.cache import capture_quarter_snapshot
    _september(tmp_path, monkeypatch, 40.9, "2026-10-01", source="yale")
    snap, _ = capture_quarter_snapshot("2026Q3")
    assert _exec_cape(snap).endswith(
        "(September 2026, the quarter's last monthly reading, read October 1, 2026 from "
        "Robert Shiller's Yale data file, the fallback source).")


def test_the_locked_readings_round_trip_exactly():
    from src import shiller
    from src.cache import _dec, _enc_cape
    r = shiller.get_cape_readings()
    back = _dec(json.loads(json.dumps(_enc_cape(r))))
    pd.testing.assert_frame_equal(back, r, check_exact=True)


# ── the five committed locks ─────────────────────────────────────────────────

def _payloads(book) -> "dict[str, dict]":
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return {q: json.loads(p) for q, p in con.execute(
            "SELECT quarter_id, snapshot_data FROM quarter_snapshots")}
    finally:
        con.close()


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_the_committed_locks_hold_cape_as_they_did_with_no_read_date(book):
    """Their payloads are pinned whole by tests/test_style_box_withdrawn.py; this names
    the part this change must not touch. A lock that holds read dates is the other kind."""
    payloads = _payloads(book)
    assert sorted(payloads) == sorted(COMMITTED)
    for q, blob in payloads.items():
        cape = blob["inputs"]["cape"]
        assert cape["kind"] == "series" and "read_on" not in cape and "source" not in cape, q


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_the_committed_locks_text_claims_no_read_date(book, tmp_path, monkeypatch):
    import src.db as db
    from src import reports, shiller
    from src.cache import get_quarter_snapshot, snapshot_price_context
    copy = tmp_path / book.name
    shutil.copyfile(book, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    today = shiller.latest_reading()
    assert today.read_on is not None, "premise: today's file has a read date to borrow"
    for q, month in COMMITTED.items():
        snap = get_quarter_snapshot(q)[0]
        with snapshot_price_context(snap):
            held = shiller.latest_reading()
            sentence = reports._cape_reading_sentence(held.value, 99.0)
        assert held.read_on is None and held.source is None, q
        assert sentence.endswith(
            f"({month}, the quarter's last monthly reading; {NOT_RECORDED})."), (q, sentence)
        assert "multpl" not in sentence and "read October" not in sentence, (q, sentence)


# ── the PDF ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def q2_report(tmp_path_factory):
    """The frozen book's committed Q2 2026 lock, rendered to HTML."""
    from src import reports
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
        mp.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
        pin_today(mp)
        point_at_frozen_book(mp, tmp_path_factory.mktemp("q2"))
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
    unpin_leftovers()
    return " ".join(html.split())


def test_the_pdf_credits_multpl_and_keeps_yale_as_the_fallback(q2_report):
    from src.shiller import CREDIT
    credit = str(escape(CREDIT))
    assert CREDIT == "multpl.com's computation of Robert Shiller's CAPE"
    # Macro Context's footnote, the methodology entry and the disclaimer.
    assert q2_report.count(credit) == 3
    assert (f"CAPE is {credit}; Shiller's own Yale data file is the fallback, and a figure "
            "read from it says so.") in q2_report
    assert f"<dd>From {credit}, one reading a month." in q2_report
    assert f"valuation data from {credit}." in q2_report
    for old in ("Robert Shiller / Yale", "econ.yale.edu", "public datasets",
                "most recent month shown"):
        assert old not in q2_report, old


def test_the_pdf_prints_the_read_date_with_each_cape_figure(q2_report):
    from src import shiller
    live = shiller.latest_reading()
    # Macro Context is built outside the lock: the file's latest reading, with its date.
    assert str(escape(f"The CAPE reading is for {shiller.reading_label(live)}.")) in q2_report
    # The executive summary is inside it: the lock's June reading, which recorded none.
    assert str(escape(f"(June 2026, the quarter's last monthly reading; {NOT_RECORDED}).")) \
        in q2_report
    assert q2_report.count("read date") == 1, "one figure has no recorded read date"


def test_an_unlocked_summary_names_the_month_and_the_read_date(tmp_path, monkeypatch):
    """A custom range takes no lock: the sentence reads the file's latest reading. It
    used to name the series' end only when the series was stale."""
    from src import reports, shiller
    sentence = reports._cape_reading_sentence(40.9, 99.0)
    assert sentence == ("CAPE stands at 40.9x, in the 99th percentile: Elevated versus "
                        f"history ({shiller.reading_label(shiller.latest_reading())}).")
    assert "CAPE data through" not in sentence


def test_no_page_or_template_credits_yale_as_the_source():
    """A figure can still say it came from Yale; nothing names Yale as where CAPE comes
    from. Read off the source of every surface that prints CAPE."""
    for path in ("templates/quarterly_report.html", "pages/3_Macro.py", "pages/1_SAA.py",
                 "src/reports.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        for old in ("Shiller / Yale", "econ.yale.edu", "Shiller dataset via multpl",
                    "multpl.com / Robert Shiller", "Data: FRED & Shiller",
                    "public datasets"):
            assert old not in text, (path, old)
    assert "{{ cape_credit }}" in (ROOT / "templates/quarterly_report.html").read_text(
        encoding="utf-8")


# ── the close-out ────────────────────────────────────────────────────────────

def test_the_close_out_says_when_the_cape_refresh_counts():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = " ".join(text.split("## Quarterly close-out", 1)[1].split("\n## ", 1)[0].split())
    assert "refresh_market_data.py --files cape pe" in section
    assert "made after that month's last NYSE session" in section
    assert "once the quarter's last month has a reading." not in section
