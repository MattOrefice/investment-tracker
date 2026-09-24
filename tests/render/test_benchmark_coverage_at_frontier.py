"""#343 — the Performance page judges benchmark coverage at its settled frontier, not
at the calendar. The page shows every benchmark series only through _C (the newest
settled price), so judging coverage "as of today" flagged a gap in data it never
displays: offline, or with a cache more than five days behind, the page warned
"Benchmark data unavailable ... as of <today>" while holding prices through _C.

Rendered against the frozen test book (prices end 2026-07-20), offline, with today
pinned FAR past the book's end. The control pins today to the book's next day, where
the old judgement and the new one agree there is no gap, so a pass there proves only
that the page renders; the far-date case is the one that separates them.
"""
import datetime

import pytest
from streamlit.testing.v1 import AppTest

FAR = datetime.date(2026, 9, 23)          # 65 days past the book's last price
NEXT_DAY = datetime.date(2026, 7, 21)     # FROZEN_TODAY


def _conftest(config):
    return next(p for p in config.pluginmanager.get_plugins()
                if getattr(p, "FROZEN_TODAY", None) is not None)


def _render(pytestconfig, tmp_path, day):
    import streamlit as st
    conftest = _conftest(pytestconfig)
    try:
        with pytest.MonkeyPatch.context() as mp:
            conftest.pin_today(mp, day)
            conftest.point_at_frozen_book(mp, tmp_path)
            st.cache_data.clear()
            at = AppTest.from_file("pages/2_Performance.py", default_timeout=120).run()
    finally:
        conftest.unpin_leftovers()
        st.cache_data.clear()
    return at


def _benchmark_warnings(at):
    return [str(w.value) for w in at.warning if "Benchmark data unavailable" in str(w.value)]


@pytest.mark.parametrize("day", [NEXT_DAY, FAR], ids=["next-day", "65-days-later"])
def test_no_benchmark_gap_is_reported_for_data_the_page_holds(pytestconfig, tmp_path, day):
    at = _render(pytestconfig, tmp_path, day)
    assert not at.exception, f"page raised: {at.exception}"
    assert at.metric, "the page rendered no figures, so the check below would be vacuous"
    assert not _benchmark_warnings(at), (
        f"with today {day} and benchmark prices through 2026-07-20, the page reported "
        f"a benchmark gap it does not display: {_benchmark_warnings(at)}")
