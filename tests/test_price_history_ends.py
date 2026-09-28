"""The close-out's check that every ticker's committed history ends on the same date
(#406 item 8): the demo's IWB and IWF ended 2026-06-08 while the rest ran to
2026-07-20, and nothing flagged it. tools/check_price_histories.py is the step."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.check_price_histories import history_ends, main


def _book(tmp_path: Path, series: "dict[str, list[str]]") -> Path:
    path = tmp_path / "book.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, adj_close REAL)")
    for ticker, days in series.items():
        con.executemany("INSERT INTO prices VALUES (?, ?, 1.0, NULL)", [(ticker, d) for d in days])
    con.commit()
    con.close()
    return path


WEEK = ["2026-09-28", "2026-09-29", "2026-09-30"]          # Mon-Wed, quarter-end Wednesday


def test_aligned_histories_pass(tmp_path, capsys):
    path = _book(tmp_path, {"VOO": WEEK, "IWB": WEEK, "BTC-USD": WEEK})
    assert history_ends(path) == {"2026-09-30": ["BTC-USD", "IWB", "VOO"]}
    assert main(["x", str(path)]) == 0


def test_a_lagging_ticker_is_named_and_fails(tmp_path, capsys):
    path = _book(tmp_path, {"VOO": WEEK, "IWB": WEEK[:1], "IWF": WEEK[:1]})
    assert history_ends(path) == {"2026-09-28": ["IWB", "IWF"], "2026-09-30": ["VOO"]}
    assert main(["x", str(path)]) == 1
    out = capsys.readouterr().out
    assert "2026-09-28: 2 ticker(s): IWB, IWF" in out and "NOT ALIGNED" in out


def test_a_weekend_row_on_a_daily_series_is_not_a_lead(tmp_path):
    """A quarter ending Saturday 2028-09-30: the funds end Friday; bitcoin has a
    Saturday close. Compared on its newest weekday close, it ends with them."""
    fri = ["2028-09-28", "2028-09-29"]
    path = _book(tmp_path, {"VOO": fri, "BTC-USD": fri + ["2028-09-30"]})
    assert history_ends(path) == {"2028-09-29": ["BTC-USD", "VOO"]}


def test_a_daily_series_that_stops_early_still_lags(tmp_path):
    """The weekday rule does not excuse a daily series that really stopped: its newest
    weekday close is before the funds'."""
    path = _book(tmp_path, {"VOO": WEEK, "BTC-USD": ["2026-09-26", "2026-09-27", "2026-09-28"]})
    assert history_ends(path) == {"2026-09-28": ["BTC-USD"], "2026-09-30": ["VOO"]}


def test_it_reads_the_committed_demo_book_without_writing(tmp_path):
    """Runs on data/demo.db read-only. What it reports there changes with every
    snapshot advance, so only its shape is pinned: every ticker once."""
    import src.db as db
    demo = Path(db.__file__).resolve().parent.parent / "data" / "demo.db"
    ends = history_ends(demo)
    con = sqlite3.connect(f"file:{demo.as_posix()}?mode=ro", uri=True)
    tickers = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM prices")}
    con.close()
    listed = [t for ts in ends.values() for t in ts]
    assert sorted(listed) == sorted(tickers)
