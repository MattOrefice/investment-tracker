"""One function computes the alpha's 95% interval, and the pages and the report read it
(#525's comment, decided by the owner on October 13, 2026).

#532's mutant run found the rule written twice: factors.alpha_ci_str, which the Factor
Profile and Benchmark Attribution pages print, and a second copy inside
factors.build_benchmark_prose, which the PDF prints. Changing 252 to 250 in the report's
copy failed the locked-figures golden; the same change in alpha_ci_str did not, because
no locked report calls it. A change to one copy would have left a page and the PDF
printing different intervals for the same regression. Both now read
factors.alpha_interval_bps, and format its ends with factors.interval_end_str.

The locked-figures golden passes unchanged: the interval is computed as before.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src import factors

ROOT = Path(__file__).resolve().parent.parent


def _result(alpha_bps: float, se_daily: float) -> dict:
    """A benchmark regression's result, as build_benchmark_prose reads one."""
    return {"betas": {"Bench-RF": 0.99, "HML": 0.012, "SMB": 0.021, "RMW": -0.008},
            "t_stats": {"Bench-RF": 97.5, "HML": 1.1, "SMB": 2.0, "RMW": -0.4},
            "alpha_annual_bps": alpha_bps, "t_alpha": 1.4, "T": 290, "r_squared": 0.981,
            "sample_start": "2025-05-01", "sample_end": "2026-06-30",
            "_hac_bse": {"const": se_daily}}


def _page_interval(result: dict) -> "str | None":
    found = re.search(r"\[([^\]]+)\]", factors.alpha_ci_str(result))
    return found.group(1) if found else None


def _report_interval(result: dict) -> "str | None":
    prose = " ".join(factors.build_benchmark_prose(result))
    found = re.search(r"95% CI: \[([^\]]+)\]", prose)
    return found.group(1) if found else None


CASES = [(138.0, 0.0000390), (-65.0, 0.0000270), (12.0, 0.0000020), (-300.0, 0.0000041),
         (52.0, float("nan"))]


@pytest.mark.parametrize("alpha_bps, se_daily", CASES)
def test_the_pages_and_the_report_print_the_same_interval(alpha_bps, se_daily):
    result = _result(alpha_bps, se_daily)
    assert _page_interval(result) == _report_interval(result)
    if se_daily != se_daily:
        assert _page_interval(result) is None, "no standard error, no interval"
        assert "(t = 1.40)" in " ".join(factors.build_benchmark_prose(result))
    else:
        assert _page_interval(result) is not None


def test_the_interval_is_the_alpha_plus_or_minus_196_annualized_standard_errors():
    lo, hi = factors.alpha_interval_bps(_result(138.0, 0.0000390))
    se_bps = 0.0000390 * 252 * 10_000
    assert (lo, hi) == (138.0 - 1.96 * se_bps, 138.0 + 1.96 * se_bps)
    assert factors.alpha_interval_bps(_result(138.0, float("nan"))) is None
    assert factors.alpha_interval_bps({"alpha_annual_bps": 1.0}) is None
    assert factors.alpha_ci_str(_result(138.0, 0.0000390)) == "+138 bps/yr [-55, +331]"


def test_an_interval_end_has_one_minus_sign():
    assert [factors.interval_end_str(x) for x in (-53.4, 306.6, -0.4, 0.0)] == [
        "-53", "+307", "-0", "+0"]


def test_both_read_the_one_function(monkeypatch):
    """Whatever the one function returns is what the pages and the report print."""
    result = _result(138.0, 0.0000390)
    monkeypatch.setattr(factors, "alpha_interval_bps", lambda r: (-1.2, 2.4))
    assert _page_interval(result) == _report_interval(result) == "-1, +2"
    monkeypatch.setattr(factors, "alpha_interval_bps", lambda r: None)
    assert _page_interval(result) is None and _report_interval(result) is None


def _function_of(tree: ast.Module, node: ast.AST) -> "str | None":
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.lineno <= node.lineno <= fn.end_lineno:
            inner = _function_of_body(fn, node)
            return inner or fn.name
    return None


def _function_of_body(fn: ast.FunctionDef, node: ast.AST) -> "str | None":
    for sub in ast.walk(fn):
        if (sub is not fn and isinstance(sub, ast.FunctionDef)
                and sub.lineno <= node.lineno <= sub.end_lineno):
            return sub.name
    return None


def test_the_rule_is_written_once():
    """The interval's 1.96 appears in factors.py only inside alpha_interval_bps, and the
    one-minus-sign format only inside interval_end_str."""
    source = (ROOT / "src" / "factors.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    z = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == 1.96]
    assert len(z) == 2, "premise: the low end and the high end"
    assert {_function_of(tree, n) for n in z} == {"alpha_interval_bps"}
    # A format spec is a JoinedStr of its own at the same place, hence the set.
    formats = {(n.lineno, n.col_offset) for n in ast.walk(tree)
               if isinstance(n, ast.JoinedStr)
               and ast.get_source_segment(source, n).startswith('f"-{abs(')}
    assert len(formats) == 1
    line, _col = formats.pop()
    assert _function_of(tree, ast.Constant(value=None, lineno=line)) == "interval_end_str"
