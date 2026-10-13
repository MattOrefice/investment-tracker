"""The close-out's check of the committed price histories (tools/check_price_histories.py).

ENDS (#406 item 8): the demo's IWB and IWF ended 2026-06-08 while the rest ran to
2026-07-20, and nothing flagged it.

GAPS (#491): the book lacked 33 closes between two stored closes of their tickers, and
an end-date check could not see them. Each ticker's whole span is now checked against
SPY's sessions, a daily series against the calendar, and SPY against the holiday table.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import FROZEN_BOOK
from tools.check_price_histories import (
    MISSING,
    OFF_SESSION,
    history_ends,
    history_gaps,
    main,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo.db"

# The tool judges end dates in demo mode only, so every test here runs in it unless it
# says otherwise: the suite's mode is the checkout's, and personal locally.
pytestmark = pytest.mark.usefixtures("use_demo_db")


def _book(tmp_path: Path, series: "dict[str, list[str]]", name: str = "book.db") -> Path:
    path = tmp_path / name
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, adj_close REAL)")
    for ticker, days in series.items():
        con.executemany("INSERT INTO prices VALUES (?, ?, 1.0, NULL)", [(ticker, d) for d in days])
    con.commit()
    con.close()
    return path


WEEK = ["2026-09-28", "2026-09-29", "2026-09-30"]          # Mon-Wed, quarter-end Wednesday


# ── ends ─────────────────────────────────────────────────────────────────────

def test_aligned_histories_pass(tmp_path, capsys):
    path = _book(tmp_path, {"SPY": WEEK, "VOO": WEEK, "IWB": WEEK, "BTC-USD": WEEK})
    assert history_ends(path) == {"2026-09-30": ["BTC-USD", "IWB", "SPY", "VOO"]}
    assert history_gaps(path) == []
    assert main(["x", str(path)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-2:] == ["Every ticker's history in book.db ends on 2026-09-30.",
                        "No ticker in book.db lacks a close inside its history."]


def test_a_lagging_ticker_is_named_and_fails(tmp_path, capsys):
    path = _book(tmp_path, {"SPY": WEEK, "VOO": WEEK, "IWB": WEEK[:1], "IWF": WEEK[:1]})
    assert history_ends(path) == {"2026-09-28": ["IWB", "IWF"], "2026-09-30": ["SPY", "VOO"]}
    assert history_gaps(path) == [], "a history that stops early has no gap inside it"
    assert main(["x", str(path)]) == 1
    out = capsys.readouterr().out
    assert "2026-09-28: 2 ticker(s): IWB, IWF" in out and "NOT ALIGNED" in out


def test_a_weekend_row_on_a_daily_series_is_not_a_lead(tmp_path):
    """A quarter ending Saturday 2028-09-30: the funds end Friday; bitcoin has a
    Saturday close. Compared on its newest weekday close, it ends with them."""
    fri = ["2028-09-28", "2028-09-29"]
    path = _book(tmp_path, {"SPY": fri, "VOO": fri, "BTC-USD": fri + ["2028-09-30"]})
    assert history_ends(path) == {"2028-09-29": ["BTC-USD", "SPY", "VOO"]}
    assert history_gaps(path) == []


def test_a_daily_series_that_stops_early_still_lags(tmp_path):
    """The weekday rule does not excuse a daily series that really stopped: its newest
    weekday close is before the funds'."""
    path = _book(tmp_path, {"SPY": WEEK, "VOO": WEEK,
                            "BTC-USD": ["2026-09-26", "2026-09-27", "2026-09-28"]})
    assert history_ends(path) == {"2026-09-28": ["BTC-USD"], "2026-09-30": ["SPY", "VOO"]}


def test_it_reads_the_committed_demo_book_without_writing(tmp_path):
    """Runs on data/demo.db read-only. What it reports there changes with every
    snapshot advance, so only its shape is pinned: every ticker once."""
    ends = history_ends(DEMO)
    con = sqlite3.connect(f"file:{DEMO.as_posix()}?mode=ro", uri=True)
    tickers = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM prices")}
    con.close()
    listed = [t for ts in ends.values() for t in ts]
    assert sorted(listed) == sorted(tickers)


# ── gaps ─────────────────────────────────────────────────────────────────────

