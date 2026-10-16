"""The Asset Evaluation page's 2022 figures and readings (#425, decided by the owner on
October 15, 2026).

2022 is stated once, as each series' calendar-year return read from the book: SPY's,
AGG's and bitcoin's. The page printed bitcoin's 2022 fall three times, twice as a
peak-to-trough decline within the year and once as the same figure signed, and called
SPY's and AGG's peak-to-trough declines what equities and bonds "fell". A sentence chosen
by a figure covers zero and missing inputs: a figure that prints as 0.0% reads "was
flat", and a sentence missing a figure does not render. The page itself is rendered in
tests/render/test_asset_evaluation_425_render.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import src.asset_evaluation as ae

PORT = {"max_dd": -0.315, "alloc_top": "10.0%", "mdd22_lo": -0.233, "mdd22_hi": -0.261}


def _returns() -> pd.Series:
    return pd.Series([0.50, 0.10, -0.50, 0.20, 9.0],
                     index=pd.to_datetime(["2021-12-31", "2022-01-03", "2022-06-01",
                                           "2022-12-30", "2023-01-03"]))


# ── return_in_year ────────────────────────────────────────────────────────────

def test_the_calendar_year_return_compounds_that_years_returns_only():
    r = _returns()
    assert ae.return_in_year(r) == pytest.approx(1.10 * 0.50 * 1.20 - 1)     # -34%
    assert ae.return_in_year(r, 2023) == pytest.approx(9.0)
    assert np.isnan(ae.return_in_year(r, 2020))
    assert np.isnan(ae.return_in_year(pd.Series(dtype=float)))


def test_it_is_not_the_peak_to_trough_decline():
    """The same year in the two measures: 1.10 falls to 0.55 within it (a 50% decline),
    and the year ends 34% down. The page printed the first and now prints the second."""
    r = _returns()
    assert ae.decline_in_year(r) == pytest.approx(0.50)
    assert ae.return_in_year(r) == pytest.approx(-0.34)


# ── the sentence ──────────────────────────────────────────────────────────────

def test_the_sentence_states_every_figure_in_the_order_given():
    assert ae.year_returns_sentence({"SPY": -0.18175, "AGG": -0.13022, "BTC": -0.64265}) == (
        "Over calendar 2022, SPY returned -18.2%, AGG returned -13.0% and BTC returned -64.3%")
    assert ae.year_returns_sentence({"SPY": 0.12}, 2023) == (
        "Over calendar 2023, SPY returned 12.0%")


@pytest.mark.parametrize("flat", [0.0, 0.0004, -0.0004])
def test_a_figure_that_prints_as_zero_reads_flat(flat):
    assert ae.year_returns_sentence({"SPY": flat, "BTC": -0.5}) == (
        "Over calendar 2022, SPY was flat and BTC returned -50.0%")


def test_a_figure_that_prints_as_a_tenth_is_stated():
    assert ae.year_returns_sentence({"SPY": 0.0006}) == "Over calendar 2022, SPY returned 0.1%"


@pytest.mark.parametrize("returns", [{}, {"SPY": -0.18, "BTC": float("nan")},
                                     {"SPY": float("nan")}])
def test_a_missing_figure_drops_the_sentence(returns):
    assert ae.year_returns_sentence(returns) is None
    assert ae.stress_2022_sentences(returns) == ""


# ── the joint-stress reading ──────────────────────────────────────────────────

JOINT = ("This joint stress scenario, not the full-sample average correlation, is the "
         "relevant measure of risk.")


def test_the_joint_stress_reading_renders_when_all_three_fell():
    assert ae.stress_2022_sentences({"SPY": -0.18175, "AGG": -0.13022, "BTC": -0.64265}) == (
        "Over calendar 2022, SPY returned -18.2%, AGG returned -13.0% and BTC returned "
        f"-64.3%: all three fell in the same year. {JOINT}")


def test_it_does_not_render_when_one_rose():
    text = ae.stress_2022_sentences({"SPY": -0.18, "AGG": 0.02, "BTC": -0.64})
    assert text == ("Over calendar 2022, SPY returned -18.0%, AGG returned 2.0% and BTC "
                    "returned -64.0%.")


def test_a_flat_figure_did_not_fall():
    text = ae.stress_2022_sentences({"SPY": -0.18, "AGG": -0.0004, "BTC": -0.64})
    assert text == ("Over calendar 2022, SPY returned -18.0%, AGG was flat and BTC "
                    "returned -64.0%.")


# ── the case against's drawdown bullet ────────────────────────────────────────

def test_without_bitcoins_2022_figure_the_bullet_keeps_the_portfolio_contrast():
    """The page passes no 2022 figure of bitcoin's own, which 5h states; the bullet still
    sets bitcoin's drawdown against the table's and sizes what the allocation did."""
    text = ae.drawdown_argument(-0.815, None, PORT, "2018-01-01")
    assert text == (
        "Bitcoin's own maximum drawdown is -81.5% over 2018-01-01–present, not the -31.5% "
        "in the table above, which is the portfolio's. It deepened the portfolio's own "
        "2022 drawdown from -23.3% at 0% allocation to -26.1% at 10.0%, with no "
        "diversification benefit when it was most needed")


def test_with_it_the_bullet_states_it_as_the_pdf_passes_it():
    text = ae.drawdown_argument(-0.815, "-66.9%", PORT, "2018-01-01")
    assert ("which is the portfolio's. Its 2022 drawdown of -66.9% came while equities and "
            "bonds fell together. It deepened") in text


def test_without_the_sweep_the_reduced_form_is_unchanged():
    assert (ae.drawdown_argument(-0.815, None, {}, "2018-01-01")
            == ae.drawdown_argument(-0.815, "-66.9%", {}, "2018-01-01"))


# ── the candidate's mixed verdict ─────────────────────────────────────────────

def test_a_mixed_verdict_claims_an_offset_only_when_one_was_found():
    """"Meaningful offset elsewhere" printed beside "No sleeve offers a low or negative
    correlation". Both cases sit in the mixed band (an average from 0.30 to 0.60)."""
    none = pd.Series({"US Large Core": 0.55, "US Small Cap": 0.50, "Core Fixed Income": 0.25,
                      "TIPS": 0.30})
    text = ae.interpret_candidate_diversification(0.40, none, "XYZ")
    assert "It is a mixed case: partial overlap with the equity sleeves. " in text
    assert "No sleeve offers a low or negative correlation." in text
    assert "offset elsewhere" not in text and "set against the offsets" not in text
    assert text.endswith("It is neither a pure double-down nor a clean diversifier.")

    some = none.copy()
    some["Core Fixed Income"] = -0.10
    text = ae.interpret_candidate_diversification(0.40, some, "XYZ")
    assert ("It is a mixed case: partial overlap with the equity sleeves and some offset "
            "elsewhere. Offset comes only from Core Fixed Income (-0.10). ") in text
    assert text.endswith("the case turns on the specific sleeve overlaps set against the "
                         "offsets.")


