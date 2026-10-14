"""The benchmark prose's cross-reference reads significance_label's cut-offs (#542).

factors.build_benchmark_prose states the intercept's significance with
prose_helpers.significance_label, and decided whether to read the intercept as the
Brinson-Fachler selection effects ("This active return is consistent with the selection
effects documented in the Brinson-Fachler attribution", the active_return_reading label)
with its own `abs(t_a) >= 1.65`. The two agreed only while the label's 10% cut-off was
1.65: moved alone, the paragraph would call the intercept "not statistically
distinguishable from zero" and then read it as the selection effects, or the reverse.
Decided by the owner on October 14, 2026: the cross-reference reads the label's
cut-offs, and its own 1.65 goes.

`abs(t) >= 1.65` and `significance_level(t) != "none"` agree for every t, NaN included,
at today's cut-offs, so no committed lock's label class moves: the golden
(tests/test_locked_figures_golden.py) passes unchanged.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

import src.prose_helpers as prose_helpers
from src import factors

ROOT = Path(__file__).resolve().parent.parent
SELECTION = ("This active return is consistent with the selection effects documented in "
             "the Brinson-Fachler attribution")
TOP = [{"holding": "AVUV", "bench": "IWM", "sel_bps": 754.0}]
TODAY = prose_helpers.SIGNIFICANCE_CUT_OFFS
TEN_UP = (("1%", 2.58), ("5%", 1.96), ("10%", 1.80))
TEN_DOWN = (("1%", 2.58), ("5%", 1.96), ("10%", 1.30))
T_VALUES = (0.0, 1.0, 1.29, 1.3, 1.5, 1.64, 1.65, 1.7, 1.79, 1.8, 1.9, 2.0, 2.6, 3.0, -1.7,
            -3.0, float("nan"))


def _result(t_alpha: float) -> dict:
    return {"betas": {"Bench-RF": 0.99, "HML": 0.012, "SMB": 0.021, "RMW": -0.008},
            "t_stats": {"Bench-RF": 97.5, "HML": 1.1, "SMB": 2.0, "RMW": -0.4},
            "alpha_annual_bps": 138.0, "t_alpha": t_alpha, "T": 290, "r_squared": 0.981,
            "sample_start": "2025-05-01", "sample_end": "2026-06-30",
            "_hac_bse": {"const": 0.0000390}}


def _intercept_paragraph(t_alpha: float, top) -> str:
    return factors.build_benchmark_prose(_result(t_alpha), top)[1]


@pytest.mark.parametrize("cut_offs", [TODAY, TEN_UP, TEN_DOWN], ids=["today", "10%-up",
                                                                      "10%-down"])
@pytest.mark.parametrize("top", [None, TOP], ids=["generic", "with-drivers"])
def test_the_cross_reference_is_stated_exactly_when_the_label_names_a_level(
        monkeypatch, cut_offs, top):
    monkeypatch.setattr(prose_helpers, "SIGNIFICANCE_CUT_OFFS", cut_offs)
    for t in T_VALUES:
        paragraph = _intercept_paragraph(t, top)
        level = prose_helpers.significance_level(t)
        assert (SELECTION in paragraph) == (level != "none"), (cut_offs, t, paragraph)
        assert (prose_helpers.SIGNIFICANCE_WORDS["none"] in paragraph) == (level == "none")


def test_with_the_ten_percent_cut_off_moved_the_paragraph_does_not_contradict_itself(
        monkeypatch):
    """The case #542 names, both ways. At t = 1.7 with the cut-off moved up to 1.80 the
    label says the intercept cannot be told from zero, and the paragraph does not then
    read it as selection effects; at t = 1.5 with it moved down to 1.30 the label names
    the 10% level, and the paragraph does read it so. Under the old 1.65 each was the
    reverse."""
    monkeypatch.setattr(prose_helpers, "SIGNIFICANCE_CUT_OFFS", TEN_UP)
    up = _intercept_paragraph(1.7, TOP)
    assert "not statistically distinguishable from zero" in up and SELECTION not in up
    monkeypatch.setattr(prose_helpers, "SIGNIFICANCE_CUT_OFFS", TEN_DOWN)
    down = _intercept_paragraph(1.5, TOP)
    assert "marginally significant (10% level)" in down
    assert f"{SELECTION}: AVUV outperformed IWM by 754 bps." in down


def test_today_the_old_rule_and_the_labels_agree_on_every_t():
    """Why no committed lock moves: at today's cut-offs the label names a level exactly
    when |t| >= 1.65, as the old rule decided."""
    assert TODAY[-1] == ("10%", 1.65)
    for t in T_VALUES + tuple(x / 1000 for x in range(1500, 1800)):
        assert factors._distinguishable(t) == (abs(t) >= 1.65), t
    assert not factors._distinguishable(math.nan)


def test_the_benchmark_prose_types_no_t_statistic_cut_off():
    """No comparison in build_benchmark_prose sets a t-statistic against a number
    typed there: its significance is the label's."""
    tree = ast.parse((ROOT / "src" / "factors.py").read_text(encoding="utf-8"))
    func = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "build_benchmark_prose")
    typed = [ast.unparse(n) for n in ast.walk(func) if isinstance(n, ast.Compare)
             and "t_" in ast.unparse(n)
             and any(isinstance(c, ast.Constant) and isinstance(c.value, float)
                     for c in ast.walk(n))]
    assert typed == [], typed