def test_a_close_missing_inside_a_history_is_named_and_fails(tmp_path, capsys):
    """What the end-date check could not see: the histories end together."""
    path = _book(tmp_path, {"SPY": WEEK, "VOO": [WEEK[0], WEEK[2]], "IWB": [WEEK[0], WEEK[2]],
                            "BTC-USD": WEEK})
    assert len(history_ends(path)) == 1, "premise: the ends are aligned"
    assert history_gaps(path) == [("IWB", "2026-09-29", MISSING), ("VOO", "2026-09-29", MISSING)]
    assert main(["x", str(path)]) == 1
    out = capsys.readouterr().out
    assert "2026-09-29: no close for 2 ticker(s): IWB, VOO" in out
    assert "Every ticker's history in book.db ends on 2026-09-30." in out
    assert out.strip().splitlines()[-1].startswith("GAPS: book.db ")


def test_a_ticker_is_checked_inside_its_own_span_only(tmp_path):
    """A fund listed later, or one that stopped, lacks nothing before its first close
    or after its last. The second is the end check's to say."""
    path = _book(tmp_path, {"SPY": WEEK, "LATER": WEEK[1:], "STOPPED": WEEK[:2]})
    assert history_gaps(path) == []


def test_a_daily_series_must_hold_every_calendar_day(tmp_path):
    """Friday to Monday without the Sunday. SPY holds neither weekend day."""
    path = _book(tmp_path, {"SPY": ["2026-09-25", "2026-09-28"],
                            "BTC-USD": ["2026-09-25", "2026-09-26", "2026-09-28"]})
    assert history_gaps(path) == [("BTC-USD", "2026-09-27", MISSING)]


def test_a_close_on_a_date_the_reference_lacks_is_named(tmp_path, capsys):
    path = _book(tmp_path, {"SPY": WEEK, "VOO": ["2026-09-27"] + WEEK})    # a Sunday
    assert history_gaps(path) == [("VOO", "2026-09-27", OFF_SESSION)]
    assert main(["x", str(path)]) == 1
    assert ("2026-09-27: a close on a date that is not a session for 1 ticker(s): VOO"
            in capsys.readouterr().out)


def test_the_reference_is_held_to_the_calendar_where_the_calendar_is_known(tmp_path):
    """A session every ticker lacks, SPY included, is invisible against SPY. The holiday
    table names it. Before the table's first year nothing is assumed."""
    lacking = _book(tmp_path, {"SPY": [WEEK[0], WEEK[2]], "VOO": [WEEK[0], WEEK[2]]}, "a.db")
    assert history_gaps(lacking) == [("SPY", "2026-09-29", MISSING)]
    labor_day = ["2026-09-04", "2026-09-07", "2026-09-08"]
    holiday = _book(tmp_path, {"SPY": labor_day, "VOO": labor_day}, "b.db")
    assert history_gaps(holiday) == [("SPY", "2026-09-07", OFF_SESSION)]
    christmas_2025 = ["2025-12-24", "2025-12-26"]              # a Wednesday and a Friday
    earlier = _book(tmp_path, {"SPY": christmas_2025, "VOO": christmas_2025}, "c.db")
    assert history_gaps(earlier) == []


def test_a_book_without_the_reference_fails(tmp_path, capsys):
    path = _book(tmp_path, {"VOO": WEEK, "IWB": WEEK})
    with pytest.raises(ValueError, match="holds no SPY closes"):
        history_gaps(path)
    assert main(["x", str(path)]) == 1
    assert "NO REFERENCE: book.db holds no SPY closes" in capsys.readouterr().out


def test_the_committed_books_hold_no_gap(capsys):
    assert history_gaps(DEMO) == []
    assert history_gaps(FROZEN_BOOK) == [], "its histories end on three dates, by design"
    assert main(["x", str(DEMO)]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        "No ticker in demo.db lacks a close inside its history.")


