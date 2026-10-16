"""The Factor Profile page's momentum caption names no fund and adds no sentence (#584,
decided by the owner on October 15, 2026).

Both of the page's Carhart expanders printed "Some incidental momentum exposure leaks
through the SPHQ quality factor." under a significant loading, the International Core
sleeve's included, though that sleeve's regression runs on VEA alone. #560 (decided by the
owner on October 14, 2026) kept the sentence only where the sleeve's regression held SPHQ.
It goes everywhere now: a sleeve's regression cannot show which of its funds carries its
loading, and #560 settled only which fund the caption could name.

A loading the label cannot tell from zero carried a sentence too: a tax reason (#573),
then "confirms the sleeve carries no systematic momentum tilt" (#581). That sentence goes
as well. Every loading's caption is "Mom loading of <b> (t = <t>) is <the significance
label's words>." and nothing more.

The page's methodology note said the supplement tests "whether the portfolio
systematically loads on the momentum premium". The supplement runs one regression per
sleeve, so the note names the two sleeves it tests, the second by _intl_core_label() as the
note's other sentences name it (#584). Its tax reason and expectation went in #581.
"""
from __future__ import annotations

import ast
import inspect
import math
import re
from pathlib import Path

import src.prose_helpers as prose_helpers
import src.sleeve_config as sleeve_config
from src import factors

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "pages" / "4_Factor_Profile.py"
FUND = re.compile(r"\b[A-Z]{3,5}\b")
T_VALUES = (0.0, 0.5, 0.9, 1.5, 1.65, 1.8, 1.96, 2.2, 2.6, 3.57, 3.83, -1.2, -1.7, -3.1,
            float("nan"))
SLEEVE_NAMES = ("International Core", "International Developed")


def _momentum_note(notes: "list[str]") -> str:
    momentum = [n for n in notes if n.startswith("Carhart Momentum supplement")]
    assert len(momentum) == 1, "premise: the page prints the momentum note"
    return momentum[0]


def test_every_caption_is_the_loading_and_its_label_and_nothing_more():
    levels = set()
    for b in (0.147, 0.056, -0.012):
        for t in T_VALUES:
            level = prose_helpers.significance_level(t)
            levels.add(level)
            assert factors.momentum_caption(b, t) == (
                f"Mom loading of {b:+.3f} (t = {t:.2f}) is "
                f"{prose_helpers.SIGNIFICANCE_WORDS[level]}."), (b, t)
    assert levels == {"1%", "5%", "10%", "none"}, "premise: every level is read"


def test_the_us_caption_names_no_fund_where_its_regression_holds_sphq():
    """The case #560 kept: the US sleeve's regression holds SPHQ, and its caption at the
    loading #560's test pinned named it."""
    assert "SPHQ" in factors._SLEEVES["us"]["tickers"], "premise: the US sleeve holds SPHQ"
    assert factors.momentum_caption(0.056, 3.57) == (
        "Mom loading of +0.056 (t = 3.57) is statistically significant at the 1% level.")


def test_no_caption_names_a_fund():
    for t in T_VALUES:
        caption = factors.momentum_caption(0.05, t)
        assert FUND.findall(caption) == [], (t, caption)


def test_an_insignificant_loading_is_stated_in_the_labels_words_and_given_no_sentence():
    for t in (0.0, 0.5, 1.5, -1.2, float("nan")):
        assert prose_helpers.significance_level(t) == "none", t
        caption = factors.momentum_caption(-0.012, t)
        assert caption == (f"Mom loading of -0.012 (t = {t:.2f}) is not statistically "
                           f"distinguishable from zero."), (t, caption)
        assert "tilt" not in caption and "confirms" not in caption, caption
    assert "nan" in factors.momentum_caption(0.1, math.nan)


def test_the_caption_takes_the_loading_and_its_t_only():
    """The sleeve's funds reached the caption only for the SPHQ sentence, which matched
    them against _MOMENTUM_THROUGH_QUALITY; both go with it."""
    assert list(inspect.signature(factors.momentum_caption).parameters) == ["b_mom", "t_mom"]
    assert not hasattr(factors, "_MOMENTUM_THROUGH_QUALITY")


def test_both_expanders_pass_the_loading_and_its_t():
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "momentum_caption"]
    assert [[ast.unparse(a) for a in c.args] + [k.arg for k in c.keywords]
            for c in calls] == [["res_mom['betas']['Mom']", "res_mom['t_stats']['Mom']"]] * 2


def test_the_methodology_note_names_the_two_sleeves_the_supplement_tests(monkeypatch):
    """Read from _intl_core_label() as the global-supplement note reads it, so each book's
    note names its own sleeve."""
    notes_by_name = {}
    for name in SLEEVE_NAMES:
        monkeypatch.setattr(factors, "_intl_core_label", lambda name=name: name)
        notes = factors.build_factor_methodology_notes({"us": None, "developed_exus": None})
        assert any(n.startswith(f"Global factor supplement ({name}):") for n in notes), (
            "premise: the note's other sentences read the same name")
        note = _momentum_note(notes)
        assert (f"to test whether the US Equity and {name} sleeves each load "
                "systematically on the momentum premium. ") in note, note
        assert "portfolio" not in note, note
        notes_by_name[name] = note
    assert len(set(notes_by_name.values())) == len(SLEEVE_NAMES), notes_by_name


def test_the_methodology_note_reads_the_international_sleeve_from_the_book(monkeypatch):
    """The personal book's VEA sleeve is International Developed; the demo's is
    International Core. The name comes from the book's international sleeves
    (sleeve_config.international_sleeves), through _intl_core_label (#591). The check this
    replaces compared the note with _intl_core_label() itself, so it held whatever the
    label returned."""
    for intl, name in ((["International Developed", "Emerging Markets"], "International Developed"),
                       (["International Core", "International Value"], "International Core")):
        monkeypatch.setattr(sleeve_config, "international_sleeves", lambda intl=intl: intl)
        assert factors._intl_core_label() == name, "premise: the label is read from the book"
        note = _momentum_note(
            factors.build_factor_methodology_notes({"us": None, "developed_exus": None}))
        assert f"whether the US Equity and {name} sleeves each load" in note, (name, note)


def test_the_methodology_note_gives_no_reason_or_expectation_for_the_loadings():
    """It said momentum is "structurally avoided in this portfolio for tax-efficiency
    reasons (high turnover → short-term gains), so a near-zero Mom loading is expected
    and confirms the construction is tax-aware", beside two loadings that are
    significant at the 1% level on both committed books (#581)."""
    notes = factors.build_factor_methodology_notes({"us": None, "developed_exus": None})
    # MIGRATED at #590: the sentence speaks of each sleeve's table and FF5 result.
    assert _momentum_note(notes).endswith("Each sleeve's supplementary table is diagnostic; "
                                          "its FF5 result is authoritative.")
    text = " ".join(notes).lower()
    for words in ("tax", "near-zero", "is expected", "structurally avoided", "turnover"):
        assert words not in text, words
