"""The Benchmark Attribution page's caption reads significance_label's cut-offs (#559,
decided by the owner on October 14, 2026).

factors.interpret_benchmark_attribution, printed under the page's regression, decided
with its own `_T_SIG = 2.0`: a style loading with |t| >= 2.0 was listed as a residual
style tilt, and an intercept with |t| >= 2.0 was "statistically significant". The page's
table stars a p-value below 0.10 (sig_marker), and the report's benchmark prose names
levels by significance_label, whose cut-offs are 2.58, 1.96 and 1.65 (#542). So a loading
or intercept with t between 1.65 and 2.0 was starred in the table and given a level in
the report, and read here as not significant.

The caption now asks the label: a style loading is listed when the label names a level,
and a significant intercept is called by that level's words. `_T_SIG` stays only for
interpret_sleeve_regression, which no page calls.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import src.prose_helpers as prose_helpers
from src import factors
from tests.test_interpretation_snapshots import _make_bench_res

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "pages" / "6_Benchmark_Attribution.py"
NO_TILT = "No statistically significant residual style tilts beyond the SAA benchmark"
VALUE_TILT = "HML: positive value tilt (VTV, AVUV)"
NOT_SIGNIFICANT = "The active return intercept is not statistically significant"
TEN_UP = (("1%", 2.58), ("5%", 1.96), ("10%", 1.80))
TEN_DOWN = (("1%", 2.58), ("5%", 1.96), ("10%", 1.30))
T_VALUES = (0.0, 0.9, 1.3, 1.5, 1.65, 1.7, 1.8, 1.9, 1.96, 1.99, 2.2, 2.6, 4.0, -1.7, -3.1,
            float("nan"))


def _caption(t_hml: float = 0.3, t_alpha: float = 0.3) -> str:
    return factors.interpret_benchmark_attribution(_make_bench_res(
        b_hml=0.10, t_hml=t_hml, t_smb=0.2, t_rmw=0.4, alpha_bps=120.0, t_alpha=t_alpha))


@pytest.mark.parametrize("cut_offs", [prose_helpers.SIGNIFICANCE_CUT_OFFS, TEN_UP, TEN_DOWN],
                         ids=["today", "10%-up", "10%-down"])
def test_the_caption_reads_the_level_the_label_names(monkeypatch, cut_offs):
    monkeypatch.setattr(prose_helpers, "SIGNIFICANCE_CUT_OFFS", cut_offs)
    for t in T_VALUES:
        level = prose_helpers.significance_level(t)
        tilt, intercept = _caption(t_hml=t), _caption(t_alpha=t)
        if level == "none":
            assert NO_TILT in tilt and VALUE_TILT not in tilt, (cut_offs, t, tilt)
            assert NOT_SIGNIFICANT in intercept, (cut_offs, t, intercept)
        else:
            assert VALUE_TILT in tilt and NO_TILT not in tilt, (cut_offs, t, tilt)
            assert (f"The active return intercept is {prose_helpers.SIGNIFICANCE_WORDS[level]}: "
                    f"a positive return") in intercept, (cut_offs, t, intercept)


def test_t_between_the_label_cut_offs_and_two_is_not_read_as_not_significant():
    """The cases #559 names. t = 1.8 is starred in the table (p below 0.10) and named
    "marginally significant (10% level)" by the label; t = 1.98 is "statistically
    significant at the 5% level". Under the caption's own 2.0 both read as not
    significant."""
    assert factors.sig_marker(0.072) == "*", "premise: the table stars p = 0.072"
    assert VALUE_TILT in _caption(t_hml=1.8)
    assert ("The active return intercept is marginally significant (10% level): a positive "
            "return") in _caption(t_alpha=1.8)
    assert ("The active return intercept is statistically significant at the 5% level: a "
            "positive return") in _caption(t_alpha=1.98)


def test_the_caption_compares_no_t_statistic_with_a_number_of_its_own():
    """Besides the signs (a comparison with zero), the one comparison with a typed number
    left is the benchmark beta's distance from one, which reads a beta, not a
    t-statistic."""
    tree = ast.parse(inspect.getsource(factors.interpret_benchmark_attribution))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "_T_SIG" not in names
    typed = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Compare)
             and any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
                     and not isinstance(c.value, bool) and c.value != 0
                     for c in ast.walk(n))]
    assert typed == ["abs(b_bench - 1.0) < 0.1"], typed


def test_the_page_prints_the_caption_and_types_no_cut_off():
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "interpret_benchmark_attribution"]
    assert len(calls) == 1, len(calls)
    typed = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Compare)
             and "t_" in ast.unparse(n)
             and any(isinstance(c, ast.Constant) and isinstance(c.value, float)
                     for c in ast.walk(n))]
    assert typed == [], typed