def test_the_book_before_the_fill_is_flagged_on_exactly_its_33_closes(tmp_path, capsys):
    """The book as main held it at 1e3e5c9: the committed one less the closes #491's fill
    wrote. The check names those 33 and nothing else."""
    from tools.fill_demo_price_gaps import CELLS
    before = tmp_path / "demo.db"
    shutil.copyfile(DEMO, before)
    os.chmod(before, 0o644)
    con = sqlite3.connect(before)
    try:
        with con:
            for ticker, day in CELLS:
                assert con.execute("DELETE FROM prices WHERE ticker = ? AND price_date = ?",
                                   (ticker, day)).rowcount == 1
    finally:
        con.close()
    assert history_gaps(before) == sorted((t, d, MISSING) for t, d in CELLS)
    assert len(CELLS) == 33
    assert main(["x", str(before)]) == 1
    out = capsys.readouterr().out
    assert "2026-05-28: no close for 10 ticker(s): AVUV, IEMG, PDBC, SCHP, SPHQ, VEA, VGIT, VNQ, VOO, VTV" in out
    assert "2026-07-08: no close for 1 ticker(s): BTC-USD" in out


# ── the end-date check is a demo close-out step ───────────────────────────────

def _personal(monkeypatch, book=None):
    """Personal mode for the rest of the test, over the module's demo pin; with ``book``,
    that file is the personal book."""
    import src.db as db
    from tests.conftest import _pin_mode
    _pin_mode(monkeypatch, "personal")
    if book is not None:
        monkeypatch.setattr(db, "DB_PATH", book)


def test_in_personal_mode_a_ticker_that_ends_early_is_listed_and_does_not_fail(
        tmp_path, capsys, monkeypatch):
    """The personal book's case: ^GSPC is read only for the Forward P/E panel, which
    fetches nothing while no estimate is on file, so its history ends where that panel
    last ran. The same book fails in demo mode, where the advance fetches every ticker."""
    path = _book(tmp_path, {"SPY": WEEK, "VOO": WEEK, "^GSPC": WEEK[:1]})
    assert main(["x", str(path)]) == 1, "premise: in demo mode this book fails"
    assert "NOT ALIGNED" in capsys.readouterr().out
    _personal(monkeypatch)
    assert main(["x", str(path)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0] == ("Personal mode, book.db: gaps only. The end dates are listed and not "
                      "judged: the alignment check is a demo close-out step.")
    assert "2026-09-28: 1 ticker(s): ^GSPC" in out and "2026-09-30: 2 ticker(s): SPY, VOO" in out
    assert out[-2:] == ["book.db's price histories end on 2 different dates. Not judged in "
                        "personal mode: a ticker no page reads is not refreshed.",
                        "No ticker in book.db lacks a close inside its history."]
    assert not [line for line in out if line.startswith("NOT ALIGNED")]


def test_in_personal_mode_a_gap_still_fails(tmp_path, capsys, monkeypatch):
    _personal(monkeypatch)
    path = _book(tmp_path, {"SPY": WEEK, "VOO": [WEEK[0], WEEK[2]], "^GSPC": WEEK[:1]})
    assert main(["x", str(path)]) == 1
    out = capsys.readouterr().out
    assert "2026-09-29: no close for 1 ticker(s): VOO" in out
    assert out.strip().splitlines()[-1].startswith("GAPS: book.db holds 1 gap(s)")


def test_in_personal_mode_a_book_without_the_reference_still_fails(tmp_path, capsys, monkeypatch):
    _personal(monkeypatch)
    path = _book(tmp_path, {"VOO": WEEK, "IWB": WEEK})
    assert main(["x", str(path)]) == 1
    assert "NO REFERENCE: book.db holds no SPY closes" in capsys.readouterr().out


def test_demo_mode_says_what_it_checked(tmp_path, capsys):
    path = _book(tmp_path, {"SPY": WEEK, "VOO": WEEK})
    assert main(["x", str(path)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "Demo mode, book.db: end dates and gaps."


def test_with_no_path_it_checks_the_modes_own_book(tmp_path, capsys, monkeypatch):
    """Demo mode reads data/demo.db. Personal mode reads the personal book, so a
    close-out step run without TRACKER_MODE=demo says so on its first line instead of
    checking the demo book under the personal rule."""
    assert main(["x"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "Demo mode, demo.db: end dates and gaps."
    mine = _book(tmp_path, {"SPY": WEEK, "VOO": WEEK, "^GSPC": WEEK[:1]}, "mine.db")
    _personal(monkeypatch, mine)
    assert main(["x"]) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("Personal mode, mine.db: gaps only.")
