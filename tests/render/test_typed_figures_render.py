"""The derived figures as the pages render them (the 2026-09-25 audit's resumed run,
item D; the derivations themselves are in tests/test_typed_figures_derived.py).

  * #406 item 7: SPAXX's rationale on the Research page states the same operational cash
    share the SAA page's own caption measures, and that cash is untargeted.
  * #406 item 10: Stage 1's caption names the naive benchmark that is selected.
  * #406 item 6: Asset Evaluation's 2022 paragraph states each series' own decline.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent.parent


def _texts(at) -> "list[str]":
    return [str(e.value) for e in list(at.markdown) + list(at.caption)]


def _all_text(at) -> str:
    """Markdown, captions and every dataframe cell: a token left raw anywhere shows."""
    cells = [str(v) for df in at.dataframe for v in df.value.to_numpy().ravel()]
    return " ".join(_texts(at) + cells)


@pytest.mark.usefixtures("frozen_book_module")
def test_the_saa_page_and_the_trade_log_render_their_tokens():
    saa = AppTest.from_file(str(ROOT / "pages" / "1_SAA.py"), default_timeout=300).run()
    assert not saa.exception, [str(e.value) for e in saa.exception]
    text = _all_text(saa)
    assert "At 17.3%, Core is sized for that role" in text                # #437
    assert "not the largest US sleeve" not in text
    assert "{{" not in text
    log = AppTest.from_file(str(ROOT / "pages" / "10_Trade_Log.py"), default_timeout=300).run()
    assert not log.exception, [str(e.value) for e in log.exception]
    assert "{{" not in _all_text(log)


def test_the_trade_log_renders_a_token_where_it_shows_the_text(tmp_path, monkeypatch):
    """SPAXX's token sits past the 120 characters the Trade Log shows of a vehicle
    rationale, so the demo alone cannot tell a rendered token from a raw one. Here one
    sits at the start, on a copy of the frozen book."""
    import sqlite3
    from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
    pin_today(monkeypatch)
    try:
        copy = point_at_frozen_book(monkeypatch, tmp_path)
        con = sqlite3.connect(copy)
        n = con.execute("UPDATE theses SET vehicle_rationale = 'Cash is {{cash}}.' WHERE "
                        "title = 'Cash / SPAXX — SPAXX'").rowcount
        con.commit()
        con.close()
        assert n == 1
        at = AppTest.from_file(str(ROOT / "pages" / "10_Trade_Log.py"), default_timeout=300).run()
    finally:
        unpin_leftovers()
    assert not at.exception, [str(e.value) for e in at.exception]
    text = _all_text(at)
    assert "Cash is 1.6% of the portfolio at the July 20, 2026 close." in text
    assert "{{" not in text


@pytest.mark.usefixtures("frozen_book_module")
def test_the_research_page_states_the_saa_pages_cash_share():
    saa = AppTest.from_file(str(ROOT / "pages" / "1_SAA.py"), default_timeout=300).run()
    assert not saa.exception, [str(e.value) for e in saa.exception]
    caption = next(t for t in _texts(saa) if t.startswith("Operational cash:"))
    share = re.search(r", (\d+\.\d)% of total", caption).group(1)
    research = AppTest.from_file(str(ROOT / "pages" / "8_Research.py"), default_timeout=300).run()
    assert not research.exception, [str(e.value) for e in research.exception]
    spaxx = next(t for t in _texts(research) if "The operational cash balance, " in t)
    assert (f"The operational cash balance, {share}% of the portfolio at the July 20, 2026 "
            "close, is untargeted liquidity") in spaxx, spaxx
    assert "{{" not in " ".join(_texts(research))


@pytest.mark.usefixtures("frozen_book_module")
def test_stage_1_names_the_naive_benchmark_that_is_selected():
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert "SAA blend vs. 60/40" in _texts(at)
    radio = next(r for r in at.radio if r.key == "naive_benchmark")
    assert radio.options == ["60/40 (60% SPY / 40% AGG)", "S&P 500 (SPY)"]
    radio.set_value("S&P 500 (SPY)").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    texts = _texts(at)
    assert "SAA blend vs. S&P 500" in texts and "SAA blend vs. 60/40" not in texts


def test_the_2022_paragraph_states_each_series_decline(tmp_path, monkeypatch):
    """On a copy of demo.db, offline: the frozen test book starts in 2024 and has no
    2022. The figures are the book's own, as read off it on 2026-09-27: bitcoin fell
    66.9% from its 2022 peak, SPY 24.5% and AGG 16.0%."""
    import streamlit as st
    import src.db as db
    import src.macro as macro
    import src.prices as prices
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)

    def offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    monkeypatch.setattr(prices._SESSION, "get", offline)
    monkeypatch.setattr(macro, "fetch_fred_series", offline)
    st.cache_data.clear()
    try:
        at = AppTest.from_file(str(ROOT / "pages" / "5_Asset_Evaluation.py"),
                               default_timeout=600).run()
    finally:
        st.cache_data.clear()
    assert not at.exception, [str(e.value) for e in at.exception]
    para = next(t for t in _texts(at) if "is the most important stress-test period" in t)
    assert ("portfolio impact: equities (SPY) fell 24.5%, bonds (AGG) fell 16.0%, and BTC "
            "fell 66.9%, all at the same time. Each is a peak-to-trough decline within "
            "2022, not a calendar-year return.") in para, para
    assert "In 2022, when BTC fell 66.9% from its peak and equities sold off" in para, para
