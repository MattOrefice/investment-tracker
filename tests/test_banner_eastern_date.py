"""Every rendered "today" is New York's date (#443).

The public demo runs on a UTC clock, so from 8 PM Eastern its date.today() is
tomorrow. The as-of banner dated "today" by it, so at 8 PM its wording changed
while nothing about the prices had: "Live data as of July 20, 2026." became "Prices
through July 20, 2026 (settled closes).", and on the last day of a quarter the locked
report it names moved to the quarter that had not yet ended in New York. The banner,
and every other rendered date that read the machine's date (Performance's quarter
labels, holding periods on Tax Lots, days held on the Trade Log), now read
asof.today_et().

Pinned here on a UTC clock: at 9 PM Eastern each renders as it does at 7 PM.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers


class _Case:
    def __init__(self, day, seven_pm, nine_pm, trim, banner):
        self.day, self.seven_pm, self.nine_pm, self.trim, self.banner = (
            day, seven_pm, nine_pm, trim, banner)


CASES = {
    # The frozen book's own last close, a Monday: the live line's wording.
    "July 20": _Case("2026-07-20", datetime(2026, 7, 20, 23, tzinfo=timezone.utc),
                     datetime(2026, 7, 21, 1, tzinfo=timezone.utc), False,
                     "Live data as of July 20, 2026. Latest locked quarterly report: "
                     "Q2 2026 (June 30, 2026)."),
    # A quarter's last day, the book cut there: the locked-report line too.
    "June 30": _Case("2026-06-30", datetime(2026, 6, 30, 23, tzinfo=timezone.utc),
                     datetime(2026, 7, 1, 1, tzinfo=timezone.utc), True,
                     "Live data as of June 30, 2026. Latest locked quarterly report: "
                     "Q1 2026 (March 31, 2026)."),
}


def _utc_machine(mp, instant: datetime) -> None:
    """A UTC server at ``instant``: date.today() is the UTC date, and the clock
    today_et() reads says ``instant``. New York's date is left to be derived."""
    import src.asof as asof
    import src.prices as prices

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    pin_today(mp, instant.astimezone(timezone.utc).date(), pin_et=False)
    mp.setattr(asof, "datetime", _Clock)
    mp.setattr(prices, "_LIVE_MARKS", {})


def _book(case, monkeypatch, tmp_path):
    path = point_at_frozen_book(monkeypatch, tmp_path)
    if case.trim:
        con = sqlite3.connect(path)
        with con:
            assert con.execute("DELETE FROM prices WHERE price_date > ?", (case.day,)).rowcount
        con.close()
    return path


@pytest.mark.parametrize("name", list(CASES))
def test_the_banner_reads_at_9pm_eastern_as_at_7pm_on_a_utc_clock(name, tmp_path, monkeypatch):
    import datetime as _dt
    from src import asof
    case = CASES[name]
    _book(case, monkeypatch, tmp_path)
    got, machine = {}, {}
    try:
        for label, instant in (("7 PM", case.seven_pm), ("9 PM", case.nine_pm)):
            with monkeypatch.context() as mp:
                _utc_machine(mp, instant)
                machine[label] = _dt.date.today()
                got[label] = asof.as_of_banner()
    finally:
        unpin_leftovers()
    # The premise: the machine's date moved between the two readings.
    assert machine["7 PM"].isoformat() == case.day != machine["9 PM"].isoformat()
    assert got["7 PM"] == case.banner, got["7 PM"]
    assert got["9 PM"] == got["7 PM"], got["9 PM"]


def _other_dates() -> "dict[str, str]":
    """The rendered dates outside the banner and the three pages below, each read
    without a date passed in: a committed series' age (Macro, Factor Profile), the
    forward-EPS estimate's age, and a harvest's wash-sale window (Tax Lots)."""
    from datetime import date
    import pandas as pd
    from src import asof, forward_pe, harvest
    lot = pd.DataFrame([{"trade_id": 1, "ticker": "VOO", "sleeve": "US Large Core",
                         "shares": 10.0, "tax_status": "ST", "cost_basis_per_share": 100.0,
                         "current_price": 80.0, "market_value": 800.0,
                         "unrealized_gl": -200.0, "unrealized_gl_pct": -0.2}])
    cand = harvest.compute_harvest_candidates(lot)[0]
    return {
        "series age": asof.staleness_note("Factor", date(2026, 3, 31), 0),
        "estimate age": str(forward_pe.staleness_days({"as_of": date(2026, 6, 1)})),
        "wash-sale window": f"{cand['wash_sale_start_date']} to {cand['wash_sale_end_date']}",
    }


def test_other_rendered_dates_read_at_9pm_eastern_as_at_7pm_on_a_utc_clock(monkeypatch):
    case = CASES["June 30"]
    got = {}
    try:
        for label, instant in (("7 PM", case.seven_pm), ("9 PM", case.nine_pm)):
            with monkeypatch.context() as mp:
                _utc_machine(mp, instant)
                got[label] = _other_dates()
    finally:
        unpin_leftovers()
    assert "(91 days ago)" in got["7 PM"]["series age"], got["7 PM"]
    assert got["7 PM"]["wash-sale window"] == "2026-05-31 to 2026-07-30"
    assert got["9 PM"] == got["7 PM"], got["9 PM"]


def _page_text(page) -> "list[str]":
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_data.clear()
    at = AppTest.from_file(page, default_timeout=600).run()
    st.cache_data.clear()
    assert not at.exception, [str(e.value) for e in at.exception]
    out = []
    for kind in ("markdown", "caption", "info", "warning", "success", "error"):
        out += [f"{kind}: {el.value}" for el in getattr(at, kind, [])]
    out += [f"metric: {m.label} = {m.value}" for m in at.metric]
    for df in at.dataframe:
        out += ["df: " + " ; ".join(map(str, row))
                for row in df.value.astype(str).itertuples(index=False)]
    return out


@pytest.mark.parametrize("page", ["pages/2_Performance.py", "pages/12_Tax_Lots.py",
                                  "pages/10_Trade_Log.py"])
def test_a_page_reads_at_9pm_eastern_as_at_7pm_on_a_utc_clock(page, tmp_path, monkeypatch):
    """On a quarter's last day: Performance names the completed quarter, Tax Lots
    counts holding periods, the Trade Log counts days held. Each from New York's date."""
    case = CASES["June 30"]
    _book(case, monkeypatch, tmp_path)
    got = {}
    try:
        for label, instant in (("7 PM", case.seven_pm), ("9 PM", case.nine_pm)):
            with monkeypatch.context() as mp:
                _utc_machine(mp, instant)
                got[label] = _page_text(page)
    finally:
        unpin_leftovers()
    assert got["7 PM"], "the page rendered nothing to compare"
    moved = [(a, b) for a, b in zip(got["7 PM"], got["9 PM"]) if a != b]
    assert len(got["7 PM"]) == len(got["9 PM"]) and not moved, moved[:3]
