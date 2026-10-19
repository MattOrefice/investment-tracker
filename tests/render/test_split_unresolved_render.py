"""A held position whose ticker split after its first trade renders as not valued, with
the reason, on the pages that value it (decided by the owner on October 18, 2026).

Rendered on frozen-book copies, offline, with today pinned. The setup records a 2-for-1
split of VTV going ex on 2026-07-15, after the demo's first VTV trade (2025-05-01): its
share counts are on the old scale, so VTV is not valued. tests/test_splits.py pins how
a split comes to be recorded and what the store does with it; here, what a reader sees.
"""
from __future__ import annotations

import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

WHY = "its share counts don't reflect its 2-for-1 split of 2026-07-15"


def _frozen_conftest(config):
    return next(p for p in config.pluginmanager.get_plugins()
                if getattr(p, "FROZEN_TODAY", None) is not None)


def _record_vtv_split(book) -> None:
    from src.prices import SPLITS_DDL, RESCALED
    con = sqlite3.connect(book)
    with con:
        con.execute(SPLITS_DDL)
        con.execute("INSERT INTO splits VALUES ('VTV', '2026-07-15', 2.0, ?, '2026-07-16')",
                    (RESCALED,))
    con.close()


def _render(pytestconfig, tmp_path, page: str) -> str:
    """Every text element ``page`` renders, joined, after the split is recorded."""
    import streamlit as st
    conftest = _frozen_conftest(pytestconfig)
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        with pytest.MonkeyPatch.context() as mp:
            conftest.pin_today(mp)
            book = conftest.point_at_frozen_book(mp, tmp_path)
            _record_vtv_split(book)
            st.cache_data.clear()
            at = AppTest.from_file(f"pages/{page}", default_timeout=180).run()
            assert not at.exception, f"{page} raised: {at.exception}"
    finally:
        conftest.unpin_leftovers()
        st.cache_data.clear()
    parts = [str(e.value) for kind in ("warning", "info", "caption", "markdown", "error")
             for e in getattr(at, kind)]
    return "\n".join(parts)


def test_the_tax_lots_page_names_the_lots_it_does_not_value(pytestconfig, tmp_path):
    text = _render(pytestconfig, tmp_path, "12_Tax_Lots.py")
    assert "VTV's" in text and f"not valued, because {WHY}." in text
    assert "They are left out of every total on this page." in text


def test_the_saa_page_says_the_position_is_not_valued_and_why(pytestconfig, tmp_path):
    text = _render(pytestconfig, tmp_path, "1_SAA.py")
    assert f"VTV is not valued, because {WHY}, so its market value counted as zero." in text


def test_the_performance_page_says_the_position_is_not_valued_and_why(pytestconfig, tmp_path):
    text = _render(pytestconfig, tmp_path, "2_Performance.py")
    assert f"VTV is not valued, because {WHY}" in text
