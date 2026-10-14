"""The Macro page reads each FRED series' first date from the series (#535, decided by
the owner on October 13, 2026).

The page's "Data sources & freshness" list typed "starts May 2023 (FRED restriction)"
for the ICE BofA HY and CCC spreads and "starts Jan 2003" for the breakeven and the TIPS
yield. FRED serves the two spreads over a window that moves forward with each fetch:
on October 13, 2026 its public file for both began 2023-10-16, so "May 2023" was true
of a fetch made in May 2026 and five months out by October. The report's footnote typed
the same date until #529 (#537) had it read the series.

Now every FRED line in the list states its first and last observation, read from the
series the page charts, and no start date is typed: the HY panel's caption says the
history before the series' first month is unavailable, where it typed "pre-2023", and
the regime backtest's label reads its first month from the series it runs on, where it
typed "since 1976".

Pinned here, on a demo.db copy with FRED stubbed by series whose first dates differ:
  * each FRED line carries its series' first and last observation;
  * the two spreads' lines and the HY caption follow the series: a later first date
    gives a later line;
  * nothing on the page types a start date for a FRED series.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

import src.config as config
import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent
PAGE = ROOT / "pages" / "3_Macro.py"
LAST = "2026-09-30"


def _fetch(firsts: dict):
    """A stand-in for every FRED fetch: a daily series of a constant from the later of
    the start asked for and the series' own first date (``firsts``) through LAST."""
    def fetch(series_id, start_date, end_date=None):
        first = max(pd.Timestamp(start_date), pd.Timestamp(firsts.get(series_id, start_date)))
        days = pd.bdate_range(first, pd.Timestamp(LAST))
        return pd.Series(3.0, index=days, name=series_id)
    return fetch


FIRSTS = {"BAMLH0A0HYM2": "2023-10-16", "BAMLH0A3HYC": "2023-10-16",
          "T10YIE": "2003-01-02", "DFII10": "2003-01-02", "T10Y2Y": "1976-06-01"}


def _render(tmp_path, monkeypatch, firsts) -> "list[str]":
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import src.macro as macro
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(macro, "fetch_fred_series", _fetch(firsts))
    monkeypatch.setattr(config, "IS_DEMO", True)
    st.cache_data.clear()
    try:
        at = AppTest.from_file(str(PAGE), default_timeout=600).run()
    finally:
        st.cache_data.clear()
    assert not at.exception, [str(e.value) for e in at.exception]
    texts = [str(e.value) for e in list(at.caption) + list(at.markdown)]
    texts += [e.label for e in at.expander]
    return texts


def _line(texts, label: str) -> str:
    lines = [line for t in texts for line in t.split("  \n") if line.startswith(f"**{label}")]
    assert len(lines) == 1, (label, lines)
    return lines[0]


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        return _render(tmp_path_factory.mktemp("macro-firsts"), mp, FIRSTS)
    finally:
        mp.undo()


def test_each_fred_line_states_its_series_first_and_last_observation(page):
    last = "Sep 30, 2026"
    for label, first in (("FRED BAMLH0A0HYM2", "Oct 16, 2023"),
                         ("FRED BAMLH0A3HYC", "Oct 16, 2023"),
                         ("FRED T10YIE", "Jan 02, 2003"), ("FRED DFII10", "Jan 02, 2003"),
                         ("FRED DGS10", "Jan 01, 1990"), ("FRED UNRATE", "Jan 01, 1948")):
        line = _line(page, label)
        assert f"first observation **{first}**, last observation **{last}**" in line, line
        assert " · starts " not in line and "FRED restriction" not in line, line


def test_every_fred_line_reads_its_window_and_none_types_a_start(page):
    lines = [line for t in page for line in t.split("  \n") if line.startswith("**FRED ")
             and "unavailable" not in line and "USREC" not in line]
    assert len(lines) >= 20, "premise: the list's FRED lines are rendered with data"
    assert all("first observation **" in line for line in lines), [
        line for line in lines if "first observation" not in line]
    assert not any(" · starts " in t or "May 2023" in t or "pre-2023" in t for t in page)


def test_the_hy_caption_and_the_backtest_read_their_first_month(page):
    joined = " ".join(page)
    assert ("FRED restricts this ICE BofA series to Oct 2023+: its history before that is "
            "unavailable from FRED's API.") in joined
    assert "Historical regime backtest (since 1976)" in page


def test_a_later_first_date_gives_a_later_line(tmp_path, monkeypatch):
    """The contrast: the same page on spreads FRED serves from January 2024."""
    later = dict(FIRSTS, BAMLH0A0HYM2="2024-01-02", BAMLH0A3HYC="2024-01-02")
    texts = _render(tmp_path, monkeypatch, later)
    assert "first observation **Jan 02, 2024**" in _line(texts, "FRED BAMLH0A0HYM2")
    assert "first observation **Jan 02, 2024**" in _line(texts, "FRED BAMLH0A3HYC")
    assert "restricts this ICE BofA series to Jan 2024+" in " ".join(texts)


def test_the_page_types_no_start_date_for_a_fred_series():
    source = PAGE.read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for typed in (r"starts (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}",
                  r"pre-\d{4} history", r"backtest \(since \d{4}\)"):
        assert not re.search(typed, code), typed
