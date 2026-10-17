"""The case for inclusion the marginal-Sharpe curve makes is read one way, by
ae.sharpe_argument, on the page and in the PDF (#629, decided by the owner on October 16,
2026).

#426 moved the PDF from the curve's 10% point to the whole curve: the best weight above 0%
against the 0% point. The page kept comparing 10% with 0%, so a curve that rose at a lower
weight and fell back by 10% made the PDF's argument and not the page's. On October 17,
2026, before the change, both rules gave the same result on both committed books: demo.db's
curve rises through 10% (0.496 → 0.582, both argue 10%), and the frozen book's falls (no
argument from either). The argument names its window as equity_like_argument does.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd
import pytest

import src.asset_evaluation as ae

ROOT = Path(__file__).resolve().parent.parent
WEIGHTS = [0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10]
MVO_UP = {"sharpe_con_no": 0.50, "sharpe_con_with": 0.52}
MVO_FLAT = {"sharpe_con_no": 0.50, "sharpe_con_with": 0.50}


def _curve(sharpe, weights=WEIGHTS):
    return pd.DataFrame({"btc_alloc": weights, "sharpe": sharpe})


def test_a_rising_curve_argues_its_top_weight_and_names_its_window():
    arg, top = ae.sharpe_argument(_curve([0.50, 0.51, 0.52, 0.53, 0.55, 0.56, 0.58]), MVO_UP)
    assert top == "10%"
    assert arg == ("Sharpe-improving against SAA target weights: the curve's highest Sharpe "
                   "is at 10% allocation (0.500 at 0% → 0.580, +0.080 over the full sample, "
                   "2018 to present); the MVO sanity check is directionally consistent")


def test_a_curve_that_peaks_below_ten_percent_argues_its_best_weight():
    """#426's case: the page's old 10%-against-0% rule made no argument here."""
    arg, top = ae.sharpe_argument(_curve([0.50, 0.52, 0.55, 0.56, 0.53, 0.51, 0.49]))
    assert top == "10%"
    assert arg.startswith("Sharpe-improving against SAA target weights: the curve's highest "
                          "Sharpe is at 3% allocation (0.500 at 0% → 0.560, +0.060 over "
                          "the full sample, 2018 to present)"), arg
    assert "MVO" not in arg, "no MVO result, no note"


@pytest.mark.parametrize("mv", [MVO_FLAT, {}, None])
def test_the_mvo_note_needs_the_constrained_sharpe_to_rise(mv):
    arg, _ = ae.sharpe_argument(_curve([0.50, 0.51, 0.52, 0.53, 0.55, 0.56, 0.58]), mv)
    assert arg and "MVO" not in arg


def test_a_falling_curve_argues_nothing_over_the_range_it_read():
    assert ae.sharpe_argument(_curve([0.50, 0.49, 0.48, 0.47, 0.45, 0.43, 0.40])) == (None, "10%")


def test_a_tie_with_the_zero_point_is_no_improvement():
    assert ae.sharpe_argument(_curve([0.50, 0.50, 0.49, 0.48, 0.47, 0.46, 0.45])) == (None, "10%")


def test_no_curve_or_no_zero_point_reads_nothing():
    assert ae.sharpe_argument(pd.DataFrame(columns=["btc_alloc", "sharpe"])) == (None, None)
    assert ae.sharpe_argument(None) == (None, None)
    assert ae.sharpe_argument(_curve([0.51, 0.52], weights=[0.01, 0.10])) == (None, None)


def test_a_missing_sharpe_is_skipped_and_all_missing_argues_nothing():
    nan = float("nan")
    arg, _ = ae.sharpe_argument(_curve([0.50, nan, 0.52, nan, 0.51, nan, nan]))
    assert "is at 2% allocation" in arg, arg
    assert ae.sharpe_argument(_curve([0.50] + [nan] * 6)) == (None, "10%")


def _calls(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == name)


def test_the_page_and_the_pdf_both_read_the_curve_through_sharpe_argument():
    page = ROOT / "pages" / "5_Asset_Evaluation.py"
    reports = ROOT / "src" / "reports.py"
    assert _calls(page, "sharpe_argument") == 1
    assert _calls(reports, "sharpe_argument") == 1
    # Each passes its own MVO result, which decides the MVO note.
    assert "ae.sharpe_argument(msc, mv)" in page.read_text(encoding="utf-8")
    assert "ae.sharpe_argument(msc, mv_result)" in reports.read_text(encoding="utf-8")
    for path in (page, reports):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r'btc_alloc"\]\s*==\s*0\.10', text), path.name
        assert "at 10% allocation" not in text, path.name
