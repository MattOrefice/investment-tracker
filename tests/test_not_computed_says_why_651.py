"""Every sleeve regression that isn't computed says why, on Factor Profile and in the
PDF, as #647's result does (#651).

Decided by the owner on October 18, 2026:
  * the excepts in run_sleeve_regressions, run_sleeve_regressions_mom,
    run_intl_tilt_regressions and the Factor Profile page's _get_fi_result,
    _get_mom_results and _get_tilt_results catch only a read's own failure; a code
    error raises;
  * a regression with fewer than 30 aligned dates returns that reason with its count,
    not None;
  * the inception-weights fallback follows #647's rule: no leg dropped, no weights
    renormalized.

On copies of the frozen book, offline. A read fails for real: the fund's closes are
deleted, so get_prices fetches, and the blocked network fails it.
"""
from __future__ import annotations

import re
import sqlite3

import pandas as pd
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

INCEPTION, FACTOR_END = "2025-05-01", "2026-03-31"     # the factor files end in March
CAUSE = "a network error: OSError"                      # point_at_frozen_book's blocked network


def _drop(path, ticker: str, day: "str | None" = None) -> None:
    """Delete ``ticker``'s closes, or its close on ``day``."""
    con = sqlite3.connect(path)
    try:
        with con:
            sql, args = "DELETE FROM prices WHERE ticker = ?", [ticker]
            if day:
                sql, args = sql + " AND price_date = ?", args + [day]
            assert con.execute(sql, args).rowcount > 0
    finally:
        con.close()


@pytest.fixture
def book(tmp_path, monkeypatch):
    path = point_at_frozen_book(monkeypatch, tmp_path)
    pin_today(monkeypatch)
    yield path
    unpin_leftovers()


def _failing(monkeypatch, ticker: str, exc: BaseException) -> None:
    """factors.get_prices raises ``exc`` for ``ticker`` and reads the rest."""
    from src import factors
    real = factors.get_prices

    def _read(t, start, end=None):
        if t == ticker:
            raise exc
        return real(t, start, end)

    monkeypatch.setattr(factors, "get_prices", _read)


# ── the equity sleeves ────────────────────────────────────────────────────────

def test_a_failed_read_of_vea_says_why(book):
    """VEA is single-ticker, so it had no per-leg except: the read raised, and the
    outer except made the sleeve None."""
    from src import factors
    _drop(book, "VEA")
    results = factors.run_sleeve_regressions(INCEPTION, FACTOR_END)
    dev = results["developed_exus"]
    assert dev["not_computed"] == f"the price read failed for VEA: {CAUSE}", dev
    assert dev["tickers"] == ["VEA"] and "betas" not in dev
    assert results["us"]["betas"], "the US sleeve is untouched"


def test_too_few_dates_says_so_with_the_count(book):
    """March 2026 alone: fewer than 30 aligned dates, and the reason counts them."""
    from src import factors
    res = factors.run_sleeve_regression("Developed", ["VEA"], "developed_exus",
                                        "2026-03-01", FACTOR_END)
    m = re.fullmatch(r"it has (\d+) aligned trading days and needs at least 30",
                     res["not_computed"])
    assert m, res
    ff = factors.load_factors("us")
    days = [d for d in pd.to_datetime(ff.index) if "2026-03-03" <= d.date().isoformat()
            <= FACTOR_END]
    assert int(m.group(1)) == len(days) < 30


def test_a_code_error_in_a_sleeve_raises(book, monkeypatch):
    from src import factors
    _failing(monkeypatch, "VEA", KeyError("close"))
    with pytest.raises(KeyError, match="close"):
        factors.run_sleeve_regressions(INCEPTION, FACTOR_END)
    with pytest.raises(KeyError, match="close"):
        factors.run_sleeve_regressions_mom(INCEPTION, FACTOR_END)


def test_the_providers_answer_is_a_read_failure_too(book, monkeypatch):
    from src import factors, prices
    _failing(monkeypatch, "VEA", prices.YahooResponseError("HTTP 503", "returned HTTP 503"))
    dev = factors.run_sleeve_regressions(INCEPTION, FACTOR_END)["developed_exus"]
    assert dev["not_computed"] == ("the price read failed for VEA: Yahoo Finance returned "
                                   "HTTP 503")


# ── the momentum supplement ──────────────────────────────────────────────────