# ── 5c: the two averages, and no reading (#601) ───────────────────────────────

def test_the_averages_sentence_states_both_and_nothing_else():
    assert ae.rolling_averages_sentence(-0.0123, 0.3712) == (
        "Before 2020, BTC's rolling 60-day correlation with SPY averaged -0.01. From 2020 "
        "on, it has averaged 0.37.")


def test_an_average_that_rounds_to_zero_prints_as_zero():
    assert ae.rolling_averages_sentence(-0.004, 0.0) == (
        "Before 2020, BTC's rolling 60-day correlation with SPY averaged 0.00. From 2020 "
        "on, it has averaged 0.00.")


def test_a_missing_average_drops_its_half():
    nan = float("nan")
    assert ae.rolling_averages_sentence(nan, 0.37) == (
        "From 2020 on, BTC's rolling 60-day correlation with SPY has averaged 0.37.")
    assert ae.rolling_averages_sentence(-0.01, nan) == (
        "Before 2020, BTC's rolling 60-day correlation with SPY averaged -0.01.")
    assert ae.rolling_averages_sentence(nan, nan) == ""


def test_the_conclusion_cites_the_figures_the_sentence_prints():
    """Both read fmt_corr, so an average just under zero is 0.00 in each."""
    rolling = pd.Series([0.006, -0.014, 0.35, 0.43],
                        index=pd.to_datetime(["2019-03-01", "2019-12-31", "2020-01-02",
                                              "2026-07-16"]))
    pre, post, _ = ae.pre_post_2020(rolling)
    assert f"{pre:.2f}" == "-0.00", "premise: a plain format prints the sign"
    assert "averaged 0.00. From 2020 on, it has averaged 0.39." in (
        ae.rolling_averages_sentence(pre, post))
    assert "it averaged 0.00 before 2020 and 0.39 from 2020 through" in ae.conclusion(rolling)


