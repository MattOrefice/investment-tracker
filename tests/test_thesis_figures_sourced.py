"""The thesis figures the #452 pass flagged: each read from the data, or dropped where the
point stands without it and its source still supports the point.

  * thesis 19's durations read src.positioning.ETF_DURATION through {{dur:}}, the table
    the fixed-income duration metric computes with;
  * thesis 22's "$35B AUM" (Vanguard gives $38.1B for the ETF share class and $70.8B for
    the fund, August 31 2026), thesis 11's "at $10k", thesis 8's "~10x P/E vs US ~22x"
    (MSCI Emerging Markets 15.23, forward 10.07, August 31 2026) and thesis 18's
    "~10x P/E" (MSCI China 13.96, forward 10.76, against MSCI ACWI's 21.85) are dropped,
    each point kept;
  * thesis 23's "toward 1%", a cash level, is dropped from the thesis and the Research
    page's copy, as thesis 12's range was; the yield trigger stays.

Thesis 14's AUM is unchanged: the issuer's page could not be read (listed on the PR).
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]
COLUMNS = ("macro_view", "view_summary", "vehicle_rationale", "exit_conditions",
           "invalidation_conditions", "expected_return_scenario")
DROPPED = {8: ["~10x P/E", "~22x"], 11: ["$10k"], 18: ["~10x P/E"], 22: ["$35B"],
           23: ["toward 1%"]}


def _theses(book):
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return {r[0]: dict(zip(COLUMNS, r[1:])) for r in con.execute(
            f"SELECT thesis_id, {', '.join(COLUMNS)} FROM theses")}
    finally:
        con.close()


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_the_dropped_figures_are_gone_and_the_points_stay(book):
    t = _theses(book)
    for tid, figures in DROPPED.items():
        for col, text in t[tid].items():
            for fig in figures:
                assert not text or fig not in text, (book.name, tid, col, fig)
    assert "EM's structural valuation discount to US equities implies" in t[8]["expected_return_scenario"]
    assert "at retail account sizes they are the only honest implementation" in t[11]["view_summary"]
    assert "China trades at a valuation discount to global equities" in t[18]["vehicle_rationale"]
    assert "VNQ is the standard for US REIT exposure: a {{er:VNQ}} ER" in t[22]["vehicle_rationale"]
    assert "It would be reduced if short rates fell materially below 2%." in t[23]["vehicle_rationale"]


def test_the_research_page_and_the_seed_drop_the_cash_level_too():
    from src.seed_position_theses import SPAXX_VEHICLE_RATIONALE
    page = (ROOT / "pages" / "8_Research.py").read_text(encoding="utf-8")
    assert "toward 1%" not in SPAXX_VEHICLE_RATIONALE and "toward 1%" not in page
    assert SPAXX_VEHICLE_RATIONALE.endswith("It would be reduced if short rates fell materially "
                                            "below 2%.")


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_thesis_19_reads_the_durations_the_metric_uses(book, tmp_path, monkeypatch):
    import src.db as db
    from src.positioning import ETF_DURATION
    from src.prose_figures import render
    cells = _theses(book)[19]
    assert all("{{dur:VGIT}} vs IEF's {{dur:IEF}}" in cells[c]
               for c in ("macro_view", "view_summary", "vehicle_rationale"))
    assert not any(re.search(r"\d+(?:\.\d+)? years", cells[c])
                   for c in ("macro_view", "view_summary", "vehicle_rationale"))
    copy = tmp_path / "book.db"
    shutil.copyfile(book, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    assert (f"Duration is modestly shorter ({ETF_DURATION['VGIT']:.1f} years vs IEF's "
            f"{ETF_DURATION['IEF']:.1f} years)") in render(cells["vehicle_rationale"])


def test_an_unknown_duration_raises():
    from src.prose_figures import render
    with pytest.raises(KeyError):
        render("{{dur:NOPE}}", targets={})
