"""The Macro page after #424 (decided by the owner on October 15, 2026), rendered on the
suite's synthetic FRED series and a copy of the demo book, offline. The readings'
branches, zero and missing cases are in tests/test_macro_424.py.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.render.test_macro_accuracy import _any, macro_page  # noqa: F401  (fixture)


def _annotations(at) -> "list[str]":
    out = []
    for chart in at.get("plotly_chart"):
        spec = json.loads(chart.proto.spec)
        out += [str(a.get("text")) for a in spec.get("layout", {}).get("annotations", [])]
    return out


def test_the_treasury_caption_names_freds_series(macro_page):
    _, texts = macro_page
    assert _any(texts, "FRED DGS3MO, DGS1, DGS2, DGS5, DGS7, DGS10, DGS20, DGS30.")
    assert not _any(texts, "DGS1Y") and not _any(texts, "DGS10Y")


def test_both_inflation_charts_say_whose_target_2_percent_is(macro_page):
    at, texts = macro_page
    lines = [a for a in _annotations(at) if a.startswith("2% ")]
    assert lines == ["2% (the Fed's target is set for PCE inflation)"] * 2, lines
    assert "Fed target 2%" not in _annotations(at)
    assert not _any(texts, "preferred gauge")
    assert _any(texts, "Core CPI at 1.2% is below 2%.")


def test_the_breakeven_compares_with_the_core_cpi_it_prints(macro_page):
    """The synthetic breakeven is seeded from hash(), which varies by process, so the
    framing is not pinned; core CPI is a deterministic ramp."""
    _, texts = macro_page
    assert _any(texts, "Compared to Core CPI at 1.2%, the market is ")
    assert _any(texts, "directly relevant to the 4.1% TIPS sleeve.")
    assert not _any(texts, "N/A")


def test_the_regime_caption_says_the_classifier_reads_levels(macro_page):
    _, texts = macro_page
    assert _any(texts, "The classifier itself reads levels: each month's label comes from "
                       "where the three variables stand against fixed thresholds")
    assert not _any(texts, "direction of travel")


def test_gdp_in_the_trend_band_is_the_rate_alone(macro_page):
    _, texts = macro_page
    assert _any(texts, "Real GDP growth is 3.0%. FRED A191RL1Q225SBEA")
    assert not _any(texts, "near the long-run trend") and not _any(texts, "Goldilocks")


def test_no_percentile_claims_a_window_it_may_not_have_used(macro_page):
    _, texts = macro_page
    assert not _any(texts, "Percentile computed relative to the selected window")


def test_credit_and_the_dollar_name_the_span_they_are_graded_on(macro_page):
    """The synthetic spreads and dollar are seeded from hash(), which varies by process,
    so the grade is matched as a pattern: whichever it is, it names its span."""
    _, texts = macro_page
    page = " ".join(texts)
    span = r"(tight against|near the median of|elevated against) its history since Sep 2023"
    assert re.search(r"IG OAS at \d+ bps, the investment-grade corporate spread over "
                     r"Treasuries, is " + span, page)
    assert re.search(r"CCC OAS at \d+ bps is " + span, page)
    assert re.search(r"percentile of the 10Y window \((strong for that window|near that "
                     r"window's median|weak for that window)\)\.", page)
    for gone in ("is historically tight", "for available history", "near its historical median",
                 "historically strong", "historically weak"):
        assert gone not in page, gone


def test_the_factor_proxies_name_their_price_date(macro_page):
    _, texts = macro_page
    assert _any(texts, "The long-only proxy (IWM−IWB) reads +0.9% as of September 30, 2026, "
                       "confirming the same direction.")
    assert _any(texts, "The long-only proxy (IWD−IWF) reads +16.1% as of September 30, 2026,")
    assert not _any(texts, "current to today")


def test_the_gap_reads_its_sign(macro_page):
    _, texts = macro_page
    assert _any(texts, "current S&P 500 earnings are running ≈57% above their "
                       "inflation-adjusted ten-year average. CAPE smooths away")


@pytest.fixture(scope="module")
def page_without_core_cpi(tmp_path_factory):
    """The same render with FRED failing for CPILFESL alone."""
    import os
    import shutil
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    import src.db as db
    import src.macro as macro
    import src.prices as prices
    from tests.render.test_macro_accuracy import ROOT, _series

    def series(series_id, start):
        if series_id == "CPILFESL":
            raise OSError("CPILFESL unavailable")
        return _series(series_id, start)

    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp("macro_no_cpi") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    mp.setattr(macro, "get_series", series)
    mp.setattr(config, "IS_DEMO", True)
    st.cache_data.clear()
    try:
        at = AppTest.from_file(str(ROOT / "pages" / "3_Macro.py"), default_timeout=600).run()
    finally:
        st.cache_data.clear()
        mp.undo()
    assert not at.exception, [str(e.value) for e in at.exception]
    return [str(e.value) for e in list(at.caption) + list(at.markdown) + list(at.info)]


def test_without_core_cpi_the_breakeven_caption_makes_no_comparison(page_without_core_cpi):
    texts = page_without_core_cpi
    caption = next(t for t in texts if t.startswith("10Y breakeven at "))
    assert ("FRED-computed). Breakeven is the hurdle rate for TIPS-vs-nominal-Treasury "
            "outperformance") in caption, caption
    for gone in ("Compared to Core CPI", "N/A", "None"):
        assert gone not in caption, gone
