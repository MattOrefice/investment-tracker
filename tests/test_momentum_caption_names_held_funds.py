"""The Factor Profile page's momentum caption names no fund the sleeve doesn't hold (#560,
decided by the owner on October 14, 2026).

Both of the page's Carhart expanders printed "Some incidental momentum exposure leaks
through the SPHQ quality factor." under a significant loading, the International Core
sleeve's included. That sleeve's regression runs on VEA alone, and SPHQ is a US fund. A
cause the caption states now names a fund read from the sleeve's own funds: the tickers
its regression runs on, which the page prints above the table. With none to name, the
caption states only what the loading shows.

A loading the label cannot tell from zero gives no reason either (#573, decided by the
owner on October 14, 2026, by #560's rule: a caption states what the loading shows, and a
cause only when the data names it). It said the absence was "consistent with the
tax-aware construction (high momentum turnover → short-term capital gains)".
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import src.prose_helpers as prose_helpers
from src import factors

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "pages" / "4_Factor_Profile.py"
US = factors._SLEEVES["us"]["tickers"]
INTERNATIONAL = factors._SLEEVES["developed_exus"]["tickers"]
FUND = re.compile(r"\b[A-Z]{3,5}\b")
T_VALUES = (0.0, 0.9, 1.5, 1.65, 1.8, 1.96, 2.2, 2.6, 3.83, -1.7, -3.1, float("nan"))
SLEEVES = (US, INTERNATIONAL, ["VOO", "VTV", "AVUV"], ["SPHQ"], [], ["IDHQ", "AVIV", "AVDV"])


def test_the_international_core_caption_states_only_what_the_loading_shows():
    assert INTERNATIONAL == ["VEA"], "premise: the International Core regression holds VEA"
    for t in (1.7, 2.0, 3.83, -3.1):
        words = prose_helpers.SIGNIFICANCE_WORDS[prose_helpers.significance_level(t)]
        assert factors.momentum_caption(0.147, t, INTERNATIONAL) == (
            f"Mom loading of +0.147 (t = {t:.2f}) is {words}."), t


def test_the_us_caption_names_sphq_which_its_regression_holds():
    assert "SPHQ" in US, "premise: the US sleeve's regression holds SPHQ"
    assert factors.momentum_caption(0.056, 3.57, US) == (
        "Mom loading of +0.056 (t = 3.57) is statistically significant at the 1% level. "
        "Some incidental momentum exposure leaks through the SPHQ quality factor.")


def test_every_fund_a_caption_names_is_one_its_sleeve_holds():
    for funds in SLEEVES:
        for t in T_VALUES:
            caption = factors.momentum_caption(0.05, t, funds)
            assert set(FUND.findall(caption)) <= set(funds), (funds, t, caption)


def test_an_insignificant_loading_names_no_fund():
    for funds in SLEEVES:
        assert FUND.findall(factors.momentum_caption(0.01, 0.5, funds)) == [], funds


def test_an_insignificant_loading_states_what_it_shows_and_gives_no_reason():
    for funds in SLEEVES:
        for t in (0.0, 0.5, 1.5, -1.2, float("nan")):
            assert prose_helpers.significance_level(t) == "none", t
            assert factors.momentum_caption(-0.012, t, funds) == (
                f"Mom loading of -0.012 (t = {t:.2f}) confirms the portfolio carries no "
                f"systematic momentum tilt."), (funds, t)


def test_both_expanders_pass_their_sleeves_funds():
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "momentum_caption"]
    assert [ast.unparse(c.args[2]) if len(c.args) == 3 else None for c in calls] == [
        "res_mom['tickers']", "res_mom['tickers']"]