def test_the_momentum_file_unread_says_so_for_both_sleeves(book, monkeypatch):
    from src import factors

    def _missing():
        raise FileNotFoundError("Committed momentum data missing")

    monkeypatch.setattr(factors, "load_umd_factor", _missing)
    mom = factors.run_sleeve_regressions_mom(INCEPTION, FACTOR_END)
    for key in ("us", "developed_exus"):
        assert mom[key]["not_computed"] == (
            "the momentum factor data could not be read (FileNotFoundError)"), mom[key]


def test_a_code_error_reading_the_momentum_file_raises(book, monkeypatch):
    from src import factors

    def _broken():
        raise KeyError("Mom")

    monkeypatch.setattr(factors, "load_umd_factor", _broken)
    with pytest.raises(KeyError, match="Mom"):
        factors.run_sleeve_regressions_mom(INCEPTION, FACTOR_END)


def test_the_momentum_supplement_says_why_a_sleeve_is_not_computed(book):
    from src import factors
    _drop(book, "VEA")
    mom = factors.run_sleeve_regressions_mom(INCEPTION, FACTOR_END)
    assert mom["developed_exus"]["not_computed"] == f"the price read failed for VEA: {CAUSE}"
    assert mom["us"]["betas"]


# ── the international tilt sleeves ───────────────────────────────────────────

def test_a_tilt_fund_and_a_control_each_say_why(book):
    from src import factors
    _drop(book, "AVIV")
    _drop(book, "IQLT")
    out = factors.run_intl_tilt_regressions(INCEPTION, FACTOR_END)
    assert out["AVIV"]["fund_result"]["not_computed"] == (
        f"the price read failed for AVIV: {CAUSE}")
    assert out["AVIV"]["control_result"]["betas"], "IVLU is read"
    assert out["IDHQ"]["control_result"]["not_computed"] == (
        f"the price read failed for IQLT: {CAUSE}")
    assert out["IDHQ"]["fund_result"]["betas"]


def test_a_code_error_in_a_tilt_regression_raises(book, monkeypatch):
    from src import factors
    _failing(monkeypatch, "AVIV", KeyError("close"))
    with pytest.raises(KeyError, match="close"):
        factors.run_intl_tilt_regressions(INCEPTION, FACTOR_END)


# ── the FI sleeve ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", ["SCHP", "HYG"])
def test_the_fi_regression_says_which_read_failed(book, ticker):
    """A fund (SCHP) or a factor proxy (HYG): the read failed, so no date is covered."""
    from src import factors
    _drop(book, ticker)
    res = factors.regress_fi_sleeve(INCEPTION, FACTOR_END)
    assert res == {"sleeve_label": "FI Sleeve — TERM / CREDIT", "tickers": ["VGIT", "SCHP"],
                   "region": "us", "not_computed": f"the price read failed for {ticker}: {CAUSE}"}


# ── the inception-weights fallback ───────────────────────────────────────────

def test_the_fallback_neither_drops_a_leg_nor_renormalizes(book, monkeypatch):
    """weights=None: each held leg is weighted by its value at inception. VTV's read of
    that day answering with no close (a fetch that stores rows outside the window
    does) leaves it unweighted; it was dropped and VOO's weight became 1."""
    from src import factors
    real = factors.get_prices

    def _read(t, start, end=None):
        if t == "VTV" and start == end == INCEPTION:
            return real(t, start, end).iloc[0:0]
        return real(t, start, end)

    monkeypatch.setattr(factors, "get_prices", _read)
    ret = factors._get_sleeve_return_series(["VOO", "VTV"], INCEPTION, FACTOR_END)
    assert ret.isna().all() and len(ret)
    assert ret.attrs["unvalued"] == {"VTV": INCEPTION}
    res = factors.run_sleeve_regression("US", ["VOO", "VTV"], "us", INCEPTION, FACTOR_END)
    assert res["not_computed"] == (
        f"VTV has no close on {INCEPTION}, the day its weight is read"), res


def test_the_fallback_says_which_inception_read_failed(book):
    from src import factors
    _drop(book, "VTV")
    res = factors.run_sleeve_regression("US", ["VOO", "VTV"], "us", INCEPTION, FACTOR_END)
    assert res["not_computed"] == f"the price read failed for VTV: {CAUSE}", res


def test_the_fallback_still_weights_a_book_it_can_read(book):
    """The control: every held leg priced on the inception date."""
    from src import factors
    res = factors.run_sleeve_regression("US", ["VOO", "VTV"], "us", INCEPTION, FACTOR_END)
    assert res["betas"] and res["T"] >= 30


