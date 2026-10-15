"""The Performance page's risk metrics render against each "Compare against" option (#417).

Selecting the 60/40 option raised `TypeError: '<=' not supported between instances of
'builtin_function_or_method' and 'Timestamp'`. `_load_naive_benchmark` has returned a
(series, gaps) pair since 710e9d3 (2026-07-08), and the risk-metrics branch took the pair
as the series. No test selected that option. The branch now unpacks the pair, and the
60/40 row carries the gap notice the Stage 1 chart already carries.

Rendered against the frozen test book, offline.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.usefixtures("frozen_book_module")

PAGE = "pages/2_Performance.py"
GAP_NOTICE = "benchmark cannot be priced"


def _render(compare_against: "str | None" = None) -> AppTest:
    import streamlit as st
    st.cache_data.clear()
    at = AppTest.from_file(PAGE, default_timeout=180)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    if compare_against is not None:
        at.radio(key="risk_bm_sel").set_value(compare_against).run()
        assert not at.exception, (compare_against, [str(e.value) for e in at.exception])
    return at


def _options() -> "list[str]":
    return list(_render().radio(key="risk_bm_sel").options)


def test_each_compare_against_option_renders_its_rows():
    options = _options()
    assert len(options) == 3 and any(o.startswith("60/40") for o in options), options
    for option in options:
        at = _render(option)
        assert at.radio(key="risk_bm_sel").value == option
        captions = [c.value for c in at.caption]
        assert f"vs {option}" in captions, (option, captions)
        tiles = {m.label for m in at.metric}
        assert {"Track. Err", "Info Ratio", "Beta", "Active Ret (ann.)"} <= tiles, (option, tiles)


def test_the_60_40_row_carries_the_gap_notice_the_chart_carries(monkeypatch):
    """A gap in the 60/40 series: the risk metrics' 60/40 row prints the shared notice,
    one more than the page prints with another benchmark selected (the Stage 1 chart's
    own notice is there either way)."""
    import src.benchmarks as bm_mod
    real = bm_mod.get_naive_series

    def gapped(kind, start, end=None):
        out = real(kind, start, end)
        if kind == "60_40":
            out.attrs["benchmark_gaps"] = [("60/40 Naive", "AGG", str(out.index[-1].date()))]
        return out

    monkeypatch.setattr(bm_mod, "get_naive_series", gapped)
    options = _options()
    sixty_forty = next(o for o in options if o.startswith("60/40"))
    blended = next(o for o in options if o.startswith("Custom Blended"))
    with_6040 = sum(GAP_NOTICE in w.value for w in _render(sixty_forty).warning)
    with_blended = sum(GAP_NOTICE in w.value for w in _render(blended).warning)
    assert with_6040 == with_blended + 1, (with_6040, with_blended)


def test_a_clean_60_40_series_prints_no_gap_notice():
    """The contrast: on the frozen book's own series, selecting 60/40 adds no notice."""
    options = _options()
    sixty_forty = next(o for o in options if o.startswith("60/40"))
    assert not [w.value for w in _render(sixty_forty).warning if GAP_NOTICE in w.value]