# ── 5d: the only positive difference, read from the table ─────────────────────

def test_the_only_positive_difference_is_named_when_there_is_one():
    diff = pd.Series({"US Large Core": -0.05, "TIPS": 0.06, "Core Fixed Income": -0.01})
    assert ae.only_positive_difference(diff) == (
        "TIPS shows the only positive daily-to-weekly difference (+0.06).")
    other = pd.Series({"US Large Core": 0.03, "TIPS": -0.02})
    assert ae.only_positive_difference(other) == (
        "US Large Core shows the only positive daily-to-weekly difference (+0.03).")


@pytest.mark.parametrize("diff", [
    {"US Large Core": 0.02, "TIPS": 0.06},                  # two positive
    {"US Large Core": -0.02, "TIPS": -0.06},                # none
    {"US Large Core": -0.02, "TIPS": 0.004},                # prints as 0.00
    {"US Large Core": float("nan"), "TIPS": float("nan")},  # missing
    {},
])
def test_otherwise_nothing_is_said(diff):
    assert ae.only_positive_difference(pd.Series(diff, dtype=float)) is None


def test_the_pdf_prints_the_averages_as_the_page_does(tmp_path, monkeypatch):
    """The PDF's section on a copy of the frozen book, offline, with the rolling series
    replaced by one whose pre-2020 average is just under zero: the PDF, the page's
    sentence and the conclusion each print 0.00."""
    from src import reports
    from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
    rolling = pd.Series([0.006, -0.014, 0.35, 0.43],
                        index=pd.to_datetime(["2019-03-01", "2019-12-31", "2020-01-02",
                                              "2026-07-16"]))
    pin_today(monkeypatch)
    try:
        point_at_frozen_book(monkeypatch, tmp_path)
        monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
        monkeypatch.setattr(ae, "compute_rolling_correlation", lambda *a, **k: rolling)
        section = reports._build_asset_eval_section()
    finally:
        unpin_leftovers()
    assert section["rolling_prose"] == "Pre-2020 average: 0.00. Post-2020 average: 0.39."
    assert "it averaged 0.00 before 2020 and 0.39 from 2020" in section["conclusion"]


def test_the_pages_5c_and_5d_sentences_are_the_helpers():
    """On the demo book TIPS is the only positive difference, so a page that typed
    5d's sentence would render it unchanged; the page has to hand its table to the
    helper, and neither sentence's words are typed on the page."""
    import ast
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "pages" / "5_Asset_Evaluation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = {n.func.attr: n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("only_positive_difference", "rolling_averages_sentence")}
    assert ast.unparse(calls["only_positive_difference"]) == (
        "ae.only_positive_difference(diff_corr)")
    assert ast.unparse(calls["rolling_averages_sentence"]) == (
        "ae.rolling_averages_sentence(*ae.pre_post_2020(rolling_corr)[:2])")
    strings = " ".join(n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str))
    for typed in ("shows the only positive", "has averaged"):
        assert typed not in strings, typed
