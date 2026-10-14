"""The Factor Profile page's momentum caption reads significance_label's cut-offs (#551,
decided by the owner on October 14, 2026).

The caption under the "Carhart Momentum Supplement (FF5 + UMD)" table, in both of the
page's expanders, decided with its own `abs(_mom_t) >= 1.96`. The table above it stars a
p-value below 0.10, and the report's prose names levels by significance_label, whose 10%
cut-off is 1.65, so a loading with t between 1.65 and 1.96 was starred in the table and
read as "no systematic momentum tilt" under it. Its significant branch also said the
loading was "economically small" and that "the loading's size confirms momentum is not a
deliberate tilt" without reading the loading: no threshold the code defines says what a
small momentum loading is, so those words go.

Both expanders now print factors.momentum_caption.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

import src.prose_helpers as prose_helpers
from src import factors

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "pages" / "4_Factor_Profile.py"
NO_TILT = "confirms the portfolio carries no systematic momentum tilt"
TEN_UP = (("1%", 2.58), ("5%", 1.96), ("10%", 1.80))
TEN_DOWN = (("1%", 2.58), ("5%", 1.96), ("10%", 1.30))
T_VALUES = (0.0, 0.9, 1.3, 1.5, 1.65, 1.7, 1.8, 1.9, 1.96, 2.2, 2.6, 4.0, -1.7, -3.1,
            float("nan"))


@pytest.mark.parametrize("cut_offs", [prose_helpers.SIGNIFICANCE_CUT_OFFS, TEN_UP, TEN_DOWN],
                         ids=["today", "10%-up", "10%-down"])
def test_the_caption_reads_the_level_the_label_names(monkeypatch, cut_offs):
    monkeypatch.setattr(prose_helpers, "SIGNIFICANCE_CUT_OFFS", cut_offs)
    for t in T_VALUES:
        caption = factors.momentum_caption(0.041, t)
        level = prose_helpers.significance_level(t)
        if level == "none":
            assert NO_TILT in caption, (cut_offs, t, caption)
        else:
            assert NO_TILT not in caption, (cut_offs, t, caption)
            assert f"is {prose_helpers.SIGNIFICANCE_WORDS[level]}." in caption, (t, caption)


def test_a_loading_between_the_10_and_5_percent_cut_offs_is_not_read_as_no_tilt():
    """The case #551 names: t = 1.8 is starred in the table (p below 0.10) and named
    "marginally significant (10% level)" by the label. Under the caption's own 1.96 it
    read "confirms the portfolio carries no systematic momentum tilt"."""
    caption = factors.momentum_caption(0.052, 1.8)
    assert caption.startswith("Mom loading of +0.052 (t = 1.80) is marginally significant "
                              "(10% level). "), caption
    assert NO_TILT not in caption
    assert factors.sig_marker(0.072) == "*", "premise: the table stars p = 0.072"


def test_the_caption_states_no_size_it_does_not_read():
    for b in (0.004, 0.05, 0.4, -0.3):
        for t in (0.5, 1.8, 2.5, 6.0):
            caption = factors.momentum_caption(b, t)
            assert "economically" not in caption and "size" not in caption, caption
            assert caption.startswith(f"Mom loading of {b:+.3f} (t = {t:.2f}) "), caption
    assert "nan" in factors.momentum_caption(0.1, math.nan) and NO_TILT in (
        factors.momentum_caption(0.1, math.nan))


def test_both_expanders_print_the_one_caption_and_the_page_types_no_cut_off():
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "momentum_caption"]
    assert len(calls) == 2, len(calls)
    typed = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Compare)
             and "_t" in ast.unparse(n)
             and any(isinstance(c, ast.Constant) and isinstance(c.value, float)
                     for c in ast.walk(n))]
    assert typed == [], typed
    source = PAGE.read_text(encoding="utf-8")
    assert "economically small" not in source and "abs(_mom_t)" not in source