# ── the surfaces ─────────────────────────────────────────────────────────────

def _page_sentence(subject: str, reason: str, consequence: str) -> str:
    from src.reports import not_computed_sentence
    return not_computed_sentence(subject, None, consequence, reason=reason,
                                 where="this page")


def _factor_profile():
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_data.clear()
    at = AppTest.from_file("pages/4_Factor_Profile.py", default_timeout=300).run()
    st.cache_data.clear()
    return at


def test_factor_profile_says_why_for_each_regression(book):
    """VEA, the FI sleeve and a tilt fund unread: each prints its sentence in place of
    its table, and the rest still render."""
    _drop(book, "VEA")
    _drop(book, "SCHP")
    _drop(book, "AVIV")
    at = _factor_profile()
    assert not at.exception, [str(e.value) for e in at.exception]
    infos = [str(i.value) for i in at.info]
    from src.factors import _intl_core_label
    for want in (
            _page_sentence(f"The {_intl_core_label()} Sleeve factor regression",
                           f"the price read failed for VEA: {CAUSE}",
                           "no loadings or fit statistics are shown for it"),
            _page_sentence("The fixed-income sleeve's factor regression",
                           f"the price read failed for SCHP: {CAUSE}",
                           "no TERM or CREDIT loadings are shown"),
            _page_sentence("The AVIV fund's factor regression",
                           f"the price read failed for AVIV: {CAUSE}",
                           "no loadings are shown for it")):
        assert want in infos, (want, infos)
    labels = " ".join(str(m.value) for m in at.markdown)
    assert "FF5: IVLU control" in labels, "the control still renders beside its fund"
    assert "FF5: AVIV (fund)" not in labels
    assert "R²" in " ".join(str(c.value) for c in at.caption), "the US table still renders"


def test_factor_profile_says_the_momentum_file_could_not_be_read(book, monkeypatch):
    from src import factors

    def _missing():
        raise FileNotFoundError("Committed momentum data missing")

    monkeypatch.setattr(factors, "load_umd_factor", _missing)
    at = _factor_profile()
    assert not at.exception, [str(e.value) for e in at.exception]
    want = _page_sentence("The momentum supplement's regression",
                          "the momentum factor data could not be read (FileNotFoundError)",
                          "no loadings are shown for it")
    assert [str(i.value) for i in at.info].count(want) == 2, "one per equity sleeve"


@pytest.mark.parametrize("name", ["regress_fi_sleeve", "run_sleeve_regressions_mom",
                                  "run_intl_tilt_regressions"])
def test_the_pages_wrappers_let_a_code_error_raise(book, monkeypatch, name):
    """Each wrapper caught everything and showed nothing, or a warning naming every
    input. A code error now reaches the page's own handler."""
    from src import factors

    def _broken(*_a, **_k):
        raise KeyError("boom")

    monkeypatch.setattr(factors, name, _broken)
    at = _factor_profile()
    assert "Factor regression unavailable. Please try again later." in [
        str(e.value) for e in at.error]


def test_the_fi_wrapper_says_a_read_failure(book, monkeypatch):
    from src import factors

    def _offline(*_a, **_k):
        raise OSError("network blocked")

    monkeypatch.setattr(factors, "regress_fi_sleeve", _offline)
    at = _factor_profile()
    assert not at.exception
    assert _page_sentence("The fixed-income sleeve's factor regression",
                          f"a price read failed: {CAUSE}",
                          "no TERM or CREDIT loadings are shown") in [str(i.value) for i in at.info]


def test_the_pdf_says_why_for_vea_and_the_fi_sleeve(book):
    from src import reports
    from src.factors import _intl_core_label
    from src.reports import not_computed_sentence
    _drop(book, "VEA")
    _drop(book, "SCHP")
    section = reports._build_factor_section(FACTOR_END)
    dev = section["sleeves"][1]
    assert dev["not_computed"] == not_computed_sentence(
        f"The {_intl_core_label()} Sleeve factor regression", None,
        "no loadings or fit statistics are shown for it",
        reason=f"the price read failed for VEA: {CAUSE}")
    assert not_computed_sentence(
        "The fixed-income sleeve's factor regression", None,
        "this section carries no fixed-income note",
        reason=f"the price read failed for SCHP: {CAUSE}") in section["prose"]
    assert section["sleeves"][0]["rows"], "the US table still renders"
