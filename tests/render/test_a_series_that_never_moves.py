"""A row whose returns never move prints "—" for its Sharpe ratio, skewness and kurtosis,
with why (#678, #690).

Skewness and kurtosis divide by the spread of a series' returns, and the Sharpe ratio by
its volatility, so a series that never moves has none of the three (decided by the owner
on October 22, 2026 for the moments and on October 27, 2026 for the Sharpe ratio).
pandas gives the moments 0.0 for such a series under 3.0.6 and NaN under 3.1.0, so the
page printed 0.00 under 3.0.6 and "—" with no reason under 3.1.0, and the PDF printed
0.00 and "nan" (measured on main, b4d0cef). The Sharpe ratio read the 0.0 fallback when
the volatility came out exactly zero, and float noise otherwise (6.05e16 for 756 returns
of 0.0005). compute_univariate_stats now decides all three with never_moves, and both
surfaces print "—" with never_moves_note's line under the table.

On copies of the frozen book, offline, today pinned to its next day, with GLD's price
held at one value: the page's table and the PDF's each show "—" in GLD's three cells and
the note naming GLD, and every other row keeps its figures.
"""
from __future__ import annotations

import pandas as pd
import pytest
from streamlit import dataframe_util
from streamlit.testing.v1 import AppTest

import src.asset_evaluation as ae
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
from tests.test_asset_eval_disposition import _render_section, _text

NOTE = ("The Sharpe ratio, skewness and kurtosis are shown as — for GLD: every daily "
        "return in its sample is the same, and for a series that never moves all three "
        "are undefined.")
PAGE_CELLS = ["Sharpe", "Skewness", "Kurtosis"]
PDF_CELLS = ["sharpe", "skewness", "kurtosis"]


def _hold_gld_flat(mp):
    """GLD's stored closes replaced by one value on the same dates."""
    series = ae._price_series

    def flat(ticker, start, end):
        real = series(ticker, start, end)
        if ticker != "GLD":
            return real
        assert not real.empty, "the frozen book holds no GLD closes: nothing to hold flat"
        return pd.Series(float(real.iloc[0]), index=real.index)

    mp.setattr(ae, "_price_series", flat)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    """(the univariate table as the page displayed it, every caption it printed)."""
    import streamlit as st
    with pytest.MonkeyPatch.context() as mp:
        point_at_frozen_book(mp, tmp_path_factory.mktemp("ae-flat-page"))
        pin_today(mp)
        _hold_gld_flat(mp)
        st.cache_data.clear()
        at = AppTest.from_file("pages/5_Asset_Evaluation.py", default_timeout=300)
        at.run()
        assert not at.exception, list(at.exception)
        tables = [dataframe_util.convert_arrow_bytes_to_pandas_df(
                      d.proto.arrow_data.styler.display_values)
                  for d in at.dataframe if d.proto.arrow_data.styler.display_values]
        shown = [t for t in tables if "Skewness" in t.columns]
        captions = [str(c.value) for c in at.caption]
        st.cache_data.clear()
    unpin_leftovers()
    assert len(shown) == 1, f"expected one univariate table, found {len(shown)}"
    return shown[0], captions


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    """(the Asset Evaluation section's context as the PDF builder made it, the section's
    rendered text). Charts are stubbed: none is read here, and each is a browser launch."""
    import src.reports as reports
    with pytest.MonkeyPatch.context() as mp:
        point_at_frozen_book(mp, tmp_path_factory.mktemp("ae-flat-pdf"))
        pin_today(mp)
        _hold_gld_flat(mp)
        mp.setattr(reports, "_chart_b64", lambda *a, **k: None)
        section = reports._build_asset_eval_section()
    unpin_leftovers()
    return section, _text(_render_section(section))


def test_the_page_shows_a_dash_for_the_row_that_never_moves(page):
    table, _ = page
    assert list(table.loc["GLD", PAGE_CELLS]) == ["—", "—", "—"]
    others = table.drop(index="GLD")
    assert len(others) >= 4, list(table.index)
    for name, row in others.iterrows():
        for col in PAGE_CELLS:
            float(row[col])    # a figure, not a dash: the rule took no other row
    assert "—" not in others[PAGE_CELLS].to_numpy()


def test_the_page_says_why_under_the_table(page):
    _, captions = page
    assert NOTE in captions


def test_the_pdf_prints_a_dash_for_the_row_that_never_moves(pdf):
    section, _ = pdf
    assert section["disposition"] == "computed", section["failure_reason"]
    rows = {r["asset"]: r for r in section["uni_rows"]}
    assert [rows["GLD"][k] for k in PDF_CELLS] == ["—", "—", "—"]
    others = [r for name, r in rows.items() if name != "GLD"]
    assert len(others) >= 4, list(rows)
    for r in others:
        for k in PDF_CELLS:
            float(r[k])
    assert not any("nan" in r[k] for r in section["uni_rows"] for k in PDF_CELLS)


def test_the_pdf_says_why_under_the_table(pdf):
    section, text = pdf
    assert section["never_moves_note"] == NOTE
    assert NOTE in text
    assert text.index("Kurtosis") < text.index(NOTE) < text.index("Full-Sample Correlation")
