"""A leg whose price read fails leaves the US equity sleeve's regression not computed,
with the reason (#647).

Decided by the owner on October 17, 2026. factors._get_sleeve_return_series read each
leg inside `except Exception: continue`, so a leg whose read raised was dropped and the
other legs kept their weights: the missing leg counted 0% on every date, and Factor
Profile and the PDF printed the regression as if every leg were in it. Now:
  * a leg whose read fails has no return on any date, the path #645 gave a missing
    stretch, and keeps its weight: no leg is dropped and no weight renormalized. The
    sleeve has no date every leg covers, so its regression is not computed;
  * the result says why, naming the leg and the read's cause, and Factor Profile and
    the PDF print that in place of the loadings;
  * the per-leg except catches only the read's own failure (the network, Yahoo's
    answer, a read-only store); a code error raises.

On copies of the frozen book, offline. A read fails for real: the fund's closes are
deleted, so get_prices fetches, and the blocked network fails it.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

US_WEIGHTS = {"VOO": 0.6, "VTV": 0.4}
INCEPTION, FACTOR_END = "2025-05-01", "2026-03-31"     # the factor files end in March
CAUSE = "a network error: OSError"                      # point_at_frozen_book's blocked network
REASON = f"the price read failed for VOO: {CAUSE}"


def _drop(path, ticker: str) -> None:
    """Delete every close ``ticker`` has: a read of it now fetches, and fails offline."""
    con = sqlite3.connect(path)
    try:
        with con:
            assert con.execute("DELETE FROM prices WHERE ticker = ?", (ticker,)).rowcount > 0
    finally:
        con.close()


@pytest.fixture
def book(tmp_path, monkeypatch):
    path = point_at_frozen_book(monkeypatch, tmp_path)
    pin_today(monkeypatch)
    yield path
    unpin_leftovers()


def test_the_leg_keeps_its_weight_and_has_no_return_on_any_date(book):
    """No leg is dropped: VTV alone would be a series with values, at 0.4 of VTV's
    return, which is what the old except left. The sleeve is NaN on every date."""
    from src import factors
    _drop(book, "VOO")
    ret = factors._get_sleeve_return_series(["VOO", "VTV"], INCEPTION, FACTOR_END,
                                            weights=US_WEIGHTS)
    assert ret.attrs["unread"] == {"VOO": CAUSE}
    assert len(ret) and ret.isna().all(), ret.dropna().head()


def test_a_read_leg_carries_no_record(book):
    """The control: the book as committed reads every leg."""
    from src import factors
    ret = factors._get_sleeve_return_series(["VOO", "VTV"], INCEPTION, FACTOR_END,
                                            weights=US_WEIGHTS)
    assert ret.attrs["unread"] == {}
    assert ret.notna().sum() > 30


def test_the_regression_is_not_computed_with_the_reason(book):
    from src import factors
    _drop(book, "VOO")
    res = factors.run_sleeve_regression("US Equity Sleeve", ["VOO", "VTV"], "us",
                                        INCEPTION, FACTOR_END, weights=US_WEIGHTS)
    assert res == {"sleeve_label": "US Equity Sleeve", "tickers": ["VOO", "VTV"],
                   "region": "us", "not_computed": REASON}
    assert factors.computed_result(res) is None


def test_the_sleeve_regressions_and_the_momentum_supplement_say_so(book):
    """The US sleeve, as the pages ask for it: every fund at its target, and VOO
    unreadable. The developed sleeve does not read VOO and is computed."""
    from src import factors
    _drop(book, "VOO")
    for results in (factors.run_sleeve_regressions(INCEPTION, FACTOR_END),
                    factors.run_sleeve_regressions_mom(INCEPTION, FACTOR_END)):
        us = results["us"]
        assert us["not_computed"] == REASON, us
        assert us["tickers"] == factors._SLEEVES["us"]["tickers"]
        assert "betas" not in us
        assert results["developed_exus"]["betas"], "VEA's regression is untouched"


def test_two_unread_legs_are_each_named(book):
    from src import factors
    _drop(book, "VOO")
    _drop(book, "VTV")
    res = factors.run_sleeve_regression("US Equity Sleeve", ["VOO", "VTV"], "us",
                                        INCEPTION, FACTOR_END, weights=US_WEIGHTS)
    assert res["not_computed"] == (f"the price read failed for VOO: {CAUSE}; "
                                   f"the price read failed for VTV: {CAUSE}")


def test_yahoos_answer_is_the_reads_own_failure_too(book, monkeypatch):
    from src import factors, prices
    real = factors.get_prices

    def _read(ticker, start, end=None):
        if ticker == "VOO":
            raise prices.YahooResponseError("Yahoo Finance returned HTTP 503 for 'VOO'.",
                                            "returned HTTP 503")
        return real(ticker, start, end)

    monkeypatch.setattr(factors, "get_prices", _read)
    res = factors.run_sleeve_regression("US Equity Sleeve", ["VOO", "VTV"], "us",
                                        INCEPTION, FACTOR_END, weights=US_WEIGHTS)
    assert res["not_computed"] == "the price read failed for VOO: Yahoo Finance returned HTTP 503"


def test_a_code_error_raises(book, monkeypatch):
    from src import factors
    real = factors.get_prices

    def _read(ticker, start, end=None):
        if ticker == "VOO":
            raise KeyError("close")
        return real(ticker, start, end)

    monkeypatch.setattr(factors, "get_prices", _read)
    with pytest.raises(KeyError, match="close"):
        factors._get_sleeve_return_series(["VOO", "VTV"], INCEPTION, FACTOR_END,
                                          weights=US_WEIGHTS)


def test_the_prose_and_the_notes_read_it_as_not_computed(book):
    """Nothing reads a figure from it: the prose says nothing of the US sleeve, and the
    methodology notes print N/A for its sample, as for a regression with no data."""
    from src import factors
    _drop(book, "VOO")
    results = factors.run_sleeve_regressions(INCEPTION, FACTOR_END)
    prose = factors.build_factor_prose(results)
    assert not [p for p in prose if "The US equity sleeve (" in p], prose
    as_none = factors.build_factor_methodology_notes(
        {"us": None, "developed_exus": results["developed_exus"]})
    assert factors.build_factor_methodology_notes(results) == as_none


def _sentence(where: str) -> str:
    from src.reports import not_computed_sentence
    return not_computed_sentence("The US Equity Sleeve factor regression", None,
                                 "no loadings or fit statistics are shown for it",
                                 reason=REASON, where=where)


def test_the_factor_profile_page_prints_the_reason(book):
    """In place of the US sleeve's table: the sentence. VEA's table still renders."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    _drop(book, "VOO")
    st.cache_data.clear()
    at = AppTest.from_file("pages/4_Factor_Profile.py", default_timeout=300).run()
    st.cache_data.clear()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert "US Equity Sleeve" in [str(h.value) for h in at.subheader]
    assert _sentence("this page") in [str(i.value) for i in at.info]
    assert _sentence("this page") == (
        "The US Equity Sleeve factor regression could not be computed for this page "
        f"({REASON}), so no loadings or fit statistics are shown for it.")
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Tickers: VOO, VTV, SPHQ, AVUV" in captions
    assert "Tickers: VEA" in captions and "R²" in captions, "VEA's table still renders"


def test_the_pdf_prints_the_reason_in_place_of_the_table(book, monkeypatch):
    """A custom-range report, which reads live."""
    import markupsafe
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    _drop(book, "VOO")
    section = reports._build_factor_section(FACTOR_END)
    us = section["sleeves"][0]
    assert us == {"label": "US Equity Sleeve", "tickers": ["VOO", "VTV", "SPHQ", "AVUV"],
                  "not_computed": _sentence("this report")}
    html = reports.generate_quarterly_report_bytes("2026-01-15", FACTOR_END,
                                                   is_demo=True).decode("utf-8")
    factor = html.split("<h2>Factor Profile</h2>", 1)[1].split("<h2>", 1)[0]
    assert str(markupsafe.escape(_sentence("this report"))) in factor
    assert factor.count("Sample window:") == 1, "VEA's table only"
    assert "Tickers: VEA" in factor
