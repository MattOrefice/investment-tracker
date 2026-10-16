"""The Macro page after #606 (decided by the owner on October 15, 2026), rendered on the
suite's synthetic FRED series and a copy of the demo book, offline. The synthetic series
are seeded from hash(), which varies by process, so spreads are matched as patterns.
"""
from __future__ import annotations

import re

import pytest

from src import macro
from tests.render.test_macro_accuracy import _any, macro_page  # noqa: F401  (fixture)


def test_the_hy_spread_and_the_hy_ig_ratio_stand_alone(macro_page):
    _, texts = macro_page
    page = " ".join(texts)
    assert re.search(r"(?:^| )HY spreads at \d+ bps\.(?: |$)", page)
    assert re.search(r"HY/IG ratio: \d+\.\d×\. FRED BAMLC0A0CM", page)
    for gone in ("mid-cycle range", "historically tight (below", "recessionary levels",
                 "stress zone", "neutral threshold", "mid-cycle norms", "tail-risk"):
        assert gone not in page, gone


def test_the_methodology_states_the_classifiers_rules(macro_page):
    _, texts = macro_page
    assert _any(texts, "(2) Early-cycle when UNRATE > 5.5% and T10Y2Y > -0.25; (3) Late-cycle "
                       "when T10Y2Y < -0.25 or UNRATE < 4.2%; (4) Mid-cycle as the default.")
    assert (macro.REGIME_UNRATE_EARLY, macro.REGIME_CURVE_TRIGGER, macro.REGIME_UNRATE_TIGHT) == (
        5.5, -0.25, 4.2), "premise: the rule the caption reads"


def test_the_transitions_name_no_input_the_classifier_lacks(macro_page):
    _, texts = macro_page
    caption = next(t for t in texts if t.startswith("Cycle regimes shift gradually"))
    assert "(USREC)" in caption and "(T10Y2Y)" in caption and "(UNRATE)" in caption
    assert "credit spread" not in caption


def test_the_dollar_states_its_12_month_change_with_a_year_of_data(macro_page):
    _, texts = macro_page
    assert any(re.match(r"[+-]?\d+\.\d% over 12 months · \d+(st|nd|rd|th) percentile of ", t)
               for t in texts)


@pytest.fixture(scope="module")
def page_with_a_short_dollar(tmp_path_factory):
    """The same render with the dollar series starting six months back."""
    import os
    import shutil
    import pandas as pd
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    import src.db as db
    import src.prices as prices
    from tests.render.test_macro_accuracy import ROOT, _series

    def series(series_id, start):
        s = _series(series_id, start)
        if series_id == "DTWEXBGS":
            s = s[s.index >= s.index.max() - pd.Timedelta(days=180)]
        return s

    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp("macro_short_dollar") / "demo.db"
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


def test_without_a_year_of_data_the_dollar_states_no_change(page_with_a_short_dollar):
    texts = page_with_a_short_dollar
    # The dollar's caption is the one whose series starts this year: every other
    # synthetic series runs from the 1970s or earlier.
    caption = next(t for t in texts
                   if re.search(r"percentile of .*\(data since [A-Z][a-z]{2} 2026\)", t))
    assert "over 12 months" not in caption, caption
    assert not any("+0.0% over 12 months" in t for t in texts)
