"""One wording for the market-data gap, and no maintainer commands for visitors
(2026-09-25 audit, item 4).

The factor-data gap was worded three ways: "a publication lag", "~1-month
publication lag" and "a refresh cycle has been missed". The first two were false
while French had published two months the committed copy lacked. Now every surface
states the data's end date and that the end is this app's last refresh.

A visitor to the public demo also saw "Run tools/refresh_market_data.py and commit
the result" and the Forward P/E panel's full data-entry procedure. In demo mode
the vintage is stated without the command, and the Forward P/E panel is hidden
while no estimate is on file. Trailing P/E showed two dates for one reading.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

import src.config as config
import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent
BANNED = ("publication lag", "refresh cycle", "tools/refresh_market_data", "forward_eps.json",
          "sp-500-eps-est")


@pytest.fixture
def render(tmp_path, monkeypatch):
    """Demo mode on a demo.db copy, offline, FRED down, every staleness warning forced
    to fire, so the warning text itself is rendered and checked."""
    import streamlit as st
    import src.asof as asof
    import src.macro as macro
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(macro, "fetch_fred_series",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("FRED down")))
    monkeypatch.setattr(asof, "MARKET_DATA_STALE_DAYS_FACTORS", -1)
    monkeypatch.setattr(asof, "MARKET_DATA_STALE_DAYS_VALUATION", -1)

    def _render(page, demo=True):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(config, "IS_DEMO", demo)
        st.cache_data.clear()
        at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=600).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        texts = []
        for kind in ("warning", "info", "error", "caption", "markdown", "subheader"):
            texts += [str(e.value) for e in getattr(at, kind)]
        return texts

    yield _render
    st.cache_data.clear()


@pytest.mark.parametrize("page", ["4_Factor_Profile.py", "6_Benchmark_Attribution.py",
                                  "3_Macro.py"])
def test_the_demo_states_vintages_in_one_wording_with_no_commands(render, page, tmp_path,
                                                                 monkeypatch):
    if page == "6_Benchmark_Attribution.py":
        # Its regression needs a blended benchmark priced near today, so it renders
        # on the frozen book with today pinned, as its own render test does.
        from tests.conftest import pin_today, point_at_frozen_book
        pin_today(monkeypatch)
        (tmp_path / "book").mkdir()
        point_at_frozen_book(monkeypatch, tmp_path / "book")
    texts = render(page)
    hits = [(b, t[:140]) for t in texts for b in BANNED if b in t]
    assert not hits, hits
    vintages = [t for t in texts if "as of this app's last refresh" in t]
    assert vintages, "no factor or valuation vintage stated"
    assert any("Fama-French factor data ends " in t for t in vintages)


def test_the_forward_pe_panel_is_hidden_in_demo_while_no_estimate_is_on_file(render):
    demo = render("3_Macro.py")
    assert not any("Forward P/E" in t for t in demo)
    personal = render("3_Macro.py", demo=False)
    assert any("S&P 500 Forward P/E" in t for t in personal), "the owner still sees it"
    assert any("sp-500-eps-est" in t for t in personal), "with the procedure to fill it"


def test_trailing_pe_has_one_date(render):
    """The warning read the month-start row (2026-08-01) beside a panel dated Aug 11.
    Both now read the reading's own observation date."""
    texts = render("3_Macro.py")
    warning = next(t for t in texts if t.startswith("Trailing P/E data ends "))
    caption = next(t for t in texts if "data as of" in t and "Source: multpl" in t
                   and "percentile" in t and "Shiller" not in t)
    warned = re.search(r"Trailing P/E data ends (\w+) (\d+), (\d{4})", warning).groups()
    shown = re.search(r"data as of (\w{3}) (\d+), (\d{4})", caption).groups()
    assert (warned[0][:3], warned[1], warned[2]) == shown, (warning, caption)
